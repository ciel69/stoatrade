"""Первый запуск `./tb setup`: проверки среды (tb.setup_env) и решения мастера (setup_cli.FirstRun). Без сети, без установки: сеть, порт, pip и браузер подменяются."""
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _bootstrap  # noqa: F401
from tb import setup_cli, setup_env


def fake_import(present):
    """importer: модули из present ({имя: версия}) «установлены», остальные нет."""
    class M(object):
        pass

    def imp(name):
        if name not in present:
            raise ImportError(name)
        m = M()
        m.__version__ = present[name]
        return m
    return imp


class Versions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(setup_env.parse_version("Python 3.12.1"), (3, 12, 1))
        self.assertEqual(setup_env.parse_version("3.9"), (3, 9))
        self.assertEqual(setup_env.parse_version("17.3rc1"), (17, 3))
        self.assertIsNone(setup_env.parse_version("нет версии"))
        self.assertIsNone(setup_env.parse_version(None))

    def test_compare_ignores_length(self):
        self.assertTrue(setup_env.version_ok((3, 9), (3, 9, 0)))
        self.assertTrue(setup_env.version_ok((3, 14, 3), (3, 9)))
        self.assertFalse(setup_env.version_ok((3, 8, 18), (3, 9)))
        self.assertFalse(setup_env.version_ok(None, (3, 9)))

    def test_python_row(self):
        self.assertEqual(setup_env.check_python((3, 12, 0))["status"], "ok")
        r = setup_env.check_python((3, 8, 10))
        self.assertEqual(r["status"], "fail")
        self.assertIn("3.9", r["fix"])


class Checks(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbenv_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_websockets_missing_old_and_ok(self):
        self.assertEqual(setup_env.check_websockets(fake_import({}))["status"], "fail")
        self.assertEqual(setup_env.check_websockets(fake_import({"websockets": "12.0"}))["status"], "fail")
        self.assertEqual(setup_env.check_websockets(fake_import({"websockets": "13.1"}))["status"], "ok")
        self.assertTrue(setup_env.websockets_ok(fake_import({"websockets": "17.3"})))
        self.assertEqual(setup_env.check_ccxt(fake_import({}))["status"], "skip")          # необязательный пакет не ошибка

    def test_pip_command_user_outside_venv(self):
        self.assertIn("--user", setup_env.pip_install_cmd("py", venv=False))
        self.assertNotIn("--user", setup_env.pip_install_cmd("py", venv=True))
        self.assertEqual(setup_env.pip_install_cmd("py", venv=True)[-1], "websockets>=13")

    def test_shown_hides_full_interpreter_path(self):
        p = os.path.join(self.root, "secret-home", "python.exe")
        s = setup_env.shown([p, "-m", "pip", "install", "websockets>=13"])
        self.assertNotIn("secret-home", s)
        self.assertIn('"websockets>=13"', s)

    def test_dist_and_write(self):
        self.assertEqual(setup_env.check_dist(self.root)["status"], "fail")
        os.makedirs(os.path.join(self.root, "frontend", "dist"))
        open(os.path.join(self.root, "frontend", "dist", "index.html"), "w").close()
        self.assertEqual(setup_env.check_dist(self.root)["status"], "ok")
        self.assertEqual(setup_env.check_writable(self.root)["status"], "ok")           # state/ и runtime/ ещё нет: пишем в корень
        self.assertFalse(os.path.exists(os.path.join(self.root, "state")))              # проверка ничего не создаёт

    def test_port_from_local_config_and_busy(self):
        os.makedirs(os.path.join(self.root, "config", "local"))
        with open(os.path.join(self.root, "config", "local", "platform.json"), "w") as f:
            json.dump({"svc": {"dash": {"port": 8790}}}, f)
        self.assertEqual(setup_env.dash_port(self.root), 8790)
        self.assertEqual(setup_env.check_port(self.root, bind=lambda p: True)["status"], "ok")
        busy = setup_env.check_port(self.root, bind=lambda p: False, running=False)
        self.assertEqual(busy["status"], "warn")
        self.assertIn("--port", busy["fix"])
        self.assertEqual(setup_env.check_port(self.root, bind=lambda p: False, running=True)["status"], "ok")   # занят своей же платформой

    def test_port_free_real_socket(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            self.assertFalse(setup_env.port_free(s.getsockname()[1]))
        finally:
            s.close()

    def test_clock_offset_and_warning(self):
        ticks = iter([1000.0, 1000.2])
        fetch = lambda url: {"retCode": 0, "result": {"timeSecond": "1044", "timeNano": "1044100000000"}}
        off, lat = setup_env.clock_offset_s(fetch, clock=lambda: next(ticks))
        self.assertAlmostEqual(off, 44.0, places=3)                                   # часы отстают на 44 с
        self.assertAlmostEqual(lat, 0.2, places=3)
        clock = iter([1000.0, 1000.1])
        net, ck = setup_env.check_network_and_clock(fetch, clock=lambda: next(clock), platform="win32")
        self.assertEqual(net["status"], "ok")
        self.assertEqual(ck["status"], "warn")
        self.assertIn("отстают", ck["note"])
        self.assertIn("w32tm", ck["fix"])

    def test_clock_small_drift_ok_and_hints_per_os(self):
        clock = iter([1000.0, 1000.0])
        _net, ck = setup_env.check_network_and_clock(lambda u: {"result": {"timeSecond": "1001"}}, clock=lambda: next(clock))
        self.assertEqual(ck["status"], "ok")
        self.assertIn("sntp", setup_env.time_sync_hint("darwin"))
        self.assertIn("timedatectl", setup_env.time_sync_hint("linux", wsl=False))
        self.assertIn("hwclock", setup_env.time_sync_hint("linux", wsl=True))

    def test_no_network_is_warning_not_failure(self):
        def down(url):
            raise OSError("dns")
        net, ck = setup_env.check_network_and_clock(down)
        self.assertEqual(net["status"], "warn")
        self.assertEqual(ck["status"], "skip")

    def test_bybit_time_formats(self):
        self.assertEqual(setup_env.bybit_server_ms({"result": {"timeNano": "1700000000123456789"}}), 1700000000123)
        self.assertEqual(setup_env.bybit_server_ms({"result": {"timeSecond": "1700000000"}}), 1700000000000)
        self.assertEqual(setup_env.bybit_server_ms({"result": {}, "time": 5}), 5)
        self.assertIsNone(setup_env.bybit_server_ms({"result": {"timeSecond": "x"}}))

    def test_run_checks_offline_has_no_network(self):
        def boom(url):
            raise AssertionError("сеть в --offline")
        rows = setup_env.run_checks(self.root, offline=True, fetch=boom, bind=lambda p: True, importer=fake_import({"websockets": "13"}), running=False)
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, ["python", "websockets", "ccxt", "dist", "write", "port", "bybit", "clock"])
        self.assertEqual([r["id"] for r in setup_env.failed(rows)], ["dist"])
        out = []
        setup_env.render(rows, out.append)
        self.assertTrue(any("frontend/dist" in line for line in out))


class Decide(unittest.TestCase):
    def test_matrix(self):
        self.assertEqual(setup_cli.decide(True, yes=True, tty=True), "skip")       # уже есть: повторный запуск ничего не делает
        self.assertEqual(setup_cli.decide(False, yes=True, tty=False), "run")
        self.assertEqual(setup_cli.decide(False, yes=False, tty=True), "ask")
        self.assertEqual(setup_cli.decide(False, yes=False, tty=False), "refuse")  # без терминала молча не ставим

    def test_run_cmd_dry_run_prints_only(self):
        out, calls = [], []
        self.assertEqual(setup_cli.run_cmd(["python", "-m", "pip", "install", "x"], dry_run=True, out=out.append, runner=calls.append), 0)
        self.assertEqual(calls, [])
        self.assertIn("$ python -m pip install x", out[0])


class Wizard(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbwiz_root_")
        self.keys = os.path.join(tempfile.mkdtemp(prefix="tbwiz_keys_"), "tb")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.addCleanup(shutil.rmtree, os.path.dirname(self.keys), True)
        os.makedirs(os.path.join(self.root, "config"))
        for n in ("agents.json", "providers.json", "platform.json"):
            src = os.path.join(_bootstrap.ROOT, "config", n)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(self.root, "config", n))
        self.env = {"TB_KEYS_DIR": self.keys, "HOME": self.keys}
        self.lines, self.calls, self.opened = [], [], []

    def wiz(self, answers=(), ws_ok=True, **kw):
        a = list(answers)
        rows = lambda: [setup_env.row("python", "Python", "ok"), setup_env.row("websockets", "websockets", "ok" if ws_ok else "fail"), setup_env.row("dist", "dist", "ok")]
        return setup_cli.FirstRun(self.root, self.env, ask=lambda q: a.pop(0) if a else "", secret=lambda q: "", out=self.lines.append,
                                  runner=lambda c: self.calls.append(c) or 0, checks=rows, opener=self.opened.append, **kw)

    def cmds(self):
        return [" ".join(c[3:]) for c in self.calls if c[1:3] == ["-m", "tb.launcher"]]

    def test_yes_without_tty_installs_and_starts_but_never_asks_keys(self):
        w = self.wiz(ws_ok=False, yes=True, tty=False)
        self.assertEqual(w.main(start=True), 1)                       # websockets «поставлен» фейком, но проверка всё ещё fail -> не запускаем
        self.assertTrue(any("websockets>=13" in c for c in self.calls))
        self.assertNotIn("start", self.cmds())
        self.assertFalse(os.path.exists(os.path.join(self.keys, "control.json")))   # без терминала ключи не создаются
        self.assertIn("скрытый ввод невозможен", "\n".join(self.lines))

    def test_interactive_defaults_start_and_open(self):
        w = self.wiz(tty=True)
        w.ask = lambda q: ""                                         # Enter на каждый вопрос: значения по умолчанию (мозг пропущен, запуск и браузер: да)
        self.assertEqual(w.main(start=True), 0)
        self.assertIn("start", self.cmds())
        self.assertEqual(len(self.opened), 1)
        self.assertTrue(self.opened[0].startswith("http://127.0.0.1:"))

    def test_decline_install_runs_nothing(self):
        w = self.wiz(answers=["n"], ws_ok=False, tty=True)
        rows = w.stage_env()
        self.assertFalse(w.stage_deps(rows))
        self.assertEqual(self.calls, [])

    def test_dry_run_executes_nothing(self):
        w = self.wiz(ws_ok=False, yes=True, tty=False, dry_run=True)
        self.assertEqual(w.main(start=True), 0)
        self.assertEqual(self.calls, [])                             # только печать команд
        self.assertTrue(any("$ " in line and "start" in line for line in self.lines))
        self.assertEqual(self.opened, [])

    def test_local_llm_unreachable_changes_nothing(self):
        w = self.wiz(answers=["4", "1"], tty=True, http_get=lambda url: (_ for _ in ()).throw(OSError("refused")))
        self.assertIsNone(w.stage_llm())
        self.assertEqual(self.calls, [])
        self.assertIn("не отвечает", "\n".join(self.lines))

    def test_local_llm_lmstudio_commands(self):
        w = self.wiz(answers=["4", "1", "", "y", "n"], tty=True, http_get=lambda url: {"data": [{"id": "qwen2.5-14b-instruct"}]})
        self.assertEqual(w.stage_llm(), "lmstudio")
        c = self.cmds()
        self.assertIn("llm provider add lmstudio --preset lmstudio", c)
        self.assertIn("llm model set lmstudio main --name qwen2.5-14b-instruct --tools --json-mode", c)
        self.assertIn("llm provider enable lmstudio", c)
        self.assertNotIn("brain enable", c)                          # на «Включить мозг?» ответили n

    def test_cloud_bad_id_skipped(self):
        w = self.wiz(answers=["3", "rm -rf"], tty=True)
        self.assertIsNone(w.stage_llm())
        self.assertEqual(self.calls, [])


class Cli(unittest.TestCase):
    def test_bad_flag_and_no_tty(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(setup_cli.main(["--bogus"]), 2)
            self.assertEqual(setup_cli.main(["--check", "--bogus"]), 2)
            self.assertEqual(setup_cli.main([]), 2)                  # unittest: не TTY, без --yes
        self.assertIn("--check", err.getvalue())

    def test_check_offline_prints_both_tables(self):
        keys = tempfile.mkdtemp(prefix="tbchk_")
        self.addCleanup(shutil.rmtree, keys, True)
        old = dict(os.environ)
        os.environ.update({"TB_KEYS_DIR": os.path.join(keys, "k"), "HOME": keys})
        try:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = setup_cli.main(["--check", "--offline"])
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertIn(rc, (0, 1))
        self.assertIn("Среда", out.getvalue())
        self.assertIn("Ключ подписи", out.getvalue())
        self.assertFalse(os.path.exists(os.path.join(keys, "k")))


if __name__ == "__main__":
    unittest.main()
