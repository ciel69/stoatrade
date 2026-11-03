"""Кандидаты на вход для entry_scout (блок досье `cands`): код заранее считает уровень, стоп, цели, расстояния, RR после издержек и горизонт,
чтобы модель выбирала сетап, а не считала. Тонкая обёртка над tb.logic.geometry (R1.2): собирает пул уровней и ctx из досье, читает config/geometry.json,
вызывает геометрию и добавляет в каждую строку geometry_ver. Алгоритм, константы и формат строк прежние (см. tb.logic.geometry).
"""
import json

from tb import paths
from tb.brain import validate as V
from tb.logic import geometry as G

CLUSTER_PCT = G.DEFAULT_CLUSTER_PCT
chase = G.chase


def load_config(root=None):
    """-> (config/geometry.json как словарь, geometry_ver). Нет файла или он битый -> умолчания геометрии (равны файлу поставки)."""
    try:
        with open(paths.config("geometry.json", root=root), encoding="utf-8") as f:
            cfg = json.load(f)
        if isinstance(cfg, dict):
            return cfg, str(cfg.get("geometry_ver") or G.GEOMETRY_VER)
    except (OSError, ValueError):
        pass
    return {}, G.GEOMETRY_VER


def pool(brief, sym):
    """-> отсортированный список цен уровней монеты (validate.level_pool: struct, levels, идеи) без дублей ближе cluster_pct."""
    return G.pool(V.level_pool(brief, sym), load_config()[0].get("cluster_pct", G.DEFAULT_CLUSTER_PCT))


def _horizon(ctx, stop_pct, rr, wide_pct=None, tp1_pct=None):
    return G._horizon(ctx, stop_pct, rr, wide_pct, tp1_pct, G._profiles(load_config()[0])[0])


def candidates(brief, sym, ctx=None, root=None):
    """-> [кандидат] (ok сначала, по убыванию rr_net) или None. ctx: validate.entry_context(brief, {"sym": sym}) (считается сам, если не передан)."""
    if not isinstance(brief, dict) or not sym:
        return None
    ctx = ctx or V.entry_context(brief, {"sym": sym})
    cfg, ver = load_config(root)
    out = G.candidates(V.level_pool(brief, sym), ctx, cfg)
    for r in out or []:
        r["geometry_ver"] = ver
    return out
