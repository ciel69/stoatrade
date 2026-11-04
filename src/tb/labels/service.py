"""Сервис labels платформы: `python -m tb.labels.service run` (запускает супервизор). R1, Requirement 12.

Цикл: захват сигналов из runtime/hot_events.jsonl каждые capture_every_s (15 с) + разметка до label_batch созревших меток за проход, heartbeat
runtime/platform/labels.hb.json, wal_checkpoint(PASSIVE) раз в checkpoint_min и TRUNCATE раз в сутки. Блокировка второй копии: runtime/platform/labels.lock
(выход с кодом 2). События в шину НЕ публикуются, ключи не читаются, из окружения процесса убираются все секреты (platform.services.labels_secret).
Свечи - публичный REST Bybit (tb.labels.klines). Планов, ордеров и записей журнала сделок сервис не создаёт.

R2 (Requirement 2.4, 2.11), только при маркере state/knowledge.v2: сервис регистрируется писателем runtime/knowledge.writers/<pid>; DbCorruptError
(open_rw при старте) и повреждение по knowledge.markers.is_corruption в label_step / capture_step останавливают записи и пишут сигнал
runtime/knowledge.corrupt, восстановление выполняет сервис цикла. Маркер runtime/knowledge.restoring проверяется в tick, перед каждой меткой
label_step, в capture_step, checkpoint_step и heartbeat: соединение закрывается, пишется подтверждение runtime/knowledge.restoring.ack/<pid>,
после снятия маркера соединение открывается заново. Без маркера v2 поведение R1 прежнее.
"""
import argparse
import collections
import os
import signal
import sqlite3
import sys
import time

from tb import paths
from tb.knowledge import markers as KM
from tb.labels import capture, klines, label, paths as paths_mod, policies, query, schema, store
from tb.platform import services as S

EXIT_LOCKED = 2
EXIT_SCHEMA = 4             # в fatal_codes супервизора: быстрый выход с этим кодом не перезапускается
HB_EVERY_S = 5.0
LABEL_BUDGET_S = 45.0       # длиннее проход разметки не тянется (захват и heartbeat не ждут дольше); остаток пачки уйдёт в следующий проход
STATS_EVERY_S = 30.0        # тяжёлые счётчики 24 ч не чаще
DAY_S = 86400
IDLE_S = 1.0
PATHS_EVERY_S = 30.0        # пути прогонов и сделок: не чаще раза в 30 с (шина и окна путей меняются медленно)


def lock_path(root=None):
    return os.path.join(S.pdir(root), "labels.lock")


def scrub_env(environ=None):
    """Убрать из окружения процесса BYBIT_*, ANTHROPIC_*, DEEPSEEK_*, OPENAI_*, *_API_KEY, *_SECRET, *_TOKEN. -> число убранных."""
    env = os.environ if environ is None else environ
    bad = [k for k in list(env) if S.labels_secret(k)]
    for k in bad:
        env.pop(k, None)
    return len(bad)


def acquire_lock(root=None):
    """Взять runtime/platform/labels.lock (O_EXCL, в файле pid). Жив владелец -> False (вторая копия); мёртвый процесс -> замок снимается."""
    from tb.core.filelock import pid_alive
    p = lock_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            try:
                with open(p, encoding="utf-8") as f:
                    pid = int((f.read().split() or ["0"])[0])
            except (OSError, ValueError):
                pid = 0
            if pid > 0 and pid_alive(pid):
                return False
            try:
                os.remove(p)
            except OSError:
                time.sleep(0.05)
            continue
        try:
            os.write(fd, str(os.getpid()).encode())
        finally:
            os.close(fd)
        return True
    return False


def release_lock(root=None):
    p = lock_path(root)
    try:
        with open(p, encoding="utf-8") as f:
            pid = int((f.read().split() or ["0"])[0])
        if pid == os.getpid():
            os.remove(p)
    except (OSError, ValueError):
        pass


class Service(object):
    def __init__(self, root, cfg=None, fetch=None, btc_cache=None, limiter=None, clock=time.time, sleep=time.sleep, log=None, conn=None):
        self.root = root
        self.cfg = cfg if cfg is not None else capture.load_cfg(root)
        self.clock, self.sleep = clock, sleep
        self.log = log or (lambda m: print(m, flush=True))
        self.v2 = False                 # R2: маркер state/knowledge.v2 и регистрация писателя
        self.db_state = None            # R2: None | "corrupt" (ждём восстановления) | "restoring" (пауза по маркеру)
        self.last_error = store.LAST_ERROR
        self.conn = None
        self._v2_check()
        self.conn = conn if conn is not None else self._open()
        self.limiter = limiter
        if fetch is None:
            self.limiter = self.limiter or klines.Limiter(float(self.cfg.get("max_rps", 5)))
            fetch = self._real_fetch
            btc_cache = btc_cache or klines.BtcCache(self.limiter)
        self.raw_fetch = fetch
        self.btc = btc_cache
        self.calls = 0
        self.geom = label.load_geom(root)
        self.pols = self._load_pols()
        self.t0 = clock()
        self.t_capture = self.t_hb = self.t_stats = None
        self.t_ckpt = self.t0
        self.t_trunc = self.t0
        self.pause_until = 0.0
        self.bad_lines = 0
        self.last_error = schema.DB_CORRUPT if self.db_state == "corrupt" else store.LAST_ERROR
        self.rate_events = collections.deque()
        self.req_samples = collections.deque()
        self.stats = {}
        self.sync_warn = store.sync_dir_warning(root)
        # R4: пути прогонов и сделок мозга. Доля времени прохода на пути — в heartbeat (Requirement 1.7).
        self.exits_cfg = None
        self.t_exits_cfg = None
        self.paths_dt = 0.0
        self.paths_hist = collections.deque(maxlen=60)
        self.t_paths = None
        self.t_rotate = 0.0

    # ---- R2: защита базы знаний (только при маркере state/knowledge.v2; без него методы ничего не меняют)
    def _v2_check(self):
        if not self.v2 and KM.has_v2(self.root):
            self.v2 = True
            KM.register_writer(self.root, "labels")
        return self.v2

    def _open(self):
        """open_rw; при маркере восстановления - пауза и подтверждение, при DbCorruptError - сигнал повреждения. -> соединение или None."""
        try:
            conn = store.open_rw(self.root)
        except store.KnowledgeRestoring:
            self.db_state = "restoring"
            self._ack()
            return None
        except store.DbCorruptError as e:
            self._corrupt("open_rw", e)
            return None
        if self.db_state is not None:
            self.log("labels: база знаний снова доступна, записи возобновлены")
            KM.remove(os.path.join(KM.ack_dir(self.root), str(os.getpid())))
        self.db_state = None
        return conn

    def _ack(self):
        if self.v2 and not KM.has_ack(self.root):
            KM.ack(self.root)

    def _close_conn(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None

    def _restoring_check(self):
        """Маркер runtime/knowledge.restoring: закрыть соединение, подтвердить, записи на паузе. -> True, если маркер есть."""
        if not KM.restoring(self.root):
            return False
        if self.db_state != "restoring":
            self.log("labels: идёт восстановление базы знаний: соединение закрыто, записи на паузе")
        self._close_conn()
        self.db_state = "restoring"
        self._ack()
        return True

    def _corrupt(self, where, e):
        """DbCorruptError: остановить записи, закрыть соединение, сигнал runtime/knowledge.corrupt для сервиса цикла (он один восстанавливает)."""
        self._close_conn()
        if self.db_state != "corrupt":
            self.log("labels: база знаний повреждена (DB_CORRUPT в %s: %s): записи остановлены до восстановления" % (where, type(e).__name__))
            try:
                KM.write_corrupt(self.root, schema.DB_CORRUPT, where=where, now=self.clock())
            except OSError:
                pass
        self.db_state = "corrupt"
        self.last_error = schema.DB_CORRUPT

    def _is_corrupt_v2(self, e):
        return self.v2 and KM.is_corruption(e)

    def _ensure_conn(self):
        """Соединение есть или открыто заново (после снятия маркера восстановления / сигнала повреждения). -> bool."""
        if self.conn is not None:
            return True
        if self.db_state == "corrupt" and KM.read_corrupt(self.root) is not None:
            return False                                  # ждём восстановления сервисом цикла (он снимает сигнал)
        self.conn = self._open()
        return self.conn is not None

    # ---- свечи
    def _real_fetch(self, sym, interval, start_s, end_s):
        return klines.fetch(sym, interval, start_s, end_s, self.limiter, now_s=self.clock())

    def fetch(self, sym, interval, start_s, end_s):
        self.calls += 1
        return self.raw_fetch(sym, interval, start_s, end_s)

    def _load_pols(self):
        try:
            return policies.load(self.root)
        except ValueError as e:
            self.log("labels: реестр политик не загружен (%s): разметка без политик" % e)
            self.last_error = "POLICIES"
            return []

    # ---- шаги
    def capture_step(self, now):
        if self.t_capture is not None and now - self.t_capture < float(self.cfg.get("capture_every_s", 15)):
            return None
        if self._restoring_check() or self.conn is None:
            return None
        self.t_capture = now
        try:
            r = capture.poll(self.conn, self.root, self.cfg, now)
        except (sqlite3.Error, OSError) as e:
            if self._is_corrupt_v2(e):
                self._corrupt("capture_step", e)
                return None
            self.last_error = type(e).__name__
            self.log("labels: захват не удался (%s)" % type(e).__name__)
            return None
        self.bad_lines += r.get("bad_lines", 0)
        return r

    def label_step(self, now):
        """Разметить до label_batch созревших меток. -> число обработанных."""
        if now < self.pause_until:
            return 0
        if self._restoring_check() or self.conn is None:
            return 0
        try:
            rows = label.due(self.conn, now, int(self.cfg.get("label_batch", 20)))
        except sqlite3.Error as e:
            if self._is_corrupt_v2(e):
                self._corrupt("label_step", e)
                return 0
            raise
        n = 0
        t_step = self.clock()
        budget = float(self.cfg.get("label_budget_s", LABEL_BUDGET_S))
        for row in rows:
            el = self.clock() - t_step
            if n and el >= budget:         # пачка из 20 меток по ~2-7 с (при сбое сети по 22+ с) дольше hb_stale_s=120 с без heartbeat: супервизор убил бы сервис
                break
            if el >= HB_EVERY_S:
                self.heartbeat(now + el)
            if self._restoring_check() or self.conn is None:   # R2: маркер проверяется перед каждой меткой пачки (проход до LABEL_BUDGET_S)
                break
            try:
                st = label.label_one(self.conn, self.root, row, self.cfg, self.pols, self.geom, self.fetch, self.btc, now)
            except Exception as e:  # noqa: BLE001 - сбой одной метки не роняет сервис; транзакция уже откатилась
                if self._is_corrupt_v2(e):
                    self._corrupt("label_step", e)
                    break
                self.last_error = type(e).__name__
                self.log("labels: %s не размечена (%s)" % (row["key"], type(e).__name__))
                try:
                    label.defer(self.conn, row, self.cfg, now, type(e).__name__[:40])
                except sqlite3.Error:
                    pass
                n += 1
                continue
            n += 1
            if st == label.RATE_LIMITED:
                self.rate_events.append(now)
                self.pause_until = now + klines.RATE_PAUSE_S
                self.log("labels: биржа ограничила запросы (403/429), пауза %d с" % klines.RATE_PAUSE_S)
                break
        return n

    def _exits_cfg(self, now):
        """config/exits.json раз в минуту (пути включены, пока файл годен); None — файла нет или он битый."""
        if self.t_exits_cfg is None or now - self.t_exits_cfg >= 60.0:
            self.exits_cfg = paths_mod.load_cfg(self.root)
            self.t_exits_cfg = now
        return self.exits_cfg

    def paths_step(self, now):
        """Догрузка путей прогонов и пути сделок мозга (R4, задачи 8 и 9): время шага учитывается в доле прохода."""
        t0 = self.clock()
        try:
            if self.t_paths is not None and now - self.t_paths < PATHS_EVERY_S:
                return None
            if self._restoring_check() or self.conn is None:
                return None
            cfg = self._exits_cfg(now)
            if not paths_mod.enabled(cfg):
                return None
            self.t_paths = now
            out = {"extend": paths_mod.extend_step(self.conn, self.root, cfg, self.fetch, now),
                   "trade": paths_mod.trade_paths_step(self.conn, self.root, cfg, self.fetch, now)}
            if now - self.t_rotate >= 3600.0:
                self.t_rotate = now
                out["rotate"] = paths_mod.rotate(self.conn, cfg, now)
            return out
        except klines.RateLimited:
            self.rate_events.append(now)
            self.pause_until = now + klines.RATE_PAUSE_S
            self.last_error = "rate_limited"
            self.log("labels: биржа ограничила запросы на путях (403/429), пауза %d с" % klines.RATE_PAUSE_S)
            return None
        except (sqlite3.Error, OSError, ValueError) as e:
            if self._is_corrupt_v2(e):
                self._corrupt("paths_step", e)
                return None
            self.last_error = type(e).__name__
            self.log("labels: пути не обновлены (%s)" % type(e).__name__)
            return None
        finally:
            self.paths_dt = self.clock() - t0

    def checkpoint_step(self, now):
        if self._restoring_check() or self.conn is None:
            return
        if now - self.t_trunc >= DAY_S:
            self.t_trunc = self.t_ckpt = now
            self._ckpt("TRUNCATE")
        elif now - self.t_ckpt >= float(self.cfg.get("checkpoint_min", 10)) * 60:
            self.t_ckpt = now
            self._ckpt("PASSIVE")

    def _ckpt(self, mode):
        try:
            self.conn.execute("PRAGMA wal_checkpoint(%s)" % mode)
        except sqlite3.Error as e:
            self.last_error = type(e).__name__
            self.log("labels: wal_checkpoint(%s) не удался (%s)" % (mode, type(e).__name__))

    def _req_total(self):
        return self.limiter.requests if self.limiter is not None else self.calls

    def heartbeat(self, now, force=False):
        if not force and self.t_hb is not None and now - self.t_hb < HB_EVERY_S:
            return
        self.t_hb = now
        self._restoring_check()
        if self.conn is not None and (force or self.t_stats is None or now - self.t_stats >= STATS_EVERY_S):
            self.t_stats = now
            try:
                self.stats = query.heartbeat_stats(self.conn, now, self.cfg.get("coverage_fields"))
            except sqlite3.Error as e:
                if self._is_corrupt_v2(e):
                    self._corrupt("heartbeat", e)
                else:
                    self.last_error = type(e).__name__
        while self.rate_events and self.rate_events[0] < now - DAY_S:
            self.rate_events.popleft()
        tot = self._req_total()
        self.req_samples.append((now, tot))
        while len(self.req_samples) > 1 and self.req_samples[1][0] <= now - 60:
            self.req_samples.popleft()
        t_old, c_old = self.req_samples[0]
        rpm = round((tot - c_old) * 60.0 / min(max(now - t_old, 1.0), 60.0), 1) if now - t_old >= 1.0 else 0.0     # запросов за последнюю минуту (при простое цикла дольше минуты - за 60 с)
        warnings = [w for w in (self.sync_warn,) if w]
        if store.LAST_ERROR:
            warnings.append(store.LAST_ERROR)
        db_mb = query.db_mb(self.root)
        st = self.stats
        tot_dt = sum(x[0] for x in self.paths_hist)
        paths_share = round(sum(x[1] for x in self.paths_hist) / tot_dt, 4) if tot_dt > 0 else None
        hb = {"captured_24h": st.get("captured_24h"), "labeled_24h": st.get("labeled_24h"), "pending": st.get("pending"), "due": st.get("due"),
              "failed_24h": st.get("failed_24h"), "no_data_24h": st.get("no_data_24h"), "rate_limited_24h": len(self.rate_events), "req_per_min": rpm,
              "bad_lines": self.bad_lines, "last_error": self.last_error or store.LAST_ERROR, "db_mb": db_mb,
              "missing_share_24h": st.get("missing_share_24h"), "warnings": warnings, "enabled": bool(self.cfg.get("enabled", True)),
              "paths_share": paths_share, "paths_partial": st.get("paths_partial"), "paths_complete": st.get("paths_complete"),
              "trade_paths": st.get("trade_paths"), "trade_path_missing": st.get("trade_path_missing")}
        if self.v2:
            hb["v2"] = True
            hb["db_state"] = self.db_state
        S.write_hb("labels", hb, root=self.root, now=now)

    def tick(self, now=None):
        now = self.clock() if now is None else now
        n = 0
        t_tick = self.clock()
        self.paths_dt = 0.0
        self._v2_check()
        if self.cfg.get("enabled", True) and not self._restoring_check() and self._ensure_conn():
            self.capture_step(now)
            n = self.label_step(now)
            self.paths_step(now)
            self.checkpoint_step(now)
        if self.paths_dt or self.paths_hist:
            self.paths_hist.append((self.clock() - t_tick, self.paths_dt))
        self.heartbeat(now)
        return n

    def close(self):
        self._close_conn()
        if self.v2:
            KM.unregister_writer(self.root)


def run(root, cfg=None, fetch=None, btc_cache=None, limiter=None, clock=time.time, sleep=time.sleep, stop=lambda: False, max_seconds=0.0, log=None):
    svc = Service(root, cfg, fetch, btc_cache, limiter, clock, sleep, log)
    t_end = clock() + max_seconds if max_seconds else None
    svc.heartbeat(clock(), force=True)
    try:
        while not stop():
            n = svc.tick()
            if t_end is not None and clock() >= t_end:
                break
            sleep(0.05 if n else IDLE_S)
    finally:
        svc.heartbeat(clock(), force=True)
        svc.close()
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.labels.service")
    ap.add_argument("cmd", choices=("run",))
    ap.add_argument("--max-seconds", type=float, default=0.0, dest="max_seconds")
    ap.add_argument("--root", default=None)
    try:
        a = ap.parse_args(list(sys.argv[1:] if argv is None else argv))
    except SystemExit:
        return 2
    if a.root:
        os.environ["TB_ROOT"] = os.path.abspath(a.root)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    scrub_env()
    root = paths.root()
    if not acquire_lock(root):
        print("labels: уже запущен (runtime/platform/labels.lock)", flush=True)
        return EXIT_LOCKED
    flag = {"stop": False}

    def _h(*_):
        flag["stop"] = True
    for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is not None:
            try:
                signal.signal(sig, _h)
            except (ValueError, OSError):
                pass
    try:
        print("labels: старт", flush=True)
        try:
            return run(root, stop=lambda: flag["stop"], max_seconds=a.max_seconds)
        except store.SchemaMigrationError as e:
            print("labels: %s: %s" % (schema.SCHEMA_MIGRATION, e), flush=True)
            return EXIT_SCHEMA
    finally:
        release_lock(root)


if __name__ == "__main__":
    sys.exit(main())
