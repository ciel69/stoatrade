"""Блок `crowd` (brain/crowd.py): настроение толпы из чисел досье; только enum и числа, флаги по порогам, сторона давления."""
import unittest

import _bootstrap  # noqa: F401
from tb.brain import crowd as C


class CrowdTest(unittest.TestCase):
    def test_empty_is_none(self):
        self.assertIsNone(C.build())
        self.assertIsNone(C.build(st={"dpos": 0.5}, ev={"range_5m_pct": 1.0}))

    def test_uai_dump_with_trapped_longs(self):
        """UAI 2026-11-01: -36% за сутки, свеча -2.85% без оборота, лонг/шорт 2.83, OI +30% за сутки: паника, пойманные лонги, давление вниз, тонкий слив."""
        c = C.build(st={"r24h": -36.05, "r1h": -1.2, "a5_pct": 1.1}, ev={"ret_5m_pct": -2.846, "turn_surge_5m": 0.0, "funding_pct": 0.005},
                    der={"oi24_pct": 30.0, "ls": 2.83}, flow={"cvd_5m_pct": -5.0, "warm_s": 30})
        self.assertEqual(c["mood"], "panic")
        self.assertEqual(c["bias"], "short")
        for f in ("dump_24h", "impulse_down", "crowded_long", "oi_building"):
            self.assertIn(f, c["flags"])
        self.assertNotIn("flow_sell", c["flags"])                      # лента не прогрета: CVD не учитывается
        self.assertNotIn("cvd5", c)
        self.assertIn("long_against_panic", c["warn"])
        self.assertIn("thin_dump", c["warn"])
        self.assertAlmostEqual(c["imp_atr"], -2.6, places=1)

    def test_panic_with_crowded_shorts_and_flush_is_exhaustion(self):
        c = C.build(st={"r24h": -25.0, "a5_pct": 1.0}, ev={"ret_5m_pct": -2.5, "turn_surge_5m": 6.0}, der={"oi24_pct": -20.0, "ls": 0.4})
        self.assertEqual((c["mood"], c["bias"]), ("panic", "long"))
        self.assertIn("surge", c["flags"])
        self.assertNotIn("thin_dump", c["warn"])

    def test_euphoria_mirror_and_crowded_sides(self):
        e = C.build(st={"r24h": 30.0, "a5_pct": 1.0}, ev={"ret_5m_pct": 2.4}, der={"ls": 3.0, "oi24_pct": 20.0})
        self.assertEqual((e["mood"], e["bias"]), ("euphoria", "long"))
        self.assertIn("short_against_euphoria", e["warn"])
        s = C.build(st={"r24h": 1.0, "a5_pct": 0.5}, ev={"ret_5m_pct": 0.1}, der={"fund_pct": -0.08})
        self.assertEqual((s["mood"], s["bias"]), ("crowded_short", "none"))
        s2 = C.build(st={"r24h": 1.0, "a5_pct": 0.5}, ev={"ret_5m_pct": 1.2}, der={"fund_pct": -0.08})       # шорты переполнены и цена пошла вверх: топливо для сквиза
        self.assertEqual((s2["mood"], s2["bias"]), ("crowded_short", "long"))
        calm = C.build(st={"r24h": 1.0, "a5_pct": 0.5}, ev={"ret_5m_pct": 0.2})
        self.assertEqual((calm["mood"], calm["bias"]), ("calm", "none"))
        self.assertNotIn("warn", calm)

    def test_only_numbers_and_enums(self):
        c = C.build(st={"r24h": -36.0, "a5_pct": 1.1}, ev={"ret_5m_pct": -2.8, "liq_side": "long"}, fng=12)
        self.assertIn(c["mood"], C.MOODS)
        self.assertIn(c["bias"], C.BIASES)
        self.assertIn("fear", c["flags"])
        self.assertIn("liq_long", c["flags"])
        for k, v in c.items():
            self.assertTrue(isinstance(v, (int, float, str)) or (isinstance(v, list) and all(isinstance(x, str) for x in v)), k)


if __name__ == "__main__":
    unittest.main()
