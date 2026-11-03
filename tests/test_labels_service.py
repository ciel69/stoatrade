"""tb.labels.service (R1.6, задача 12): цикл захвата и разметки на фейковом fetch, heartbeat, блокировка второй копии, пауза 403/429, checkpoint,
свойство 8 (тени не торгуют: файлы state/plans, trades, decisions*, bus не меняются, событий нет), свойство 12 (окружение без ключей),
расширение платформы (ORDER/DEFAULTS/service_cmd/child_env) и пороги жёлтого статуса. Сеть не используется."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import _labels_helpers as H
from tb.core import bus
from tb.labels import klines, label, query, schema, service, store
from tb.platform import services as S
from tb.platform import status as ST

DAY = 86400


def snapshot(root):
    """{путь: (размер, sha1)} всех файлов корня, кроме самой базы меток, её копий и входного hot_events.jsonl, сервисных heartbeat/lock labels."""
    skip_dirs = (os.path.join(root, "state", "backups"),)
    out = {}
    for dp, dn, fn in os.walk(root):
        if dp in skip_dirs:
            continue
        for n in fn:
            p = os.path.join(dp, n)
            rel = os.path.relpath(p, root).replace("\\", "/")
            if rel.startswith("state/knowledge.") or rel == "runtime/hot_events.jsonl" or rel.startswith("runtime/platform/labels."):
                continue
            with open(p, "rb") as f:
                data = f.read()
            out[rel] = (len(data), hashlib.sha1(data).hexdigest())
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        self.mk = H.Market()
        self.btc = H.FakeBtc(self.mk)
        self.logs = []
        self.svc = None

    def mkservice(self, **kw):
        kw.setdefault("fetch", self.mk.fetch)
        kw.setdefault("btc_cache", self.btc)
        self.svc = service.Service(self.root, log=self.logs.append, **kw)
        self.addCleanup(self.svc.close)
        return self.svc

    def hb(self, now=None):
        age, d = S.read_hb("labels", self.root, now)
        return d

    def count(self, sql, *a):
        return self.svc.conn.execute(sql, a).fetchone()[0]


class TestLoop(Base):
    def test_capture_then_label_when_mature(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0), H.hot_event(1, sym="SOLUSDT", direction="down")])
        n = s.tick(H.T0 + 10)
        self.assertEqual(n, 0)                                                                   # захвачено, но не созрело
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='pending'"), 2)
        self.assertEqual(self.mk.calls, [])
        mature = self.count("SELECT MIN(mature_ts) FROM signals")
        self.assertEqual(s.tick(mature - 1), 0)
        n = s.tick(mature + 2 * 60)                                                              # созрели обе (t0 и t0+1)
        self.assertEqual(n, 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM outcomes"), 6)
        self.assertGreater(self.count("SELECT COUNT(*) FROM policy_runs"), 0)

    def test_capture_period_and_batch_size(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(i, sym="C%dUSDT" % i) for i in range(5)])
        s.cfg["label_batch"] = 2
        s.tick(H.T0 + 10)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals"), 5)
        H.write_events(self.root, [H.hot_event(50, sym="LATEUSDT")])
        s.tick(H.T0 + 15)                                                                        # 5 с после прошлого захвата: рано
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals"), 5)
        s.tick(H.T0 + 25)                                                                        # 15 с прошло
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals"), 6)
        mature = self.count("SELECT MAX(mature_ts) FROM signals WHERE sym!='LATEUSDT'")
        s.tick(mature)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), 2)       # label_batch=2
        s.tick(mature + 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), 4)

    def test_rate_limit_pauses_all_requests_60s(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0), H.hot_event(1, sym="SOLUSDT")])
        s.tick(H.T0 + 10)
        mature = self.count("SELECT MAX(mature_ts) FROM signals")
        self.mk.fail = klines.RateLimited("HTTP 429")
        s.tick(mature)
        calls = len(self.mk.calls)
        self.assertEqual(calls, 1)                                                               # после первой же 429 остальные метки не трогаем
        self.assertEqual(len(s.rate_events), 1)
        self.mk.fail = None
        s.tick(mature + 30)
        self.assertEqual(len(self.mk.calls), calls)                                              # пауза 60 с
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE attempts>0"), 0)          # попытки не тратились
        s.tick(mature + 61)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), 2)
        self.assertEqual(self.hb()["rate_limited_24h"], 1)

    def test_unexpected_error_defers_row_and_loop_survives(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0)])
        s.tick(H.T0 + 10)
        mature = self.count("SELECT mature_ts FROM signals")
        self.mk.fail = ValueError("странный ответ")
        self.assertEqual(s.tick(mature), 1)
        self.assertEqual(s.last_error, "ValueError")
        self.assertEqual(self.count("SELECT attempts FROM signals"), 1)
        self.assertTrue(any("не размечена" in m for m in self.logs))
        self.mk.fail = None
        self.assertEqual(s.tick(mature + 31 * 60), 1)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), 1)

    def test_slow_batch_keeps_heartbeat_fresh_and_is_cut_by_budget(self):
        """Реальная сеть: ~2 с на метку, при сбое 22+ с. Пачка из 20 меток без heartbeat дольше hb_stale_s=120 с: супервизор убил бы сервис.
        Внутри прохода heartbeat пишется не реже HB_EVERY_S, а сам проход обрывается по label_budget_s (остаток уйдёт в следующий)."""
        class Clk(object):
            t = float(H.T0 + 10)
        clk = Clk()

        def slow_fetch(sym, interval, start_s, end_s):
            clk.t += 10.0                                                   # 3 запроса на метку -> 30 с на метку
            return self.mk.fetch(sym, interval, start_s, end_s)
        s = self.mkservice(fetch=slow_fetch, clock=lambda: clk.t)
        H.write_events(self.root, [H.hot_event(i, sym="C%dUSDT" % i) for i in range(6)])
        s.tick(H.T0 + 10)
        mature = self.count("SELECT MAX(mature_ts) FROM signals")
        clk.t = float(mature)
        beats = []
        real_hb = s.heartbeat
        s.heartbeat = lambda now, force=False: (beats.append(now), real_hb(now, force))[1]
        n = s.tick()
        done = self.count("SELECT COUNT(*) FROM signals WHERE status='done'")
        self.assertEqual(n, done)
        self.assertLess(done, 6)                                            # бюджет 45 с оборвал проход после 2 меток (по 30 с)
        self.assertGreaterEqual(done, 1)
        self.assertTrue(any(b > mature + service.HB_EVERY_S for b in beats), beats)   # heartbeat писался внутри прохода
        s.tick()
        self.assertGreater(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), done)

    def test_disabled_does_not_capture(self):
        with open(os.path.join(self.root, "config", "labels.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["enabled"] = False
        s = self.mkservice(cfg=cfg)
        H.write_events(self.root, [H.hot_event(0)])
        s.tick(H.T0 + 10)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals"), 0)
        self.assertFalse(self.hb()["enabled"])

    def test_checkpoint_passive_and_daily_truncate(self):
        class Rec(object):
            def __init__(self, c):
                self.c, self.sql = c, []

            def execute(self, sql, *a):
                self.sql.append(sql)
                return self.c.execute(sql, *a)

            def __getattr__(self, n):
                return getattr(self.c, n)
        real = store.open_rw(self.root)
        self.addCleanup(real.close)
        rec = Rec(real)
        s = service.Service(self.root, fetch=self.mk.fetch, btc_cache=self.btc, log=self.logs.append, conn=rec, clock=lambda: 1000.0)
        pr = lambda: [x for x in rec.sql if "wal_checkpoint" in x]      # noqa: E731
        s.checkpoint_step(1000.0 + 9 * 60)
        self.assertEqual(pr(), [])
        s.checkpoint_step(1000.0 + 10 * 60)
        self.assertEqual(pr(), ["PRAGMA wal_checkpoint(PASSIVE)"])
        s.checkpoint_step(1000.0 + 11 * 60)
        self.assertEqual(len(pr()), 1)
        s.checkpoint_step(1000.0 + DAY)
        self.assertEqual(pr()[-1], "PRAGMA wal_checkpoint(TRUNCATE)")
        s.checkpoint_step(1000.0 + DAY + 10 * 60)
        self.assertEqual(pr()[-1], "PRAGMA wal_checkpoint(PASSIVE)")


class TestHeartbeat(Base):
    FIELDS = ("captured_24h", "labeled_24h", "pending", "due", "failed_24h", "no_data_24h", "rate_limited_24h", "req_per_min", "bad_lines",
              "last_error", "db_mb", "missing_share_24h", "warnings")

    def test_fields_and_values(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0), H.hot_event(1, sym="SOLUSDT"), H.hot_event(2, sym="ETHUSDT")])
        with open(os.path.join(self.root, "runtime", "hot_events.jsonl"), "ab") as f:
            f.write(b"not json\n")
        s.tick(H.T0 + 10)
        d = self.hb()
        for k in self.FIELDS:
            self.assertIn(k, d)
        self.assertEqual((d["captured_24h"], d["labeled_24h"], d["pending"], d["due"], d["failed_24h"], d["no_data_24h"], d["bad_lines"]), (3, 0, 3, 0, 0, 0, 1))
        self.assertEqual(d["warnings"], [])
        self.assertIsNone(d["last_error"])
        self.assertGreater(d["db_mb"], 0)
        self.assertIsNone(d["missing_share_24h"])
        self.assertEqual(d["service"], "labels")
        mature = self.count("SELECT MAX(mature_ts) FROM signals")
        self.assertEqual(self.hb()["due"], 0)
        s.t_stats = None
        self.mk.empty = False
        s.tick(mature + 100)                                                                     # размечаются, пересчёт счётчиков
        d = self.hb()
        self.assertEqual((d["labeled_24h"], d["pending"], d["due"]), (3, 0, 0))
        self.assertIsNotNone(d["missing_share_24h"])
        self.assertGreater(d["req_per_min"], 0)

    def test_due_failed_and_no_data_counters(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(i, sym="C%dUSDT" % i) for i in range(4)])
        s.tick(H.T0 + 10)
        mature = self.count("SELECT MAX(mature_ts) FROM signals")
        s.conn.execute("UPDATE signals SET status='failed', code='FETCH', labeled_ts=? WHERE sym='C0USDT'", (mature,))
        s.conn.execute("UPDATE signals SET status='no_data', code='NO_DATA', labeled_ts=? WHERE sym='C1USDT'", (mature,))
        s.conn.execute("UPDATE signals SET status='failed', code='FETCH', labeled_ts=? WHERE sym='C2USDT'", (mature - 2 * DAY,))   # старше суток
        s.t_stats = None
        s.heartbeat(mature + 10, force=True)
        d = self.hb()
        self.assertEqual((d["failed_24h"], d["no_data_24h"], d["pending"], d["due"]), (1, 1, 1, 1))

    def test_sync_dir_warning(self):
        d = tempfile.mkdtemp(prefix="OneDrive_tb_")
        self.addCleanup(__import__("shutil").rmtree, d, True)
        self.root = d
        os.makedirs(os.path.join(d, "runtime"))
        s = service.Service(d, cfg={"enabled": True}, fetch=self.mk.fetch, btc_cache=self.btc, log=self.logs.append)
        self.addCleanup(s.close)
        s.heartbeat(H.T0, force=True)
        self.assertIn(schema.W_LABELS_SYNC_DIR, self.hb()["warnings"])

    def test_db_corrupt_is_reported(self):
        self.addCleanup(setattr, store, "LAST_ERROR", None)
        os.makedirs(os.path.join(self.root, "state"))
        with open(os.path.join(self.root, "state", "knowledge.sqlite"), "wb") as f:
            f.write(b"this is not a sqlite database" * 100)
        s = self.mkservice()
        s.heartbeat(H.T0, force=True)
        d = self.hb()
        self.assertEqual(d["last_error"], schema.DB_CORRUPT)
        self.assertIn(schema.DB_CORRUPT, d["warnings"])
        self.assertTrue([n for n in os.listdir(os.path.join(self.root, "state")) if n.startswith("knowledge.corrupt-")])


class TestLock(Base):
    def test_second_copy_blocked(self):
        self.assertTrue(service.acquire_lock(self.root))
        self.assertTrue(os.path.exists(service.lock_path(self.root)))
        self.assertEqual(os.path.basename(service.lock_path(self.root)), "labels.lock")
        self.assertFalse(service.acquire_lock(self.root))                                        # владелец жив (этот процесс)
        service.release_lock(self.root)
        self.assertFalse(os.path.exists(service.lock_path(self.root)))
        self.assertTrue(service.acquire_lock(self.root))
        service.release_lock(self.root)

    def test_stale_lock_of_dead_process_is_taken_over(self):
        p = subprocess.Popen([sys.executable, "-c", "pass"])
        p.wait()
        os.makedirs(os.path.dirname(service.lock_path(self.root)), exist_ok=True)
        with open(service.lock_path(self.root), "w") as f:
            f.write(str(p.pid))
        self.assertTrue(service.acquire_lock(self.root))
        with open(service.lock_path(self.root)) as f:
            self.assertEqual(int(f.read()), os.getpid())
        service.release_lock(self.root)

    def test_main_exits_2_when_locked_and_keeps_foreign_lock(self):
        self.assertTrue(service.acquire_lock(self.root))
        self.addCleanup(service.release_lock, self.root)
        with mock.patch.dict(os.environ, {"TB_ROOT": self.root}), mock.patch("builtins.print"):
            self.assertEqual(service.main(["run", "--max-seconds", "0.001"]), 2)
        self.assertTrue(os.path.exists(service.lock_path(self.root)))                            # чужой замок не снят

    def test_main_runs_and_releases(self):
        with mock.patch.dict(os.environ, {"TB_ROOT": self.root}), mock.patch("builtins.print"):
            self.assertEqual(service.main(["run", "--max-seconds", "0.001"]), 0)
        self.assertFalse(os.path.exists(service.lock_path(self.root)))
        self.assertTrue(os.path.exists(S.hb_file("labels", self.root)))
        self.assertTrue(os.path.exists(store.db_path(self.root)))

    def test_usage_error_is_2(self):
        with mock.patch("sys.stderr", new=__import__("io").StringIO()):
            self.assertEqual(service.main(["bogus"]), 2)


class TestNoTrading(Base):
    """Свойство 8: разметка и тени не создают планов, сделок, решений и событий шины."""

    def test_state_untouched_and_no_bus_events(self):
        for rel, text in (("state/plans/plan-1.json", '{"id":"plan-1"}'), ("state/trades.jsonl", '{"id":"t1"}\n'), ("state/decisions-20261102.jsonl", '{"d":1}\n'),
                          ("state/decisions.jsonl", '{"d":0}\n'), ("state/bus/events.jsonl", '{"seq":1}\n'), ("runtime/bus/x.json", "{}")):
            p = os.path.join(self.root, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(text)
        before = snapshot(self.root)
        seq0 = bus.last_seq(root=self.root)
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0), H.hot_event(1, sym="SOLUSDT", direction="down"), H.hot_event(2, sym="ETHUSDT", direction=None)])
        s.tick(H.T0 + 10)
        mature = self.count("SELECT MAX(mature_ts) FROM signals")
        s.tick(mature + 100)
        self.assertEqual(self.count("SELECT COUNT(*) FROM signals WHERE status='done'"), 3)
        self.assertGreater(self.count("SELECT COUNT(*) FROM policy_runs WHERE filled=1"), 0)      # тени реально прогнаны
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual(bus.last_seq(root=self.root), seq0)

    def test_labels_package_has_no_bus_or_trading_imports(self):
        d = os.path.join(_bootstrap.SRC, "tb", "labels")
        bad = re.compile(r"^\s*(?:from|import)\s+tb\.(?:core\.bus|core\.plans?|executor|venues|gateway|llm|brain|control|intel\.items|core\.journal)", re.M)
        for n in os.listdir(d):
            if n.endswith(".py"):
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    self.assertIsNone(bad.search(f.read()), n)


class TestEnvironment(unittest.TestCase):
    """Свойство 12: у процесса labels нет переменных с ключами."""
    SECRETS = {"BYBIT_API_KEY": "x", "BYBIT_ENV": "mainnet", "ANTHROPIC_API_KEY": "x", "ANTHROPIC_BASE_URL": "u", "DEEPSEEK_API_KEY": "x", "DEEPSEEK_MODEL": "m",
               "OPENAI_API_KEY": "x", "OPENAI_ORG": "o", "MY_API_KEY": "x", "GH_TOKEN": "x", "SOME_SECRET": "x", "lower_token": "x", "bybit_lower": "x"}
    SAFE = {"PATH": "/bin", "HOME": "/h", "TB_ROOT": "/old", "SOMETHING": "1", "TOKENIZER": "1", "SECRETARY": "1", "PYTHONPATH": "/p"}

    def test_child_env_strips_every_secret_for_labels(self):
        env = S.child_env("labels", dict(self.SECRETS, **self.SAFE), "/tmp/r", profile="paper")
        for k in self.SECRETS:
            self.assertNotIn(k, env, k)
        for k in ("PATH", "HOME", "SOMETHING", "TOKENIZER", "SECRETARY"):
            self.assertIn(k, env, k)
        self.assertEqual(env["TB_PLATFORM_SERVICE"], "labels")
        self.assertEqual(env["TB_ROOT"], os.path.abspath("/tmp/r"))

    def test_other_services_keep_non_key_variables(self):
        env = S.child_env("scan", dict(self.SECRETS, **self.SAFE), "/tmp/r")
        self.assertIn("DEEPSEEK_MODEL", env)                      # общая чистка прежняя (BYBIT_/ANTHROPIC_/прокси), расширена только для labels
        self.assertNotIn("BYBIT_API_KEY", env)

    def test_scrub_env_in_process(self):
        env = dict(self.SECRETS, **self.SAFE)
        self.assertEqual(service.scrub_env(env), len(self.SECRETS))
        self.assertEqual(sorted(env), sorted(self.SAFE))

    def test_main_scrubs_os_environ(self):
        root = tempfile.mkdtemp(prefix="tblabenv_")
        self.addCleanup(__import__("shutil").rmtree, root, True)
        with mock.patch.dict(os.environ, dict(self.SECRETS, TB_ROOT=root)), mock.patch("builtins.print"):
            self.assertEqual(service.main(["run", "--max-seconds", "0.001"]), 0)
            for k in self.SECRETS:
                self.assertNotIn(k, os.environ, k)

    def test_no_key_directory_access_in_package(self):
        d = os.path.join(_bootstrap.SRC, "tb", "labels")
        for n in os.listdir(d):
            if n.endswith(".py"):
                with open(os.path.join(d, n), encoding="utf-8") as f:
                    src = f.read()
                self.assertNotIn("TB_KEYS_DIR", src, n)
                self.assertNotIn("keyweb", src, n)
                self.assertNotIn(".config/traider-bot", src, n)


class TestPlatformRegistration(unittest.TestCase):
    def test_order_names_defaults(self):
        self.assertIn("labels", S.ORDER)
        self.assertIn("labels", S.SERVICE_NAMES)
        self.assertLess(S.ORDER.index("dash"), S.ORDER.index("labels"))
        for prof in S.PROFILES:
            self.assertIn("labels", S.DEFAULTS["services"][prof])
        self.assertEqual(S.DEFAULTS["svc"]["labels"]["hb"], "file")
        self.assertEqual(S.parse_services("labels,exec"), ["exec", "labels"])

    def test_config_platform_json_has_labels_in_all_profiles(self):
        with open(os.path.join(H.PROJECT, "config", "platform.json"), encoding="utf-8") as f:
            d = json.load(f)
        for prof in ("paper", "testnet", "trade"):
            self.assertIn("labels", d["services"][prof], prof)
        self.assertEqual(d["svc"]["labels"]["hb"], "file")
        cfg = S.load_cfg(None)
        for prof in S.PROFILES:
            sv = S.default_services(prof, cfg)
            self.assertIn("labels", sv)
            self.assertEqual(sv, S.parse_services(",".join(sv)))

    def test_service_cmd(self):
        cfg = S.load_cfg(None)
        self.assertEqual(S.service_cmd("labels", "paper", False, cfg, py="py"), ["py", "-m", "tb.labels.service", "run"])


class TestStatus(unittest.TestCase):
    """Жёлтый статус при due > 500 или failed_24h > 5% захваченных за 24 ч (config/labels.json:warn)."""

    def setUp(self):
        self.root = H.mk_root(self)
        self.now = 1_800_000_000.0

    def put(self, **hb):
        svc = {"state": "running", "pid": 1, "started_ts": self.now - 50, "restarts": 0, "tries": 0, "hb_ok": True, "hb_age_s": 1.0}
        S.write_json(S.lock_file(self.root), {"pid": os.getpid(), "started": self.now - 100, "profile": "paper", "observe": False, "services": ["exec", "labels"]})
        S.write_json(S.status_file(self.root), {"ts_epoch": self.now, "running": True, "services": {"exec": dict(svc), "labels": dict(svc)}})
        S.write_json(os.path.join(self.root, "runtime", "exec_health.json"), {"ts_epoch": self.now, "alive": True, "mode": "paper", "positions": 0, "hold": None})
        os.utime(os.path.join(self.root, "runtime", "exec_health.json"), (self.now, self.now))
        base = {"captured_24h": 1000, "labeled_24h": 900, "pending": 10, "due": 0, "failed_24h": 0, "no_data_24h": 0, "rate_limited_24h": 0, "req_per_min": 10,
                "bad_lines": 0, "last_error": None, "db_mb": 5.0, "missing_share_24h": 0.0, "warnings": []}
        base.update(hb)
        S.write_hb("labels", base, root=self.root, now=self.now)

    def status(self):
        return ST.build_status(self.root, self.now)

    def test_green_when_healthy(self):
        self.put()
        st = self.status()
        self.assertEqual(st["color"], "green", st["reasons"])

    def test_due_threshold(self):
        self.put(due=500)
        self.assertEqual(self.status()["color"], "green")
        self.put(due=501)
        st = self.status()
        self.assertEqual(st["color"], "yellow")
        self.assertTrue([r for r in st["reasons"] if "labels" in r and "501" in r], st["reasons"])

    def test_failed_share_threshold(self):
        self.put(captured_24h=1000, failed_24h=50)
        self.assertEqual(self.status()["color"], "green")                                        # ровно 5%
        self.put(captured_24h=1000, failed_24h=51)
        st = self.status()
        self.assertEqual(st["color"], "yellow")
        self.assertTrue([r for r in st["reasons"] if "51 из 1000" in r], st["reasons"])
        self.put(captured_24h=0, failed_24h=1)                                                   # нечего делить - один отказ уже сверх порога
        self.assertEqual(self.status()["color"], "yellow")

    def test_corrupt_and_sync_warnings(self):
        self.put(last_error="DB_CORRUPT", warnings=["DB_CORRUPT"])
        self.assertTrue([r for r in self.status()["reasons"] if "DB_CORRUPT" in r])
        self.put(warnings=["W_LABELS_SYNC_DIR"])
        st = self.status()
        self.assertEqual(st["color"], "yellow")
        self.assertTrue([r for r in st["reasons"] if "W_LABELS_SYNC_DIR" in r])

    def test_thresholds_follow_config(self):
        p = os.path.join(self.root, "config", "labels.json")
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        d["warn"] = {"due": 10, "failed_share": 0.5}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(d, f)
        self.put(due=11)
        self.assertEqual(self.status()["color"], "yellow")
        self.put(due=10, captured_24h=100, failed_24h=40)
        self.assertEqual(self.status()["color"], "green")

    def test_not_running_service_not_judged(self):
        self.put(due=9999)
        s = S.read_json(S.status_file(self.root))
        s["services"]["labels"]["state"] = "failed"
        S.write_json(S.status_file(self.root), s)
        st = self.status()
        self.assertEqual(st["color"], "red")
        self.assertFalse([r for r in st["reasons"] if "очередь" in r])

    def test_pure_reasons_function(self):
        self.assertEqual(ST.labels_reasons({}), [])
        self.assertEqual(ST.labels_reasons({"due": 501, "failed_24h": 0}, {"due": 500}).__len__(), 1)
        self.assertEqual(ST.labels_reasons({"due": 5, "captured_24h": 100, "failed_24h": 6}, None).__len__(), 1)
        self.assertEqual(ST.labels_reasons({"due": "x", "failed_24h": True}), [])


class TestQueryThroughput(Base):
    def test_throughput_matches_heartbeat_after_loop(self):
        s = self.mkservice()
        H.write_events(self.root, [H.hot_event(0), H.hot_event(1, sym="SOLUSDT")])
        s.tick(H.T0 + 10)
        mature = self.count("SELECT MAX(mature_ts) FROM signals")
        s.tick(mature + 10)
        t = query.throughput(self.root, mature + 10)
        self.assertEqual((t["captured_24h"], t["labeled_24h"], t["pending"], t["due"]), (2, 2, 0, 0))
        self.assertGreater(t["db_mb"], 0)
        self.assertGreater(t["cursor_offset"], 0)
        self.assertIsNone(t["oldest_pending_age_s"])


if __name__ == "__main__":
    unittest.main()
