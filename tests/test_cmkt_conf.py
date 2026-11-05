"""tb.cmkt.conf: строгая загрузка config/cmkt.json (R1.7, задача 4; свойство 22; Requirements 1.6, 2.4, 3.4, 4.4, 12.4, 16.1, 21.3)."""
import copy
import json
import os
import shutil
import tempfile
import unittest

import _bootstrap  # noqa: F401
from tb import paths
from tb.cmkt import conf as C
from tb.logic import cmkt as L

PROJECT_CFG = paths.config("cmkt.json", root=paths.DEFAULT_ROOT)


def shipped():
    with open(PROJECT_CFG, encoding="utf-8") as f:
        return json.load(f)


def with_(cfg, path, value):
    c = copy.deepcopy(cfg)
    d = c
    keys = path.split(".")
    for k in keys[:-1]:
        d = d[k]
    if value is _DEL:
        del d[keys[-1]]
    else:
        d[keys[-1]] = value
    return c


_DEL = object()


class Root(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tb-cmktconf-")
        os.makedirs(os.path.join(self.root, "config"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, obj_or_text):
        text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
        with open(paths.config("cmkt.json", root=self.root), "w", encoding="utf-8") as f:
            f.write(text)


class TestShipped(unittest.TestCase):
    def test_project_config_valid(self):
        cfg, errs = C.load(paths.DEFAULT_ROOT)
        self.assertEqual(errs, [])
        self.assertEqual(cfg["schema"], C.SCHEMA)

    def test_defaults_equal_file(self):
        self.assertEqual(shipped(), C.DEFAULTS)
        self.assertEqual(C.check(C.defaults()), [])

    def test_logic_defaults_equal_file_sections(self):
        for sec in ("sync", "book", "cov"):
            self.assertEqual(L.DEFAULTS[sec], C.DEFAULTS[sec], sec)

    def test_shipped_values(self):
        cfg = shipped()
        self.assertEqual(cfg["sync"]["ping_s"], 10)
        self.assertEqual(cfg["sync"]["silent_s"], 15)
        self.assertEqual(cfg["focus"]["max_deep"], 20)
        self.assertEqual(cfg["focus"]["max_light"], 120)
        self.assertEqual(cfg["brief"], {"mode": "compact", "max_tokens": 90, "syms": 1, "experiment": False})
        self.assertEqual(cfg["tool"], {"max_bytes": 2048, "per_hour": 30})
        self.assertIs(cfg["compat_xflow"], False)
        self.assertEqual(len(C.markets(cfg)), 11)                       # 11 рынков первой очереди
        self.assertEqual(sorted("%s_%s" % m for m in C.markets(cfg)), sorted(L.MARKETS))

    def test_no_paid_aggregator_hosts(self):
        hosts = [h for v in shipped()["venues"].values() for h in v["hosts"]]
        self.assertTrue(hosts)
        self.assertFalse([h for h in hosts if any(p in h for p in C.PAID_HOSTS)])


class TestLoad(Root):
    def test_missing_file_gives_defaults(self):
        cfg, errs = C.load(self.root)
        self.assertEqual(errs, [])
        self.assertEqual(cfg, C.DEFAULTS)
        cfg["focus"]["max_deep"] = 1
        self.assertEqual(C.DEFAULTS["focus"]["max_deep"], 20)           # копия, не сам DEFAULTS

    def test_broken_json(self):
        for text in ("{", "[]", '{"schema": "tb.cmkt_cfg/1", "schema": "x"}', '{"a": NaN}'):
            self.write(text)
            cfg, errs = C.load(self.root)
            self.assertIsNone(cfg, text)
            self.assertTrue(errs and all(e.startswith("E_CONF:") for e in errs), text)
        with self.assertRaises(C.ConfError):
            C.load_strict(self.root)

    def test_valid_file_loads(self):
        self.write(C.defaults())
        self.assertEqual(C.load_strict(self.root), C.DEFAULTS)

    def test_unknown_and_missing_keys(self):
        d = C.defaults()
        self.write(with_(d, "focus.extra", 1))
        self.assertEqual(C.load(self.root)[1], ["E_CONF:focus.extra"])
        self.write(with_(d, "sync.ping_s", _DEL))
        self.assertEqual(C.load(self.root)[1], ["E_CONF:sync.ping_s"])
        self.write(with_(d, "venues.okx.extra", True))
        self.assertEqual(C.load(self.root)[1], ["E_CONF:venues.okx.extra"])
        self.write(with_(d, "tool", 5))
        self.assertEqual(C.load(self.root)[1], ["E_CONF:tool"])

    def test_types(self):
        d = C.defaults()
        for path, bad in (("enabled", 1), ("compat_xflow", "no"), ("focus.max_deep", 20.0), ("focus.max_deep", True),
                          ("brief.mode", "huge"), ("cov.min_cov", "0.6"), ("store.dir", "../x"), ("store.dir", "/abs"),
                          ("focus.core", ["btc"]), ("focus.core", ["BTC", "BTC"]), ("book.bands_pct", [0.5, 0.25]),
                          ("book.bands_pct", [0.3]), ("venues.okx.markets", ["swap"]), ("venues.okx.markets", []),
                          ("schema", "tb.cmkt_cfg/2")):
            self.assertEqual(C.check(with_(d, path, bad)), ["E_CONF:" + path], (path, bad))


class TestBounds(unittest.TestCase):
    def ok(self, path, v):
        self.assertEqual(C.check(with_(C.defaults(), path, v)), [], (path, v))

    def bad(self, path, v, code=None):
        self.assertIn(code or "E_CONF:" + path, C.check(with_(C.defaults(), path, v)), (path, v))

    def test_focus_limits(self):
        self.ok("focus.max_deep", 40)
        self.bad("focus.max_deep", 41)
        self.ok("focus.max_light", 200)
        self.bad("focus.max_light", 201)
        self.bad("focus.deep_top_n", 21, "E_CONF:focus.deep_top_n")       # больше max_deep

    def test_brief_and_tool(self):
        for v in (40, 600):
            self.ok("brief.max_tokens", v)
        for v in (39, 601):
            self.bad("brief.max_tokens", v)
        for v in (1, 3):
            self.ok("brief.syms", v)
        for v in (0, 4):
            self.bad("brief.syms", v)
        self.ok("brief.mode", "full")
        for v in (512, 8192):
            self.ok("tool.max_bytes", v)
        for v in (511, 8193):
            self.bad("tool.max_bytes", v)

    def test_lag_override(self):
        self.ok("sync.lag_max_ms_venue", {"okx": 2000})
        self.bad("sync.lag_max_ms_venue", {"okx": 2001}, "E_CONF:sync.lag_max_ms_venue.okx")
        self.bad("sync.lag_max_ms_venue", {"mexc": 1500}, "E_CONF:sync.lag_max_ms_venue.mexc")    # биржи нет в venues
        self.bad("sync.lag_max_ms", 2001)

    def test_property_22_liveness(self):
        """Свойство 22: silent_s < ping_s + 5 -> E_CONF:sync.silent_s; умолчания (10 и 15 с) проходят."""
        d = C.defaults()
        self.assertEqual((d["sync"]["ping_s"], d["sync"]["silent_s"]), (10, 15))
        self.assertEqual(C.check(d), [])
        self.assertEqual(C.check(with_(d, "sync.silent_s", 14)), ["E_CONF:sync.silent_s"])
        c = with_(with_(d, "sync.ping_s", 20), "sync.silent_s", 25)
        self.assertEqual(C.check(c), [])
        self.assertEqual(C.check(with_(c, "sync.silent_s", 24)), ["E_CONF:sync.silent_s"])

    def test_rest_weight_and_oi_reserve(self):
        d = C.defaults()
        self.assertEqual(C.rest_weight(d, "binance", "perp"), 400)
        self.assertEqual(C.rest_weight(d, "bybit", "spot"), 100)
        self.assertEqual(C.oi_reserve(d, "binance", "perp"), 240)
        self.assertEqual(C.oi_reserve(d, "binance", "spot"), 0)
        self.ok("limits.oi_reserve_per_min", {"binance_perp": 400})               # равен общему весу
        self.assertEqual(C.check(with_(d, "limits.oi_reserve_per_min", {"binance_perp": 401})),
                         ["E_CONF:limits.oi_reserve_per_min.binance_perp"])
        self.assertEqual(C.check(with_(d, "limits.oi_reserve_per_min", {"okx_perp": 10})),
                         ["E_CONF:limits.oi_reserve_per_min.okx_perp"])           # резерв без общего веса рынка
        rw = dict(d["limits"]["rest_weight_per_min"])
        del rw["gate"]
        self.assertEqual(sorted(C.check(with_(d, "limits.rest_weight_per_min", rw))),
                         ["E_CONF:limits.rest_weight_per_min.gate_perp", "E_CONF:limits.rest_weight_per_min.gate_spot"])
        rw["gate"] = 60
        rw["coinbase_perp"] = 10                                                    # у Coinbase нет perp
        self.assertEqual(C.check(with_(d, "limits.rest_weight_per_min", rw)), ["E_CONF:limits.rest_weight_per_min.coinbase_perp"])
        self.bad("limits.rest_weight_per_min", {"binance_perp": 0, "binance_spot": 1, "bybit": 1, "okx": 1, "bitget": 1, "gate": 1,
                                                "coinbase": 1}, "E_CONF:limits.rest_weight_per_min.binance_perp")

    def test_disabled_venue_needs_no_weight(self):
        d = with_(C.defaults(), "venues.gate.enabled", False)
        rw = dict(d["limits"]["rest_weight_per_min"])
        del rw["gate"]
        self.assertEqual(C.check(with_(d, "limits.rest_weight_per_min", rw)), [])
        self.assertFalse(C.venue_enabled(d, "gate"))
        self.assertEqual(len(C.markets(d)), 9)
        self.assertEqual(len(C.markets(d, enabled_only=False)), 11)

    def test_other_cross_checks(self):
        self.bad("book.max_bps", 210, "E_CONF:book.max_bps")                     # не кратно 25
        c = with_(C.defaults(), "book.bands_pct", [0.5, 1, 2])
        self.assertEqual(C.check(c), ["E_CONF:book.imb_band_first_wave"])        # умолчание 0.25 (задача 2) без полосы 0.25
        c = with_(c, "book.imb_band_first_wave", 0.5)
        self.assertEqual(C.check(c), [])
        self.bad("book.wall_min_usd", 50000, "E_CONF:book.wall_min_usd")
        self.bad("limits.backoff_max_s", 0.5)
        c = with_(C.defaults(), "limits.backoff_s", 30)
        self.assertEqual(C.check(with_(c, "limits.backoff_max_s", 20)), ["E_CONF:limits.backoff_max_s"])
        self.bad("budget.cpu_p99_cores", 1.0, "E_CONF:budget.cpu_p99_cores")
        self.bad("budget.light_min", 130, "E_CONF:budget.light_min")
        c = with_(C.defaults(), "store.deep_every_s", 2)
        self.assertEqual(C.check(with_(c, "store.light_every_s", 5)), ["E_CONF:store.light_every_s"])
        self.bad("sync.lead_offset_max_age_s", 300, "E_CONF:sync.lead_offset_max_age_s")
        self.bad("limits.subs_per_conn", {"bitget": 50}, "E_CONF:limits.subs_per_conn.default")
        self.bad("limits.subs_per_conn", {"default": 200, "mexc": 10}, "E_CONF:limits.subs_per_conn.mexc")

    def test_hosts(self):
        d = C.defaults()
        self.bad("venues.okx.hosts", ["ws.okx.com", "open-api.coinglass.com"], "E_CONF:venues.okx.hosts")
        self.bad("venues.okx.hosts", [], "E_CONF:venues.okx.hosts")
        self.bad("venues.okx.hosts", ["ws.okx.com/path"], "E_CONF:venues.okx.hosts")
        self.bad("venues.okx.hosts", ["WS.OKX.COM"], "E_CONF:venues.okx.hosts")
        self.assertTrue(C.host_allowed(d, "okx", "ws.okx.com"))
        self.assertTrue(C.host_allowed(d, "okx", "WS.okx.com:8443"))
        self.assertIsNone(C.check_host(d, "binance", "fstream.binance.com"))
        self.assertEqual(C.check_host(d, "okx", "fstream.binance.com"), C.HOST_DENIED)   # хост другой биржи
        self.assertEqual(C.check_host(d, "okx", "evil.example.com"), "HOST_DENIED")
        self.assertEqual(C.check_host(d, "mexc", "api.mexc.com"), "HOST_DENIED")
        self.assertEqual(C.check_host(d, "okx", None), "HOST_DENIED")

    def test_new_venue_by_config_only(self):
        """Новая биржа (fakex) — запись venues и вес; схема не меняется (Requirement 2.2)."""
        d = C.defaults()
        d["venues"]["fakex"] = {"enabled": True, "markets": ["perp"], "hosts": ["fakex.test.local"]}
        d["limits"]["rest_weight_per_min"]["fakex"] = 10
        self.assertEqual(C.check(d), [])
        self.assertIn(("fakex", "perp"), C.markets(d))
        self.bad("venues", {"Bad_Venue": {"enabled": True, "markets": ["perp"], "hosts": ["a.b.cc"]}}, "E_CONF:venues.Bad_Venue")
        self.bad("venues", {"abcdefghijkl": {"enabled": True, "markets": ["perp"], "hosts": ["a.b.cc"]}}, "E_CONF:venues.abcdefghijkl")

    def test_config_hash(self):
        a = C.defaults()
        b = json.loads(json.dumps(a, sort_keys=False, indent=4))
        self.assertEqual(C.config_hash(a), C.config_hash(b))
        self.assertNotEqual(C.config_hash(a), C.config_hash(with_(a, "brief.max_tokens", 91)))
        self.assertEqual(len(C.config_hash(a)), 64)


class TestNoSecrets(unittest.TestCase):
    def test_module_has_no_key_strings(self):
        src_dir = os.path.join(_bootstrap.SRC, "tb", "cmkt")
        for f in os.listdir(src_dir):
            if f.endswith(".py"):
                with open(os.path.join(src_dir, f), encoding="utf-8") as fh:
                    text = fh.read()
                for bad in ("hmac", "API_KEY", "SECRET", "passphrase", "traider-bot", "'.config'", '".config"'):
                    self.assertNotIn(bad, text, (f, bad))


if __name__ == "__main__":
    unittest.main()
