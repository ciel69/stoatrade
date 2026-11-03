"""`./tb test`: все тесты tests/ (unittest), по умолчанию параллельно по модулям.

  ./tb test                     модули распределяются по процессам (число = процессоры), самые долгие первыми
  ./tb test -j 1                прежний режим: один процесс, unittest discover
  ./tb test -j 8                восемь процессов
  ./tb test --slow              ещё и медленный набор (TB_SLOW_TESTS=1: нагрузочные и пороги скорости)
  ./tb test test_dash2 test_learn_report.TestSize   только эти модули / классы / тесты
  ./tb test -k csrf             только тесты, в имени которых есть подстрока (как unittest -k; можно несколько)
  ./tb test --durations 15      в конце 15 самых долгих модулей

Параллельный режим: единица работы — модуль тестов (тесты одного модуля идут в одном процессе подряд, как раньше); модуль, который
в прошлом прогоне шёл дольше SPLIT_S (или из ALWAYS_SPLIT), делится на классы. Процессы переиспользуются. Вывод — сводка как у unittest (Ran N tests in X s, OK / FAILED со списком FAIL/ERROR и трассами); вывод тестов
в stdout/stderr показывается только для упавших модулей. Код выхода 0/1.

Последовательная группа (SERIAL_LAST): tests/test_zz_realstate_guard.py сверяет реальные state/, config/, runtime/ со снимком до
прогона, поэтому идёт в основном процессе ПОСЛЕ всех остальных, а снимок снимается здесь до старта процессов (импорт _bootstrap).
Кроме того после каждого модуля процесс проверяет, что тест не оставил в окружении TB_ROOT (как test_env_not_leaked).

Длительности модулей прошлого прогона хранятся во временном каталоге ОС (не в runtime/ проекта: туда смотрит сторож реальных
каталогов): tb-test-durations-<хеш корня>.json; без файла порядок — по размеру файла модуля.
"""
import argparse
import concurrent.futures as cf
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import time
import unittest

from tb import paths

SERIAL_LAST = ("test_zz_realstate_guard",)
ALWAYS_SPLIT = ("test_knowledge_migrate",)       # делится на классы и без файла длительностей
SPLIT_S = 20.0
PARALLEL_PERF_FACTOR = 3        # TB_PERF_FACTOR рабочих процессов, если не задан: пороги времени тестов (60 с e2e, перф при --slow) с запасом;
                                # точный замер скорости: ./tb test --slow -j 1
SEP1, SEP2 = "=" * 70, "-" * 70


def tests_dir(root=None):
    return os.path.join(root or paths.root(), "tests")


def all_modules(tdir):
    """Модули верхнего уровня tests/ по шаблону unittest discover (test*.py), по алфавиту."""
    return sorted(f[:-3] for f in os.listdir(tdir) if f.startswith("test") and f.endswith(".py") and os.path.isfile(os.path.join(tdir, f)))


def durations_path(tdir):
    h = hashlib.sha1(os.path.abspath(tdir).lower().encode("utf-8")).hexdigest()[:10]
    return os.path.join(tempfile.gettempdir(), "tb-test-durations-%s.json" % h)


def load_durations(tdir):
    try:
        with open(durations_path(tdir), encoding="utf-8") as f:
            d = json.load(f)
        return {k: float(v) for k, v in d.items()} if isinstance(d, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_durations(tdir, new):
    old = load_durations(tdir)
    old.update(new)
    try:
        p = durations_path(tdir)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(old, f, sort_keys=True)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


def parse_targets(names, tdir):
    """Позиционные имена -> {модуль: [полные имена unittest] или None (весь модуль)}. Принимает test_x, tests/test_x.py, test_x.Class.test."""
    out = {}
    for raw in names:
        n = raw.replace("\\", "/")
        if n.startswith("tests/"):
            n = n[len("tests/"):]
        if n.endswith(".py"):
            n = n[:-3]
        n = n.replace("/", ".")
        mod = n.split(".", 1)[0]
        if not os.path.isfile(os.path.join(tdir, mod + ".py")):
            raise SystemExit("tb test: нет модуля tests/%s.py" % mod)
        if mod == n:
            out[mod] = None
        elif out.get(mod, []) is not None:
            out.setdefault(mod, []).append(n)
    return out


def _patterns(ks):
    return ["*%s*" % k if "*" not in k else k for k in ks] or None


def make_loader(ks):
    loader = unittest.TestLoader()
    loader.testNamePatterns = _patterns(ks)
    return loader


def _count(suite):
    return suite.countTestCases()


# ---------------------------------------------------------------- рабочий процесс

def _prepare_paths(tdir):
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for p in (src, tdir):
        if p not in sys.path:
            sys.path.insert(0, p)


def _desc(test):
    d = test.shortDescription()
    return "%s\n%s" % (test, d) if d else str(test)


class _Capture(io.TextIOWrapper):
    """Поток для stdout/stderr теста в рабочем процессе: текст и .buffer (байты), как у настоящей консоли; getvalue() -> текст."""

    def __init__(self):
        super().__init__(io.BytesIO(), encoding="utf-8", errors="replace", newline="\n", write_through=True)

    def getvalue(self):
        self.flush()
        return self.buffer.getvalue().decode("utf-8", "replace")


def _empty(label, err=None):
    return {"mod": label, "run": 0, "failures": [], "errors": [err] if err else [], "skipped": 0, "xfail": 0, "xpass": [], "dt": 0.0, "output": ""}


def run_unit(tdir, label, mod, names, ks, verbosity=1):
    """Выполняется в рабочем процессе: модуль целиком (names=None) или его классы/тесты. -> сводка, пригодная для pickle.
    Вывод тестов в stdout/stderr собирается отдельно от отчёта unittest и возвращается только при падении."""
    _prepare_paths(tdir)
    t0 = time.time()
    buf, rep = _Capture(), io.StringIO()
    loader = make_loader(ks)
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        try:
            suite = loader.loadTestsFromNames(names) if names else loader.loadTestsFromName(mod)
        except Exception:               # модуль не импортируется: как _FailedTest у discover
            import traceback
            r = _empty(label, ("%s (импорт модуля)" % label, traceback.format_exc()))
            r.update(run=1, dt=time.time() - t0, output=buf.getvalue())
            return r
        res = unittest.TextTestRunner(stream=rep, verbosity=verbosity).run(suite)
    errors = [(_desc(t), tb) for t, tb in res.errors]
    leaked = os.environ.pop("TB_ROOT", None)
    if leaked:
        errors.append(("%s (окружение после модуля)" % label, "утечка TB_ROOT=%s: тест не вернул окружение\n" % leaked))
    bad = bool(res.failures or errors or res.unexpectedSuccesses)
    output = ""
    if bad:
        output = (rep.getvalue() if verbosity > 1 else "") + buf.getvalue()
    return {"mod": label, "run": res.testsRun, "failures": [(_desc(t), tb) for t, tb in res.failures], "errors": errors, "skipped": len(res.skipped),
            "xfail": len(res.expectedFailures), "xpass": [str(t) for t in res.unexpectedSuccesses], "dt": time.time() - t0, "output": output}


def split_by_class(mod):
    """Модуль -> ["mod.Атрибут", ...] по классам с тестами (порядок loadTestsFromModule). Импортирует модуль в основном процессе."""
    import importlib
    m = importlib.import_module(mod)
    out = []
    for sub in unittest.defaultTestLoader.loadTestsFromModule(m):
        tests = list(sub) if isinstance(sub, unittest.TestSuite) else [sub]
        if not tests:
            continue
        cls = type(tests[0])
        names = [k for k, v in vars(m).items() if v is cls]
        if not names:
            return None
        n = "%s.%s" % (mod, names[0])
        if n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------- сводка

class Totals(object):
    def __init__(self):
        self.run = self.skipped = self.xfail = 0
        self.failures, self.errors, self.xpass, self.outputs, self.dts = [], [], [], [], {}

    def add(self, r):
        self.run += r["run"]
        self.skipped += r["skipped"]
        self.xfail += r["xfail"]
        self.failures += r["failures"]
        self.errors += r["errors"]
        self.xpass += r["xpass"]
        self.dts[r["mod"]] = r["dt"]
        if r.get("output"):
            self.outputs.append((r["mod"], r["output"]))

    def add_result(self, mod, res, dt):
        self.add({"mod": mod, "run": res.testsRun, "failures": [(_desc(t), tb) for t, tb in res.failures], "errors": [(_desc(t), tb) for t, tb in res.errors],
                  "skipped": len(res.skipped), "xfail": len(res.expectedFailures), "xpass": [str(t) for t in res.unexpectedSuccesses], "dt": dt, "output": ""})

    def module_dts(self):
        """Длительности частей и сумма по модулю (метка «mod.Class» -> ещё и «mod»): по сумме решается, делить ли модуль в следующий раз."""
        out = dict(self.dts)
        for k, v in self.dts.items():
            if "." in k:
                m = k.split(".", 1)[0]
                out[m] = out.get(m, 0.0) + v if m not in self.dts else out[m]
        return out

    def ok(self):
        return not (self.failures or self.errors or self.xpass)

    def report(self, out, elapsed, durations=0):
        out.write("\n")
        for mod, text in self.outputs:
            out.write("%s\nвывод модуля %s:\n%s\n%s\n" % (SEP2, mod, SEP2, text.rstrip()))
        for kind, rows in (("ERROR", self.errors), ("FAIL", self.failures)):
            for d, tb in rows:
                out.write("%s\n%s: %s\n%s\n%s\n" % (SEP1, kind, d, SEP2, tb.rstrip()))
        for t in self.xpass:
            out.write("%s\nUNEXPECTED SUCCESS: %s\n" % (SEP1, t))
        if durations:
            out.write("%s\nсамые долгие модули:\n" % SEP2)
            for m, dt in sorted(self.dts.items(), key=lambda x: -x[1])[:durations]:
                out.write("  %7.2f с  %s\n" % (dt, m))
        out.write("%s\nRan %d test%s in %.3fs\n\n" % (SEP2, self.run, "" if self.run == 1 else "s", elapsed))
        extra = [("skipped", self.skipped), ("expected failures", self.xfail), ("unexpected successes", len(self.xpass))]
        if self.ok():
            tail = ", ".join("%s=%d" % (k, v) for k, v in extra if v)
            out.write("OK%s\n" % (" (%s)" % tail if tail else ""))
        else:
            parts = [("failures", len(self.failures)), ("errors", len(self.errors))] + extra
            out.write("FAILED (%s)\n" % ", ".join("%s=%d" % (k, v) for k, v in parts if v))
        out.flush()


# ---------------------------------------------------------------- режимы

def run_sequential(tdir, targets, ks, verbosity):
    """-j 1: один процесс. Без фильтров — ровно прежний `unittest discover`."""
    _prepare_paths(tdir)
    loader = make_loader(ks)
    if targets is None:
        suite = loader.discover(tdir, top_level_dir=tdir)
    else:
        names = []
        for mod in sorted(targets, key=lambda m: (m in SERIAL_LAST, m)):
            names += targets[mod] or [mod]
        suite = loader.loadTestsFromNames(names)
    return 0 if unittest.TextTestRunner(verbosity=verbosity).run(suite).wasSuccessful() else 1


def _serial_in_parent(tdir, mods, targets, ks, verbosity, tot, out):
    for mod in mods:
        t0 = time.time()
        loader = make_loader(ks)
        suite = loader.loadTestsFromNames(targets[mod]) if targets and targets.get(mod) else loader.loadTestsFromName(mod)
        if not _count(suite):
            continue
        buf = io.StringIO()
        res = unittest.TextTestRunner(stream=buf, verbosity=verbosity).run(suite)
        tot.add_result(mod, res, time.time() - t0)
        out.write("." if res.wasSuccessful() else "F")
        out.flush()


def make_units(mods, targets, tdir, durations):
    """-> [(метка, модуль, имена или None)] в порядке запуска (самые долгие первыми). Модуль дольше SPLIT_S в прошлом прогоне (или из
    ALWAYS_SPLIT) делится на классы: иначе один такой модуль держит весь прогон (test_knowledge_migrate ~ 400 тестов)."""
    units = []
    for m in mods:
        names = (targets or {}).get(m)
        if names is None and (m in ALWAYS_SPLIT or durations.get(m, 0.0) >= SPLIT_S):
            try:
                parts = split_by_class(m)
            except Exception:
                parts = None
            if parts and len(parts) > 1:
                share = durations.get(m, 0.0) / len(parts)
                units += [(n, m, [n], durations.get(n, share)) for n in parts]
                continue
        units.append((m, m, names, durations.get(m)))

    def key(u):
        if u[3] is not None:
            return -u[3]
        try:
            return -os.path.getsize(os.path.join(tdir, u[1] + ".py")) / 20000.0
        except OSError:
            return 0.0
    return [u[:3] for u in sorted(units, key=key)]


def run_parallel(tdir, targets, ks, jobs, verbosity, durations, out=None):
    out = out or sys.stdout
    _prepare_paths(tdir)
    import _bootstrap  # noqa: F401  снимок реальных каталогов ДО запуска процессов (для SERIAL_LAST), окружение герметичности
    mods = list(targets) if targets is not None else all_modules(tdir)
    serial = [m for m in SERIAL_LAST if m in mods]
    units = make_units([m for m in mods if m not in serial], targets, tdir, load_durations(tdir))
    jobs = max(1, min(jobs, len(units) or 1))
    os.environ.setdefault("TB_PERF_FACTOR", str(PARALLEL_PERF_FACTOR))      # процессы делят машину: пороги времени тестов с запасом
    out.write("tb test: %d модулей (%d частей), процессов %d%s\n" % (len(mods), len(units) + len(serial), jobs,
                                                                   ", медленный набор" if os.environ.get("TB_SLOW_TESTS") == "1" else ""))
    out.flush()
    tot = Totals()
    t0 = time.time()
    pending = list(units)
    for attempt in range(2):            # процесс-исполнитель умер (os._exit, сбой интерпретатора) -> оставшиеся части в новом пуле один раз
        if not pending:
            break
        done = set()
        try:
            with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
                futs = {ex.submit(run_unit, tdir, label, mod, names, ks, verbosity): label for label, mod, names in pending}
                for fu in cf.as_completed(futs):
                    label = futs[fu]
                    try:
                        r = fu.result()
                    except cf.process.BrokenProcessPool:
                        raise
                    except Exception as e:
                        r = _empty(label, ("%s (рабочий процесс)" % label, "%s: %s\n" % (type(e).__name__, e)))
                    done.add(label)
                    tot.add(r)
                    out.write("." if not (r["failures"] or r["errors"] or r["xpass"]) else ("E" if r["errors"] else "F"))
                    out.flush()
        except cf.process.BrokenProcessPool as e:
            pending = [u for u in pending if u[0] not in done]
            if attempt == 1:
                for u in pending:
                    tot.add(_empty(u[0], ("%s (рабочий процесс)" % u[0], "рабочий процесс завершился аварийно: %s\n" % e)))
            continue
        pending = []
    _serial_in_parent(tdir, serial, targets, ks, verbosity, tot, out)
    elapsed = time.time() - t0
    if targets is None and not ks:
        save_durations(tdir, tot.module_dts())
    tot.report(out, elapsed, durations)
    return 0 if tot.ok() else 1


def default_jobs():
    n = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return max(1, n)


def main(argv):
    ap = argparse.ArgumentParser(prog="tb test", description="тесты tests/ (unittest); по умолчанию параллельно по модулям")
    ap.add_argument("names", nargs="*", help="модули, классы или тесты: test_x, test_x.Class, test_x.Class.test_y")
    ap.add_argument("-j", "--jobs", type=int, default=None, help="число процессов (по умолчанию: процессоры); 1 — один процесс, как раньше")
    ap.add_argument("-k", dest="k", action="append", default=[], help="только тесты, в имени которых есть подстрока (как unittest -k)")
    ap.add_argument("--slow", action="store_true", help="медленный набор тоже (TB_SLOW_TESTS=1)")
    ap.add_argument("-v", "--verbose", action="store_true", help="подробный вывод unittest (в параллельном режиме — в выводе упавших модулей)")
    ap.add_argument("--durations", type=int, default=0, metavar="N", help="показать N самых долгих модулей (параллельный режим)")
    a = ap.parse_args(argv)
    if a.slow:
        os.environ["TB_SLOW_TESTS"] = "1"
    tdir = tests_dir()
    targets = parse_targets(a.names, tdir) if a.names else None
    jobs = a.jobs if a.jobs is not None else default_jobs()
    verbosity = 2 if a.verbose else 1
    if jobs <= 1:
        return run_sequential(tdir, targets, a.k, verbosity)
    return run_parallel(tdir, targets, a.k, jobs, verbosity, a.durations)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
