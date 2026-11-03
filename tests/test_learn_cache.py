import random
import threading
import time
import unittest

import _bootstrap  # noqa: F401
from tb.learn.cache import Cache, Flight


class Counter:
    def __init__(self, delay=0.0, fail=False):
        self.n = 0
        self.delay = delay
        self.fail = fail
        self._mu = threading.Lock()

    def __call__(self):
        with self._mu:
            self.n += 1
            n = self.n
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return n


class CacheTest(unittest.TestCase):
    def test_single_flight_threads(self):
        for seed in range(5):
            rng = random.Random(seed)
            nthr = rng.randint(4, 24)
            c = Cache()
            ld = Counter(delay=0.05)
            out = []
            mu = threading.Lock()
            bar = threading.Barrier(nthr)

            def worker():
                bar.wait()
                r = c.get("k", "s", ld, 60, 10)
                with mu:
                    out.append(r)

            ts = [threading.Thread(target=worker) for _ in range(nthr)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            self.assertEqual(ld.n, 1)
            self.assertEqual(len(out), nthr)
            self.assertEqual({v for v, _ in out}, {1})
            self.assertEqual(sum(1 for _, cached in out if not cached), 1)

    def test_distinct_keys_independent(self):
        c = Cache()
        a, b = Counter(), Counter()
        c.get("a", "s", a, 60, 10, now=0)
        c.get("b", "s", b, 60, 10, now=0)
        self.assertEqual((a.n, b.n), (1, 1))

    def test_ttl_property(self):
        for seed in range(20):
            rng = random.Random(seed)
            ttl = rng.randint(5, 100)
            c = Cache()
            ld = Counter()
            c.get("k", "s", ld, ttl, 1, now=100.0)
            dt = rng.uniform(0, ttl * 2)
            v, cached = c.get("k", "s", ld, ttl, 1, now=100.0 + dt)
            if dt < ttl:
                self.assertTrue(cached)
                self.assertEqual(ld.n, 1)
            else:
                self.assertFalse(cached)
                self.assertEqual(ld.n, 2)

    def test_sig_change_respects_min_interval(self):
        for seed in range(20):
            rng = random.Random(seed)
            mi = rng.randint(2, 20)
            c = Cache()
            ld = Counter()
            c.get("k", "s1", ld, 60, mi, now=0.0)
            dt = rng.uniform(0, 59)
            v, cached = c.get("k", "s2", ld, 60, mi, now=dt)
            if dt < mi:
                self.assertTrue(cached, (seed, dt, mi))
                self.assertEqual(v, 1)
            else:
                self.assertFalse(cached, (seed, dt, mi))
                self.assertEqual(v, 2)

    def test_sig_same_no_recompute(self):
        c = Cache()
        ld = Counter()
        c.get("k", "s", ld, 60, 10, now=0)
        _, cached = c.get("k", "s", ld, 60, 10, now=30)
        self.assertTrue(cached)
        self.assertEqual(ld.n, 1)

    def test_exception_not_cached(self):
        c = Cache()
        bad = Counter(fail=True)
        with self.assertRaises(RuntimeError):
            c.get("k", "s", bad, 60, 10, now=0)
        with self.assertRaises(RuntimeError):
            c.get("k", "s", bad, 60, 10, now=1)
        self.assertEqual(bad.n, 2)
        good = Counter()
        v, cached = c.get("k", "s", good, 60, 10, now=2)
        self.assertEqual((v, cached), (1, False))

    def test_exception_does_not_drop_old_value_or_block_waiters(self):
        c = Cache()
        c.get("k", "s", Counter(), 5, 1, now=0)
        bad = Counter(delay=0.03, fail=True)
        errs = []

        def w():
            try:
                c.get("k", "s", bad, 5, 1, now=10)
            except RuntimeError:
                errs.append(1)

        ts = [threading.Thread(target=w) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        # исключение не отравило: все ждавшие по очереди пробуют заново, ни один не завис
        self.assertEqual(len(errs), 4)
        v, cached = c.get("k", "s", Counter(), 5, 1, now=11)
        self.assertFalse(cached)

    def test_monotonic_default_clock(self):
        c = Cache()
        ld = Counter()
        c.get("k", "s", ld, 60, 10)
        _, cached = c.get("k", "s", ld, 60, 10)
        self.assertTrue(cached)
        self.assertEqual(ld.n, 1)

    def test_clock_backwards_recomputes(self):
        c = Cache()
        ld = Counter()
        c.get("k", "s", ld, 60, 10, now=100)
        _, cached = c.get("k", "s", ld, 60, 10, now=50)
        self.assertFalse(cached)

    def test_clear(self):
        c = Cache()
        ld = Counter()
        c.get("k", "s", ld, 60, 10, now=0)
        c.clear()
        c.get("k", "s", ld, 60, 10, now=1)
        self.assertEqual(ld.n, 2)


class FlightTest(unittest.TestCase):
    def test_one_thread_per_key(self):
        f = Flight()
        ld = Counter(delay=0.05)
        futs = [f.start("m", ld) for _ in range(8)]
        self.assertTrue(all(x is futs[0] for x in futs))
        self.assertIs(f.peek("m"), futs[0])
        self.assertEqual(futs[0].result(timeout=5), 1)
        self.assertEqual(ld.n, 1)

    def test_restart_after_done(self):
        f = Flight()
        ld = Counter()
        a = f.start("m", ld)
        a.result(timeout=5)
        b = f.start("m", ld)
        self.assertEqual(b.result(timeout=5), 2)

    def test_peek_missing(self):
        self.assertIsNone(Flight().peek("x"))

    def test_exception_in_future_then_restart(self):
        f = Flight()
        fut = f.start("m", Counter(fail=True))
        with self.assertRaises(RuntimeError):
            fut.result(timeout=5)
        ok = f.start("m", Counter())
        self.assertEqual(ok.result(timeout=5), 1)

    def test_distinct_keys(self):
        f = Flight()
        a = f.start("a", Counter())
        b = f.start("b", Counter())
        self.assertIsNot(a, b)
        a.result(timeout=5)
        b.result(timeout=5)


if __name__ == "__main__":
    unittest.main()
