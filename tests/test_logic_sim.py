"""R1.3: симулятор плана по минутным свечам tb.logic.sim (Requirement 7).

Условные обозначения сценариев: свеча R(i, o, h, l, c) = (60*i, o, h, l, c, 1.0); t0 = 0, задержка по умолчанию 60 с, поэтому рыночный вход
идёт по open свечи R1. Комиссии: taker 0.001, maker 0.0005; проскальзывание и спред в ручных сценариях нули, если не оговорено; tick 0.01,
лимит и тейк заполняются при проходе насквозь на 1 тик (>= уровень + 0.01 для тейка long). Ручные расчёты (формула и числа) записаны в тексте
тестов; эталон не берётся вызовом tb.logic.paper / tb.logic.rules.
"""
import unittest

import _bootstrap  # noqa: F401
from tb.logic import sim

COSTS = {"fee": 0.001, "maker_fee": 0.0005, "slip": 0.0, "stop_slip_bps": 0.0, "spread_bps": 0.0}
AP = {"enabled": True, "be_r": 0.7, "trail_activate_r": 1.0, "trail_lock_frac": 0.5, "min_profit_r": 0.2, "stop_slip_bps": 0}
MANAGE = {"latency_s": 60}
MANAGE_AP = {"latency_s": 60, "auto_protect": AP}


def R(i, o, h, l, c):
    return (60 * i, o, h, l, c, 1.0)


def plan(side="long", stop=99.0, tps=(), fracs=None, kind="market", level=100.0, ttl=30, hold=240):
    p = {"side": side, "entry": {"kind": kind, "level": level, "ttl_min": ttl}, "stop": stop, "tps": list(tps), "max_hold_min": hold, "tick": 0.01}
    if fracs is not None:
        p["fracs"] = list(fracs)
    return p


def go(p, rows, costs=None, manage=None, t0=0):
    return sim.run(p, rows, t0, costs or COSTS, MANAGE if manage is None else manage)


class SimExitReasons(unittest.TestCase):
    def assertNear(self, a, b, places=9):
        self.assertAlmostEqual(a, b, places=places)

    def test_sl(self):
        # long вход 100 (open R1), стоп 99: R = 1. R2 low 98.9 <= 99 -> стоп по 99 (без гэпа, проскальзывание 0).
        # r_gross = (99-100)/1 = -1; комиссии: 100*0.001 + 99*0.001 = 0.199 R; r_net = -1.199.
        # mae = (100-98.9)/1 = 1.1; mfe = только R1: (100.5-100)/1 = 0.5 (свеча со стопом в mfe не входит); hold = (120-60)/60 = 1 мин.
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 100.2, 98.9, 99)]
        r = go(plan(), rows)
        self.assertTrue(r["filled"])
        self.assertEqual((r["entry_ts"], r["exit_reason"], r["truncated"]), (60, "sl", False))
        self.assertNear(r["entry_px"], 100.0)
        self.assertNear(r["r_gross"], -1.0)
        self.assertNear(r["fees_r"], 0.199)
        self.assertNear(r["r_net"], -1.199)
        self.assertNear(r["mae_r"], 1.1)
        self.assertNear(r["mfe_r"], 0.5)
        self.assertNear(r["hold_min"], 1.0)
        self.assertEqual(r["parts"][0]["why"], "sl")

    def test_tp_full(self):
        # тейк 102 (frac 1): R2 high 102.01 >= 102 + 1 тик -> по 102 maker. r_gross = (102-100)/1 = 2;
        # комиссии: 100*0.001 + 102*0.0005 = 0.1 + 0.051 = 0.151; r_net = 1.849; mfe = (102.01-100)/1 = 2.01.
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 102.01, 99.5, 102)]
        r = go(plan(tps=[102.0]), rows)
        self.assertEqual(r["exit_reason"], "tp")
        self.assertNear(r["r_gross"], 2.0)
        self.assertNear(r["fees_r"], 0.151)
        self.assertNear(r["r_net"], 1.849)
        self.assertNear(r["mfe_r"], 2.01)

    def test_tp_touch_without_passing_does_not_fill(self):
        # high ровно 102.00 = тейк: касание без прохода на тик не заполняет (7.3); позиция живёт до конца данных
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 102.0, 99.5, 101)]
        r = go(plan(tps=[102.0]), rows)
        self.assertEqual((r["exit_reason"], r["truncated"]), ("timeout", True))

    def test_be(self):
        # R1 (вход 100, R=1) закрывается на 100.8: pnl 0.8R >= be_r 0.7 -> безубыток. Цель: 100*(1 + (2*0.001*1e4 + 2)/1e4) = 100.22;
        # пол прибыли: 100*(1 + 20/1e4) + 0.2*1 = 100.2 + 0.2 = 100.4 (издержки круга 20 бп + stop_slip 0 + min_profit_r 0.2R) -> стоп 100.4.
        # R2 low 100.3 <= 100.4: выход по 100.4. r_gross = 0.4; комиссии 0.1 + 100.4*0.001 = 0.2004; r_net = 0.1996.
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.9, 99.5, 100.8), R(2, 100.8, 101, 100.3, 100.5)]
        r = go(plan(), rows, manage=MANAGE_AP)
        self.assertEqual(r["exit_reason"], "be")
        self.assertNear(r["parts"][0]["px"], 100.4)
        self.assertNear(r["r_gross"], 0.4)
        self.assertNear(r["fees_r"], 0.2004)
        self.assertNear(r["r_net"], 0.1996)

    def test_trail(self):
        # R1: open 100, high 102, close 101.5 (pnl 1.5R). Сначала безубыток: стоп 100.4 (см. test_be). Затем mfe_lock:
        # mfe = (102-100)/1 = 2R >= 1.0; цель = 100 + 1*2*0.5 = 101.0; выше пола 100.4; не ближе 8 бп к цене (101.5*(1-0.0008) = 101.42) -> 101.0.
        # R2 low 100.9 <= 101.0 -> выход по 101.0 (open 101.5 выше стопа). r_gross = 1.0; комиссии 0.1 + 0.101 = 0.201; r_net = 0.799.
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 102, 99.8, 101.5), R(2, 101.5, 101.6, 100.9, 101)]
        r = go(plan(), rows, manage=MANAGE_AP)
        self.assertEqual(r["exit_reason"], "trail")
        self.assertNear(r["parts"][0]["px"], 101.0)
        self.assertNear(r["r_gross"], 1.0)
        self.assertNear(r["r_net"], 0.799)

    def test_timeout_with_slip_and_spread(self):
        # slip 0.0002, спред 2 бп (bid/ask = цена*(1 -/+ 0.0001)). Вход: ask = 100*1.0001, плюс slip: 100*1.0001*1.0002 = 100.030002.
        # max_hold 3 мин: конец R3 = 240 >= 60 + 180 -> закрытие по close R3 = 100.5: продажа по bid 100.5*0.9999 и slip: 100.5*0.9999*0.9998 = 100.46985201.
        # R = 100.030002 - 99 = 1.030002; r_gross = (100.46985201 - 100.030002)/1.030002; комиссии (100.030002 + 100.46985201)*0.001/R.
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.4, 99.6, 100.2), R(2, 100.2, 100.6, 99.9, 100.3), R(3, 100.3, 100.8, 100.0, 100.5)]
        c = dict(COSTS, slip=0.0002, spread_bps=2.0)
        r = go(plan(hold=3), rows, costs=c)
        e, x = 100 * 1.0001 * 1.0002, 100.5 * 0.9999 * 0.9998
        self.assertNear(r["entry_px"], 100.030002)
        self.assertNear(x, 100.46985201)
        self.assertEqual((r["exit_reason"], r["truncated"]), ("timeout", False))
        self.assertNear(r["r_gross"], (x - e) / (e - 99.0))
        self.assertNear(r["fees_r"], (e + x) * 0.001 / (e - 99.0))
        self.assertNear(r["hold_min"], 3.0)

    def test_truncated(self):
        # свечи кончились раньше max_hold: выход по close последней (R2 = 100.3), truncated = true
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.4, 99.6, 100.2), R(2, 100.2, 100.6, 99.9, 100.3)]
        r = go(plan(hold=240), rows)
        self.assertEqual((r["exit_reason"], r["truncated"]), ("timeout", True))
        self.assertNear(r["parts"][0]["px"], 100.3)
        self.assertNear(r["r_gross"], 0.3)

    def test_ttl(self):
        # лимит 98 ниже рынка, ttl 5 мин (срок 300 с), свечи покрывают срок, цена до 98 не доходит -> ttl, не заполнено
        rows = [R(i, 100, 100.5, 99.0, 100) for i in range(8)]
        r = go(plan(kind="limit", level=98.0, ttl=5, stop=97.0), rows)
        self.assertEqual((r["filled"], r["exit_reason"], r["entry_ts"], r["truncated"]), (False, "ttl", None, False))
        self.assertEqual(r["parts"], [])

    def test_no_fill_without_data(self):
        self.assertEqual(go(plan(), [])["exit_reason"], "no_fill")
        # все свечи раньше t0 + latency
        r = go(plan(), [R(0, 100, 100, 100, 100)])
        self.assertEqual((r["filled"], r["exit_reason"], r["truncated"]), (False, "no_fill", True))
        # лимит, данные кончились раньше срока ttl
        r = go(plan(kind="limit", level=98.0, ttl=30, stop=97.0), [R(i, 100, 100.5, 99.0, 100) for i in range(5)])
        self.assertEqual((r["exit_reason"], r["truncated"]), ("no_fill", True))


class SimOrderingAndGaps(unittest.TestCase):
    def assertNear(self, a, b, places=9):
        self.assertAlmostEqual(a, b, places=places)

    def test_stop_and_tp_same_candle_stop_wins(self):
        # 7.4(а): R2 задела и стоп 99 (low 98.5), и тейк 102 (high 102.5) -> стоп. r_net = -1 - 0.199 = -1.199
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100, 102.5, 98.5, 100)]
        r = go(plan(tps=[102.0]), rows)
        self.assertEqual(r["exit_reason"], "sl")
        self.assertNear(r["r_net"], -1.199)

    def test_limit_fill_and_stop_same_candle(self):
        # 7.4(б): лимит 100 (maker 0.0005), стоп 99. R1: open 101, low 98.8 -> заполнение по 100, затем стоп по 99 в той же свече.
        # r_gross = -1; комиссии 100*0.0005 + 99*0.001 = 0.05 + 0.099 = 0.149; r_net = -1.149. mfe = 0 (в свече заполнения берётся close 99.5 < входа),
        # mae = (100-98.8)/1 = 1.2.
        rows = [R(0, 101, 101, 101, 101), R(1, 101, 101, 98.8, 99.5)]
        r = go(plan(kind="limit", level=100.0, stop=99.0), rows)
        self.assertTrue(r["filled"])
        self.assertEqual((r["entry_ts"], r["exit_reason"]), (60, "sl"))
        self.assertNear(r["entry_px"], 100.0)
        self.assertNear(r["r_net"], -1.149)
        self.assertNear(r["mae_r"], 1.2)
        self.assertNear(r["mfe_r"], 0.0)

    def test_tp_and_be_same_candle_be_from_next_candle(self):
        # 7.4(в): тейки 101 и 104 по 0.5. R1 (open 100, high 101.05, low 99.5, close 100.8): первый тейк 0.5 по 101 (maker), pnl на close 0.8R >= 0.7
        # -> безубыток 100.4 (см. test_be) со СЛЕДУЮЩЕЙ свечи. R2 (low 100.3): остаток 0.5 по 100.4, причина be.
        # r_gross = 0.5*(101-100) + 0.5*(100.4-100) = 0.5 + 0.2 = 0.7; комиссии 0.1 + 0.5*101*0.0005 + 0.5*100.4*0.001 = 0.1 + 0.02525 + 0.0502 = 0.17545
        # r_net = 0.52455; mfe = 1.05, mae = 0.5 (R1 low 99.5)
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 101.05, 99.5, 100.8), R(2, 100.8, 101, 100.3, 100.5)]
        r = go(plan(tps=[101.0, 104.0], fracs=[0.5, 0.5]), rows, manage=MANAGE_AP)
        self.assertEqual(r["exit_reason"], "be")
        self.assertEqual([(p["ts"], p["why"]) for p in r["parts"]], [(60, "tp1"), (120, "be")])
        self.assertNear(r["parts"][0]["px"], 101.0)
        self.assertNear(r["parts"][1]["px"], 100.4)
        self.assertNear(r["r_gross"], 0.7)
        self.assertNear(r["fees_r"], 0.17545)
        self.assertNear(r["r_net"], 0.52455)
        self.assertNear(r["mfe_r"], 1.05)
        self.assertNear(r["mae_r"], 0.5)

    def test_be_not_active_in_same_candle(self):
        # R1 закрывается на 100.8 (BE запланирован), но low R1 100.0 ниже будущего стопа 100.4: в R1 действует прежний стоп 99 -> позиция жива,
        # стоп сработал бы только со следующей свечи
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.9, 100.0, 100.8), R(2, 100.8, 101, 100.9, 100.95)]
        r = go(plan(), rows, manage=MANAGE_AP)
        self.assertEqual((r["exit_reason"], r["truncated"]), ("timeout", True))

    def test_confirm_candle_not_checked_for_stop(self):
        # 7.4(г): candle_confirm long, уровень 100, стоп 98.5. Подтверждающая 5м свеча [0,300) закрывается на 100.6 > 100, но внутри неё low 98.0 < стопа:
        # стоп не проверяется. Вход по open первой минуты после подтверждения (ts 300) = 100.7. Дальше R5 low 100.5 стоп не задевает.
        rows = [R(0, 100, 100.2, 99.9, 100.1), R(1, 100.1, 100.3, 99.8, 100.2), R(2, 100.2, 100.3, 98.0, 100.2), R(3, 100.2, 100.5, 100.0, 100.4),
                R(4, 100.4, 100.7, 100.3, 100.6), R(5, 100.7, 101.0, 100.5, 100.9)]
        r = go(plan(kind="candle_confirm", level=100.0, stop=98.5), rows)
        self.assertTrue(r["filled"])
        self.assertEqual(r["entry_ts"], 300)
        self.assertNear(r["entry_px"], 100.7)
        self.assertEqual((r["exit_reason"], r["truncated"]), ("timeout", True))
        self.assertNear(r["mae_r"], (100.7 - 100.5) / (100.7 - 98.5))

    def test_confirm_candle_close_must_pass_level(self):
        rows = [R(i, 99.5, 99.6, 99.4, 99.5) for i in range(31)]
        r = go(plan(kind="candle_confirm", level=100.0, stop=98.5, ttl=30), rows)
        self.assertEqual((r["filled"], r["exit_reason"]), (False, "ttl"))

    def test_candle_confirm_limit(self):
        # после подтверждения (конец 300) лимит на уровне 100: R5 low 99.9 <= 100 - 0.01 -> заполнение по 100 (maker), ts 300
        rows = [R(i, 100.2, 100.5, 100.1, 100.4 + (0.2 if i == 4 else 0)) for i in range(5)]
        rows.append(R(5, 100.5, 100.8, 99.9, 100.2))
        r = go(plan(kind="candle_confirm_limit", level=100.0, stop=98.5), rows)
        self.assertTrue(r["filled"])
        self.assertEqual(r["entry_ts"], 300)
        self.assertNear(r["entry_px"], 100.0)
        # R = 100 - 98.5 = 1.5; выход по close R5 100.2 (конец данных): комиссии (100*0.0005 + 100.2*0.001)/1.5
        self.assertEqual((r["exit_reason"], r["truncated"]), ("timeout", True))
        self.assertNear(r["fees_r"], (100.0 * 0.0005 + 100.2 * 0.001) / 1.5)

    def test_limit_touch_without_pass_through_does_not_fill(self):
        rows = [R(i, 100.5, 101.0, 100.0, 100.5) for i in range(8)]     # low ровно 100.00 = уровень лимита: касание без прохода
        r = go(plan(kind="limit", level=100.0, stop=98.0, ttl=5), rows)
        self.assertEqual((r["filled"], r["exit_reason"]), (False, "ttl"))

    def test_gap_through_stop_long(self):
        # гэп: R2 открывается 97.5 ниже стопа 99. Цена = open 97.5 (спред 0), плюс stop_slip 10 бп: 97.5*(1-0.001) = 97.4025.
        # r_gross = (97.4025-100)/1 = -2.5975; комиссии 0.1 + 97.4025*0.001 = 0.1974025; r_net = -2.7949025
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 97.5, 97.8, 97.0, 97.6)]
        r = go(plan(), rows, costs=dict(COSTS, stop_slip_bps=10.0))
        self.assertEqual(r["exit_reason"], "sl")
        self.assertNear(r["parts"][0]["px"], 97.4025)
        self.assertNear(r["r_gross"], -2.5975)
        self.assertNear(r["r_net"], -2.7949025)

    def test_gap_through_stop_short(self):
        # short вход 100 (bid), стоп 101; R2 открывается 102.5: исполнение по 102.5*(1+0.001) = 102.6025; r_gross = -2.6025;
        # комиссии 0.1 + 102.6025*0.001 = 0.2026025; r_net = -2.8051025
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 102.5, 103.0, 102.2, 102.8)]
        r = go(plan(side="short", stop=101.0), rows, costs=dict(COSTS, stop_slip_bps=10.0))
        self.assertEqual(r["exit_reason"], "sl")
        self.assertNear(r["r_gross"], -2.6025)
        self.assertNear(r["r_net"], -2.8051025)

    def test_gap_through_tp_fills_at_tp_price(self):
        # R2 открывается 103.5 выше тейка 102: тейк по цене тейка 102, а не по open
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 103.5, 104.0, 103.4, 103.8)]
        r = go(plan(tps=[102.0]), rows)
        self.assertEqual(r["exit_reason"], "tp")
        self.assertNear(r["parts"][0]["px"], 102.0)
        self.assertNear(r["r_net"], 1.849)

    def test_short_tp(self):
        # short 100 (taker 0.1), тейк 98: R2 low 97.98 <= 98 - 0.01 -> по 98 maker. r_gross = 2; комиссии 0.1 + 98*0.0005 = 0.149; r_net = 1.851
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 98, 98.1, 97.98, 98)]
        r = go(plan(side="short", stop=101.0, tps=[98.0]), rows)
        self.assertEqual(r["exit_reason"], "tp")
        self.assertNear(r["r_net"], 1.851)

    def test_two_tps_in_one_candle(self):
        # тейки 101 и 102 по 0.5 в одной свече (high 102.02): r_gross = 0.5*1 + 0.5*2 = 1.5; комиссии 0.1 + 0.5*101*0.0005 + 0.5*102*0.0005 = 0.15075
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100), R(2, 100.5, 102.02, 100.4, 102)]
        r = go(plan(tps=[101.0, 102.0], fracs=[0.5, 0.5]), rows)
        self.assertEqual((r["exit_reason"], len(r["parts"])), ("tp", 2))
        self.assertNear(r["r_gross"], 1.5)
        self.assertNear(r["r_net"], 1.5 - 0.15075)

    def test_latency(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 101, 101, 101, 101), R(2, 102, 102.5, 101.9, 102.2), R(3, 102.2, 102.5, 101.9, 102.2)]
        r = go(plan(stop=99.0), rows, manage={"latency_s": 120})
        self.assertEqual(r["entry_ts"], 120)
        self.assertNear(r["entry_px"], 102.0)


class SimGeneral(unittest.TestCase):
    def test_deterministic_and_inputs_untouched(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 102, 99.8, 101.5), R(2, 101.5, 101.6, 100.9, 101)]
        before = [tuple(x) for x in rows]
        p = plan(tps=[105.0])
        pc = {k: (list(v) if isinstance(v, list) else v) for k, v in p.items()}
        a = go(p, rows, manage=MANAGE_AP)
        b = go(p, [list(x) for x in rows], manage=MANAGE_AP)
        self.assertEqual(a, b)
        self.assertEqual(rows, before)
        self.assertEqual(p, pc)

    def test_auto_protect_flat_equals_nested_and_disabled(self):
        rows = [R(0, 100, 100, 100, 100), R(1, 100, 100.9, 99.5, 100.8), R(2, 100.8, 101, 100.3, 100.5)]
        nested = go(plan(), rows, manage=MANAGE_AP)
        flat = go(plan(), rows, manage=dict(AP, latency_s=60))
        self.assertEqual(nested, flat)
        off = go(plan(), rows, manage={"latency_s": 60, "auto_protect": dict(AP, enabled=False)})
        self.assertEqual((off["exit_reason"], off["truncated"]), ("timeout", True))
        self.assertEqual(go(plan(), rows)["exit_reason"], "timeout")

    def test_bad_stop(self):
        r = go(plan(stop=101.0), [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100)])
        self.assertEqual((r["filled"], r["exit_reason"], r.get("error")), (False, "no_fill", "BAD_STOP"))

    def test_result_fields(self):
        r = go(plan(), [R(0, 100, 100, 100, 100), R(1, 100, 100.5, 99.5, 100)])
        for k in ("filled", "entry_ts", "entry_px", "exit_reason", "parts", "r_gross", "r_net", "fees_r", "mfe_r", "mae_r", "hold_min", "truncated"):
            self.assertIn(k, r)
        self.assertIn(r["exit_reason"], ("sl", "tp", "be", "trail", "timeout", "ttl", "no_fill"))


if __name__ == "__main__":
    unittest.main()
