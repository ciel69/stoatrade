"""Загрузка config/memory.json (шкалы, веса, пороги). Нет файла в корне -> берётся файл проекта по умолчанию; битый файл -> ValueError."""
import hashlib
import json
import os
import re

from tb import paths
from tb.core.filelock import atomic_write


def load(root=None):
    for r in (root, None) if root else (None,):
        p = paths.config("memory.json", root=r)
        if os.path.isfile(p):
            break
    else:
        p = paths.config("memory.json", root=paths.DEFAULT_ROOT)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except OSError:
        with open(paths.config("memory.json", root=paths.DEFAULT_ROOT), encoding="utf-8") as f:
            d = json.load(f)
    except ValueError as e:
        raise ValueError("config/memory.json: %s" % e)
    if not isinstance(d, dict) or d.get("schema") != "tb.memory_cfg/1":
        raise ValueError("config/memory.json: ожидается schema tb.memory_cfg/1")
    return d


STORES = ("jsonl", "sqlite")
_RE_STORE = re.compile(r'("store"\s*:\s*)"(?:jsonl|sqlite)"')


def set_store(root, value):
    """Переключение хранилища случаев и уроков (R2.9b, Requirement 3.3, 3.5): store в memory.json корня (нет файла в корне -> копия файла
    проекта с новым значением). Вызывают только `./tb knowledge migrate` и `export-jsonl` под cases.lock и lessons.lock. Правка точечная
    (оформление файла сохраняется); без ключа - файл переписывается целиком. -> путь файла."""
    if value not in STORES:
        raise ValueError("store: %r не из %s" % (value, list(STORES)))
    p = paths.config("memory.json", root=root)
    src = p if os.path.isfile(p) else paths.config("memory.json", root=paths.DEFAULT_ROOT)
    with open(src, encoding="utf-8") as f:
        text = f.read()
    new, n = _RE_STORE.subn(lambda m: '%s"%s"' % (m.group(1), value), text, count=1)
    try:
        ok = n == 1 and json.loads(new).get("store") == value
    except ValueError:
        ok = False
    if not ok:
        d = json.loads(text)
        d["store"] = value
        new = json.dumps(d, ensure_ascii=False, indent=2) + "\n"
    atomic_write(p, new)
    return p


def cfg_hash(cfg):
    """Хеш настроек (для предрегистрации A/B: параметры фиксируются до старта)."""
    return hashlib.sha256(json.dumps(cfg, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
