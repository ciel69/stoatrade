"""Что считать «стопом» для серий стопов и пауз после стопа (guard, защиты исполнителя, досье мозга, дашборд). Чистый модуль без зависимостей.

Закрытие по стопу (reason == "sl") с результатом в плюс не убыток: стоп подтянули в прибыль (безубыток, трейлинг) и цена его забрала
(JCT 2026-10-31: закрыта по стопу с +0.698 USDT, а счётчик «стопы подряд» показал 1/3). Такая сделка серию стопов рвёт, как тейк.
Нет результата (pnl_usdt пуст) -> считается убыточным стопом (осторожно: пауза лишней не бывает).
"""


def loss_stop(t):
    """True, если сделка закрыта по стопу без прибыли.

    >>> loss_stop({"reason": "sl", "pnl_usdt": -0.4}), loss_stop({"reason": "sl", "pnl_usdt": 0.698}), loss_stop({"reason": "tp", "pnl_usdt": -0.1})
    (True, False, False)
    >>> loss_stop({"reason": "sl"})
    True
    """
    if not isinstance(t, dict) or t.get("reason") != "sl":
        return False
    pnl = t.get("pnl_usdt")
    return not (isinstance(pnl, (int, float)) and not isinstance(pnl, bool) and pnl > 0)
