"""Слои v2 (SPEC_V2, 05 WP0): AST-проверки исходников без импорта проверяемых модулей.

1. tb.brain, tb.logic, tb.core, tb.dash не импортируют tb.venues и tb.executor (прямо).
   Мозг (tb.brain) не достаёт до них и транзитивно по внутренним импортам tb.
2. tb.logic.*: никаких os, sys, socket, urllib, asyncio, subprocess, threading, tb.paths и вызовов open(), time.time(), time.sleep().
3. Строка BYBIT_API_SECRET встречается в src/tb только в launcher.py, venues/bybit.py, bybit_stream.py, bybit_wsx.py.
4. tb.brain не читает ~/.config/traider-bot (нет «.config» в строковых литералах).
5. R1: tb.labels не импортирует venues/executor/gateway/llm/brain/control/intel.items (прямо и транзитивно);
   tb.logic.bars/geometry/sim/costs чисты (п. 2) и не тянут биржу/исполнитель транзитивно.
6. R2 (задача 22): tb.logic.learn_cond/learn_rules чисты и импортируют из tb только tb.logic; tb.knowledge и tb.learnloop не импортируют
   brain/gateway/mcp/venues/executor/control/launcher/secretfile и tb.llm кроме usage (прямо и транзитивно), не знают каталога ключей;
   tb.learnloop не импортирует CLI вне пакета и localcfg; запись в config/ из tb.learnloop невозможна (ConfigWrites: функции, которые
   строят путь в config/ и пишут файл); config/learnloop.json (promotion.mode) пишет только tb.knowledge.learn_cli.set_mode.
7. R1.7 (задача 24, свойство 17, Requirements 3.1–3.3): tb.cmkt не импортирует торговлю, исполнителя, шлюз, мозг, llm, управление,
   launcher, MCP, файлы ключей, метки, цикл обучения, знания и intel (прямо и транзитивно), не содержит hmac, строк ключей, каталога
   ключей и чтения config/local/; tb.logic.cmkt чист; tb.core.cmktdoc не импортирует tb.cmkt и открывает ряды только на чтение;
   шлюз, мозг, метки, цикл обучения, исполнитель и дашборд читают сводный рынок только через tb.core.cmktdoc; в tb.gateway нет
   названий бирж (список берётся из config/cmkt.json и tb.logic.cmkt.MARKETS).
8. R4 (задача 14, свойство 19, Requirement 18.3): tb.core.exitdoc не импортирует tb.learnloop, tb.knowledge, tb.labels, tb.venues,
   tb.executor (прямо и транзитивно по внутренним импортам tb) и ничего не пишет — только читает файлы runtime/ и config/exits.json.
9. R4 (задача 23, свойство 23, Requirement 18.1–18.5): tb.logic.exitpol/exitsim/qty чисты по п. 2 и импортируют из tb только tb.logic;
   tb.core.exitdoc не пишет в config/; модули tb.learnloop.exits_* проходят loop_hit и ConfigWrites; единственные писатели config/
   в tb.knowledge — learn_cli.set_mode (learnloop.json) и exits_cli.set_mode (exits.json); проход цикла выходов на фикстурной базе
   во временном корне не меняет хеши config/risk_profile.json, risk_levels.json, exec_limits.json.
"""
import ast
import hashlib
import json
import os
import re
import shutil
import tempfile
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


# ---------------------------------------------------------------- R2: цикл обучения (задача 22; Requirements 8.4, 8.7, 8.8, 16.2)
LOOP_FORBIDDEN = ("tb.brain", "tb.gateway", "tb.venues", "tb.executor", "tb.control", "tb.launcher", "tb.mcp", "tb.core.secretfile")
LOOP_LLM_ALLOWED = ("tb.llm.usage",)
LOOP_CLI = ("tb.knowledge.learn_cli", "tb.knowledge.rulebook_cli", "tb.knowledge.cli", "tb.core.localcfg")   # tb.learnloop их не импортирует
KEY_LITERALS = (".config", "traider-bot", "API_SECRET", "API_KEY", "BYBIT_")


def loop_hit(source, module):
    """Запрещённые импорты tb.knowledge / tb.learnloop (прямо; для замыкания — то же): мозг, шлюз, MCP, биржа, исполнитель, управление,
    launcher, файлы ключей, любой tb.llm кроме usage. Для пакета tb.learnloop ещё CLI вне пакета и localcfg (писатели config/)."""
    out = []
    for n in imports_of(source, module):
        if under(n, LOOP_FORBIDDEN) or (under(n, ("tb.llm",)) and n != "tb.llm" and not under(n, LOOP_LLM_ALLOWED)):
            out.append(n)
        elif under(module, ("tb.learnloop",)) and under(n, LOOP_CLI):
            out.append(n)
    return sorted(out)


def _docstring_ids(tree):
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str):
            ids.add(id(n.body[0].value))
    return ids


def code_literals(source):
    """Строковые литералы кода без docstring-ов."""
    tree = ast.parse(source)
    doc = _docstring_ids(tree)
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc]


def key_literals(source):
    """Литералы каталога ключей и имён ключей (~/.config/traider-bot, *_API_KEY, BYBIT_*) в коде модуля."""
    return sorted(set(x for x in code_literals(source) for k in KEY_LITERALS if k in x))


WRITE_OS = ("replace", "rename", "remove", "unlink", "makedirs", "mkdir", "rmdir", "truncate", "link", "symlink", "chmod")
CONFIG_WRITE_FUNCS = ("update_local",)            # tb.core.localcfg: запись config/local/*
CONFIG_FILES = ("risk_profile.json", "risk_levels.json", "exec_limits.json")

# R4 (задача 23, свойство 23, Requirement 18): логика выходов, читатель политики и цикл выходов.
EXITS_LOGIC = ("exitpol", "exitsim", "qty")      # tb.logic.*: чистота п. 2 и из tb только tb.logic.*
EXITDOC_BANNED = ("tb.learnloop", "tb.knowledge", "tb.labels", "tb.venues", "tb.executor")      # tb.core.exitdoc (18.3)
EXITS_LOOP_MODULES = ("tb.learnloop.exits_conf", "tb.learnloop.exits_eval", "tb.learnloop.exits_report",
                      "tb.learnloop.exits_search", "tb.learnloop.exits_trial", "tb.learnloop.exits_versions",
                      "tb.learnloop.exits_watch")                                            # модули цикла выходов (18.2)
PROTECTED_CONFIG_FILES = CONFIG_FILES                                                        # хеши до/после прохода (18.5)
# единственные писатели config/ в tb.knowledge (18.2): CLI владельца, у каждого один файл
CONFIG_WRITER_FILES = {"tb.knowledge.learn_cli": "learnloop.json", "tb.knowledge.exits_cli": "exits.json"}


def _fname(f):
    if isinstance(f, ast.Attribute):
        return (f.value.id if isinstance(f.value, ast.Name) else None), f.attr
    if isinstance(f, ast.Name):
        return None, f.id
    return None, ""


def is_write_call(n):
    """Вызов, пишущий файл: open с режимом w/a/x/+ (или не литералом), os.open не O_RDONLY, os.replace/rename/remove/makedirs..., shutil.*,
    любой вызов с "write" в имени (write_json, write_text, _write_atomic, f.write) и update_local."""
    base, nm = _fname(n.func)
    if nm == "open" and base in (None, "io", "builtins", "codecs"):
        mode = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "mode"), None)
        return mode is not None and not (isinstance(mode, ast.Constant) and isinstance(mode.value, str) and not any(c in mode.value for c in "wax+"))
    if nm == "open" and base == "os":
        fl = n.args[1] if len(n.args) > 1 else None
        return not (isinstance(fl, ast.Attribute) and fl.attr == "O_RDONLY")
    if base == "os" and nm in WRITE_OS:
        return True
    if base == "shutil":
        return True
    return "write" in nm.lower() or nm in CONFIG_WRITE_FUNCS


class ConfigWrites(object):
    """Статический анализ записей в config/ (Requirement 8.4): функция модуля — писатель config/, если она и строит путь в config/, и пишет файл.
    Путь в config/: вызов paths.config(...) (target — имя файла: только с этим аргументом), литерал "config" / "config/..." / имя охраняемого
    файла, вызов функции (своего модуля или модуля tb.*), которая возвращает такой путь, update_local. Запись — is_write_call в теле функции или
    в функции своего модуля, которую она вызывает (замыкание по вызовам). Свойства функций соседних модулей читаются с диска (load_source)."""

    def __init__(self, target=None, load_source=None):
        self.target = target
        self.load = load_source or disk_source
        self.cache, self.consts_cache = {}, {}

    def consts(self, module):
        if module not in self.consts_cache:
            src = self.load(module)
            d = {}
            if src is not None:
                for n in ast.parse(src).body:
                    if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
                        d.update((t.id, n.value.value) for t in n.targets if isinstance(t, ast.Name))
            self.consts_cache[module] = d
        return self.consts_cache[module]

    def aliases(self, tree, module):
        al = {}
        pkg = module.rsplit(".", 1)[0] if "." in module else module
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    al[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    parts = pkg.split(".")
                    parts = parts[:len(parts) - (n.level - 1)] if n.level > 1 else parts
                    base = ".".join(parts + ([n.module] if n.module else []))
                for a in n.names:
                    full = base + "." + a.name
                    al[a.asname or a.name] = full if (full.startswith("tb.") and self.load(full) is not None) else (base, a.name)
        return al

    def _str(self, e, module, al):
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return e.value
        if isinstance(e, ast.Name):
            return self.consts(module).get(e.id)
        if isinstance(e, ast.Attribute) and isinstance(e.value, ast.Name) and isinstance(al.get(e.value.id), str):
            return self.consts(al[e.value.id]).get(e.attr)
        return None

    def _lit(self, v):
        if not isinstance(v, str):
            return False
        if self.target is not None:
            return v == self.target
        return v == "config" or v.startswith(("config/", "config\\")) or v in CONFIG_FILES

    def _ref_call(self, n, module, al):
        base, nm = _fname(n.func)
        if nm in CONFIG_WRITE_FUNCS and self.target is None:
            return True
        tgt = al.get(base) if base else al.get(nm)
        if (base and tgt == "tb.paths" and nm == "config") or (base is None and tgt == ("tb.paths", "config")):
            if self.target is None:
                return True
            return any(self._str(a, module, al) == self.target for a in list(n.args) + [k.value for k in n.keywords])
        other = tgt if base and isinstance(tgt, str) else (tgt[0] if base is None and isinstance(tgt, tuple) else None)
        if other and other.startswith("tb.") and other != module:
            info = self.module(other)
            return bool(info and info.get(nm, {}).get("ret"))
        return False

    def module(self, module, source=None):
        """-> {функция: {"ref", "write", "ret"}} (ret — возвращает путь в config/). source — текст вместо файла (проверка самого анализатора)."""
        key = module if source is None else (module, source)
        if key in self.cache:
            return self.cache[key] or {}
        self.cache[key] = None                       # защита от циклов импорта
        src = self.load(module) if source is None else source
        if src is None:
            return {}
        tree = ast.parse(src)
        al, doc = self.aliases(tree, module), _docstring_ids(tree)
        for n in ast.walk(tree):                     # ключи словарей и индексы ({"config": ...}, d["config"]) — не пути
            if isinstance(n, ast.Dict):
                doc.update(id(k) for k in n.keys if k is not None)
            elif isinstance(n, ast.Subscript):
                doc.add(id(n.slice))
        defs = {}
        for fn in ast.walk(tree):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defs.setdefault(fn.name, []).append(fn)

        def local(n):
            base, nm = _fname(n.func)
            return nm if nm in defs and base in (None, "self", "cls") else None
        st = {}
        for name, fns in defs.items():
            s = {"ref": False, "write": False, "ret": False, "calls": set(), "rcalls": set()}
            for fn in fns:
                for n in ast.walk(fn):
                    if isinstance(n, ast.Call):
                        s["write"] = s["write"] or is_write_call(n)
                        s["ref"] = s["ref"] or self._ref_call(n, module, al)
                        if local(n):
                            s["calls"].add(local(n))
                    elif isinstance(n, ast.Constant) and id(n) not in doc and self._lit(n.value):
                        s["ref"] = True
                for r in (x for x in ast.walk(fn) if isinstance(x, ast.Return) and x.value is not None):
                    for n in ast.walk(r.value):
                        if isinstance(n, ast.Call):
                            s["ret"] = s["ret"] or self._ref_call(n, module, al)
                            if local(n):
                                s["rcalls"].add(local(n))
                        elif isinstance(n, ast.Constant) and self._lit(n.value):
                            s["ret"] = True
            st[name] = s
        changed = True
        while changed:
            changed = False
            for s in st.values():
                new_ret = s["ret"] or any(st[c]["ret"] for c in s["rcalls"])
                new_ref = s["ref"] or new_ret or any(st[c]["ret"] for c in s["calls"])
                new_write = s["write"] or any(st[c]["write"] for c in s["calls"])
                if (new_ret, new_ref, new_write) != (s["ret"], s["ref"], s["write"]):
                    s["ret"], s["ref"], s["write"] = new_ret, new_ref, new_write
                    changed = True
        out = dict((k, {"ref": v["ref"], "write": v["write"], "ret": v["ret"]}) for k, v in st.items())
        self.cache[key] = out
        return out

    def writers(self, module, source=None):
        return sorted(k for k, v in self.module(module, source).items() if v["ref"] and v["write"])


def config_writers(mods, target=None):
    """Писатели config/ (Requirement 8.4): модуль -> функции. target — имя файла (None — любой путь в config/)."""
    scan = ConfigWrites(target=target)
    return dict((m, scan.writers(m)) for m in mods if scan.writers(m))


def exit_logic_hit(source, module="tb.logic.x"):
    """Чистота логики выходов (Requirement 18.1): запреты tb.logic и из tb только tb.logic.* (для замыкания по импортам)."""
    return logic_violations(source) + ["tb:" + n for n in imports_of(source, module)
                                       if n.startswith("tb.") and not under(n, ("tb.logic",))]


def exitdoc_hit(source, module="tb.core.exitdoc"):
    """Слои tb.core.exitdoc (Requirement 18.3): запрещённые пакеты прямо; транзитивно — через closure_hits."""
    return sorted(n for n in imports_of(source, module) if under(n, EXITDOC_BANNED))


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


# ---------------------------------------------------------------- R1.7: сводный рынок (задача 24; свойство 17; Requirements 3.1–3.3)
ROOT = os.path.dirname(os.path.dirname(SRC))
CMKT = "tb.cmkt"
CMKT_FORBIDDEN = ("tb.venues", "tb.executor", "tb.gateway", "tb.brain", "tb.llm", "tb.control", "tb.launcher", "tb.mcp",
                  "tb.core.secretfile", "tb.labels", "tb.learnloop", "tb.knowledge", "tb.intel")
CMKT_SECRET_LITERALS = ("SECRET", "passphrase", "PASSPHRASE")       # к KEY_LITERALS: .config, traider-bot, API_SECRET, API_KEY, BYBIT_
CMKT_CONSUMERS = ("brain", "gateway", "labels", "learnloop", "executor", "dash")
# модули вне списка потребителей, которым импорт tb.cmkt разрешён: они не читают данные рынка
CMKT_REACH_OK = (
    "tb.platform.services",     # реестр сервисов: имена `cmkt@<venue><p|s>`, команды запуска, чистка окружения (design.md, Architecture)
    "tb.intel.service",         # сервис intel: не запускает XStream при `compat_xflow` (design.md, «Изменения потребителей»)
)


def cmkt_import_violations(source, module="tb.cmkt.x"):
    """Запрещённые импорты tb.cmkt (Requirement 3.2; для замыкания — то же): торговые слои, исполнитель, шлюз, мозг, llm, управление,
    launcher, MCP, файлы ключей, метки, цикл обучения, знания, intel."""
    return sorted(n for n in imports_of(source, module) if under(n, CMKT_FORBIDDEN))


def _is_local_config(x):
    """Литерал, читающий config/local/: каталог `local` в config/ (`paths.config("local", ...)`) или `localcfg` (писатель local)."""
    low = x.strip().lower()
    return "localcfg" in low or "config/local" in low or "config\\local" in low or low == "local"


def cmkt_secret_violations(source, module="tb.cmkt.x"):
    """Requirement 3.1: в tb.cmkt нет подписи (hmac), строк ключей и каталога ключей (~/.config/traider-bot), чтения config/local/."""
    out = ["import:" + n for n in imports_of(source, module)
           if n == "hmac" or n.startswith("hmac.") or under(n, ("tb.core.localcfg",))]
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in ("hmac", "localcfg"):
            out.append("call:%s.%s" % (n.value.id, n.attr))
    out += ["lit:" + x[:40] for x in key_literals(source)]
    out += ["lit:" + x[:40] for x in code_literals(source)
            if any(k in x for k in CMKT_SECRET_LITERALS) or _is_local_config(x)]
    return sorted(set(out))


def cmkt_reach_violations(roots, allowed=CMKT_REACH_OK, load_source=None):
    """Модули вне tb.cmkt, достижимые от roots по внутренним импортам tb и импортирующие tb.cmkt (прямо или через посредника):
    модуль -> импорты. tb.cmkt — терминал (его собственные запреты проверяет test_cmkt_layer_is_isolated), allowed — модули-исключения,
    которые в tb.cmkt не спускаются."""
    load = load_source or disk_source
    seen, stack, bad = set(), list(roots), {}
    while stack:
        m = stack.pop()
        if m in seen or under(m, (CMKT,)) or under(m, allowed):
            continue
        seen.add(m)
        src = load(m)
        if src is None:
            continue
        imps = imports_of(src, m)
        hit = [n for n in imps if under(n, (CMKT,))]
        if hit:
            bad[m] = hit
        stack.extend(n for n in imps if n.startswith("tb.") and n not in seen)
    return bad


def cmkt_venue_names():
    """Названия бирж для проверки шлюза: ключи `venues` из config/cmkt.json и префиксы `MARKETS` из tb.logic.cmkt (в тесте не копируются)."""
    names = set()
    try:
        with open(os.path.join(ROOT, "config", "cmkt.json"), encoding="utf-8") as f:
            names.update(str(k).lower() for k in (json.load(f).get("venues") or {}))
    except (OSError, ValueError):
        pass
    try:
        from tb.logic import cmkt as L
        names.update(str(m).split("_", 1)[0].lower() for m in L.MARKETS)
    except (ImportError, AttributeError):
        pass
    return sorted(n for n in names if n)


def venue_literals(source, venues):
    """Строковые литералы с названием биржи отдельным словом: `gateway` и `gateway_audit.jsonl` биржей `gate` не считаются."""
    pat = re.compile(r"(?<![a-z0-9])(%s)(?![a-z0-9])" % "|".join(re.escape(v) for v in venues), re.I)
    return sorted(set(x for x in string_literals(source) if pat.search(x)))


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

    def test_cmkt_checkers_catch_violations(self):
        for src in ("import tb.venues.bybit", "from tb.executor import ports", "from tb.gateway import catalog", "from tb.brain import brief",
                    "from tb.llm.keys import x", "from tb.control import client", "from tb import launcher", "import tb.mcp.server",
                    "from tb.core import secretfile", "from tb.labels import store", "from tb.learnloop import cycle",
                    "from tb.knowledge import rulebook_view", "from tb.intel import items", "from ..venues import bybit",
                    "def f():\n    from tb.intel.xex import x"):
            self.assertTrue(cmkt_import_violations(src, "tb.cmkt.x"), src)
        self.assertFalse(cmkt_import_violations("from tb.logic import cmkt\nfrom tb.core import filelock\nfrom tb.marketdata import wsclient\n"
                                                "import tb.fsutil\nfrom . import conf", "tb.cmkt.x"))
        for src in ("import hmac", "import hmac as H", "import hmac\nhmac.new(b'k', b'm', 'sha256')", "k = 'BYBIT_API_SECRET'",
                    "p = os.path.join(h, '.config', 'traider-bot')", "n = env['BINANCE_API_KEY']", "x = 'passphrase'",
                    "from tb import paths\np = paths.config('local', 'exec_limits.json')", "from tb.core import localcfg",
                    "from tb.core import localcfg as LC\nLC.update_local('x', f)"):
            self.assertTrue(cmkt_secret_violations(src), src)
        for src in ("from tb import paths\np = paths.config('cmkt.json')\nX = 'state/cmkt'", "import json\nimport os\nX = 'локальный'",
                    "from tb.core import filelock"):
            self.assertEqual(cmkt_secret_violations(src), [], src)
        # транзитивная достижимость tb.cmkt от потребителей (реестр сервисов и intel — исключения)
        mods = {"tb.brain.a": "from tb.core import cmktdoc", "tb.core.cmktdoc": "from tb.logic import cmkt\nimport tb.paths",
                "tb.logic.cmkt": "x = 1", "tb.paths": "y = 1",
                "tb.brain.c": "from tb.mid import f", "tb.mid": "def g():\n    import tb.cmkt.conf", "tb.cmkt": "", "tb.cmkt.conf": "z = 1",
                "tb.brain.d": "from tb.platform import services", "tb.platform.services": "from tb.cmkt import conf",
                "tb.brain.e": "from tb.intel import service", "tb.intel.service": "from tb.cmkt import conf"}
        self.assertEqual(cmkt_reach_violations(["tb.brain.a"], load_source=mods.get), {})
        self.assertEqual(cmkt_reach_violations(["tb.brain.c"], load_source=mods.get), {"tb.mid": ["tb.cmkt.conf"]})
        self.assertEqual(cmkt_reach_violations(["tb.brain.d", "tb.brain.e"], load_source=mods.get), {})
        # названия бирж: только отдельным словом
        self.assertTrue(venue_literals("x = 'сводка по Bybit'", ("bybit", "gate")))
        self.assertTrue(venue_literals("x = 'BYBIT_'", ("bybit", "gate")))
        self.assertEqual(venue_literals("p = 'state/gateway_audit.jsonl'\nx = 'tb.gateway'\ny = 'gateway_actor'", ("bybit", "gate")), [])


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

    # ---------------------------------------------------------------- R2: цикл обучения (задача 22; свойство 15)
    def test_logic_learn_modules_are_pure(self):
        """tb.logic.learn_cond и learn_rules чисты (п. 2) и из tb импортируют только tb.logic.* (транзитивно)."""
        def hit(src, m):
            return logic_violations(src) + [("tb", i) for i in imports_of(src, m) if i.startswith("tb.") and not under(i, ("tb.logic",))]
        for n in ("learn_cond", "learn_rules"):
            self.assertTrue(os.path.isfile(os.path.join(SRC, "logic", n + ".py")), n)
        self.assertEqual(closure_hits(["tb.logic.learn_cond", "tb.logic.learn_rules"], hit, disk_source), {})

    def test_knowledge_and_learnloop_layers(self):
        """tb.knowledge и tb.learnloop не импортируют мозг, шлюз, MCP, биржу, исполнитель, управление, launcher, файлы ключей и tb.llm кроме usage
        (прямо и транзитивно); tb.learnloop не импортирует CLI вне пакета и localcfg; литералов каталога ключей нет (Requirements 8.7, 8.8, 16.2)."""
        mods = [module_name(p) for p in py_files("knowledge")] + [module_name(p) for p in py_files("learnloop")]
        self.assertIn("tb.learnloop.cycle", mods)
        self.assertIn("tb.knowledge.rulebook_view", mods)
        bad = {}
        for mod in mods:
            src = disk_source(mod)
            issues = loop_hit(src, mod) + ["lit:" + x[:30] for x in key_literals(src)]
            if issues:
                bad[mod] = issues
        self.assertEqual(bad, {})
        trans = closure_hits(mods, lambda s, m: [n for n in loop_hit(s, m) if not under(n, LOOP_CLI)], disk_source)
        self.assertEqual(trans, {})

    def test_learnloop_does_not_write_config(self):
        """Запись в config/ из пакета tb.learnloop невозможна (Requirement 8.4): ни одна функция не строит путь в config/ и не пишет файл;
        paths.config есть только в загрузчиках conf и exits_conf (R4, config/exits.json; только чтение)."""
        scan = ConfigWrites()
        bad = {}
        loaders = ("tb.learnloop.conf", "tb.learnloop.exits_conf")
        for path in py_files("learnloop"):
            mod = module_name(path)
            w = scan.writers(mod)
            if w:
                bad[mod] = w
            if mod not in loaders and "paths.config" in read(path):
                bad.setdefault(mod, []).append("paths.config")
        self.assertEqual(bad, {})
        for mod in loaders:
            conf = scan.module(mod)
            self.assertTrue(conf["load"]["ref"], mod)                # анализатор видит путь загрузчика
            self.assertFalse(any(v["write"] for v in conf.values()), mod)
        self.assertTrue(scan.module("tb.learnloop.watch")["write_lease"]["write"])      # и видит запись аренды (в runtime/, не в config/)
        self.assertFalse(scan.module("tb.learnloop.watch")["write_lease"]["ref"])
        self.assertEqual(config_writers([module_name(p) for p in py_files("learnloop")], target="exits.json"), {})

    def test_promotion_mode_writer_only_learn_cli(self):
        """Requirement 18.2, свойство 23: config/learnloop.json (promotion.mode) пишет только tb.knowledge.learn_cli.set_mode,
        config/exits.json (promotion.mode) — только tb.knowledge.exits_cli.set_mode (R4, задачи 21 и 23); других писателей
        config/ в tb.knowledge и tb.learnloop нет. Единственный источник ожиданий — CONFIG_WRITER_FILES."""
        all_mods = [module_name(os.path.join(d, f)) for d, _, fs in os.walk(SRC) for f in sorted(fs) if f.endswith(".py")]
        for mod, fname in sorted(CONFIG_WRITER_FILES.items()):
            self.assertEqual(config_writers(all_mods, target=fname), {mod: ["set_mode"]}, fname)
        gate_mods = [module_name(p) for p in py_files("knowledge")] + [module_name(p) for p in py_files("learnloop")]
        self.assertEqual(config_writers(gate_mods), dict((m, ["set_mode"]) for m in CONFIG_WRITER_FILES))

    def test_loop_checkers_catch_violations(self):
        """Проверки слоёв и записей цикла ловят искусственные нарушения (иначе тесты выше ничего не доказывают)."""
        for src in ("import tb.venues.bybit", "from tb.executor import ports", "from tb import gateway", "from tb.control import client",
                    "from tb.brain import cands", "from tb.llm import keys", "from tb.llm.providers import x", "from tb.core import secretfile",
                    "from tb import launcher", "import tb.mcp.server", "def f():\n    from tb.brain.validate import v", "from ..venues import bybit"):
            self.assertTrue(loop_hit(src, "tb.learnloop.x"), src)
            self.assertTrue(loop_hit(src, "tb.knowledge.x"), src)
        for src in ("from tb.knowledge import learn_cli", "from tb.knowledge import rulebook_cli as RC", "from tb.core import localcfg",
                    "def f():\n    from tb.knowledge.learn_cli import set_mode"):
            self.assertTrue(loop_hit(src, "tb.learnloop.x"), src)
            self.assertFalse(loop_hit(src, "tb.knowledge.x"), src)
        self.assertFalse(loop_hit("from tb.llm import usage\nfrom tb.llm.usage import read_rows\nfrom tb.knowledge import store2\nfrom tb.labels import store\n"
                                  "from tb.memory import lessons\nfrom tb.core import bus\nfrom tb.platform import services\nfrom . import conf", "tb.learnloop.x"))
        self.assertEqual(key_literals('"""Убрать BYBIT_* и *_API_KEY."""\nx = 1'), [])
        for src in ("p = os.path.join(home, '.config', 'traider-bot')", "k = 'BYBIT_API_SECRET'", "n = env['OPENAI_API_KEY']"):
            self.assertTrue(key_literals(src), src)
        mods = {"tb.learnloop.a": "from tb.mid import f", "tb.mid": "def g():\n    from tb.deep import h", "tb.deep": "import tb.control.client"}
        self.assertEqual(closure_hits(["tb.learnloop.a"], loop_hit, mods.get), {"tb.deep": ["tb.control.client"]})
        # записи в config/
        scan = ConfigWrites()
        head = "import os\nfrom tb import paths\nfrom tb.knowledge import markers as KM\nfrom tb.knowledge import rulebook_view as RV\n"
        bad = {
            "f": "def f(root):\n    p = paths.config('learnloop.json', root=root)\n    with open(p, 'w') as fh:\n        fh.write('x')\n",
            "g": "def cp(r):\n    return paths.config('risk_profile.json', root=r)\ndef g(r):\n    KM.write_json(cp(r), {})\n",
            "h": "def h(r):\n    os.replace('t', os.path.join(r, 'config', 'exec_limits.json'))\n",
            "k": "from tb.knowledge import learn_cli as X\ndef k(r):\n    X._write_atomic(X.cfg_path(r), 't')\n",
            "m": "from tb.core import localcfg\ndef m():\n    localcfg.update_local('x', lambda d: d)\n",
            "n": "def n(r):\n    for x in (1,):\n        _put(paths.config('local', 'risk_levels.json', root=r))\ndef _put(p):\n    os.makedirs(p)\n",
            "s": "class S(object):\n    def p(self, r):\n        return paths.config('memory.json', root=r)\n    def s(self, r):\n        open(self.p(r), mode='a').close()\n",
        }
        for name, body in bad.items():
            self.assertIn(name, scan.writers("tb.learnloop.t_" + name, head + body), name)
        good = {
            "w": "def w(r):\n    KM.write_json(RV.lease_path(r), {})\n",
            "rd": "def rd(r):\n    with open(paths.config('learnloop.json', root=r), encoding='utf-8') as f:\n        return f.read()\n",
            "rb": "def rb(r):\n    with open(paths.config('risk_profile.json', root=r), 'rb') as f:\n        return f.read()\n",
            "rt": "def rt(r):\n    KM.write_json(paths.runtime('learn', 'x.json', root=r), {'config': 1})\n",
            "doc": "def doc(r):\n    \"\"\"Пишет не в config/ (config/learnloop.json только читается).\"\"\"\n    open(paths.state('a', root=r), 'w').close()\n",
        }
        for name, body in good.items():
            self.assertEqual(scan.writers("tb.learnloop.t_" + name, head + body), [], name)
        only = ConfigWrites(target="learnloop.json")
        self.assertEqual(only.writers("tb.x.a", head + bad["f"]), ["f"])
        self.assertEqual(only.writers("tb.x.g", head + bad["g"]), [])                       # risk_profile — не learnloop.json
        self.assertEqual(only.writers("tb.x.c", "from tb import paths\nfrom tb.learnloop import conf as LC\nimport os\n"
                                      "def c(r):\n    os.replace('t', paths.config(LC.FILE, root=r))\n"), ["c"])   # имя файла через константу модуля

    # ---------------------------------------------------------------- R1.7: сводный рынок (задача 24; свойство 17)
    def test_cmkt_layer_is_isolated(self):
        """Requirement 3.2: tb.cmkt не импортирует торговые слои, исполнителя, шлюз, мозг, llm, управление, launcher, MCP, файлы ключей,
        метки, цикл обучения, знания и intel — ни прямо, ни транзитивно."""
        mods = [module_name(p) for p in py_files("cmkt")]
        self.assertIn("tb.cmkt.aggregate", mods)
        self.assertIn("tb.cmkt.ex.binance", mods)
        bad = {}
        for mod in mods:
            found = cmkt_import_violations(disk_source(mod), mod)
            if found:
                bad[mod] = found
        self.assertEqual(bad, {})
        self.assertEqual(closure_hits(mods, cmkt_import_violations, disk_source), {})

    def test_cmkt_has_no_keys_and_no_local_config(self):
        """Requirement 3.1: в tb.cmkt нет hmac, строк ключей и каталога ключей, чтения config/local/."""
        bad = {}
        for path in py_files("cmkt"):
            mod = module_name(path)
            issues = cmkt_secret_violations(read(path), mod)
            if issues:
                bad[mod] = issues
        self.assertEqual(bad, {})

    def test_cmkt_services_are_scrubbed(self):
        """Requirement 3.1: окружение процессов cmkt и сборщиков cmkt@* чистится как у labels."""
        from tb.platform import services
        self.assertIn("cmkt", services.SCRUBBED_SERVICES)
        self.assertTrue(services.scrubbed("cmkt"))
        self.assertTrue(services.scrubbed("cmkt@binancep"))
        self.assertTrue(services.scrubbed("cmkt@coinbases"))

    def test_logic_cmkt_is_pure(self):
        """Requirement 3.2: tb.logic.cmkt чист (без os, сети, часов, open) и из tb импортирует только tb.logic.* транзитивно."""
        self.assertTrue(os.path.isfile(os.path.join(SRC, "logic", "cmkt.py")))

        def hit(src, m):
            return logic_violations(src) + [("tb", i) for i in imports_of(src, m) if i.startswith("tb.") and not under(i, ("tb.logic",))]
        self.assertEqual(closure_hits(["tb.logic.cmkt"], hit, disk_source), {})

    def test_cmktdoc_does_not_import_cmkt(self):
        """Requirement 3.3: tb.core.cmktdoc не импортирует tb.cmkt ни прямо, ни транзитивно через tb.*; ряды открываются только на чтение."""
        mod = "tb.core.cmktdoc"
        src = read(os.path.join(SRC, "core", "cmktdoc.py"))
        self.assertFalse([n for n in imports_of(src, mod) if under(n, (CMKT,))])
        self.assertEqual(closure_hits([mod], lambda s, m: [n for n in imports_of(s, m) if under(n, (CMKT,))], disk_source), {})
        self.assertIn("mode=ro", src)
        self.assertIn("uri=True", src)

    def test_cmkt_consumers_read_only_through_cmktdoc(self):
        """Requirement 3.3: шлюз, мозг, метки, цикл обучения, исполнитель и дашборд не импортируют tb.cmkt (прямо и транзитивно):
        сводный рынок приходит только через tb.core.cmktdoc или файлы runtime/. Исключения CMKT_REACH_OK — реестр сервисов (имена
        cmkt@*) и сервис intel (выключение XStream): данных рынка в них нет."""
        mods = []
        for layer in CMKT_CONSUMERS:
            mods.extend(module_name(p) for p in py_files(layer))
        self.assertIn("tb.brain.cands", mods)
        self.assertIn("tb.gateway.catalog", mods)
        self.assertEqual(cmkt_reach_violations(mods), {})
        for mod in ("tb.brain.cands", "tb.brain.brief", "tb.gateway.catalog", "tb.labels.capture"):
            self.assertIn("tb.core.cmktdoc", imports_of(disk_source(mod), mod), mod)   # путь чтения — читатель, не сервис

    def test_gateway_has_no_venue_names(self):
        """Requirement 3.3: в tb.gateway нет строковых названий бирж (они приходят данными из cmktdoc). Список названий — из
        config/cmkt.json и MARKETS tb.logic.cmkt; исключений нет, `gateway`/`gateway_audit` названием биржи `gate` не считаются."""
        venues = cmkt_venue_names()
        self.assertGreaterEqual(len(venues), 6)
        bad = {}
        for path in py_files("gateway"):
            found = venue_literals(read(path), venues)
            if found:
                bad[module_name(path)] = found
        self.assertEqual(bad, {})

    # ---------------------------------------------------------------- R4: читатель политики выхода (задача 14, свойство 19)
    def test_exitdoc_layers(self):
        """Requirement 18.3: tb.core.exitdoc не импортирует tb.learnloop, tb.knowledge, tb.labels, tb.venues, tb.executor — прямо и
        транзитивно по внутренним импортам tb; модуль только читает runtime/ и config/exits.json (ни записи файла, ни функции-писателя)."""
        mod = "tb.core.exitdoc"

        src = read(os.path.join(SRC, "core", "exitdoc.py"))
        self.assertFalse(exitdoc_hit(src, mod))
        self.assertEqual(closure_hits([mod], exitdoc_hit, disk_source), {})
        self.assertEqual(learn_write_violations(src), [])
        self.assertEqual(config_writers([mod]), {})
        mods = {"tb.core.exitdoc": "import tb.knowledge"}
        self.assertEqual(closure_hits(["tb.core.exitdoc"], exitdoc_hit, mods.get), {"tb.core.exitdoc": ["tb.knowledge"]})

    def test_exits_logic_modules_are_pure(self):
        """Requirement 18.1, свойство 23: tb.logic.exitpol/exitsim/qty подчиняются чистоте tb.logic (без os, сети, часов, open) и
        импортируют из tb только tb.logic.* прямо и транзитивно по tb.logic."""
        for name in EXITS_LOGIC:
            path = os.path.join(SRC, "logic", name + ".py")
            self.assertTrue(os.path.isfile(path), name)
            self.assertEqual(logic_violations(read(path)), [], name)
        self.assertEqual(closure_hits(["tb.logic." + n for n in EXITS_LOGIC], exit_logic_hit, disk_source), {})
        for src in ("import os", "import time\nt = time.time()", "x = open('f')", "from tb import paths",
                    "from tb.core import journal", "def f():\n    from tb.venues import bybit"):
            self.assertTrue(exit_logic_hit(src, "tb.logic.exitsim"), src)
        self.assertFalse(exit_logic_hit("import math\nfrom tb.logic import exitpol\nfrom tb.logic import qty as Q", "tb.logic.exitsim"))
        mods = {"tb.logic.exitpol": "from tb.core import journal"}
        self.assertIn("tb:tb.core", closure_hits(["tb.logic.exitpol"], exit_logic_hit, mods.get).get("tb.logic.exitpol", []))

    def test_learnloop_exits_modules_pass_loop_rules(self):
        """Requirement 18.2, свойство 23: модули tb.learnloop.exits_* входят в loop_hit (мозг, шлюз, MCP, биржа, исполнитель,
        управление, launcher, ключи; tb.llm кроме usage) и в ConfigWrites (цикл не пишет в config/), прямо и транзитивно."""
        mods = sorted(module_name(p) for p in py_files("learnloop") if os.path.basename(p).startswith("exits_"))
        for known in EXITS_LOOP_MODULES:
            self.assertIn(known, mods)
        bad = {}
        for mod in mods:
            src = disk_source(mod)
            issues = loop_hit(src, mod) + ["lit:" + x[:30] for x in key_literals(src)]
            if issues:
                bad[mod] = issues
        self.assertEqual(bad, {})
        self.assertEqual(config_writers(mods), {})
        self.assertEqual(config_writers(mods, target="exits.json"), {})
        trans = closure_hits(mods, lambda s, m: [n for n in loop_hit(s, m) if not under(n, LOOP_CLI)], disk_source)
        self.assertEqual(trans, {})
        for src in ("import tb.brain.runner", "from tb.gateway import core", "from tb import venues", "from tb.executor import ports",
                    "from tb.control import client", "from tb import launcher", "import tb.mcp.server", "from tb.core import secretfile",
                    "def f():\n    from tb.brain.validate import v"):
            self.assertTrue(loop_hit(src, "tb.learnloop.exits_watch"), src)
            self.assertTrue(loop_hit(src, "tb.learnloop.exits_eval"), src)
        self.assertFalse(loop_hit("from tb.knowledge import store2\nfrom tb.labels import store\nfrom tb.logic import exitpol\n"
                                  "from tb.core import exec_cfg\nfrom tb.llm import usage", "tb.learnloop.exits_eval"))

    def test_exits_cycle_keeps_protected_config_hashes(self):
        """Requirement 18.5, свойство 23: полный проход шагов цикла выходов на фикстурной базе во временном корне не меняет
        config/risk_profile.json, config/risk_levels.json, config/exec_limits.json — хеши до и после совпадают."""
        from tb.knowledge import store2
        from tb.labels import store as LST
        from tb.learnloop import conf as LC
        from tb.learnloop import cycle as CY
        from tb.learnloop import exits_conf as EC
        from tb.learnloop import exits_versions as XV

        root = tempfile.mkdtemp(prefix="tblay_exits_")
        self.addCleanup(shutil.rmtree, root, True)
        os.makedirs(os.path.join(root, "config"))
        os.makedirs(os.path.join(root, "runtime"))
        cfg_dir = os.path.join(_bootstrap.ROOT, "config")
        for name in sorted(os.listdir(cfg_dir)):
            if name.endswith(".json") and os.path.isfile(os.path.join(cfg_dir, name)):
                shutil.copy(os.path.join(cfg_dir, name), os.path.join(root, "config", name))

        def hashes():
            out = {}
            for name in PROTECTED_CONFIG_FILES:
                with open(os.path.join(root, "config", name), "rb") as f:
                    out[name] = hashlib.sha256(f.read()).hexdigest()
            return out

        before = hashes()
        now = 1793750400                                             # 2026-11-04T00:00:00Z
        lcfg, lerrs = LC.load(root)
        self.assertEqual(lerrs, [])
        conn = LST.open_rw(root)
        self.addCleanup(conn.close)
        store2.migrate_x1(conn, root, backup=False, now=now)
        store2.migrate_v2a(conn, root, enabled=True, backup=False, now=now)
        self.assertEqual(EC.load(root)[1], [])
        self.assertEqual(XV.init(conn, EC.current_floor(root), now - 12 * 86400), "X-0001")
        steps = [s for s in CY.default_steps() if s.name.startswith("exits_")]
        self.assertEqual(sorted(s.name for s in steps), ["exits_after", "exits_eval", "exits_promote", "exits_register",
                                                         "exits_search", "exits_verdicts", "exits_watch"])
        cyc = CY.Cycle(root, lcfg, steps=steps)
        ctx = CY.Ctx(root, conn, lcfg, now, cycle=cyc)
        ran = dict((s.name, getattr(CY, "step_" + s.name)(ctx)) for s in steps)
        self.assertEqual(sorted(ran), sorted(s.name for s in steps))
        self.assertEqual([n for n, rep in ran.items() if "code" not in rep], [])
        self.assertEqual(hashes(), before)

    def test_exits_checkers_catch_violations(self):
        """Проверяльщики циклов выходов ловят искусственные нарушения (иначе тесты свойства 23 ничего не доказывают)."""
        for src in ("from tb import learnloop", "from tb.knowledge import store2", "from tb.labels import paths",
                    "from tb import venues", "from tb.executor import ports", "def f():\n    from tb.labels import store"):
            self.assertTrue(exitdoc_hit(src, "tb.core.exitdoc"), src)
        self.assertFalse(exitdoc_hit("from tb import paths\nfrom tb.logic import exitpol", "tb.core.exitdoc"))
        for src in ("import tb.brain.runner", "from tb import gateway", "from tb.core import secretfile"):
            self.assertTrue(exit_logic_hit(src, "tb.logic.exitpol"), src)
        self.assertFalse(exit_logic_hit("import math\nfrom tb.logic import exitpol", "tb.logic.exitpol"))
        for src in ("import tb.venues.bybit", "from tb.executor import ports", "from tb import launcher", "from tb.llm import keys"):
            self.assertTrue(loop_hit(src, "tb.learnloop.exits_search"), src)
        self.assertFalse(loop_hit("from tb.logic import exitsim\nfrom tb.labels import store", "tb.learnloop.exits_search"))
        head = "from tb import paths\nimport os\n"
        body = "def w(r):\n    p = paths.config('exits.json', root=r)\n    open(p, 'w').close()\n"
        self.assertEqual(ConfigWrites(target="exits.json").writers("tb.learnloop.t_w", head + body), ["w"])
        self.assertEqual(config_writers(["tb.learnloop.exits_search"], target="exits.json"), {})

    def test_layer_dirs_exist(self):
        for layer in ("brain", "logic", "core", "dash", "executor", "venues", "gateway", "mcp", "llm", "control", "labels", "learn",
                      "knowledge", "learnloop", "cmkt"):
            self.assertTrue(os.path.isfile(os.path.join(SRC, layer, "__init__.py")), layer)


if __name__ == "__main__":
    unittest.main()
