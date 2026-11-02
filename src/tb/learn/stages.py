"""Заглушки этапов «Уроки» и «Версии мозга до/после» (R0.2, задача 11). Без чтения файлов: источники (lessons.jsonl, swaps.jsonl,
версии правил) появятся в R2.6."""

STAGE = 2
ROADMAP = "R2.6"
NOTE = "появится на этапе 2"
TITLES = {"lessons": "Уроки", "versions": "Версии мозга до/после"}


def _stub(bid):
    return {"id": bid, "title": TITLES[bid], "status": "stage", "note": NOTE, "reason": None, "stage": STAGE, "roadmap": ROADMAP}


def lessons(ctx=None):
    return _stub("lessons")


def versions(ctx=None):
    return _stub("versions")


def build(ctx=None):
    """-> {"lessons": блок, "versions": блок}."""
    return {"lessons": lessons(ctx), "versions": versions(ctx)}
