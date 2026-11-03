"""R2 (задача 8): уроки меток в memory.lessons — create(evidence_src="labels"), lesson_id под lessons.lock, transition, refresh без уроков меток,
бюджет propose, conf_eff без затухания, max_active по группам, размер строки, applicable с обеими группами, BAD_SRC команд владельца.
Свойства 4 (совместимость фасада) и 13 (уникальность идентификаторов) из design.md."""
import contextlib
import io
import json
import os
import subprocess
import sys
import threading
import time
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
import test_memory_lessons as tml                     # модулем, чтобы классы тестов не запускались второй раз
from _memory_helpers import NOW, TmpRoot
from tb.memory import cases, chain, lessons
from tb.memory import cli as memory_cli

NC = [{"feature": "confirmed", "op": "=", "value": False}]
RNG = [{"feature": "range_5m_pct", "op": ">=", "value": 1.5}]
CL = {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.4}
DAY = 86400.0


def ev_extra(n=40, diff=-0.35, se=0.1, clusters=5, **kw):
    """Компактное evidence урока меток, как его готовит цикл (задача 7)."""
    ev = {"sample": {"policy": "P2", "ver": 1, "ctx_ver": "c1", "env_norm": "paper", "a": 1761000000, "b": 1762209600, "holdout_start": 1762209600,
                     "holdout_end": 1762814400, "cut_ts": 1762987200, "hash": "0123456789abcdef"},
          "screen": {"n": 92, "diff": -0.31, "p": 0.0011}, "holdout": {"n": n, "diff": diff, "p": 0.004},
          "n_closed": n, "diff": diff, "se": se, "last_ts": "2026-11-10T00:00:00Z",
          "clusters": ["%d:short" % (1762209600 + 3600 * i) for i in range(clusters)]}
    ev.update(kw)
    return ev


def lines(root):
    rows, _broken = chain.read(lessons.lessons_path(root))
    return rows


class R2Base(TmpRoot):
    def cfg_with(self, **lc):
        cfg = json.loads(json.dumps(self.cfg))
        cfg["lessons"].update(lc)
        return cfg

    def mk_labels(self, cond=None, claim=None, now=NOW, cfg=None, **kw):
        return lessons.create(self.root, cond or NC, claim or CL, "code", [], cfg or self.cfg, now, evidence_src="labels",
                              evidence_extra=kw.pop("extra", None) or ev_extra(**kw))

    def mk_brain(self, cond=None, claim=None, by="code", now=NOW, cfg=None):
        return lessons.create(self.root, cond or NC, claim or dict(CL, effect=1.0), by, cases.load(self.root), cfg or self.cfg, now)


class TestCreateLabels(R2Base):
    def test_no_stats_and_fields(self):
        extra = ev_extra(clusters=7)
        with mock.patch.object(lessons, "stats", side_effect=AssertionError("stats() при labels")):
            l = lessons.create(self.root, NC, CL, "code", [], self.cfg, NOW, evidence_src="labels", evidence_extra=extra)
        self.assertEqual((l["lesson_id"], l["status"], l["created_by"], l["rev"]), ("L-0001", "hyp", "code", 1))
        self.assertIsNone(l["expires_ts"])
        self.assertEqual(l["evidence"]["src"], "labels")
        self.assertEqual(l["evidence"]["sample"], extra["sample"])
        self.assertEqual(len(l["evidence"]["clusters"]), lessons.LABELS_IDS_MAX)
        self.assertEqual(len(extra["clusters"]), 7)                                  # evidence_extra вызывающего не меняется
        self.assertNotIn("src", extra)
        self.assertEqual(l["ts"], cases.iso(NOW))
        self.assertEqual(l["history"], [{"ts": cases.iso(NOW), "status": "hyp", "reason_code": "NEW"}])
        self.assertEqual(l["txt"], lessons.render_txt(l))
        self.assertTrue(lessons.is_labels(l))
        self.assertEqual(lessons.validate_claim(l["claim"]), [])

    def test_conf_formula_and_cap(self):
        lc = self.cfg["lessons"]
        cap = min(lc["conf_cap_hyp"], lc["conf_cap_reviewer"])
        l = self.mk_labels(n=2, diff=-0.4, se=0.4)                                   # n_eff = 1 -> 1 - e^-0.1 = 0.095 < cap
        self.assertAlmostEqual(l["conf"], round(1 - 2.718281828459045 ** -0.1, 3), places=3)
        l2 = self.mk_labels(cond=RNG, n=60, diff=-0.4, se=0.05)
        self.assertEqual(l2["conf"], cap)                                             # ограничено conf_cap_reviewer
        l3 = self.mk_labels(cond=[{"feature": "spread_bps", "op": ">=", "value": 9.0}], n=0)
        self.assertEqual(l3["conf"], 0.05)

    def test_labels_only_code_hyp_and_valid_extra(self):
        for kw in ({"created_by": "owner"}, {"created_by": "reviewer"}):
            with self.assertRaises(ValueError):
                lessons.create(self.root, NC, CL, kw["created_by"], [], self.cfg, NOW, evidence_src="labels", evidence_extra=ev_extra())
        with self.assertRaises(ValueError):
            lessons.create(self.root, NC, CL, "code", [], self.cfg, NOW, status="confirmed", evidence_src="labels", evidence_extra=ev_extra())
        with self.assertRaises(ValueError):
            lessons.create(self.root, NC, CL, "code", [], self.cfg, NOW, evidence_src="labels", evidence_extra=None)
        with self.assertRaises(ValueError):
            lessons.create(self.root, NC, CL, "code", [], self.cfg, NOW, evidence_src="sky")
        for bad in ({"n_closed": "40"}, {"n_closed": True}, {"diff": None}, {"diff": float("nan")}, {"se": -1}):
            ex = ev_extra()
            ex.update(bad)
            with self.assertRaises(ValueError):
                lessons.create(self.root, NC, CL, "code", [], self.cfg, NOW, evidence_src="labels", evidence_extra=ex)
        self.assertEqual(lines(self.root), [])

    def test_brain_default_unchanged(self):
        self.put(tml.make_pool())
        real = lessons.stats
        with mock.patch.object(lessons, "stats", side_effect=real) as st:
            l = self.mk_brain()
        self.assertTrue(st.called)
        self.assertNotIn("src", l["evidence"])
        self.assertEqual(l["expires_ts"], NOW + self.cfg["lessons"]["ttl_hyp_days"] * DAY)
        self.assertFalse(lessons.is_labels(l))

    def test_max_active_by_group(self):
        cfg = self.cfg_with(max_active=1)
        self.mk_labels(cfg=cfg)
        with self.assertRaises(lessons.LessonRefused) as e:
            self.mk_labels(cond=RNG, cfg=cfg)
        self.assertEqual(e.exception.code, "BUDGET")
        self.assertIsInstance(e.exception, ValueError)
        self.put(tml.make_pool())
        self.assertEqual(self.mk_brain(cfg=cfg)["lesson_id"], "L-0002")             # группа мозга считается отдельно
        lessons.transition(self.root, "L-0001", "rejected", "TRIAL_REJECT", NOW, cfg=cfg)
        self.assertEqual(self.mk_labels(cond=RNG, cfg=cfg)["lesson_id"], "L-0003")  # место освободилось

    def test_row_size_truncates_ids(self):
        first = self.mk_labels()
        base = len(chain.canon(first).encode("utf-8"))
        limit = lessons.LINE_MAX - lessons.LABELS_RESERVE
        filler = "ab " * ((limit - base + 25) // 3)                                   # не помещается с 5 id, помещается после усечения
        l = self.mk_labels(cond=RNG, note=filler)
        self.assertLess(len(l["evidence"]["clusters"]), 5)
        self.assertGreater(len(l["evidence"]["clusters"]), 0)
        self.assertLessEqual(len(chain.canon(l).encode("utf-8")), limit)
        self.assertEqual(lines(self.root)[-1]["lesson_id"], "L-0002")

    def test_row_size_too_big(self):
        with self.assertRaises(lessons.LessonRefused) as e:
            self.mk_labels(note="ab " * 1500)
        self.assertEqual(e.exception.code, "EVIDENCE_TOO_BIG")
        self.assertEqual(lines(self.root), [])
        self.assertEqual(self.mk_labels()["lesson_id"], "L-0001")                     # идентификатор не потрачен


class TestBrainIsolation(R2Base):
    def test_propose_budget_ignores_labels(self):
        self.put(tml.make_pool())
        cfg = self.cfg_with(max_new_per_day=1)
        before = lessons.propose(self.root, cases.load(self.root), cfg, NOW)
        self.assertEqual(len(before), 1)
        self.mk_labels(cond=[{"feature": "spread_bps", "op": ">=", "value": 9.0}], cfg=cfg)
        self.assertEqual(len(lessons.propose(self.root, cases.load(self.root), cfg, NOW)), 1)
        lessons.create(self.root, [{"feature": "blackout", "op": "=", "value": True}], dict(CL, effect=1.0), "code", cases.load(self.root), cfg, NOW)
        self.assertEqual(lessons.propose(self.root, cases.load(self.root), cfg, NOW), [])  # урок мозга бюджет съедает, как прежде

    def test_conf_eff_no_decay(self):
        self.put(tml.make_pool())
        lab = self.mk_labels(cond=RNG)
        brain = self.mk_brain()
        later = NOW + 365 * DAY
        self.assertEqual(lessons.conf_eff(lab, later, self.cfg), lab["conf"])
        self.assertLess(lessons.conf_eff(brain, later, self.cfg), brain["conf"])

    def test_refresh_ignores_labels(self):
        self.put(tml.make_pool())
        lab = self.mk_labels(claim=dict(CL, dir="higher"))           # на пуле мозга условие NC хуже базы: refresh мозга отверг бы этот урок
        lab2 = self.mk_labels(cond=RNG)
        cfg = self.cfg_with(max_active=1)
        brain = self.mk_brain(cfg=cfg)
        for now in (NOW + DAY, NOW + 400 * DAY):
            ch = lessons.refresh(self.root, cases.load(self.root), cfg, now)
            self.assertFalse([c for c in ch if c["lesson_id"] in (lab["lesson_id"], lab2["lesson_id"])], ch)
        st = lessons.read_all(self.root)
        self.assertEqual(st[lab["lesson_id"]], lab)
        self.assertEqual(st[lab2["lesson_id"]], lab2)
        self.assertEqual(st[brain["lesson_id"]]["status"], "expired")          # урок мозга живёт по прежним правилам
        self.assertEqual(st[brain["lesson_id"]]["history"][-1]["reason_code"], "EXPIRED")

    def test_refresh_no_contradiction_or_eviction_from_labels(self):
        self.put(tml.make_pool())
        cfg = self.cfg_with(max_active=1)
        brain = self.mk_brain(cfg=cfg)
        self.mk_labels(claim=dict(CL, dir="higher"), cfg=cfg)                     # противоположный claim на том же условии
        lessons.refresh(self.root, cases.load(self.root), cfg, NOW + DAY)
        b = lessons.read_all(self.root)[brain["lesson_id"]]
        self.assertIn(b["status"], lessons.ACTIVE)                               # не вытеснен: в группе мозга 1 урок при лимите 1
        self.assertEqual(b["contradicts"], [])

    def test_refresh_dry_run_same(self):
        self.mk_labels()
        self.assertEqual(lessons.refresh(self.root, [], self.cfg, NOW + 400 * DAY, dry_run=True), [])

    def test_applicable_both_groups(self):
        self.put(tml.make_pool())
        brain = self.mk_brain()
        lab = self.mk_labels(cond=RNG, n=60, diff=-0.4, se=0.05)
        ls = list(lessons.read_all(self.root).values())
        out = lessons.applicable(ls, {"confirmed": False, "range_5m_pct": 1.8}, NOW + DAY, self.cfg, limit=10)
        by = dict((x["id"], x) for x in out)
        self.assertEqual(set(by), {brain["lesson_id"], lab["lesson_id"]})
        self.assertEqual(by[lab["lesson_id"]]["src"], "labels")
        self.assertNotIn("src", by[brain["lesson_id"]])
        self.assertEqual(set(by[brain["lesson_id"]]), {"id", "st", "n", "conf", "txt", "contradicts"})
        out2 = lessons.applicable(ls, {"range_5m_pct": 1.8}, NOW + 3650 * DAY, self.cfg, limit=10)  # null expires_ts и нет затухания: виден
        self.assertEqual([x["id"] for x in out2], [lab["lesson_id"]])

    def test_reasons_extended(self):
        self.assertEqual(lessons.REASONS[:8], ("NEW", "ENOUGH_N", "CONTRADICTED", "EXPIRED", "MERGED", "OWNER", "EVICTED", "REFRESH"))
        for r in ("TRIAL_PROMOTE", "TRIAL_REJECT", "NO_VERDICT", "UNREACHABLE_N", "LOSS_CAP", "SAMPLE_CHANGED", "CTX_CHANGED", "RESTORED", "BUDGET"):
            self.assertIn(r, lessons.REASONS)
        self.assertEqual(len(set(lessons.REASONS)), len(lessons.REASONS))


class TestTransition(R2Base):
    def test_confirm_expire_and_idempotent(self):
        l = self.mk_labels()
        t1 = NOW + 10 * DAY
        c = lessons.transition(self.root, l["lesson_id"], "confirmed", "TRIAL_PROMOTE", t1)
        self.assertEqual((c["status"], c["rev"]), ("confirmed", 2))
        self.assertEqual(c["expires_ts"], t1 + self.cfg["lessons"]["ttl_confirmed_days"] * DAY)
        self.assertEqual(c["conf"], l["conf"])                                    # без чисел испытания conf прежний
        self.assertEqual(c["history"][-1], {"ts": cases.iso(t1), "status": "confirmed", "reason_code": "TRIAL_PROMOTE"})
        n = len(lines(self.root))
        self.assertIsNone(lessons.transition(self.root, l["lesson_id"], "confirmed", "TRIAL_PROMOTE", t1 + 60))
        self.assertEqual(len(lines(self.root)), n)                                # повтор ничего не пишет
        e = lessons.transition(self.root, l["lesson_id"], "expired", "EXPIRED", t1 + 91 * DAY)
        self.assertEqual(e["status"], "expired")
        self.assertTrue(chain.verify(lessons.lessons_path(self.root))["ok"])

    def test_confirm_with_trial_numbers(self):
        l = self.mk_labels()
        c = lessons.transition(self.root, l["lesson_id"], "confirmed", "TRIAL_PROMOTE", NOW, cfg=self.cfg, nums={"n_closed": 150, "diff": -0.3, "se": 0.05})
        self.assertGreater(c["conf"], l["conf"])
        self.assertLessEqual(c["conf"], 0.95)
        with self.assertRaises(ValueError):
            lessons.transition(self.root, self.mk_labels(cond=RNG)["lesson_id"], "confirmed", "TRIAL_PROMOTE", NOW, nums={"n_closed": "x", "diff": 1})

    def test_reject_codes(self):
        for i, code in enumerate(lessons.LABEL_REJECT_REASONS):
            l = self.mk_labels(extra=ev_extra(clusters=1, tag="t%d" % i))
            r = lessons.transition(self.root, l["lesson_id"], "rejected", code, NOW)
            self.assertEqual((r["status"], r["history"][-1]["reason_code"]), ("rejected", code))

    def test_invalid_transitions(self):
        l = self.mk_labels()
        lid = l["lesson_id"]
        for st, code in (("expired", "EXPIRED"), ("merged", "MERGED"), ("confirmed", "OWNER"), ("confirmed", "ENOUGH_N"), ("rejected", "CONTRADICTED"),
                         ("rejected", "EXPIRED"), ("hyp", "NEW"), ("nonsense", "OWNER")):
            if st == "hyp":
                self.assertIsNone(lessons.transition(self.root, lid, st, code, NOW))   # тот же статус — без записи
                continue
            with self.assertRaises(ValueError, msg=(st, code)):
                lessons.transition(self.root, lid, st, code, NOW)
        lessons.transition(self.root, lid, "confirmed", "TRIAL_PROMOTE", NOW)
        with self.assertRaises(ValueError):
            lessons.transition(self.root, lid, "rejected", "TRIAL_REJECT", NOW)          # confirmed -> rejected только OWNER
        with self.assertRaises(ValueError):
            lessons.transition(self.root, lid, "hyp", "NEW", NOW)                        # назад нельзя
        self.assertEqual(lessons.transition(self.root, lid, "rejected", "OWNER", NOW)["status"], "rejected")
        with self.assertRaises(ValueError):
            lessons.transition(self.root, lid, "confirmed", "TRIAL_PROMOTE", NOW)        # из терминального
        with self.assertRaises(ValueError):
            lessons.transition(self.root, "L-9999", "rejected", "OWNER", NOW)

    def test_brain_lesson_bad_src(self):
        self.put(tml.make_pool())
        b = self.mk_brain()
        with self.assertRaises(lessons.LessonRefused) as e:
            lessons.transition(self.root, b["lesson_id"], "rejected", "OWNER", NOW)
        self.assertEqual(e.exception.code, "BAD_SRC")

    def test_rereads_under_lock(self):
        l = self.mk_labels()
        lessons.transition(self.root, l["lesson_id"], "rejected", "TRIAL_REJECT", NOW)
        with self.assertRaises(ValueError):                                              # устаревшее представление вызывающего не важно
            lessons.transition(self.root, l["lesson_id"], "confirmed", "TRIAL_PROMOTE", NOW)


class TestOwnerCommands(R2Base):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            rc = memory_cli.main(["lesson"] + list(argv), root=self.root)
        return rc, buf.getvalue().strip()

    def test_owner_set_bad_src(self):
        self.put(tml.make_pool())
        lab = self.mk_labels(cond=RNG)
        brain = self.mk_brain()
        for act in ("confirm", "expire", "merge"):
            with self.assertRaises(lessons.LessonRefused) as e:
                lessons.owner_set(self.root, lab["lesson_id"], act, self.cfg, NOW, into=brain["lesson_id"])
            self.assertEqual(e.exception.code, "BAD_SRC")
        with self.assertRaises(lessons.LessonRefused):
            lessons.owner_set(self.root, brain["lesson_id"], "merge", self.cfg, NOW, into=lab["lesson_id"])  # урок мозга не трогает урок меток
        self.assertEqual(lessons.read_all(self.root)[lab["lesson_id"]]["rev"], 1)
        r = lessons.owner_set(self.root, lab["lesson_id"], "reject", self.cfg, NOW)
        self.assertEqual((r["status"], r["history"][-1]["reason_code"]), ("rejected", "OWNER"))
        self.assertEqual(lessons.owner_set(self.root, lab["lesson_id"], "reject", self.cfg, NOW)["rev"], 2)  # повтор без записи

    def test_cli_bad_src(self):
        lab = self.mk_labels()
        for act in ("confirm", "expire"):
            rc, out = self.run_cli(act, lab["lesson_id"])
            self.assertEqual(rc, 2)
            self.assertEqual(json.loads(out)["code"], "BAD_SRC")
        p = os.path.join(self.root, "add.json")
        for d in ({"cond": RNG, "claim": CL, "evidence_src": "labels"}, {"cond": RNG, "claim": CL, "evidence": {"src": "labels"}}):
            with open(p, "w", encoding="utf-8") as f:
                json.dump(d, f)
            rc, out = self.run_cli("add", "--file", p)
            self.assertEqual((rc, json.loads(out)["code"]), (2, "BAD_SRC"))
        self.assertEqual(len(lessons.read_all(self.root)), 1)
        rc, out = self.run_cli("reject", lab["lesson_id"])
        self.assertEqual((rc, json.loads(out)["status"]), (0, "rejected"))

    def test_cli_brain_unchanged(self):
        p = os.path.join(self.root, "add.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"cond": NC, "claim": dict(CL, effect=1.0)}, f)
        rc, out = self.run_cli("add", "--file", p)
        self.assertEqual((rc, json.loads(out)["status"]), (0, "hyp"))
        rc, out = self.run_cli("confirm", "L-0001")
        self.assertEqual((rc, json.loads(out)["status"]), (0, "confirmed"))


_CHILD = r"""
import os, sys, time
sys.path.insert(0, sys.argv[1])
from tb.memory import cfg as memcfg, lessons
root, n, now = sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
cfg = memcfg.load()
open(os.path.join(root, "child.ready"), "w").close()
go = os.path.join(root, "go")
deadline = time.time() + 60
while not os.path.exists(go):
    if time.time() > deadline:
        sys.exit(3)
    time.sleep(0.002)
for i in range(n):
    # путь from_hint: create(..., "reviewer", ...) уроком мозга
    lessons.create(root, [{"feature": "side", "op": "=", "value": "long"}], {"metric": "mean_r", "dir": "lower", "vs": "base", "effect": 0.6}, "reviewer", [], cfg, now + i)
"""


class TestConcurrentCreate(R2Base):
    """Свойство 13: цикл (поток) и from_hint (другой процесс) создают уроки одновременно — идентификаторы различны."""
    N = 50

    def test_thread_and_process(self):
        src = os.path.join(_bootstrap.ROOT, "src")
        child = subprocess.Popen([sys.executable, "-c", _CHILD, src, self.root, str(self.N), str(NOW)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            ready = os.path.join(self.root, "child.ready")
            deadline = time.time() + 60
            while not os.path.exists(ready) and child.poll() is None and time.time() < deadline:
                time.sleep(0.005)
            self.assertTrue(os.path.exists(ready), "дочерний процесс не стартовал")
            errs = []
            start = threading.Event()

            def loop():
                start.wait()
                try:
                    for i in range(self.N):
                        lessons.create(self.root, RNG, CL, "code", [], self.cfg, NOW + i, evidence_src="labels", evidence_extra=ev_extra(clusters=2))
                except Exception as e:  # noqa: BLE001
                    errs.append(e)
            th = threading.Thread(target=loop)
            th.start()
            open(os.path.join(self.root, "go"), "w").close()
            start.set()
            th.join(120)
            _out, err = child.communicate(timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        self.assertFalse(th.is_alive())
        self.assertEqual(errs, [])
        self.assertEqual(child.returncode, 0, err.decode("utf-8", "replace")[-2000:])
        rows = lines(self.root)
        self.assertEqual(len(rows), 2 * self.N)
        ids = [r["lesson_id"] for r in rows]
        self.assertEqual(len(set(ids)), 2 * self.N)                                   # ни одного повтора
        self.assertEqual(sorted(ids), ["L-%04d" % i for i in range(1, 2 * self.N + 1)])
        self.assertEqual(sum(1 for r in rows if lessons.is_labels(r)), self.N)
        self.assertTrue(chain.verify(lessons.lessons_path(self.root))["ok"])
        self.assertFalse(os.path.exists(lessons.lessons_lock(self.root)))


if __name__ == "__main__":
    unittest.main()
