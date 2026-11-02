"""Слои v2 (SPEC_V2, 05 WP0): AST-проверки исходников без импорта проверяемых модулей.

1. tb.brain, tb.logic, tb.core, tb.dash не импортируют tb.venues и tb.executor (прямо).
   Мозг (tb.brain) не достаёт до них и транзитивно по внутренним импортам tb.
2. tb.logic.*: никаких os, sys, socket, urllib, asyncio, subprocess, threading, tb.paths и вызовов open(), time.time(), time.sleep().
3. Строка BYBIT_API_SECRET встречается в src/tb только в launcher.py, venues/bybit.py, bybit_stream.py, bybit_wsx.py.
4. tb.brain не читает ~/.config/traider-bot (нет «.config» в строковых литералах).
5. R1: tb.labels не импортирует venues/executor/gateway/llm/brain/control/intel.items (прямо и транзитивно);
   tb.logic.bars/geometry/sim/costs чисты (п. 2) и не тянут биржу/исполнитель транзитивно.
"""
import ast
import os
import re
import unittest

import _bootstrap  # noqa: F401

SRC = os.path.join(_bootstrap.SRC, "tb")
NO_BYBIT_LAYERS = ("brain", "logic", "core", "dash", "gateway", "mcp", "llm", "control", "lifecycle")
FORBIDDEN_FROM_LAYERS = ("tb.venues", "tb.executor")
# явные исключения для п. 1: (пакет-источник модуль) -> разрешённый префикс; сейчас пусто
EXCEPTIONS = {}
LOGIC_FORBIDDEN_IMPORTS = ("os", "sys", "socket", "urllib", "asyncio", "subprocess", "threading", "tb.paths")
LOGIC_FORBIDDEN_CALLS = ("open", "time.time", "time.sleep")
SECRET_NAME = "BYBIT_API_SECRET"
SECRET_ALLOWED = ("launcher.py", os.path.join("venues", "bybit.py"), os.path.join("venues", "bybit_stream.py"),
                  os.path.join("venues", "bybit_wsx.py"))


def py_files(layer):
    top = os.path.join(SRC, layer)
    for d, _, fs in os.walk(top):
        for f in sorted(fs):
            if f.endswith(".py"):
                yield os.path.join(d, f)


def module_name(path):
    rel = os.path.relpath(path, os.path.dirname(SRC))[:-3].replace(os.sep, ".")
    return rel[:-9] if rel.endswith(".__init__") else rel


def imports_of(source, module="tb.x"):
    """-> множество имён импортируемых модулей (полные, относительные импорты разрешены относительно module). Учитывает вложенные импорты."""
    out = set()
    pkg = module.rsplit(".", 1)[0] if "." in module else module
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom):
            base = n.module or ""
            if n.level:
                parts = pkg.split(".")
                parts = parts[:len(parts) - (n.level - 1)] if n.level > 1 else parts
                base = ".".join(parts + ([n.module] if n.module else []))
            out.add(base)
            out.update(base + "." + a.name for a in n.names)   # from tb import venues -> tb.venues
    return out


def under(name, prefixes):
    return any(name == p or name.startswith(p + ".") for p in prefixes)


def forbidden_layer_imports(source, module="tb.x"):
    return sorted(n for n in imports_of(source, module) if under(n, FORBIDDEN_FROM_LAYERS))


LABELS_FORBIDDEN = ("tb.venues", "tb.executor", "tb.gateway", "tb.llm", "tb.brain", "tb.control", "tb.intel.items")


def labels_violations(source, module="tb.labels.x"):
    return sorted(n for n in imports_of(source, module) if under(n, LABELS_FORBIDDEN))


LEARN_FORBIDDEN = ("tb.venues", "tb.executor", "tb.gateway", "tb.control", "tb.brain", "tb.intel.items", "tb.dash", "tb.dash2")
LEARN_LLM_ALLOWED = ("tb.llm.usage",)               # из tb.llm страница читает только журнал вызовов (load.py)
LEARN_BANNED_NET = ("subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib", "multiprocessing", "ctypes", "webbrowser", "requests")
LEARN_WRITE_CALLS = ("remove", "unlink", "rmdir", "makedirs", "mkdir", "write_text", "write_bytes", "rmtree", "open_rw", "executescript")
LEARN_WRITE_MODULES = ("shutil",)                 # любой вызов shutil.*; os.replace/rename/system/popen: ниже (str.replace и dict.copy не путать)
LEARN_OS_WRITES = ("replace", "rename", "system", "popen", "truncate", "link", "symlink")


def learn_import_violations(source, module="tb.learn.x"):
    """Запрещённые импорты tb.learn: слои бирж/исполнителя/шлюза/управления/мозга/текстов новостей/дашбордов, любой tb.llm кроме usage, сеть и процессы."""
    out = []
    for n in imports_of(source, module):
        if under(n, LEARN_FORBIDDEN) or under(n, LEARN_BANNED_NET):
            out.append(n)
        elif under(n, ("tb.llm",)) and n != "tb.llm" and not under(n, LEARN_LLM_ALLOWED):
            out.append(n)
    return sorted(out)


def learn_write_violations(source):
    """Запись на диск и открытие базы на запись в tb.learn: open с режимом w/a/x/+, удаление, переименование, open_rw (страница только читает)."""
    out = []
    for n in ast.walk(ast.parse(source)):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
        if nm == "open":
            mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
            if mode is not None and not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and not any(c in mode.value for c in "wax+")):
                out.append("open:mode")           # режим не литерал только на чтение
        elif nm in LEARN_WRITE_CALLS:
            out.append("call:" + nm)
        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and (
                f.value.id in LEARN_WRITE_MODULES or (f.value.id == "os" and f.attr in LEARN_OS_WRITES)):
            out.append("call:%s.%s" % (f.value.id, f.attr))
    return out


def closure_hits(roots, hit, load_source):
    """По внутренним импортам tb от roots (в том числе внутри функций): модуль -> то, что вернул hit(source, module). load_source(mod) -> текст или None."""
    seen, stack, bad = set(), list(roots), {}
    while stack:
        m = stack.pop()
        if m in seen:
            continue
        seen.add(m)
        src = load_source(m)
        if src is None:
            continue
        h = hit(src, m)
        if h:
            bad[m] = h
        stack.extend(n for n in imports_of(src, m) if n.startswith("tb.") and n not in seen)
    return bad


def disk_source(mod):
    path = os.path.join(os.path.dirname(SRC), *mod.split("."))
    path = os.path.join(path, "__init__.py") if os.path.isdir(path) else path + ".py"
    return read(path) if os.path.isfile(path) else None


def learn_transitive_hit(source, module):
    """Для замыкания: запрещённые слои и tb.llm.* вне usage. Дочерние модули llm.usage проверяются так же, чужие llm в замыкание не попадают."""
    return sorted(n for n in imports_of(source, module) if under(n, LEARN_FORBIDDEN) or (under(n, ("tb.llm",)) and n != "tb.llm" and not under(n, LEARN_LLM_ALLOWED)))


def logic_violations(source):
    """-> список нарушений чистоты logic: запрещённые импорты и вызовы."""
    bad = [("import", n) for n in imports_of(source) if under(n, LOGIC_FORBIDDEN_IMPORTS)]
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Call):
            f, name = n.func, None
            if isinstance(f, ast.Name):
                name = f.id
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                name = f.value.id + "." + f.attr
            if name in LOGIC_FORBIDDEN_CALLS:
                bad.append(("call", name))
    return bad


def string_literals(source):
    return [n.value for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestHelpers(unittest.TestCase):
    """Сам проверяльщик ловит нарушения (иначе тест слоёв ничего не доказывает)."""

    def test_detects_layer_imports(self):
        for src in ("import tb.venues.bybit", "from tb.venues import bybit", "from tb import venues", "from tb.executor.ports import X",
                    "def f():\n    from tb.venues.bybit import Client"):
            self.assertTrue(forbidden_layer_imports(src), src)
        self.assertFalse(forbidden_layer_imports("from tb import paths\nimport json\nfrom tb.core import journal"))

    def test_relative_import_resolved(self):
        self.assertTrue(forbidden_layer_imports("from ..venues import bybit", "tb.core.x"))
        self.assertFalse(forbidden_layer_imports("from . import journal", "tb.core.x"))

    def test_logic_violations(self):
        self.assertTrue(logic_violations("import os"))
        self.assertTrue(logic_violations("from tb import paths"))
        self.assertTrue(logic_violations("import urllib.request"))
        self.assertTrue(logic_violations("x = open('f')"))
        self.assertTrue(logic_violations("import time\nt = time.time()"))
        self.assertTrue(logic_violations("import time\ntime.sleep(1)"))
        self.assertEqual(logic_violations("import math\nfrom collections import namedtuple\nx = math.floor(1.5)"), [])


class TestLayers(unittest.TestCase):
    def test_no_venues_or_executor_in_brain_logic_core_dash(self):
        bad = {}
        for layer in NO_BYBIT_LAYERS:
            for path in py_files(layer):
                mod = module_name(path)
                found = [n for n in forbidden_layer_imports(read(path), mod) if not under(n, EXCEPTIONS.get(mod, ()))]
                if found:
                    bad[mod] = found
        self.assertEqual(bad, {})

    def closure_violations(self, roots):
        """По внутренним импортам tb от roots: модуль -> запрещённые импорты (venues/executor)."""
        seen, stack, bad = set(), list(roots), {}
        while stack:
            mod = stack.pop()
            if mod in seen:
                continue
            seen.add(mod)
            path = os.path.join(os.path.dirname(SRC), *mod.split("."))
            path = os.path.join(path, "__init__.py") if os.path.isdir(path) else path + ".py"
            if not os.path.isfile(path):
                continue
            imps = imports_of(read(path), mod)
            hit = [n for n in imps if under(n, FORBIDDEN_FROM_LAYERS)]
            if hit:
                bad[mod] = hit
            stack.extend(n for n in imps if n.startswith("tb.") and n not in seen)
        return bad

    def test_brain_transitively_clean(self):
        """Всё, что импортирует tb.brain (по внутренним импортам tb), тоже не тянет tb.venues и tb.executor."""
        self.assertEqual(self.closure_violations([module_name(p) for p in py_files("brain")]), {})

    def test_modules_brain_will_reuse_are_clean(self):
        """brief импортирует dash.model (зафиксированный долг), планы используют guard/journal: их замыкание тоже чисто."""
        self.assertEqual(self.closure_violations(["tb.dash.model", "tb.core.guard", "tb.core.journal", "tb.core.session"]), {})

    def test_logic_is_pure(self):
        bad = {}
        for path in py_files("logic"):
            v = logic_violations(read(path))
            if v:
                bad[module_name(path)] = v
        self.assertEqual(bad, {})

    def test_secret_name_only_in_allowed_files(self):
        found = []
        for d, _, fs in os.walk(SRC):
            for f in fs:
                if f.endswith(".py"):
                    p = os.path.join(d, f)
                    if SECRET_NAME in read(p) and os.path.relpath(p, SRC) not in SECRET_ALLOWED:
                        found.append(os.path.relpath(p, SRC))
        self.assertEqual(found, [])

    def test_brain_does_not_read_user_config(self):
        bad = []
        for path in py_files("brain"):
            if any(".config" in s for s in string_literals(read(path))):
                bad.append(module_name(path))
        self.assertEqual(bad, [])

    def test_gateway_and_mcp_are_sealed(self):
        """WP11-A: шлюз и MCP не импортируют биржу, исполнитель, процессы, сеть и launcher; ключи и произвольные файлы не читают."""
        banned_imports = ("tb.venues", "tb.executor", "tb.launcher", "tb.marketdata", "subprocess", "socket", "ssl", "http", "urllib", "ftplib", "smtplib",
                          "asyncio", "multiprocessing", "ctypes", "webbrowser", "xmlrpc", "requests", "pty")
        banned_calls = ("os.system", "os.popen", "os.execv", "os.execl", "os.execvp", "os.spawnl", "os.spawnv", "eval", "exec", "compile", "__import__")
        open_allowed = {"tb.gateway.registry", "tb.gateway.audit"}      # единственные модули с open(): agents.json/токен/аудит
        bad = {}
        for layer in ("gateway", "mcp"):
            for path in py_files(layer):
                mod, src = module_name(path), read(path)
                issues = [n for n in imports_of(src, mod) if under(n, banned_imports)]
                for n in ast.walk(ast.parse(src)):
                    if isinstance(n, ast.Call):
                        f = n.func
                        name = f.id if isinstance(f, ast.Name) else (f.value.id + "." + f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else "")
                        if name in banned_calls or (name == "open" and mod not in open_allowed):
                            issues.append("call:" + name)
                lits = [x for x in string_literals(src) if not (x.endswith("_") and x.isupper())]    # префиксы очистки окружения (BYBIT_) не ключи
                if any("bybit" in x.lower() or x.endswith(".env") or "API_SECRET" in x or "API_KEY" in x for x in lits):
                    issues.append("key-literal")
                if mod != "tb.gateway.registry" and any(".config" in x for x in lits):
                    issues.append("user-config-literal")
                if issues:
                    bad[mod] = issues
        self.assertEqual(bad, {})

    def test_gateway_transitive_closure_has_no_exchange_code(self):
        self.assertEqual(self.closure_violations(["tb.gateway", "tb.gateway.cli", "tb.mcp.server"]), {})

    def test_only_registry_reads_user_config_dir_and_only_agents(self):
        src = read(os.path.join(SRC, "gateway", "registry.py"))
        self.assertIn('"agents"', src)
        for fn in ("find_key_file", "key_dirs", "read_env_file"):
            self.assertNotIn(fn, src)

    def test_llm_layer_rules(self):
        """WP11-C1: слой llm не тянет биржу, исполнитель, launcher и маркетдату (транзитивно); каталог ключей знает только llm/keys.py;
        ключ не пишется в окружение; имён моделей в коде нет (они только в config/providers.json)."""
        mods = [module_name(p) for p in py_files("llm")]
        self.assertEqual(self.closure_violations(mods), {})
        bad = {}
        for path in py_files("llm"):
            mod, src = module_name(path), read(path)
            issues = [n for n in imports_of(src, mod) if under(n, ("tb.launcher", "tb.marketdata", "tb.venues", "tb.executor", "subprocess"))]
            lits = string_literals(src)
            if mod != "tb.llm.keys" and any(".config" in x or x.endswith(".env") or "traider-bot" in x for x in lits):
                issues.append("user-config-literal")
            if any("BYBIT" in x.upper() for x in lits):
                issues.append("bybit-literal")
            for x in lits:
                if re.search(r"(deepseek-|claude-|gpt-|qwen|gemini|llama)", x, re.I) and "\n" not in x and len(x) < 60:
                    issues.append("model-name:" + x[:20])
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Attribute) and n.attr in ("putenv", "setdefault") and isinstance(n.value, ast.Attribute) and n.value.attr == "environ":
                    issues.append("environ-write")
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Subscript) and isinstance(t.value, ast.Attribute) and t.value.attr == "environ"
                                                     for t in n.targets):
                    issues.append("environ-write")
            if issues:
                bad[mod] = issues
        self.assertEqual(bad, {})

    # ---------------------------------------------------------------- WP12: плоскость управления
    def test_dash_layering_wp12(self):
        """Дашборд: без биржи, исполнителя, процессов и сигналов; из tb.control только client и auth; из tb.platform только status/services (чтение /api/platform)."""
        banned = ("tb.venues", "tb.executor", "tb.lifecycle", "tb.llm", "tb.launcher", "subprocess", "signal", "multiprocessing", "socket", "ctypes")
        banned_calls = ("os.kill", "os.system", "os.popen", "os.spawnl", "os.spawnv", "os.execv", "os.execl", "os.fork", "os.killpg")
        bad = {}
        for path in py_files("dash"):
            mod, src = module_name(path), read(path)
            issues = []
            for n in imports_of(src, mod):
                if under(n, banned):
                    issues.append(n)
                elif n.startswith("tb.platform") and not under(n, ("tb.platform.status", "tb.platform.services")) and n != "tb.platform":
                    issues.append(n)
                elif n.startswith("tb.intel") and not n.endswith(".dashapi") and n != "tb.intel":
                    issues.append(n)
                elif n.startswith("tb.control") and not under(n, ("tb.control.client", "tb.control.auth")) and n != "tb.control":
                    issues.append(n)
            for n in ast.walk(ast.parse(src)):
                if isinstance(n, ast.Call):
                    f = n.func
                    name = f.value.id + "." + f.attr if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else ""
                    if name in banned_calls:
                        issues.append("call:" + name)
            if issues:
                bad[mod] = issues
        self.assertEqual(bad, {})

    def test_dash_closure_has_no_exchange_or_process_code(self):
        mods = [module_name(p) for p in py_files("dash")]
        self.assertEqual(self.closure_violations(mods), {})
        seen, stack = set(), ["tb.control.client", "tb.control.auth", "tb.dash.ctlweb"]
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            pth = os.path.join(os.path.dirname(SRC), *m.split(".")) + ".py"
            if os.path.isfile(pth):
                imps = imports_of(read(pth), m)
                self.assertFalse([i for i in imps if under(i, ("subprocess", "signal", "tb.platform", "tb.venues", "tb.executor", "tb.launcher", "tb.lifecycle", "tb.llm"))], m)
                stack.extend(i for i in imps if i.startswith("tb.control") or i.startswith("tb.dash.ctlweb"))

    def test_client_writes_only_inbox(self):
        """tb.control.client пишет на диск только в функции submit и только в paths.control_inbox(); не тянет platform/executor/venues."""
        path = os.path.join(SRC, "control", "client.py")
        src = read(path)
        self.assertFalse([n for n in imports_of(src, "tb.control.client") if under(n, ("tb.platform", "tb.executor", "tb.venues", "subprocess", "tb.launcher"))])
        tree = ast.parse(src)
        writers = []
        for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
            for n in ast.walk(fn):
                if isinstance(n, ast.Call):
                    f = n.func
                    nm = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
                    mode = n.args[1].value if nm == "open" and len(n.args) > 1 and isinstance(n.args[1], ast.Constant) else None
                    if nm in ("replace", "remove", "rename", "makedirs", "mkdir", "unlink", "write_text") or (nm == "open" and mode and any(c in str(mode) for c in "wax+")):
                        writers.append((fn.name, nm))
        self.assertTrue(writers)
        self.assertEqual(sorted(set(f for f, _ in writers)), ["submit"])
        used = sorted(set(n.attr for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef) and fn.name == "submit" for n in ast.walk(fn)
                          if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "paths"))
        self.assertEqual(used, ["control_inbox"])

    def test_user_config_dir_literal_only_in_key_modules(self):
        """Путь ~/.config/traider-bot знают только launcher, шлюз (токены агентов), llm/keys, tb.control.auth и core/risklevels (ключ подписи уровня, WP16)."""
        allowed = {os.path.join("launcher.py"), os.path.join("llm", "keys.py"), os.path.join("gateway", "registry.py"), os.path.join("control", "auth.py"),
                   os.path.join("core", "risklevels.py")}
        found = set()
        for d, _, fs in os.walk(SRC):
            for f in fs:
                if f.endswith(".py"):
                    p = os.path.join(d, f)
                    if ".config" in [x for x in string_literals(read(p))]:
                        found.add(os.path.relpath(p, SRC))
        self.assertEqual(found - allowed, set())
        self.assertIn(os.path.join("control", "auth.py"), found)

    def test_control_layer_has_no_exchange_or_secrets(self):
        mods = [module_name(p) for p in py_files("control")]
        self.assertEqual(self.closure_violations(mods), {})
        bad = {}
        for path in py_files("control"):
            mod, src = module_name(path), read(path)
            issues = [n for n in imports_of(src, mod) if under(n, ("tb.venues", "tb.executor", "tb.launcher", "tb.marketdata", "tb.llm.keys", "socket", "urllib", "http", "ssl"))]
            lits = string_literals(src)
            if any("BYBIT_" in x.upper() for x in lits if len(x) < 40) or any("API_SECRET" in x or "API_KEY" in x for x in lits):
                issues.append("bybit-literal")
            if mod != "tb.control.auth" and any(x in (".config",) for x in lits):
                issues.append("user-config-literal")
            if issues:
                bad[mod] = issues
        self.assertEqual(bad, {})

    def test_brain_gateway_mcp_llm_do_not_import_control_or_news_items(self):
        bad = {}
        for layer in ("brain", "gateway", "mcp", "llm"):
            for path in py_files(layer):
                mod, src = module_name(path), read(path)
                issues = [n for n in imports_of(src, mod) if under(n, ("tb.control", "tb.intel.items"))]
                lits = [x for x in string_literals(src) if len(x) < 80]
                if layer in ("brain", "gateway", "mcp") and any("items.jsonl" in x or "headlines" in x.lower() for x in lits):
                    issues.append("news-text-literal")
                if issues:
                    bad[mod] = issues
        self.assertEqual(bad, {})

    def test_risklevels_is_the_only_core_user_of_signing_key(self):
        """WP16 читает ключ подписи уровня сам (_read_key); другие слои core не трогают control.json и не импортируют tb.control."""
        bad = []
        for path in py_files("core"):
            mod, src = module_name(path), read(path)
            if mod == "tb.core.risklevels":
                continue
            if any("control.json" in x for x in string_literals(src)) or [n for n in imports_of(src, mod) if under(n, ("tb.control",))]:
                bad.append(mod)
        self.assertEqual(bad, [])

    # ---------------------------------------------------------------- WP-X: провайдеры LLM, ключи, реестр бирж
    def test_exchange_registry_is_isolated_from_llm_brain_control_dash(self):
        """Реестр бирж (tb.venues.registry) знает только биржевой код, конфиг бирж и исполнителя; про LLM, мозг, ctl и дашборд не знает. И наоборот: llm, brain, control, dash и core
        не импортируют tb.venues (см. test_no_venues_or_executor_in_brain_logic_core_dash); конфиг бирж читает core/exchcfg без биржевого кода."""
        src = read(os.path.join(SRC, "venues", "registry.py"))
        self.assertFalse([n for n in imports_of(src, "tb.venues.registry") if under(n, ("tb.llm", "tb.brain", "tb.control", "tb.dash", "tb.gateway", "tb.mcp"))])
        for path in py_files("llm"):
            self.assertFalse([n for n in imports_of(read(path), module_name(path)) if under(n, ("tb.venues", "tb.executor"))], path)
        ex = read(os.path.join(SRC, "core", "exchcfg.py"))
        self.assertFalse([n for n in imports_of(ex, "tb.core.exchcfg") if under(n, ("tb.venues", "tb.executor", "tb.llm", "tb.brain"))])

    def test_llm_key_writers_are_only_keystore_and_dash_keyweb(self):
        """Запись файлов ключей LLM (tb.core.secretfile): только llm/keystore (терминал) и dash/keyweb (ввод из дашборда, write-only). Дашборд не читает ключи (tb.llm.keys) и не импортирует tb.llm."""
        users = []
        for d, _, fs in os.walk(SRC):
            for f in fs:
                if f.endswith(".py"):
                    p = os.path.join(d, f)
                    mod = module_name(p)
                    if any(n == "tb.core.secretfile" or n.startswith("tb.core.secretfile.") for n in imports_of(read(p), mod)):
                        users.append(os.path.relpath(p, SRC))
        self.assertEqual(sorted(users), sorted([os.path.join("dash", "keyweb.py"), os.path.join("llm", "keystore.py"), os.path.join("llm", "providers.py")]))
        for path in py_files("dash"):
            self.assertFalse([n for n in imports_of(read(path), module_name(path)) if under(n, ("tb.llm", "tb.venues", "tb.brain"))], path)

    def test_secretfile_rules(self):
        """Файлы ключей: зарезервированные имена бирж и служебных файлов закрыты списком, пути в модуле нет (каталог передаёт вызывающий)."""
        src = read(os.path.join(SRC, "core", "secretfile.py"))
        self.assertFalse([x for x in string_literals(src) if ".config" in x or "traider-bot" in x])
        from tb.core import secretfile
        for kf, ke in (("bybit_mainnet_trade.env", "BYBIT_API_KEY"), ("okx.env", "OKX_X1"), ("control.env", "XYZ_KEY"), ("ok.env", "BINANCE_SECRET")):
            self.assertIsNotNone(secretfile.check_spec(kf, ke), (kf, ke))
        self.assertIsNone(secretfile.check_spec("openai.env", "OPENAI_API_KEY"))
        self.assertIsNone(secretfile.check_spec("deepseek.env", "DEEPSEEK_API_KEY"))

    def test_settings_control_modules_have_no_secrets_or_exchange_code(self):
        for name in ("settings_args", "settings_ctl"):
            src = read(os.path.join(SRC, "control", name + ".py"))
            self.assertFalse([n for n in imports_of(src, "tb.control." + name) if under(n, ("tb.venues", "tb.executor", "tb.llm.keys", "tb.core.secretfile"))], name)

    # ---------------------------------------------------------------- R1: фабрика меток
    def test_labels_layer_rules(self):
        """R1 (14.1): tb.labels не импортирует venues/executor/gateway/llm/brain/control/intel.items ни прямо, ни транзитивно."""
        banned = LABELS_FORBIDDEN
        bad = {}
        for path in py_files("labels"):
            mod = module_name(path)
            found = labels_violations(read(path), mod)
            if found:
                bad[mod] = found
        self.assertEqual(bad, {})
        mods = [module_name(p) for p in py_files("labels")]
        seen, stack, trans = set(), list(mods), {}
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            path = os.path.join(os.path.dirname(SRC), *m.split("."))
            path = os.path.join(path, "__init__.py") if os.path.isdir(path) else path + ".py"
            if not os.path.isfile(path):
                continue
            imps = imports_of(read(path), m)
            hit = [n for n in imps if under(n, banned)]
            if hit:
                trans[m] = hit
            stack.extend(n for n in imps if n.startswith("tb.") and n not in seen)
        self.assertEqual(trans, {})

    def test_labels_checker_catches_violations(self):
        for src in ("import tb.venues.bybit", "from tb.executor import ports", "from tb import gateway", "from tb.llm.keys import x",
                    "from tb.brain import brief", "from tb.control import client", "from tb.intel.items import load",
                    "def f():\n    from tb.brain.validate import v"):
            self.assertTrue(labels_violations(src, "tb.labels.x"), src)
        self.assertTrue(labels_violations("from ..venues import bybit", "tb.labels.x"))
        self.assertFalse(labels_violations("from tb.logic import sim\nfrom tb.core import expstats\nfrom tb.intel import other", "tb.labels.x"))

    def test_logic_r1_modules_are_pure(self):
        """R1 (14.2): bars, geometry, sim, costs подчиняются правилам чистоты tb.logic (прямо) и не тянут биржу/исполнитель транзитивно."""
        names = ("bars", "geometry", "sim", "costs")
        for n in names:
            path = os.path.join(SRC, "logic", n + ".py")
            self.assertTrue(os.path.isfile(path), n)
            self.assertEqual(logic_violations(read(path)), [], n)
        self.assertEqual(self.closure_violations(["tb.logic." + n for n in names]), {})
        # транзитивная чистота по внутренним импортам tb.logic
        seen, stack, bad = set(), ["tb.logic." + n for n in names], {}
        while stack:
            m = stack.pop()
            if m in seen:
                continue
            seen.add(m)
            path = os.path.join(os.path.dirname(SRC), *m.split(".")) + ".py"
            if not os.path.isfile(path):
                continue
            v = logic_violations(read(path))
            if v:
                bad[m] = v
            stack.extend(i for i in imports_of(read(path), m) if i.startswith("tb.logic.") and i not in seen)
        self.assertEqual(bad, {})

    # ---------------------------------------------------------------- R0.2: страница «Учится ли мозг»
    def test_learn_layer_rules(self):
        """R0.2 (14): tb.learn не импортирует venues/executor/gateway/control/brain/intel.items/dash/dash2, из tb.llm берёт только usage, не лезет в сеть и процессы."""
        bad = {}
        for path in py_files("learn"):
            mod = module_name(path)
            found = learn_import_violations(read(path), mod)
            if found:
                bad[mod] = found
        self.assertEqual(bad, {})

    def test_learn_transitive_closure_is_clean(self):
        """Те же слои запрещены транзитивно (через tb.memory, tb.core, tb.labels, tb.llm.usage); tb.llm.* кроме usage в замыкание не попадает."""
        mods = [module_name(p) for p in py_files("learn")]
        self.assertTrue(mods)
        self.assertEqual(closure_hits(mods, learn_transitive_hit, disk_source), {})

    def test_learn_does_not_write_or_open_db_rw(self):
        """Страница только читает: нет записи файлов, удаления, open_rw (базу меток открывает store.open_ro)."""
        bad = {}
        for path in py_files("learn"):
            v = learn_write_violations(read(path))
            if v:
                bad[module_name(path)] = v
        self.assertEqual(bad, {})
        src = "\n".join(read(p) for p in py_files("learn"))
        self.assertIn("open_ro", src)
        self.assertNotIn("open_rw", src)
        self.assertNotIn("PRAGMA query_only", src)       # режим только чтения задаёт store.open_ro, не tb.learn
        self.assertFalse([x for x in string_literals(src) if ".config" in x or "API_SECRET" in x or "API_KEY" in x])

    def test_learn_checker_catches_violations(self):
        for src in ("import tb.venues.bybit", "from tb.executor import ports", "from tb import gateway", "from tb.control import client",
                    "from tb.brain import brief", "from tb.intel.items import load", "from tb.dash import model", "from tb.dash2 import topics",
                    "from tb.llm import keys", "from tb.llm.providers import x", "import socket", "import urllib.request", "import subprocess",
                    "def f():\n    from tb.brain.validate import v", "def f():\n    import tb.dash2.server"):
            self.assertTrue(learn_import_violations(src, "tb.learn.x"), src)
        self.assertTrue(learn_import_violations("from ..venues import bybit", "tb.learn.x"))
        self.assertFalse(learn_import_violations("from tb.llm import usage as u\nfrom tb.llm.usage import read_rows\nfrom tb.labels import store\n"
                                                 "from tb.memory import backtest\nfrom tb.core import expstats\nfrom tb import paths\nfrom . import conf", "tb.learn.x"))
        for src in ("open('f', 'w')", "open('f', mode='a')", "open('f', 'r+')", "open('f', m)", "import os\nos.remove('f')", "import os\nos.replace('a', 'b')",
                    "store.open_rw(root)", "import shutil\nshutil.rmtree('d')", "from pathlib import Path\nPath('f').write_text('x')", "os.makedirs('d')"):
            self.assertTrue(learn_write_violations(src), src)
        for src in ("open('f')", "open('f', 'r')", "open('f', encoding='utf-8')", "open('f', 'rb')", "store.open_ro(root)", "s.replace('a', 'b')", "d.copy()", "import copy\ncopy.deepcopy(x)"):
            self.assertEqual(learn_write_violations(src), [], src)

    def test_learn_closure_checker_catches_transitive_and_nested(self):
        mods = {"tb.learn.a": "from tb.mid import f", "tb.mid": "def g():\n    from tb.deep import h", "tb.deep": "import tb.venues.bybit",
                "tb.learn.b": "from tb.llm import usage", "tb.llm.usage": "from tb.llm import providers", "tb.llm": "", "tb.llm.providers": "x = 1",
                "tb.learn.c": "import tb.ok", "tb.ok": "import json"}
        got = closure_hits(["tb.learn.a"], learn_transitive_hit, mods.get)
        self.assertEqual(got, {"tb.deep": ["tb.venues.bybit"]})
        got = closure_hits(["tb.learn.b"], learn_transitive_hit, mods.get)
        self.assertEqual(got, {"tb.llm.usage": ["tb.llm.providers"]})                # usage, тянущий другой llm, ловится
        self.assertEqual(closure_hits(["tb.learn.c"], learn_transitive_hit, mods.get), {})

    def test_layer_dirs_exist(self):
        for layer in ("brain", "logic", "core", "dash", "executor", "venues", "gateway", "mcp", "llm", "control", "labels", "learn"):
            self.assertTrue(os.path.isfile(os.path.join(SRC, layer, "__init__.py")), layer)


if __name__ == "__main__":
    unittest.main()
