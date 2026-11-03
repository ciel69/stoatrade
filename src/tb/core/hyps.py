"""Словарь гипотез (R2, Requirement 19.1): закрытый список семейств тега `hyp` и нормализация написаний.

Чистый модуль: читает только config/hypotheses.json (корень проекта или TB_ROOT; словарь общий, а не по временному корню данных), без сети, без
записи и без часов. Нормализация `family(raw)`: нижний регистр и `_` -> `-`; точное совпадение с семейством; иначе таблица `aliases` (ключи хранятся
нормализованными); иначе самое длинное семейство, за которым в теге идёт `-` (`range-fade-lower-edge` -> `range-fade`); иначе `other` с исходным
значением. `family(None)` и пустая строка -> (None, None): сделка без тега остаётся без семейства. Журнал сделок не переписывается.
"""
import json
import os

from tb import paths

OTHER = "other"
FILE = "hypotheses.json"

_CACHE = {}


def _key(s):
    return str(s).strip().lower().replace("_", "-")


def _load():
    """-> (families tuple, aliases dict, policy_map dict) по config/hypotheses.json; кэш по пути и mtime. Нет файла или мусор -> только `other`."""
    p = paths.config(FILE)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        mt = None
    hit = _CACHE.get(p)
    if hit and hit[0] == mt:
        return hit[1]
    fams, aliases, pmap = [], {}, {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    if isinstance(d, dict):
        for x in d.get("families") or []:
            if isinstance(x, str) and x and _key(x) not in fams:
                fams.append(_key(x))
        if OTHER not in fams:
            fams.append(OTHER)
        al = d.get("aliases")
        if isinstance(al, dict):
            aliases = dict((_key(k), _key(v)) for k, v in al.items() if isinstance(k, str) and isinstance(v, str) and _key(v) in fams)
        pm = d.get("policy_map")
        if isinstance(pm, dict):
            pmap = dict((_key(k), v) for k, v in pm.items() if isinstance(v, dict) and _key(k) in fams)
    else:
        fams = [OTHER]
    out = (tuple(fams), aliases, pmap)
    _CACHE[p] = (mt, out)
    return out


def families():
    """-> кортеж семейств (HYP_FAMILIES), `other` последним или где задан в файле."""
    return _load()[0]


def aliases():
    return dict(_load()[1])


def policy_map():
    """-> {семейство: {"policy", "kinds"}} (справка меток, задача 36)."""
    return dict(_load()[2])


def family(raw):
    """Тег гипотезы -> (семейство, исходник или None). Исходник возвращается только когда тег неизвестен и стал `other`."""
    if raw is None:
        return None, None
    s = _key(raw)
    if not s:
        return None, None
    fams, al, _ = _load()
    if s in fams:
        return s, None
    if s in al:
        return al[s], None
    best = None
    for f in fams:
        if f != OTHER and s.startswith(f + "-") and (best is None or len(f) > len(best)):
            best = f
    if best:
        return best, None
    return OTHER, str(raw)


HYP_FAMILIES = families()
