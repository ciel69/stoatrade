"""tb.core.hyps и config/hypotheses.json (R2, Requirement 19.1, свойство 30): закрытый словарь семейств и нормализация тега гипотезы."""
import json
import os
import random
import string
import unittest

import _bootstrap  # noqa: F401
from tb import paths
from tb.brain import validate
from tb.core import hyps

ROOT = paths.DEFAULT_ROOT


def jread(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return json.load(f)


# значения hyp из state/trades.jsonl на 2026-11-03 (20 написаний на ~70 сделках) -> ожидаемое семейство
HISTORY_2026_11_03 = {
    "breakout-retest": "breakout-retest", "breakout_retest": "breakout-retest", "patrol-breakout": "breakout-retest", "patrol-retest": "breakout-retest",
    "fade-s5m-res-range": "range-fade", "range-fade": "range-fade", "range-fade-lower-edge": "range-fade", "range-fade-s5m-res": "range-fade",
    "range-fade-sup-s5m": "range-fade", "range-top-reject-lvl": "range-fade", "range_fade": "range-fade",
    "funding-squeeze-reclaim": "funding-crowded-reversal", "funding_extreme_short": "funding-crowded-reversal", "reclaim-funding": "funding-crowded-reversal",
    "level_reclaim": "reclaim-level", "vol-reclaim": "reclaim-level", "level_reject": "level-reject", "sweep_reject": "sweep-reject",
    "trend-pullback": "trend-pullback", "trend_pullback": "trend-pullback",
}


class TestDictionary(unittest.TestCase):
    def test_families_are_cards_plus_setups_without_card_plus_other(self):
        cards = {c["hyp"] for c in jread("config", "playbook.json")["cards"]}
        self.assertEqual(len(cards), 14)
        setup_fams = {hyps.family(s)[0] for s in validate.SETUPS}
        self.assertEqual(setup_fams - cards, {"level-reject", "momentum-chase", "other"})
        self.assertEqual(set(hyps.HYP_FAMILIES), cards | setup_fams | {"other"})
        self.assertEqual(len(hyps.HYP_FAMILIES), len(set(hyps.HYP_FAMILIES)))
        self.assertEqual(list(hyps.HYP_FAMILIES), jread("config", "hypotheses.json")["families"])

    def test_aliases_are_normalized_and_point_to_families(self):
        raw = jread("config", "hypotheses.json")["aliases"]
        for k, v in raw.items():
            self.assertEqual(k, k.lower().replace("_", "-"), k)
            self.assertNotIn(k, hyps.HYP_FAMILIES, k)                       # точное совпадение с семейством проверяется раньше: такой алиас мёртв
            self.assertIn(v, hyps.HYP_FAMILIES, k)

    def test_setup_names_from_requirement(self):
        for raw, fam in (("level_reclaim", "reclaim-level"), ("sweep_reject", "sweep-reject"), ("range_fade", "range-fade"), ("breakout_retest", "breakout-retest"),
                         ("trend_pullback", "trend-pullback"), ("level_reject", "level-reject"), ("momentum_chase", "momentum-chase"), ("none", "other")):
            self.assertEqual(hyps.family(raw), (fam, None), raw)

    def test_policy_map(self):
        pm = jread("config", "hypotheses.json")["policy_map"]
        self.assertEqual(pm, {"liq-cascade-reclaim": {"policy": "P4", "kinds": ["liq_cascade"]}, "oi-flush-reclaim": {"policy": "P4", "kinds": ["oi_flush"]},
                              "oi-build-squeeze": {"policy": "P2", "kinds": ["oi_spike"]}})
        pol = {p["id"]: p for p in jread("config", "shadow_policies.json")["policies"]}
        for fam, m in pm.items():
            self.assertIn(fam, hyps.HYP_FAMILIES)
            self.assertTrue(set(m["kinds"]) <= set(pol[m["policy"]]["kinds"]), fam)
        self.assertEqual(hyps.policy_map(), pm)

    def test_module_is_pure(self):
        with open(hyps.__file__, encoding="utf-8") as f:
            src = f.read()
        imports = [ln.strip() for ln in src.splitlines() if ln.startswith(("import ", "from "))]
        self.assertEqual(imports, ["import json", "import os", "from tb import paths"])
        self.assertNotIn("open(p, \"w", src)


class TestFamily(unittest.TestCase):
    def test_none_and_empty_have_no_family(self):
        for raw in (None, "", "   "):
            self.assertEqual(hyps.family(raw), (None, None))

    def test_exact_case_and_underscore(self):
        self.assertEqual(hyps.family("range-fade"), ("range-fade", None))
        self.assertEqual(hyps.family("Range_Fade"), ("range-fade", None))
        self.assertEqual(hyps.family("OI_BUILD_SQUEEZE"), ("oi-build-squeeze", None))

    def test_longest_prefix_family(self):
        self.assertEqual(hyps.family("range-fade-lower-edge"), ("range-fade", None))
        self.assertEqual(hyps.family("liq-cascade-reclaim-v2"), ("liq-cascade-reclaim", None))
        self.assertEqual(hyps.family("Trend_Pullback_1h"), ("trend-pullback", None))
        self.assertEqual(hyps.family("range-fader")[0], "other")          # префикс без '-' не считается

    def test_unknown_is_other_with_raw(self):
        self.assertEqual(hyps.family("probe-x"), ("other", "probe-x"))
        self.assertEqual(hyps.family("H-New"), ("other", "H-New"))

    def test_history_2026_11_03_reduces_to_families(self):
        for raw, fam in HISTORY_2026_11_03.items():
            self.assertEqual(hyps.family(raw), (fam, None), raw)
        self.assertEqual(len(HISTORY_2026_11_03), 20)
        self.assertEqual(len({hyps.family(r)[0] for r in HISTORY_2026_11_03}), 7)

    def test_property_any_tag_maps_into_families(self):
        rnd = random.Random(30)
        alphabet = string.ascii_letters + string.digits + "._-"
        fams = [f for f in hyps.HYP_FAMILIES if f != "other"]
        for _ in range(2000):
            raw = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 40)))
            fam, src = hyps.family(raw)
            self.assertIn(fam, hyps.HYP_FAMILIES, raw)
            self.assertEqual(src is not None, fam == "other" and raw.strip().lower().replace("_", "-") != "none", raw)
        for _ in range(500):                                               # варианты написания одного семейства дают одно семейство
            f = rnd.choice(fams)
            v = "".join(c.upper() if rnd.random() < 0.5 else c for c in f)
            v = "".join("_" if c == "-" and rnd.random() < 0.5 else c for c in v)
            if rnd.random() < 0.5:
                v += rnd.choice("-_") + "".join(rnd.choice(string.ascii_lowercase) for _ in range(rnd.randint(1, 8)))
            got = hyps.family(v)[0]
            # суффикс может удлинить тег до другого, более длинного семейства (oi-flush -> ...): тогда это тоже его префикс
            self.assertTrue(got == f or got.startswith(f + "-"), (v, got))


if __name__ == "__main__":
    unittest.main()
