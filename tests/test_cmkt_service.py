"""R1.7, задача 17: сервис сводки `cmkt`, сборщики `cmkt@*` под супервизором, строка статуса и команды `./tb cmkt`
(Requirements 3.1, 11.3, 11.4, 12.5, 18.1, 18.2, 18.3; свойство 21).

Всё во временном корне: реальные биржи в config/cmkt.json выключены, включена только поддельная `fakex` (её сборщик не ходит в сеть:
ws://127.0.0.1 без allow_plain отклоняется белым списком). Боевые runtime/ и state/ не трогаются.
"""
import contextlib
import copy
import io
import json
import os
import time
import unittest

import _bootstrap  # noqa: F401
import _platform_helpers as H
from tb import __main__ as TBM
from tb.cmkt import cli as CLI
from tb.cmkt import conf as C
from tb.cmkt import focus as FO
from tb.cmkt import service as SV
from tb.platform import services as S
from tb.platform import status as ST
from tb.platform import supervisor as SUP

PERF = float(os.environ.get("TB_PERF_FACTOR") or 1.0)
SECRETS = {"BYBIT_API_KEY": "k", "ANTHROPIC_API_KEY": "a", "DEEPSEEK_API_KEY": "d", "OPENAI_API_KEY": "o", "MY_SECRET": "s", "GH_TOKEN": "t",
           "OKX_PASSPHRASE_SECRET": "p"}
SAFE = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""), "LANG": "C"}
MARKETS11 = [("binance", "perp"), ("binance", "spot"), ("bybit", "perp"), ("bybit", "spot"), ("okx", "perp"), ("okx", "spot"),
             ("bitget", "perp"), ("bitget", "spot"), ("gate", "perp"), ("gate", "spot"), ("coinbase", "spot")]
HB_LOOP = ("import os, time\nfrom tb.platform import services as S\n"
           "while True:\n    S.write_hb(os.environ['TB_PLATFORM_SERVICE'], root=os.environ['TB_ROOT'])\n    time.sleep(0.05)\n")


def fake_cfg(enabled=True):
    """Конфиг сводного рынка временного корня: реальные биржи выключены, включена fakex (perp и spot)."""
    c = copy.deepcopy(C.defaults())
    for v in c["venues"].values():
        v["enabled"] = False
    c["venues"]["fakex"] = {"enabled": True, "markets": ["perp", "spot"], "hosts": ["fakex.test", "api.fakex.test"]}
    c["limits"]["rest_weight_per_min"]["fakex"] = 600
    c["enabled"] = enabled
    return c


def write_cfg(root, cfg):
    p = os.path.join(root, "config", "cmkt.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(cfg if isinstance(cfg, str) else json.dumps(cfg))
    return p


def run_cli(argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = TBM.main(["cmkt"] + list(argv))
    return rc, buf.getvalue()


# ---------------------------------------------------------------- свойство 21
class TestProperty21(unittest.TestCase):
    def test_names_roundtrip_all_11_markets(self):
        for v, k in MARKETS11:
            n = S.cmkt_name(v, k)
            self.assertEqual(S.cmkt_market(n), (v, k), n)
            self.assertTrue(S.RE_CMKT.match(n), n)
        self.assertEqual(S.cmkt_market("cmkt@binancep"), ("binance", "perp"))
        self.assertEqual(S.cmkt_market("cmkt@coinbases"), ("coinbase", "spot"))
        for bad in ("cmkt", "cmkt@", "cmkt@p", "cmkt@x_y", "cmkt@binancex", "cmkt@Binancep", "cmkt@1abcp", "cmkt@abcdefghijklp", "exec@okx", None, 5):
            self.assertIsNone(S.cmkt_market(bad), bad)

    def test_parse_services_accepts_collectors_rejects_bad(self):
        out = S.parse_services("dash,cmkt@okxs,exec,cmkt,cmkt@binancep")
        self.assertEqual(out, ["exec", "cmkt", "cmkt@okxs", "cmkt@binancep", "dash"])
        self.assertEqual(S.parse_services("cmkt@binancep"), ["cmkt@binancep"])          # сборщик без сводки допустим
        for bad in ("cmkt@x_y", "cmkt@binancex", "cmkt@"):
            with self.assertRaises(S.ConfigError):
                S.parse_services("cmkt," + bad)
        sv = ["exec", "scan", "feed"] + ["cmkt"] + [S.cmkt_name(v, k) for v, k in MARKETS11] + ["context", "dash"]
        self.assertEqual(S.parse_services(",".join(sv)), sv)

    def test_stop_order_summary_before_collectors(self):
        sel = S.parse_services("exec,scan,feed,cmkt,cmkt@binancep,cmkt@okxs,context,dash,labels")
        order = S.stop_order(sel)
        self.assertEqual(sorted(order), sorted(sel))
        self.assertLess(order.index("cmkt"), order.index("cmkt@binancep"))
        self.assertLess(order.index("cmkt"), order.index("cmkt@okxs"))
        self.assertLess(order.index("labels"), order.index("cmkt"))                     # потребители раньше сводки
        self.assertEqual(order[-1], "exec")
        self.assertEqual(S.stop_order(["cmkt@okxs", "dash"]), ["dash", "cmkt@okxs"])

    def test_env_without_secrets_for_cmkt_and_collectors(self):
        for name in ["cmkt"] + [S.cmkt_name(v, k) for v, k in MARKETS11]:
            env = S.child_env(name, dict(SECRETS, **SAFE), "/tmp/r", profile="trade")
            self.assertEqual([k for k in env if S.labels_secret(k)], [], name)
            self.assertFalse([k for k in env if k in SECRETS], name)
            self.assertEqual(env["TB_PLATFORM_SERVICE"], name)
        self.assertIn("DEEPSEEK_API_KEY", S.child_env("dash", dict(SECRETS, **SAFE), "/tmp/r"))    # прочие сервисы — прежняя чистка

    def test_supervisor_settings_from_template(self):
        cfg = S.deep_merge(S.DEFAULTS, {})
        self.assertEqual(S.svc_cfg(cfg, "cmkt@binancep"), {"hb": "file", "hb_stale_s": 30.0, "grace_s": 60.0})
        self.assertEqual(S.svc_cfg(cfg, "cmkt"), {"hb": "file", "hb_stale_s": 30.0, "grace_s": 60.0})
        cfg["svc"]["cmkt@okxs"] = {"hb": "none"}
        self.assertEqual(S.svc_cfg(cfg, "cmkt@okxs"), {"hb": "none"})                  # своя запись важнее шаблона
        self.assertEqual(S.svc_cfg(cfg, "exec@okx"), {})


class TestCommandsAndExpansion(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)

    def test_service_cmd(self):
        self.assertEqual(S.service_cmd("cmkt", "paper", False, S.DEFAULTS, py="py"), ["py", "-m", "tb.cmkt.service", "run"])
        self.assertEqual(S.service_cmd("cmkt@binancep", "trade", True, S.DEFAULTS, py="py"),
                         ["py", "-m", "tb.cmkt.collector", "run", "--market", "binance_perp"])
        self.assertTrue(S.module_available("tb.cmkt.service"))
        self.assertTrue(S.module_available("tb.cmkt.collector"))

    def test_cmkt_services_by_config(self):
        self.assertEqual(S.cmkt_services(self.root), ["cmkt"] + [S.cmkt_name(v, k) for v, k in MARKETS11])      # нет файла — умолчания
        cfg = C.defaults()
        cfg["venues"]["gate"]["enabled"] = False
        cfg["venues"]["okx"]["markets"] = ["perp"]
        write_cfg(self.root, cfg)
        got = S.cmkt_services(self.root)
        self.assertNotIn("cmkt@gatep", got)
        self.assertNotIn("cmkt@okxs", got)
        self.assertEqual(got[:2], ["cmkt", "cmkt@binancep"])
        write_cfg(self.root, fake_cfg(enabled=False))
        self.assertEqual(S.cmkt_services(self.root), [])
        write_cfg(self.root, "{broken")
        self.assertEqual(S.cmkt_services(self.root), ["cmkt"])                           # сводка покажет E_CONF в heartbeat

    def test_default_services_expand_cmkt_in_profiles(self):
        write_cfg(self.root, fake_cfg())
        cfg = S.load_cfg(None)
        for prof in S.PROFILES:
            sv = S.default_services(prof, cfg, self.root)
            self.assertNotIn("cmkt@binancep", sv)
            i = sv.index("cmkt")
            self.assertEqual(sv[i:i + 3], ["cmkt", "cmkt@fakexp", "cmkt@fakexs"], prof)
            self.assertEqual(S.parse_services(",".join(sv)), sv, prof)

    def test_repo_platform_json(self):
        with open(os.path.join(_bootstrap.ROOT, "config", "platform.json"), encoding="utf-8") as f:
            d = json.load(f)
        for prof in S.PROFILES:
            self.assertIn("cmkt", d["services"][prof], prof)
        for k in ("cmkt", "cmkt@"):
            self.assertEqual(d["svc"][k], {"hb": "file", "hb_stale_s": 30.0, "grace_s": 60.0})


# ---------------------------------------------------------------- супервизор
def mk_platform(test, root, selected, cfg, cmds=None, base_env=None):
    pf = SUP.Platform(root, "paper", False, selected, cfg, base_env=base_env if base_env is not None else dict(os.environ), cmds=cmds,
                      emit=lambda t, d, **k: None)
    test.addCleanup(pf.stop_all)
    return pf


def pump(pf, pred, timeout):
    end = time.time() + timeout * PERF
    while time.time() < end:
        pf.tick()
        if pred():
            return True
        time.sleep(0.05)
    return pred()


class TestSupervisorTemplate(unittest.TestCase):
    def test_stale_heartbeat_of_one_collector_restarts_only_it(self):
        root = H.mk_root(self)
        cfg = H.fast_cfg()
        cfg["svc"]["cmkt@"] = {"hb": "file", "hb_stale_s": 0.4 * PERF, "grace_s": 0.2 * PERF}
        cmds = {"cmkt@fakexp": H.script(root, "sleep.py", H.SLEEP), "cmkt@fakexs": H.script(root, "hb.py", HB_LOOP)}
        pf = mk_platform(self, root, ["cmkt@fakexp", "cmkt@fakexs"], cfg, cmds=cmds)
        pf.start_all()
        bad, good = pf.svcs["cmkt@fakexp"], pf.svcs["cmkt@fakexs"]
        pid_good = good.pid()
        self.assertTrue(pump(pf, lambda: bad.restarts >= 1, 10.0))
        self.assertIn("heartbeat", bad.last_error)
        self.assertTrue(pump(pf, lambda: good.hb_ok is True, 5.0))
        self.assertEqual((good.restarts, good.pid()), (0, pid_good))


class TestPlatformRealProcesses(unittest.TestCase):
    """Сводка и сборщики fakex настоящими процессами под супервизором: старт, heartbeat, падение одного сборщика, остановка."""

    def test_up_restart_and_down(self):
        root = H.mk_root(self)
        write_cfg(root, fake_cfg())
        cfg = H.fast_cfg()
        selected = S.parse_services(",".join(S.cmkt_services(root)))
        self.assertEqual(selected, ["cmkt", "cmkt@fakexp", "cmkt@fakexs"])
        pf = mk_platform(self, root, selected, cfg, base_env=dict(os.environ, **SECRETS))
        for n in selected:
            self.assertFalse([k for k in pf.svcs[n].env if k in SECRETS], n)
        pf.start_all()

        def hb_ok(name):
            age, d = S.read_hb(name, root)
            return age is not None and d.get("pid") == pf.svcs[name].pid()
        self.assertTrue(pump(pf, lambda: all(hb_ok(n) for n in selected), 40.0), [S.read_hb(n, root) for n in selected])
        self.assertTrue(pump(pf, lambda: os.path.exists(os.path.join(root, "runtime", "cmkt", "snap.json")), 20.0))
        _, hb = S.read_hb("cmkt", root)
        self.assertEqual(hb.get("markets"), ["fakex_perp", "fakex_spot"])
        self.assertIn("budget", hb)
        _, chb = S.read_hb("cmkt@fakexp", root)
        self.assertEqual(chb.get("market"), "fakex_perp")
        # падение одного сборщика: перезапуск только его, остальные работают
        victim, other = pf.svcs["cmkt@fakexp"], pf.svcs["cmkt@fakexs"]
        pid_other, pid_summary = other.pid(), pf.svcs["cmkt"].pid()
        victim.proc.kill()
        self.assertTrue(pump(pf, lambda: victim.restarts >= 1 and victim.state == "running", 20.0))
        self.assertTrue(pump(pf, lambda: hb_ok("cmkt@fakexp"), 40.0))
        self.assertEqual((other.pid(), other.restarts), (pid_other, 0))
        self.assertEqual(pf.svcs["cmkt"].pid(), pid_summary)
        pf.stop_all()
        self.assertTrue(all(pf.svcs[n].state == "stopped" for n in selected))
        log = H.read(S.log_file("platform", root))
        self.assertIn("остановка сервисов: cmkt, cmkt@fakexp, cmkt@fakexs", log)


# ---------------------------------------------------------------- сервис сводки
class TestSummary(unittest.TestCase):
    NOW = 1793750400.0

    def test_venues_out(self):
        ex = {"binance_perp": "DOWN", "binance_spot": "LAG", "okx_perp": None, "okx_spot": "DOWN", "gate_perp": "OFF", "gate_spot": "OFF",
              "bybit_perp": "OFF", "bybit_spot": "SILENT"}
        out = SV.venues_out(ex, {"binance": self.NOW - 400}, self.NOW)
        self.assertEqual(out, {"binance": self.NOW - 400, "bybit": self.NOW})            # okx частично в сводке, gate выключена
        self.assertEqual(SV.venues_out({"binance_perp": None}, out, self.NOW + 1), {})

    def test_budget_totals(self):
        bud = C.defaults()["budget"]
        own = {"cpu_cores": 0.2, "rss_mb": 300}
        cols = [{"cpu_cores": 0.5, "cpu_p99_cores": 1.0, "rss_mb": 700, "proc_p99_ms": 120, "net_mbit": 30, "disk_gb_day": 5},
                {"cpu_cores": 0.9, "cpu_p99_cores": 2.0, "rss_mb": 600, "proc_p99_ms": 350, "net_mbit": 10, "disk_gb_day": None}]
        t = SV.budget_totals(own, cols, bud)
        self.assertEqual((t["cpu_cores"], t["rss_mb"], t["proc_p99_ms"], t["net_mbit"], t["n"]), (1.6, 1600.0, 350.0, 40.0, 3))
        self.assertEqual(sorted(t["over"]), ["cpu_avg_cores", "proc_p99_ms", "rss_mb"])
        self.assertEqual(SV.budget_totals({}, [], bud)["over"], [])

    def test_heartbeat_fields_and_budget_to_focus(self):
        root = H.mk_root(self)
        cfg = fake_cfg()
        cfg["budget"]["rss_mb"] = 1                                                       # заведомо превышен
        clock = [self.NOW]
        fo = FO.Focus(root, cfg, clock=lambda: clock[0])
        svc = SV.Summary(root, cfg, clock=lambda: clock[0], focus=fo)
        self.addCleanup(svc.close)
        svc.tick(int(self.NOW) - 1, self.NOW)
        hb = svc.heartbeat(self.NOW)
        self.assertEqual(hb["markets"], ["fakex_perp", "fakex_spot"])
        self.assertIn("rss_mb", hb["budget"]["over"])
        self.assertEqual(hb["venues_out"], {"fakex": self.NOW})                           # сборщиков нет: биржа вне сводки
        self.assertEqual(fo.over_since, self.NOW)
        age, d = S.read_hb("cmkt", root, self.NOW)
        self.assertEqual(d["venues_out"], {"fakex": self.NOW})
        self.assertIn("snap_ts", d)

    def test_main_conf_error_and_off(self):
        root = H.mk_root(self)
        write_cfg(root, "{broken")
        self.assertEqual(SV.main(["run", "--root", root]), 2)
        self.assertEqual(S.read_hb("cmkt", root)[1]["state"], "E_CONF")
        write_cfg(root, fake_cfg(enabled=False))
        t0 = time.time()
        self.assertEqual(SV.main(["run", "--root", root, "--max-seconds", "0.2"]), 0)
        self.assertLess(time.time() - t0, 5.0 * PERF)
        self.assertEqual(S.read_hb("cmkt", root)[1]["state"], "OFF")

    def test_main_runs_and_writes_snapshot(self):
        root = H.mk_root(self)
        write_cfg(root, fake_cfg())
        self.assertEqual(SV.main(["run", "--root", root, "--max-seconds", str(2.5 * PERF)]), 0)
        self.assertTrue(os.path.exists(os.path.join(root, "runtime", "cmkt", "snap.json")))
        _, hb = S.read_hb("cmkt", root)
        self.assertTrue(hb.get("stopped"))
        self.assertGreaterEqual(hb.get("ticks") or 0, 1)


# ---------------------------------------------------------------- статус
class TestStatusBlock(unittest.TestCase):
    NOW = 1793750400.0

    def hb(self, **kw):
        d = {"state": "ok", "snap_ts": self.NOW - 1, "warn": [], "venues_out": {}, "store": {"disk_low": False}, "store_code": None,
             "n_ex": {"perp": 5, "spot": 5}, "n_syms": 40}
        d.update(kw)
        return d

    def test_green_and_none(self):
        self.assertIsNone(ST.cmkt_block({}, self.NOW))
        cb = ST.cmkt_block(self.hb(), self.NOW, snap_mtime=self.NOW - 1.5, cov24=0.95)
        self.assertEqual((cb["color"], cb["codes"]), ("green", []))
        self.assertIn("perp 5 / spot 5", ST.cmkt_line(cb))

    def test_yellow_codes(self):
        cases = [
            (dict(), dict(snap_mtime=self.NOW - 6), "SNAP_STALE"),
            (dict(venues_out={"binance": self.NOW - 301, "okx": self.NOW - 400}), {}, "VENUES_OUT"),
            (dict(warn=["CLOCK_SKEW"]), {}, "CLOCK_SKEW"),
            (dict(warn=["BUDGET"]), {}, "BUDGET"),
            (dict(warn=["FOCUS_NO_HEAT"]), {}, "FOCUS_NO_HEAT"),
            (dict(store={"disk_low": True}), {}, "DISK_LOW"),
            (dict(), dict(cov24=0.85), "CM_COV"),
            (dict(state="E_CONF", errors=["E_CONF:sync.silent_s"]), {}, "CONF"),
        ]
        for hbkw, kw, code in cases:
            kw.setdefault("snap_mtime", self.NOW - 1)
            cb = ST.cmkt_block(self.hb(**hbkw), self.NOW, **kw)
            self.assertEqual((cb["color"], cb["codes"]), ("yellow", [code]), code)
        cb = ST.cmkt_block(self.hb(venues_out={"binance": self.NOW - 301, "okx": self.NOW - 100}), self.NOW, snap_mtime=self.NOW)
        self.assertEqual(cb["codes"], [])                                                 # одна биржа дольше 300 с — не жёлтый
        cb = ST.cmkt_block(self.hb(snap_ts=None), self.NOW, uptime_s=3)
        self.assertEqual(cb["codes"], [])                                                 # только стартовал: снимка ещё нет
        cb = ST.cmkt_block(self.hb(state="OFF"), self.NOW)
        self.assertEqual((cb["state"], cb["color"]), ("off", "off"))
        self.assertEqual(ST.cmkt_cov({"cm_cov_24h": {"focus": 0.5}}), 0.5)
        self.assertIsNone(ST.cmkt_cov({}))

    def _running_root(self, services):
        root = H.mk_root(self)
        S.write_json(S.lock_file(root), {"pid": os.getpid(), "started": time.time() - 100, "profile": "paper", "services": list(services)})
        S.write_json(S.status_file(root), {"ts_epoch": time.time(), "services": services})
        return root

    def test_build_status_yellow_never_red(self):
        now = time.time()
        root = self._running_root({"cmkt": {"state": "running", "started_ts": now - 100, "hb_ok": True},
                                   "cmkt@okxs": {"state": "failed", "last_error": "упал, код 1"}})
        S.write_hb("cmkt", self.hb(snap_ts=now - 20, warn=["CLOCK_SKEW"]), root=root, now=now)
        st = ST.build_status(root, now)
        self.assertEqual(st["color"], "yellow")
        self.assertEqual(sorted(st["cmkt"]["codes"]), ["CLOCK_SKEW", "SNAP_STALE"])
        self.assertTrue(any("cmkt@okxs" in r for r in st["reasons"]))
        self.assertIn("сводный рынок: ЖЁЛТЫЙ", ST.format_text(st))
        self.assertIn("cmkt@:0/1", ST.format_line(st))
        os.makedirs(os.path.join(root, "runtime", "cmkt"), exist_ok=True)
        with open(os.path.join(root, "runtime", "cmkt", "snap.json"), "w") as f:
            f.write("{}")
        S.write_hb("cmkt", self.hb(snap_ts=now - 1), root=root, now=now)
        S.write_json(S.status_file(root), {"ts_epoch": now, "services": {"cmkt": {"state": "running", "started_ts": now - 100, "hb_ok": True}}})
        st = ST.build_status(root, now)
        self.assertEqual((st["cmkt"]["color"], st["cmkt"]["codes"]), ("green", []))


# ---------------------------------------------------------------- команды
class TestCli(unittest.TestCase):
    def setUp(self):
        self.root = H.mk_root(self)
        write_cfg(self.root, fake_cfg())

    def test_empty_root(self):
        rc, out = run_cli(["--root", self.root, "status"])
        self.assertEqual(rc, 0)
        self.assertIn("fakex_perp", out)
        self.assertIn("нет heartbeat", out)
        self.assertEqual(run_cli(["--root", self.root, "focus"])[0], 1)
        self.assertEqual(run_cli(["--root", self.root, "show", "BTC"])[0], 1)
        self.assertEqual(run_cli(["--root", self.root, "at", "BTC", "bad-ts"])[0], 2)
        self.assertEqual(run_cli(["--root", self.root])[0], 2)

    def test_with_service_files(self):
        now = time.time()
        cfg = fake_cfg()
        svc = SV.Summary(self.root, cfg, focus=FO.Focus(self.root, cfg))
        try:
            svc.tick(int(now) - 1, now)
            svc.heartbeat(now)
        finally:
            svc.close()
        S.write_hb("cmkt@fakexp", {"market": "fakex_perp", "state": "down", "err": "HOST_DENIED", "subs": 12, "conns": 1, "alive": 0,
                                   "reconnects": 3, "resyncs": 1, "rest": 20, "cpu_cores": 0.01, "rss_mb": 50.0, "proc_ms_p99": 2.0,
                                   "net_mbit": 0.0}, root=self.root, now=now)
        rc, out = run_cli(["--root", self.root, "status"])
        self.assertEqual(rc, 0)
        self.assertIn("HOST_DENIED", out)
        self.assertIn("ярусы: deep 3", out)
        rc, out = run_cli(["--root", self.root, "status", "--json"])
        d = json.loads(out)
        self.assertEqual([m["mkt"] for m in d["markets"] if m["enabled"]], ["fakex_perp", "fakex_spot"])
        self.assertEqual(d["markets"][-2]["subs"], 12)
        rc, out = run_cli(["--root", self.root, "focus"])
        self.assertEqual(rc, 0)
        self.assertIn("BTC", out)
        rc, out = run_cli(["--root", self.root, "show", "btcusdt"])
        self.assertEqual(rc, 0, out)
        self.assertIn("BTC: ts", out)
        rc, out = run_cli(["--root", self.root, "show", "BTC", "--json"])
        self.assertEqual(json.loads(out)["sym"], "BTC")

    def test_at_reads_through_cmktdoc(self):
        try:
            from tb.core import cmktdoc  # noqa: F401
        except ImportError:
            self.skipTest("tb.core.cmktdoc ещё нет (задача 16)")
        rc, out = run_cli(["--root", self.root, "at", "BTC", "2026-11-04T10:00:00Z"])
        d = json.loads(out)
        self.assertTrue("cm" in d or "cm_missing" in d or d.get("pending"), d)
        self.assertEqual(rc, 0 if "cm" in d else 1)
        self.assertEqual(CLI.parse_ts("2026-11-04T10:00:00Z"), CLI.parse_ts("1793786400"))

    def test_read_only(self):
        """Команды не создают файлов в корне (кроме того, что уже есть)."""
        before = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(self.root) for f in fs)
        for argv in (["status"], ["status", "--json"], ["focus"], ["show", "BTC"]):
            run_cli(["--root", self.root] + argv)
        after = sorted(os.path.join(dp, f) for dp, _, fs in os.walk(self.root) for f in fs)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
