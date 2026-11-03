"""Сервис learnloop платформы: `python -m tb.learnloop.service run` (запускает супервизор; задача 10, Requirements 2.6, 2.11, 15.1, 15.4, 15.5).

Цикл обучения R2: проходы tb.learnloop.cycle по расписанию (observe 15 мин, раз в час, суточные после daily_after), heartbeat
runtime/platform/learnloop.hb.json (каждые 5 с и между шагами), блокировка второй копии runtime/platform/learnloop.lock (выход с кодом 2).
Окружение чистится как у сервиса labels (platform.services.labels_secret): ключей нет, сети нет; в шину пишутся только события rulebook_promoted/rulebook_rollback (всегда info, мозг не будят).

learnloop.enabled=false (или битый config/learnloop.json): сервис живёт и пишет heartbeat «выключен», базу не открывает, ничего не мигрирует
и не пишет. enabled=true: open_rw, migrate_v2a (с копией premigrate) и init_rulebook (оба идемпотентны), регистрация писателем
runtime/knowledge.writers/<pid>.

Повреждение (Requirement 2.11) — единственный исполнитель восстановления: DbCorruptError при открытии, повреждение в шаге цикла
(store2.is_corruption; IntegrityError и прочее — STEP_ERROR), провал суточного integrity_check, сигнал runtime/knowledge.corrupt сервиса меток
(проверяется в каждом проходе) -> store2.restore(auto=True): маркер, подтверждения писателей (restore_ack_timeout_s), свежая проверенная копия,
переименование с -wal/-shm, AUTO_RESTORED; после успеха — строка DB_CORRUPT в learn_events. Провал протокола (restore_failed.json) — красный
статус и ожидание ручного ./tb knowledge restore. Оставшийся маркер при старте — store2.recover_stale_marker. Чужой маркер восстановления
(ручной restore) — соединение закрывается, подтверждение, пауза.
"""
import argparse
import os
import signal
import sqlite3
import sys
import time

from tb import paths
from tb.knowledge import markers as KM
from tb.knowledge import store2
from tb.labels import store as LST
from tb.learnloop import conf as LC
from tb.learnloop import cycle as CY
from tb.learnloop import versions as V
from tb.platform import services as S

NAME = "learnloop"
EXIT_LOCKED = 2
HB_EVERY_S = 5.0
CFG_RELOAD_S = 60.0
MIGRATE_RETRY_S = 3600.0
IDLE_S = 1.0
STATES = ("disabled", "starting", "ok", "restoring", "restore_failed", "migration_error", "db_error")


def lock_path(root=None):
    return os.path.join(S.pdir(root), "learnloop.lock")


def scrub_env(environ=None):
    """Убрать из окружения BYBIT_*, ANTHROPIC_*, DEEPSEEK_*, OPENAI_*, *_API_KEY, *_SECRET, *_TOKEN (как у labels). -> число убранных."""
    env = os.environ if environ is None else environ
    bad = [k for k in list(env) if S.labels_secret(k)]
    for k in bad:
        env.pop(k, None)
    return len(bad)


def acquire_lock(root=None, pid=None):
    """runtime/platform/learnloop.lock (O_EXCL, в файле pid). Жив владелец -> False (вторая копия); мёртвый — замок снимается."""
    from tb.core.filelock import pid_alive
    pid = os.getpid() if pid is None else int(pid)
    p = lock_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    for _ in range(3):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except (FileExistsError, PermissionError):
            try:
                with open(p, encoding="utf-8") as f:
                    owner = int((f.read().split() or ["0"])[0])
            except (OSError, ValueError):
                owner = 0
            if owner > 0 and pid_alive(owner):
                return False
            try:
                os.remove(p)
            except OSError:
                time.sleep(0.05)
            continue
        try:
            os.write(fd, str(pid).encode())
        finally:
            os.close(fd)
        return True
    return False


def release_lock(root=None, pid=None):
    pid = os.getpid() if pid is None else int(pid)
    p = lock_path(root)
    try:
        with open(p, encoding="utf-8") as f:
            owner = int((f.read().split() or ["0"])[0])
        if owner == pid:
            os.remove(p)
    except (OSError, ValueError):
        pass


class Service(object):
    def __init__(self, root, cfg=None, steps=None, policy=None, mcfg=None, clock=time.time, sleep=time.sleep, log=None):
        self.root = root
        self.clock, self.sleep = clock, sleep
        self.log = log or (lambda m: print(m, flush=True))
        self.fixed_cfg = cfg is not None
        self.cfg, self.cfg_errors = (cfg, []) if cfg is not None else LC.load(root)
        self.cycle = CY.Cycle(root, self.cfg, steps, policy, mcfg, self.log)
        self.conn = None
        self.state = "starting"
        self.writer = False
        self.started = False
        self.t_hb = None
        self.t_cfg = clock()
        self.migrate_err_ts = None
        self.last_error = None
        self.restore_error = None
        self.restores = 0
        self._recovering = False
        self.corrupt_event = None           # DB_CORRUPT для learn_events после успешного восстановления

    # ---- конфиг
    def enabled(self):
        return LC.is_enabled(self.cfg)

    def _reload_cfg(self, now):
        if self.fixed_cfg or now - self.t_cfg < CFG_RELOAD_S:
            return
        self.t_cfg = now
        cfg, errs = LC.load(self.root)
        if cfg != self.cfg:
            self.log("learnloop: config/learnloop.json перечитан (enabled=%s)" % LC.is_enabled(cfg))
        self.cfg, self.cfg_errors = cfg, errs
        self.cycle.cfg = cfg

    # ---- соединение
    def _close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
            self.conn = None

    def _own_marker(self):
        d = KM.read_json(KM.restoring_path(self.root))
        return isinstance(d, dict) and int(d.get("pid") or 0) == os.getpid()

    def _foreign_restore(self):
        """Ручной ./tb knowledge restore (чужой маркер): закрыть соединение, подтвердить, пауза. -> True, если маркер есть."""
        if not KM.restoring(self.root) or self._own_marker():
            return False
        if self.state != "restoring":
            self.log("learnloop: идёт восстановление базы знаний: соединение закрыто, проходы на паузе")
        self._close()
        self.state = "restoring"
        if self.writer and not KM.has_ack(self.root):
            KM.ack(self.root)
        return True

    def _connect(self, now):
        """open_rw, migrate_v2a, init_rulebook, регистрация писателем. -> bool."""
        if self.migrate_err_ts is not None and now - self.migrate_err_ts < MIGRATE_RETRY_S:
            return False
        try:
            conn = LST.open_rw(self.root)
        except LST.KnowledgeRestoring:
            self.state = "restoring"
            return False
        except LST.DbCorruptError as e:
            self.recover("open_rw", e, now)
            return False
        except (LST.SchemaMigrationError, sqlite3.Error, OSError) as e:
            if store2.is_corruption(e):
                self.recover("open_rw", e, now)
            else:
                self.state, self.last_error = "db_error", type(e).__name__
            return False
        try:
            store2.migrate_v2a(conn, self.root, enabled=True, now=now, sleep=self.sleep)
            V.init_rulebook(conn, now)
        except store2.KnowledgeMigrationError as e:
            conn.close()
            self.state, self.last_error, self.migrate_err_ts = "migration_error", e.code, now
            self.log("learnloop: %s" % e)
            return False
        except Exception as e:  # noqa: BLE001
            conn.close()
            if store2.is_corruption(e):
                self.recover("init", e, now)
            else:
                self.state, self.last_error = "db_error", type(e).__name__
            return False
        self.migrate_err_ts = None
        if not self.writer and KM.has_v2(self.root):
            KM.register_writer(self.root, NAME)
            self.writer = True
        KM.remove(os.path.join(KM.ack_dir(self.root), str(os.getpid())))
        self.conn = conn
        self.state = "ok"
        if self.corrupt_event is not None:
            ev, self.corrupt_event = self.corrupt_event, None
            try:
                store2.log_event(conn, "knowledge", "DB_CORRUPT", "code", ref=ev.get("where"), body=ev, error=True, now=ev.get("ts"))
            except Exception:  # noqa: BLE001
                pass
        return True

    # ---- восстановление
    def recover(self, where, err, now):
        """Протокол восстановления (единственный исполнитель). -> True при успехе (соединение открыто заново)."""
        self._close()
        if self._recovering:                        # восстановленная база снова не открывается: без повторов, ручной restore
            self.state, self.restore_error = "restore_failed", "REOPEN_FAILED"
            return False
        self.log("learnloop: база знаний повреждена (DB_CORRUPT в %s: %s): автовосстановление из копии" % (where, type(err).__name__))
        self.state = "restoring"
        self.corrupt_event = {"where": str(where)[:40], "err": type(err).__name__, "ts": int(now)}
        self._recovering = True
        try:
            try:
                rep = store2.restore(self.root, auto=True, ack_timeout_s=float(self.cfg["restore"]["restore_ack_timeout_s"]), clock=self.clock,
                                     sleep=self.sleep)
            except store2.RestoreError as e:
                self.state, self.restore_error = "restore_failed", e.code
                self.log("learnloop: восстановление прервано (%s): нужен ./tb knowledge restore" % e.code)
                return False
            self.restores += 1
            self.restore_error = None
            self.log("learnloop: база знаний восстановлена из %s (AUTO_RESTORED)" % rep.get("src"))
            return self._connect(self.clock())
        finally:
            self._recovering = False

    def _startup(self, now):
        self.started = True
        try:
            r = store2.recover_stale_marker(self.root, ack_timeout_s=float(self.cfg["restore"]["restore_ack_timeout_s"]), clock=self.clock,
                                            sleep=self.sleep)
        except store2.RestoreError as e:
            self.state, self.restore_error = "restore_failed", e.code
            return
        if r is not None:
            self.log("learnloop: оставшийся маркер восстановления: %s" % (r if isinstance(r, str) else "протокол повторён"))

    # ---- проход
    def tick(self, now=None):
        now = self.clock() if now is None else now
        self._reload_cfg(now)
        if not self.enabled():
            self._close()
            self.state = "disabled"
            self.cycle.run_start = None
            self.heartbeat(now)
            return 0
        if self.cycle.run_start is None:
            self.cycle.run_start = now
        self.cycle.credit(now)
        if not self.started:
            self._startup(now)
        ran = 0
        if self._foreign_restore():
            pass
        elif os.path.exists(KM.restore_failed_path(self.root)):
            self._close()
            self.state = "restore_failed"
        elif self.conn is not None or self._connect(now):
            if self.cycle.pass_due(now):
                ran = self._pass(now)
                self.heartbeat(now, force=True)
        self.heartbeat(now)
        return ran

    def _pass(self, now):
        sig = KM.read_corrupt(self.root)
        if sig is not None:                        # повреждение, замеченное только сервисом меток: восстановление в этом же проходе
            self.recover("signal:%s" % (sig.get("where") or "labels"), store2.DbCorruptError("DB_CORRUPT: сигнал runtime/knowledge.corrupt"), now)
            return 0
        try:
            rep = self.cycle.run_pass(self.conn, now, beat=lambda: self.heartbeat(self.clock()))
        except CY.Corruption as e:
            self.recover(e.where, e.err, now)
            return 0
        except Exception as e:  # noqa: BLE001 - сбой каркаса не роняет сервис (торговля и мозг не зависят от цикла)
            self.last_error = type(e).__name__
            self.log("learnloop: проход не завершён (%s: %s)" % (type(e).__name__, e))
            if store2.is_corruption(e):
                self.recover("pass", e, now)
            return 0
        return len(rep["ran"]) + len(rep["errors"])

    # ---- heartbeat и статус
    def items(self):
        """Пункты статуса [{"code", "text"}]: цикла (cycle.codes/reasons) и сервиса (DB_CORRUPT при провале протокола, KNOWLEDGE_MIGRATION,
        пауза восстановления и ошибка открытия базы без кода)."""
        if not self.enabled():
            return []
        out = [{"code": c, "text": r} for c, r in zip(self.cycle.codes, self.cycle.reasons)]
        if self.state == "restore_failed":
            out.append({"code": "DB_CORRUPT", "text": "цикл обучения: база знаний повреждена, автовосстановление не удалось (%s): ./tb knowledge restore"
                        % (self.restore_error or "-")})
        elif self.state == "restoring":
            out.append({"code": None, "text": "цикл обучения: идёт восстановление базы знаний, проходы на паузе"})
        elif self.state == "migration_error":
            out.append({"code": "KNOWLEDGE_MIGRATION", "text": "цикл обучения: миграция v2a не применена (KNOWLEDGE_MIGRATION): цикл не работает, торговля и метки работают"})
        elif self.state == "db_error":
            out.append({"code": None, "text": "цикл обучения: база знаний не открывается (%s)" % (self.last_error or "-")})
        return out

    def hb_data(self, now):
        cy = self.cycle
        items = self.items()
        acc = cy.acc or {}
        d = {"enabled": self.enabled(), "state": self.state, "mode": LC.promotion_mode(self.cfg), "conf_errors": list(self.cfg_errors[:5]),
             "items": items, "codes": [it["code"] for it in items if it["code"]], "last_pass_ts": cy.last_pass_ts, "last_ok_ts": cy.last_ok_ts, "run_start": cy.run_start,
             "stale_min": self.cfg.get("stale_min"), "last_error": self.last_error, "restores": self.restores}
        if self.enabled():
            d.update(ready=None if cy.ready is None else {"ok": cy.ready["ok"], "failed": cy.ready["failed"]}, stubs=cy.stubs(), no_event=cy.no_event,
                     errors=dict((cy.last_report or {}).get("errors") or {}),
                     day={"day": acc.get("day"), "work_h": round(float(acc.get("work_s") or 0) / 3600.0, 2), "obs_h": len(acc.get("oh") or [])})
        return d

    def heartbeat(self, now, force=False):
        if not force and self.t_hb is not None and 0 <= now - self.t_hb < HB_EVERY_S:
            return
        self.t_hb = now
        S.write_hb(NAME, self.hb_data(now), root=self.root, now=now)

    def close(self):
        if self.conn is not None and self.cycle.state is not None:
            try:
                self.cycle.credit(self.clock())
                self.cycle.save(self.conn, self.clock())          # учёт работы после последнего прохода не теряется при остановке
            except Exception:  # noqa: BLE001
                pass
        self._close()
        if self.writer:
            KM.unregister_writer(self.root)
            self.writer = False


def run(root, cfg=None, steps=None, clock=time.time, sleep=time.sleep, stop=lambda: False, max_seconds=0.0, log=None):
    svc = Service(root, cfg, steps, clock=clock, sleep=sleep, log=log)
    t_end = clock() + max_seconds if max_seconds else None
    svc.heartbeat(clock(), force=True)
    try:
        while not stop():
            svc.tick()
            if t_end is not None and clock() >= t_end:
                break
            sleep(IDLE_S)
    finally:
        svc.close()
        svc.heartbeat(clock(), force=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.learnloop.service")
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
        print("learnloop: уже запущен (runtime/platform/learnloop.lock)", flush=True)
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
        print("learnloop: старт", flush=True)
        return run(root, stop=lambda: flag["stop"], max_seconds=a.max_seconds)
    finally:
        release_lock(root)


if __name__ == "__main__":
    sys.exit(main())
