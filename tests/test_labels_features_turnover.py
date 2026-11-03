"""R2 задача 32 (Requirement 7.10): coin_class меток берётся по обороту из ev["turn_24h_musd"] (млн USD) так же, как classify_coin."""
import unittest

import _bootstrap  # noqa: F401
from tb.labels import features
from tb.memory import features as memfeat

T0 = 1793577600 + 12 * 3600
CFG = {"coin_class": {"major": ["BTC", "ETH"], "meme": ["DOGE"], "alt_mid": ["SOL"], "turnover_mid_usd": 2e8, "turnover_small_usd": 2e7}}


def cls(sym, ev):
    return features.compute([], [], [], [], T0, sym, ev, None, mem_cfg=CFG)[0]["coin_class"]


class Turnover(unittest.TestCase):
    def test_thresholds(self):
        for musd, want in ((500.0, "alt_mid"), (200.0, "alt_mid"), (199.9, "alt_small"), (20.0, "alt_small"), (19.9, "meme"), (0.0, "meme")):
            with self.subTest(musd=musd):
                got = cls("XYZUSDT", {"turn_24h_musd": musd})
                self.assertEqual(got, want)
                self.assertEqual(got, memfeat.classify_coin("XYZUSDT", CFG, musd * 1e6))

    def test_string_number(self):
        self.assertEqual(cls("XYZUSDT", {"turn_24h_musd": "35.5"}), "alt_small")

    def test_reference_coin_ignores_turnover(self):
        for sym, want in (("BTCUSDT", "major"), ("DOGEUSDT", "meme"), ("SOLUSDT", "alt_mid")):
            for ev in ({}, {"turn_24h_musd": 1.0}, {"turn_24h_musd": 9999.0}):
                with self.subTest(sym=sym, ev=ev):
                    self.assertEqual(cls(sym, ev), want)

    def test_missing_field(self):
        for ev in ({}, None, {"turn_24h_musd": None}, {"turn_24h_musd": "n/a"}, {"turn_24h_musd": float("nan")}, "bad"):
            with self.subTest(ev=ev):
                self.assertEqual(cls("XYZUSDT", ev), "unknown")

    def test_old_key_not_used(self):
        self.assertEqual(cls("XYZUSDT", {"turnover_usd": 5e8}), "unknown")


if __name__ == "__main__":
    unittest.main()
