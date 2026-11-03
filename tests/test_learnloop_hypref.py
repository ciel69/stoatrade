"""tb.learnloop.hypref (R2, задача 36; Requirements 19.5, 19.7): справка меток для гипотез по policy_map.

Кластеры (час, сторона) по policy_runs политики семейства за 14 суток, среднее значений кластеров, 95% t-интервал, фильтр env_norm, виды сигнала
семейства, текущая версия политики, флаги gap/halt и незаполненные прогоны исключены; семейство без policy_map и с выключенной политикой справки
не имеет; соединение только для чтения на вызов и закрыто; восстановление базы, нет базы, нет политик — NO_DATA без файла; атомарная запись
runtime/learn/hyp_ref.json; шаг в реестре цикла настоящий. Временные корни, сети нет."""
import json
import math
import os
import shutil
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.core import expstats as X
from tb.core import hyps
from tb.knowledge import markers as KM
from tb.labels import policies as LP
from tb.labels import store
from tb.learnloop import cycle as CY
from tb.learnloop import hypref as HR

PROJECT = _bootstrap.ROOT
NOW = 1793750400            # 2026-11-03T00:00:00Z
H = 3600
D = 86400
POLS = [{"id": "P2", "ver": 1, "kinds": ["range_5m", "oi_spike", "liq_cascade"], "side": "with_dir", "enabled": True},
        {"id": "P4", "ver": 1, "kinds": ["range_5m", "liq_cascade", "oi_flush"], "side": "against_dir", "enabled": True},
        {"id": "P4", "ver": 2, "kinds": ["range_5m", "liq_cascade", "oi_flush"], "side": "against_dir", "enabled": True}]
PMAP = {"liq-cascade-reclaim": {"policy": "P4", "kinds": ["liq_cascade"]}, "oi-flush-reclaim": {"policy": "P4", "kinds": ["oi_flush"]},
        "oi-build-squeeze": {"policy": "P2", "kinds": ["oi_spike"]}}

_SIG = ("INSERT INTO signals(key, t0, sym, type, side, tradable, env, ev, status, attempts, mature_ts, next_try_ts, captured_ts, labeled_ts, schema_ver)"
        " VALUES(?,?,?,?,?,1,?,'{}','done',0,?,?,?,?,1)")
_RUN = ("INSERT OR REPLACE INTO policy_runs(key, policy_id, policy_ver, geometry_ver, ctx_ver, t0, sym, type, side, plan, code, filled, r_net, flags)"
        " VALUES(?,?,?,'g1','c1',?,?,?,?,NULL,NULL,?,?,?)")


def t_ci(vals):
    n = len(vals)
    m = sum(vals) / n
    sd = math.sqrt(sum((v - m) ** 2 for v in vals) / (n - 1))
    h = X.t_quantile(0.975, n - 1) * sd / math.sqrt(n)
    return n, m, m - h, m + h


class Base(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbhr_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.c = store.open_rw(self.root)
        self.addCleanup(self.c.close)
        self.i = 0

    def run_(self, t0, typ="liq_cascade", sig_side="long", side="short", r=0.5, pol="P4", ver=2, env="mainnet", filled=1, flags=None, sym=None):
        self.i += 1
        key = "S-%d" % self.i
        sym = sym or "S%dUSDT" % self.i
        self.c.execute(_SIG, (key, t0, sym, typ, sig_side, env, t0 + H, t0 + H, t0 + 10, t0 + 2 * H))
        self.c.execute(_RUN, (key, pol, ver, t0, sym, typ, side, 1 if filled else 0, r, flags))
        return key

    def compute(self, pmap=PMAP, pols=POLS, now=NOW):
        return HR.compute(self.c, now, {"hypref": {"window_days": 14}}, pmap, pols, self.root)


class TestCompute(Base):
    def test_clusters_mean_and_t_interval(self):
        h0 = NOW - 3 * D
        h0 -= h0 % H
        self.run_(h0 + 10, r=1.0)                         # кластер 1 (час h0, short): прогоны 1.0 и 0.0 -> 0.5
        self.run_(h0 + 900, r=0.0)
        self.run_(h0 + 100, side=None, sig_side="short", r=-0.4)    # сторона по политике against_dir: short -> long, кластер 2: -0.4
        self.run_(h0 + 2 * H, r=0.3)                      # кластер 3: 0.3
        self.run_(h0 + 5 * H, r=-0.1)                     # кластер 4: -0.1
        doc = self.compute()
        e = doc["families"]["liq-cascade-reclaim"]["env"]["mainnet"]
        n, m, lo, hi = t_ci([0.5, -0.4, 0.3, -0.1])
        self.assertEqual((e["n"], e["n_runs"]), (n, 5))
        self.assertAlmostEqual(e["mean"], round(m, 4))
        self.assertAlmostEqual(e["lo"], round(lo, 4))
        self.assertAlmostEqual(e["hi"], round(hi, 4))
        f = doc["families"]["liq-cascade-reclaim"]
        self.assertEqual((f["policy"], f["policy_ver"], f["kinds"]), ("P4", 2, ["liq_cascade"]))     # текущая (последняя включённая) версия
        self.assertEqual((doc["schema"], doc["window_days"], doc["b"] - doc["a"]), (HR.SCHEMA, 14, 14 * D))

    def test_filters(self):
        t = NOW - 2 * D
        self.run_(t, r=0.2)                                        # входит
        self.run_(t + H, r=0.4)                                    # входит
        self.run_(NOW - 14 * D - 1, r=9.0)                         # раньше окна
        self.run_(NOW, r=9.0)                                      # t0 = b: вне [a, b)
        self.run_(t + 2 * H, r=9.0, filled=0)                      # не заполнен
        self.run_(t + 3 * H, r=None)                               # нет r_net
        self.run_(t + 4 * H, r=9.0, flags="gap")                   # gap/halt исключены, как metric_rows
        self.run_(t + 5 * H, r=9.0, flags="x,halt")
        self.run_(t + 6 * H, r=9.0, typ="range_5m")                # вид не семейства
        self.run_(t + 7 * H, r=9.0, ver=1)                         # старая версия политики
        self.run_(t + 8 * H, r=9.0, pol="P2", side="long")         # другая политика
        self.run_(t + 9 * H, r=9.0, env="testnet")                 # другое env_norm
        doc = self.compute()
        e = doc["families"]["liq-cascade-reclaim"]["env"]
        self.assertEqual((e["mainnet"]["n"], e["mainnet"]["mean"]), (2, 0.3))
        self.assertEqual((e["testnet"]["n"], e["testnet"]["mean"], e["testnet"]["lo"]), (1, 9.0, None))     # n < 2 — без интервала

    def test_env_norm_empty_is_mainnet(self):
        self.run_(NOW - D, env=None, r=0.1)
        self.run_(NOW - D + H, env="", r=0.3)
        e = self.compute()["families"]["liq-cascade-reclaim"]["env"]
        self.assertEqual((e["mainnet"]["n"], e["testnet"]["n"]), (2, 0))

    def test_only_policy_map_families(self):
        self.run_(NOW - D, typ="oi_spike", pol="P2", ver=1, side="long", r=0.2)
        doc = self.compute()
        self.assertEqual(sorted(doc["families"]), sorted(PMAP))
        self.assertEqual(doc["families"]["oi-build-squeeze"]["env"]["mainnet"]["n"], 1)
        self.assertEqual(doc["families"]["oi-flush-reclaim"]["env"]["mainnet"], {"n": 0, "n_runs": 0, "mean": None, "lo": None, "hi": None})
        for fam in ("range-fade", "breakout-retest", "reclaim-level", "other"):      # многовидовые и прочие — без справки
            self.assertNotIn(fam, doc["families"])

    def test_disabled_policy_no_ref(self):
        pols = [dict(p, enabled=False) if p["id"] == "P4" else p for p in POLS]
        doc = self.compute(pols=pols)
        self.assertEqual(sorted(doc["families"]), ["oi-build-squeeze"])

    def test_project_policy_map(self):
        """Начальная карта config/hypotheses.json и политики config/shadow_policies.json (Requirement 19.5)."""
        pm = hyps.policy_map()
        self.assertEqual(pm, PMAP)
        pols = LP.load(PROJECT)
        sides = dict((p["id"], p["side"]) for p in pols)
        self.assertEqual((sides["P4"], sides["P2"]), ("against_dir", "with_dir"))
        for fam, e in pm.items():
            pol = [p for p in pols if p["id"] == e["policy"]][0]
            self.assertTrue(set(e["kinds"]) <= set(pol["kinds"]), fam)


class TestRun(Base):
    def setUp(self):
        Base.setUp(self)
        os.makedirs(os.path.join(self.root, "config"), exist_ok=True)
        shutil.copy(os.path.join(PROJECT, "config", "shadow_policies.json"), os.path.join(self.root, "config", "shadow_policies.json"))

    def read(self):
        with open(HR.ref_path(self.root), encoding="utf-8") as f:
            return json.load(f)

    def test_writes_file_atomically_and_closes_connection(self):
        self.run_(NOW - D, ver=1, r=0.25)
        self.run_(NOW - D + H, ver=1, r=0.75)
        opened = []
        real = store.open_ro

        def spy(root=None):
            c = real(root)
            opened.append(c)
            return c

        with mock.patch.object(store, "open_ro", spy), mock.patch.object(KM, "write_json", wraps=KM.write_json) as wj:
            rep = HR.run(self.root, NOW, {"hypref": {"window_days": 14}})
        self.assertEqual(rep["code"], "OK")
        self.assertEqual(rep["n"]["liq-cascade-reclaim"], 2)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(Exception):
            opened[0].execute("SELECT 1")                         # соединение на вызов закрыто
        wj.assert_called_once()
        doc = self.read()
        self.assertEqual(doc["families"]["liq-cascade-reclaim"]["env"]["mainnet"]["mean"], 0.5)
        self.assertFalse([x for x in os.listdir(os.path.dirname(HR.ref_path(self.root))) if x.endswith(".tmp")])

    def test_restoring_no_data_no_file(self):
        os.makedirs(os.path.dirname(KM.restoring_path(self.root)), exist_ok=True)
        with open(KM.restoring_path(self.root), "w", encoding="utf-8") as f:
            f.write("{}")
        rep = HR.run(self.root, NOW)
        self.assertEqual((rep["code"], rep["reason"]), ("NO_DATA", "KNOWLEDGE_RESTORING"))
        self.assertFalse(os.path.exists(HR.ref_path(self.root)))

    def test_no_db_and_no_policies(self):
        empty = tempfile.mkdtemp(prefix="tbhr0_")
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertEqual(HR.run(empty, NOW)["reason"], "NO_POLICIES")
        os.makedirs(os.path.join(empty, "config"))
        shutil.copy(os.path.join(PROJECT, "config", "shadow_policies.json"), os.path.join(empty, "config", "shadow_policies.json"))
        self.assertEqual(HR.run(empty, NOW)["reason"], "NO_DB")
        self.assertFalse(os.path.exists(HR.ref_path(empty)))

    def test_db_unchanged(self):
        self.run_(NOW - D, ver=1)
        before = self.c.execute("SELECT COUNT(*), SUM(r_net) FROM policy_runs").fetchone()
        HR.run(self.root, NOW)
        self.assertEqual(self.c.execute("SELECT COUNT(*), SUM(r_net) FROM policy_runs").fetchone(), before)

    def test_step_in_cycle_registry(self):
        st = [s for s in CY.default_steps() if s.name == "hypref"]
        self.assertEqual(len(st), 1)
        s = st[0]
        self.assertEqual((s.period, s.interval, s.stub, s.fn), ("hourly", "hypref_s", False, HR.step_hypref))
        self.run_(NOW - D, ver=1, r=0.1)
        ctx = CY.Ctx(self.root, self.c, {"hypref": {"window_days": 14}}, NOW)
        self.assertEqual(s.fn(ctx)["code"], "OK")
        self.assertIn("liq-cascade-reclaim", self.read()["families"])


if __name__ == "__main__":
    unittest.main()
