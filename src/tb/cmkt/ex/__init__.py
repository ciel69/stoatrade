"""Адаптеры бирж сводного рынка: модуль tb.cmkt.ex.<venue> с классом ADAPTER (наследник tb.cmkt.adapter.VenueAdapter).

Новая биржа — новый модуль здесь и запись `venues.<id>` в config/cmkt.json; сводка, логика и хранение не меняются (Requirement 2.2).
"""
import importlib

from tb.cmkt import adapter as A


def load(venue, cfg=None, meta=None):
    """Экземпляр адаптера биржи по имени модуля; неизвестное или недопустимое имя -> ValueError."""
    if not A.valid_venue(venue):
        raise ValueError("venue: %r" % (venue,))
    try:
        mod = importlib.import_module("tb.cmkt.ex." + venue)
    except ImportError:
        raise ValueError("venue: нет адаптера %s" % venue)
    cls = getattr(mod, "ADAPTER", None)
    if not isinstance(cls, type) or not issubclass(cls, A.VenueAdapter) or cls.venue != venue:
        raise ValueError("venue: модуль %s без ADAPTER" % venue)
    return cls(cfg=cfg, meta=meta)
