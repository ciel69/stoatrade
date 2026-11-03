"""WP-X: провайдеры LLM без правки кода (config/local/providers.json), шаблоны, ключи (write-only), профили и роли мозга, переопределения параметров/бюджета роли,
цепочки через подписанные ревизии, запреты для live-ролей. Сети нет (фейковый сервер на loopback), ключи только поддельные, TB_KEYS_DIR временный."""
import json
import os
import shutil
import stat
import time
import unittest

import _bootstrap  # noqa: F401
from _brain import BrainBase, jdump, jload
from fakes.fake_llm_http import FakeLLMServer, R, ds_ok
from tb.brain import conf as conf_mod, profiles, swap
from tb.core import secretfile
from tb.llm import adapters, keys, keystore, provstore, providers
from tb.llm.types import Req, user

FAKE = "sk-FAKEPROVIDER-0123456789abcdefXYZ1d1c"


class Base(BrainBase):
    def setUp(self):
        BrainBase.setUp(self)
        shutil.copy(os.path.join(_bootstrap.ROOT, "config", "provider_presets.json"), self.p("config", "provider_presets.json"))

    def local(self, name="providers"):
        return jload(self.p("config", "local", name + ".json"))


class TestPresets(Base):
    def test_all_presets_valid_unverified_and_marked(self):
        pr = provstore.presets(self.root)
        for need in ("openai", "gemini", "openrouter", "anthropic", "deepseek", "local"):
            self.assertIn(need, pr)
        for pid, t in pr.items():
            self.assertIs(t.get("verified"), False, pid)
            self.assertIn("НЕ ПРОВЕРЕНО", t.get("note", ""), pid)
            _models, err = providers._check_provider(pid, dict(t, enabled=False))
            self.assertIsNone(err, (pid, err))

    def test_add_from_preset_is_disabled_and_local_layer_only(self):
        r = provstore.add(self.root, "openai", preset="openai")
        self.assertTrue(r["ok"], r)
        loc = self.local()
        self.assertFalse(loc["providers"]["openai"]["enabled"])
        shipped = jload(self.p("config", "providers.json"))
        self.assertNotIn("openai", shipped["providers"])                 # поставляемый файл не тронут
        reg = providers.load(self.root)
        self.assertIn("openai", reg.providers)
        self.assertFalse(reg.providers["openai"].enabled)
        self.assertFalse(reg.providers["openai"].verified)
        self.assertEqual(reg.providers["openai"].adapter, "openai-compatible")      # openai_compat приведён к внутреннему имени

    def test_duplicate_and_bad_ids_rejected(self):
        self.assertFalse(provstore.add(self.root, "deepseek", preset="deepseek")["ok"])
        self.assertFalse(provstore.add(self.root, "Bad Id", preset="openai")["ok"])
        self.assertFalse(provstore.add(self.root, "x1", preset="nope")["ok"])
        self.assertFalse(provstore.add(self.root, "x2")["ok"])

    def test_invalid_spec_not_written(self):
        r = provstore.add(self.root, "bad", spec={"adapter": "nonexistent", "base_url": "https://x.example/v1", "models": {"m": {"name": "m1", "price": {"in": 1, "out": 1}}}})
        self.assertFalse(r["ok"])
        self.assertFalse(os.path.exists(self.p("config", "local", "providers.json")))

    def test_gemini_and_local_use_openai_compat_through_config_only(self):
        provstore.add(self.root, "gem", preset="gemini")
        provstore.add(self.root, "lms", preset="lmstudio")
        reg = providers.load(self.root)
        g = reg.providers["gem"]
        ad = adapters.make(g, g.models["flash"], transport=object())
        url, _h, body = ad.build(Req(system="s", messages=[user("hi")], max_tokens=100, temperature=0.2))
        self.assertTrue(url.endswith("/v1beta/openai/chat/completions"), url)
        self.assertEqual(body["model"], g.models["flash"].name)
        self.assertIn("max_tokens", body)
        # провайдер без ключа (локальный сервер): заголовка Authorization нет
        lp = reg.providers["lms"]
        self.assertEqual(adapters.make(lp, lp.models["main"], transport=object()).auth_headers(None), {})

    def test_openai_preset_uses_max_completion_tokens(self):
        provstore.add(self.root, "oai", preset="openai")
        p = providers.load(self.root).providers["oai"]
        _u, _h, body = adapters.make(p, p.models["mini"], transport=object()).build(Req(system="s", messages=[user("hi")], max_tokens=100, temperature=0.2))
        self.assertIn("max_completion_tokens", body)
        self.assertNotIn("max_tokens", body)


class TestEnableAndKeys(Base):
    def test_enable_requires_key_then_ok(self):
        provstore.add(self.root, "openai", preset="openai")
        r = provstore.set_enabled(self.root, "openai", True)
        self.assertFalse(r["ok"])
        self.assertEqual(r["code"], "E_NO_KEY")
        st = keystore.set_key("openai.env", "OPENAI_API_KEY", FAKE)
        self.assertTrue(st["present"])
        self.assertEqual(st["mask"], "…1d1c")
        self.assertNotIn(FAKE, json.dumps(st))
        self.assertTrue(provstore.set_enabled(self.root, "openai", True)["ok"])
        self.assertTrue(providers.load(self.root).providers["openai"].enabled)
        self.assertTrue(provstore.set_enabled(self.root, "openai", False)["ok"])

    def test_key_file_mode_and_format(self):
        keystore.set_key("openai.env", "OPENAI_API_KEY", FAKE)
        path = os.path.join(self.keys, "openai.env")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "OPENAI_API_KEY=%s\n" % FAKE)
        self.assertEqual(keys.load("openai.env", "OPENAI_API_KEY").reveal(), FAKE)       # читает ровно тот же keys.py, что и мозг
        self.assertEqual(keystore.status("openai.env", "OPENAI_API_KEY")["mask"], "…1d1c")
        keystore.clear_key("openai.env", "OPENAI_API_KEY")
        self.assertFalse(keystore.status("openai.env", "OPENAI_API_KEY")["present"])

    def test_exchange_key_names_cannot_be_used(self):
        for kf, ke in (("bybit_mainnet_trade.env", "BYBIT_API_KEY"), ("control.json", "X"), ("okx.env", "OKX_API_KEY"), ("ok.env", "BYBIT_API_SECRET"), ("../x.env", "ABC_KEY"),
                       ("a b.env", "ABC_KEY")):
            with self.assertRaises(secretfile.SecretError, msg=(kf, ke)):
                keystore.set_key(kf, ke, FAKE)
        # провайдер не может указать на торговый ключ: иначе он ушёл бы на чужой base_url
        r = provstore.add(self.root, "evil", spec={"adapter": "openai_compat", "base_url": "https://x.example/v1", "key_file": "bybit_mainnet_trade.env", "key_env": "BYBIT_API_SECRET",
                                                    "models": {"m": {"name": "m1", "price": {"in": 1, "out": 1}}}})
        self.assertFalse(r["ok"])
        self.assertEqual(os.listdir(self.keys).count("bybit_mainnet_trade.env"), 0)

    def test_bad_values_rejected_without_echo(self):
        for bad in ("short", "has space in it 123456", "line\nbreak123456789", "ключ-кириллица-1234567", "x" * 500):
            with self.assertRaises(secretfile.SecretError) as cm:
                keystore.set_key("openai.env", "OPENAI_API_KEY", bad)
            self.assertNotIn(bad.strip()[:8] if len(bad) > 8 else "###", str(cm.exception).replace("печатных", ""))
        self.assertFalse(os.path.exists(os.path.join(self.keys, "openai.env")))


class TestModelsAndCheck(Base):
    def setUp(self):
        Base.setUp(self)
        provstore.add(self.root, "openai", preset="openai")

    def test_model_set_and_remove_and_profile_guard(self):
        r = provstore.model_set(self.root, "openai", "nano", {"name": "tiny-model-1", "tools": True, "price": {"in": 0.1, "out": 0.4}})
        self.assertTrue(r["ok"], r)
        self.assertIn("nano", providers.load(self.root).providers["openai"].models)
        self.assertFalse(provstore.model_set(self.root, "openai", "bad", {"name": "x!", "price": {"in": 1, "out": 1}})["ok"])
        keystore.set_key("openai.env", "OPENAI_API_KEY", FAKE)
        provstore.set_enabled(self.root, "openai", True)
        self.assertTrue(profiles.profile_set(self.root, "oai-nano", "openai", "nano")["ok"])
        r = provstore.model_remove(self.root, "openai", "nano")
        self.assertEqual((r["ok"], r["code"]), (False, "E_IN_USE"))
        self.assertEqual((provstore.remove(self.root, "openai")["code"]), "E_IN_USE")
        self.assertTrue(profiles.profile_delete(self.root, "oai-nano")["ok"])
        self.assertTrue(provstore.model_remove(self.root, "openai", "nano")["ok"])
        self.assertTrue(provstore.remove(self.root, "openai")["ok"])
        self.assertNotIn("openai", providers.load(self.root).providers)

    def test_shipped_provider_cannot_be_removed_only_disabled(self):
        self.assertEqual(provstore.remove(self.root, "deepseek")["code"], "E_STATE")
        self.assertTrue(provstore.set_enabled(self.root, "anthropic", False)["ok"])

    def test_check_offline_and_live_with_fake_server(self):
        r = provstore.check(self.root, "openai")
        self.assertFalse(r["ok"])
        self.assertFalse(r["data"]["checked"][0]["key"]["present"])
        self.assertNotIn("live", r["data"]["checked"][0])
        srv = FakeLLMServer().start()
        self.addCleanup(srv.stop)
        provstore.add(self.root, "loc", spec={"adapter": "openai_compat", "base_url": srv.url + "/v1", "timeouts": {"connect_s": 2, "read_s": 2, "total_s": 5},
                                              "retry": {"max": 0, "base_s": 1, "max_s": 2, "on": [500]},
                                              "models": {"m": {"name": "m-live", "tools": True, "json_mode": True, "price": {"in": 0, "out": 0}}}})
        srv.push("/v1/models", R(200, {"data": [{"id": "m-live"}]}))
        srv.push("/v1/chat/completions", R(200, ds_ok('{"ok":true}')))
        provstore.set_enabled(self.root, "loc", True)
        rr = provstore.check(self.root, "loc", live=True)
        self.assertTrue(rr["ok"], rr)
        live = rr["data"]["checked"][0]["live"]
        self.assertTrue(live["model_name_in_list"])
        self.assertEqual(live["ping"]["finish"], "stop")

    def test_update_validates_and_keeps_local_layer(self):
        self.assertTrue(provstore.update(self.root, "openai", {"limits": {"day_usd": 1.5}, "note": "моя заметка"})["ok"])
        self.assertEqual(providers.load(self.root).providers["openai"].limits["day_usd"], 1.5)
        self.assertFalse(provstore.update(self.root, "openai", {"base_url": "ftp://x"})["ok"])
        self.assertFalse(provstore.update(self.root, "openai", {"enabled": True})["ok"])          # enabled только через set_enabled


class TestProfilesAndRoles(Base):
    def setUp(self):
        Base.setUp(self)
        provstore.add(self.root, "openai", preset="openai")
        keystore.set_key("openai.env", "OPENAI_API_KEY", FAKE)
        provstore.set_enabled(self.root, "openai", True)

    def test_profile_create_update_delete(self):
        r = profiles.profile_set(self.root, "oai-mini", "openai", "mini", {"temperature": 0.3, "max_tokens": 1200, "thinking": "off"})
        self.assertTrue(r["ok"], r)
        self.assertFalse(r["data"]["live_ok"])
        c = conf_mod.load(self.root)
        self.assertEqual(c.profiles["oai-mini"].params["max_tokens"], 1200)
        self.assertFalse(profiles.profile_set(self.root, "bad", "openai", "nope")["ok"])
        self.assertFalse(profiles.profile_set(self.root, "bad", "nope", "x")["ok"])
        self.assertFalse(profiles.profile_set(self.root, "oai-mini", params={"temperature": 9})["ok"])
        self.assertTrue(profiles.profile_set(self.root, "oai-mini", params={"temperature": 0.5})["ok"])
        self.assertEqual(conf_mod.load(self.root).profiles["oai-mini"].params["temperature"], 0.5)
        self.assertTrue(profiles.profile_delete(self.root, "oai-mini")["ok"])
        self.assertFalse(profiles.profile_delete(self.root, "ds-pro")["ok"])           # поставляемый профиль

    def test_profile_requires_enabled_provider(self):
        provstore.set_enabled(self.root, "openai", False)
        r = profiles.profile_set(self.root, "oai-mini", "openai", "mini")
        self.assertEqual(r["code"], "E_PROVIDER_OFF")

    def test_changing_model_resets_live_ok_label(self):
        profiles.profile_set(self.root, "oai-mini", "openai", "mini")
        self.assertFalse(conf_mod.load(self.root).profiles["oai-mini"].live_ok)
        loc = self.local("brain")
        self.assertIs(loc["profiles"]["oai-mini"]["live_ok"], False)

    def test_chain_edit_signed_revision_and_rollback(self):
        profiles.profile_set(self.root, "oai-mini", "openai", "mini")
        r = swap.set_chain(self.root, {"role": "shared.news_reader", "primary": "oai-mini", "fallback": ["ds-flash", "ds-pro"]}, self.t, by="test")
        self.assertTrue(r["ok"], r)
        o, st = swap.read_active(self.root)
        self.assertEqual(st, "ok")
        self.assertEqual(o["shared_roles"]["news_reader"], {"primary": "oai-mini", "fallback": ["ds-flash", "ds-pro"]})
        self.assertTrue(swap.verify_swaps(self.root)["ok"])
        r2 = swap.set_chain(self.root, {"role": "shared.news_reader", "fallback": ["ds-pro", "ds-flash"]}, self.t + 1, by="test")      # порядок запасных
        self.assertTrue(r2["ok"])
        self.assertEqual(swap.read_active(self.root)[0]["shared_roles"]["news_reader"]["fallback"], ["ds-pro", "ds-flash"])
        rb = swap.rollback(self.root, {}, self.t + 2, by="test")
        self.assertTrue(rb["ok"], rb)
        self.assertEqual(swap.read_active(self.root)[0]["shared_roles"]["news_reader"]["fallback"], ["ds-flash", "ds-pro"])
        bad = swap.set_chain(self.root, {"role": "shared.news_reader", "primary": "oai-mini", "fallback": ["oai-mini"]}, self.t, by="test")
        self.assertEqual(bad["code"], "E_SWAP_PROFILE")
        self.assertEqual(swap.set_chain(self.root, {"role": "shared.news_reader", "primary": "ghost"}, self.t, by="test")["code"], "E_SWAP_PROFILE")

    def test_delete_profile_in_chain_forbidden_and_use_all_one_revision(self):
        profiles.profile_set(self.root, "oai-mini", "openai", "mini")
        r = swap.use(self.root, {"all": True, "profile": "oai-mini"}, self.t, by="test")
        self.assertTrue(r["ok"], r)
        a = swap.load_effective(self.root)
        self.assertEqual(a.rev, r["data"]["rev"])
        self.assertTrue(all(ch["primary"] == "oai-mini" for t in a.teams.values() for ch in t["roles"].values() if t is a.teams[a.live_team]))
        self.assertTrue(all(ch["primary"] == "oai-mini" for ch in a.shared_roles.values()))
        d = profiles.profile_delete(self.root, "oai-mini")
        self.assertIn(d["code"], ("E_IN_USE", "E_LIVE_IN_USE"))
        self.assertFalse(d["ok"])

    def test_live_role_gates(self):
        profiles.profile_set(self.root, "oai-mini", "openai", "mini")
        self.set_platform("live")
        for p in conf_mod.load(self.root).profiles.values():
            swap.record_probe(self.root, p, True, self.t)
        # профиль live-роли в live нельзя ни менять, ни удалять, ни переопределять параметры роли
        r = profiles.profile_set(self.root, "ds-pro", params={"temperature": 0.9})
        self.assertEqual(r["code"], "E_LIVE_IN_USE")
        self.assertEqual(profiles.role_opts_set(self.root, "position_manager", params={"temperature": 0.9})["code"], "E_LIVE_IN_USE")
        # запасные live-роли в live не меняются
        r = swap.set_chain(self.root, {"role": "position_manager", "fallback": ["ds-flash"]}, self.t, by="test")
        self.assertEqual(r["code"], "E_STATE")
        # смена основного без фразы отклонена гейтом use
        r = swap.set_chain(self.root, {"role": "position_manager", "primary": "ds-flash"}, self.t, by="test")
        self.assertFalse(r["ok"])
        self.assertIn(r["code"], ("E_SWAP_PHRASE", "E_SWAP_NO_LIVE_RIGHTS", "E_SWAP_AMBIGUOUS_STATE", "E_SWAP_PROBE", "E_SWAP_LIMIT_DAY"))
        # не live-роль в live меняется сразу
        self.assertTrue(swap.set_chain(self.root, {"role": "shared.news_reader", "fallback": ["ds-pro"]}, self.t, by="test")["ok"])
        # провайдера live-роли выключить нельзя (проверяет ctl через live_roles_using)
        self.assertTrue(profiles.live_roles_using(self.root, "ds-pro"))

    def test_role_opts_params_budget_and_effective_table(self):
        r = profiles.role_opts_set(self.root, "news_reader", params={"temperature": 0.7, "max_tokens": 900}, budget={"day_usd": 0.25})
        self.assertTrue(r["ok"], r)
        row = [x for x in profiles.role_rows(self.root, self.t)["roles"] if x["role"] == "news_reader"][0]
        self.assertEqual(row["params_override"]["temperature"], 0.7)
        self.assertEqual(row["params_effective"]["temperature"], 0.7)
        self.assertEqual(row["budget"]["day_usd"], 0.25)
        self.assertEqual(row["today"]["budget_left_usd"], 0.25)
        self.assertEqual(row["primary"]["id"], "ds-flash")
        self.assertFalse(profiles.role_opts_set(self.root, "news_reader", params={"temperature": 5})["ok"])
        self.assertFalse(profiles.role_opts_set(self.root, "ghost", params={"temperature": 1})["ok"])
        self.assertTrue(profiles.role_opts_set(self.root, "news_reader", clear=True)["ok"])
        self.assertNotIn("news_reader", conf_mod.load(self.root).role_opts)

    def test_cleared_budget_overrides_shipped_default(self):
        """Очищенное поле «Бюджет в день» снимает лимит и тогда, когда значение лежит в поставляемом config/brain.json (entry_scout: 0.5)."""
        if "day_usd" not in ((conf_mod.load(self.root).role_opts.get("entry_scout") or {}).get("budget") or {}):
            self.skipTest("в тестовом корне нет базового бюджета entry_scout")
        self.assertTrue(profiles.role_opts_set(self.root, "entry_scout", budget={"day_usd": None})["ok"])
        self.assertNotIn("day_usd", conf_mod.load(self.root).role_opts["entry_scout"]["budget"])
        self.assertTrue(profiles.role_opts_set(self.root, "entry_scout", params={"temperature": 0.3})["ok"])    # другая правка не возвращает лимит
        c = conf_mod.load(self.root).role_opts["entry_scout"]
        self.assertNotIn("day_usd", c["budget"])
        self.assertEqual(c["params"]["temperature"], 0.3)
        self.assertTrue(profiles.role_opts_set(self.root, "entry_scout", budget={"day_usd": 0.4})["ok"])
        self.assertEqual(conf_mod.load(self.root).role_opts["entry_scout"]["budget"]["day_usd"], 0.4)

    def test_runner_applies_role_override_and_budget(self):
        profiles.role_opts_set(self.root, "news_reader", params={"temperature": 0.77, "max_tokens": 321})
        c = conf_mod.load(self.root)
        self.assertEqual(c.role_opts["news_reader"]["params"]["temperature"], 0.77)
        self.assertEqual(c.role_opts["news_reader"]["params"]["max_tokens"], 321)


class TestCli(Base):
    def run_cli(self, mod, argv):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = mod.main(argv)
        return rc, buf.getvalue()

    def test_llm_cli_manage_and_audit_chain(self):
        from tb.control import audit
        from tb.llm import cli as lcli
        rc, out = self.run_cli(lcli, ["presets"])
        self.assertEqual(rc, 0)
        self.assertIn("openrouter", out)
        rc, out = self.run_cli(lcli, ["provider", "add", "gem", "--preset", "gemini"])
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli(lcli, ["provider", "enable", "gem"])
        self.assertEqual(rc, 1)                                                  # без ключа нельзя
        keystore.set_key("gemini.env", "GEMINI_API_KEY", FAKE)
        rc, out = self.run_cli(lcli, ["key", "status", "gem"])
        self.assertEqual(rc, 0)
        self.assertIn("…1d1c", out)
        self.assertNotIn(FAKE, out)
        rc, out = self.run_cli(lcli, ["provider", "enable", "gem"])
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli(lcli, ["model", "set", "gem", "lite", "--name", "tiny-1", "--price-in", "0.1", "--price-out", "0.2", "--tools"])
        self.assertEqual(rc, 0, out)
        self.assertIn("lite", providers.load(self.root).providers["gem"].models)
        rc, out = self.run_cli(lcli, ["key", "clear", "gem"])
        self.assertEqual(rc, 0)
        self.assertFalse(keystore.status("gemini.env", "GEMINI_API_KEY")["present"])
        rows = audit.tail(self.root, 50)
        self.assertGreaterEqual(len([r for r in rows if r["src"] == "cli"]), 5)
        self.assertTrue(audit.verify(self.root)["ok"])
        self.assertNotIn(FAKE, json.dumps(rows))

    def test_key_set_needs_tty_and_never_reads_argv(self):
        from tb.llm import cli as lcli
        provstore.add(self.root, "gem", preset="gemini")
        rc, _out = self.run_cli(lcli, ["key", "set", "gem"])          # в тесте нет TTY: ключ из аргументов не принимается вообще
        self.assertEqual(rc, 2)
        self.assertFalse(keystore.status("gemini.env", "GEMINI_API_KEY")["present"])
        self.assertEqual(self.run_cli(lcli, ["key", "set", "gem", FAKE])[0], 2)          # лишний аргумент отклонён парсером

    def test_brain_cli_profile_role(self):
        from tb.brain import brain_cli
        provstore.add(self.root, "gem", preset="gemini")
        keystore.set_key("gemini.env", "GEMINI_API_KEY", FAKE)
        provstore.set_enabled(self.root, "gem", True)
        rc, out = self.run_cli(brain_cli, ["profile", "set", "gem-flash", "--provider", "gem", "--model", "flash", "--set", "temperature=0.4"])
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli(brain_cli, ["role", "chain", "shared.reviewer", "--primary", "gem-flash", "--fallback", "ds-flash"])
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli(brain_cli, ["role", "budget", "reviewer", "--day-usd", "0.4"])
        self.assertEqual(rc, 0, out)
        rc, out = self.run_cli(brain_cli, ["role", "list"])
        row = [r for r in json.loads(out)["roles"] if r["role"] == "reviewer"][0]
        self.assertEqual((row["primary"]["id"], row["budget"]["day_usd"]), ("gem-flash", 0.4))
        rc, out = self.run_cli(brain_cli, ["use", "all", "ds-flash"])
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.run_cli(brain_cli, ["profile", "delete", "gem-flash"])[0], 0)           # из цепочек уже убран (use all)


class TestKeyNeverLeaks(Base):
    def test_key_not_in_ctl_audit_queue_journal_or_responses(self):
        """Ключ пишется только в файл ключа: нигде больше (аудит ctl, очередь, ответы, журнал смен, локальные конфиги, status)."""
        provstore.add(self.root, "openai", preset="openai")
        secretfile_path = os.path.join(self.keys, "openai.env")
        st = keystore.set_key("openai.env", "OPENAI_API_KEY", FAKE)
        provstore.set_enabled(self.root, "openai", True)
        listing = json.dumps(provstore.listing(self.root), ensure_ascii=False)
        self.assertNotIn(FAKE, listing)
        self.assertIn("…1d1c", listing)
        self.assertNotIn(FAKE, json.dumps(st))
        offenders = []
        for d, _dirs, files in os.walk(self.root):
            for f in files:
                try:
                    with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                        if FAKE in fh.read():
                            offenders.append(os.path.relpath(os.path.join(d, f), self.root))
                except OSError:
                    pass
        self.assertEqual(offenders, [])
        with open(secretfile_path, encoding="utf-8") as f:
            self.assertIn(FAKE, f.read())


class TestModelsAndPing(Base):
    """provstore.list_models / ping на фейковом сервере: форматы, лимит, утечка ключа, редиректы."""

    def setUp(self):
        Base.setUp(self)
        self.srv = FakeLLMServer().start()
        self.addCleanup(self.srv.stop)

    def add(self, pid="loc", adapter="openai_compat", **extra):
        spec = {"adapter": adapter, "base_url": self.srv.url + ("" if adapter == "anthropic" else "/v1"), "key_file": pid + ".env", "key_env": pid.upper() + "_KEY",
                "timeouts": {"connect_s": 2, "read_s": 2, "total_s": 5}, "retry": {"max": 0, "base_s": 1, "max_s": 2, "on": [500]},
                "models": {"m": {"name": "m-live", "price": {"in": 0, "out": 0}}}}
        spec.update(extra)
        self.assertTrue(provstore.add(self.root, pid, spec=spec)["ok"])
        keystore.set_key(pid + ".env", pid.upper() + "_KEY", FAKE)

    def test_openai_format_sorted_and_key_stays_server_side(self):
        self.add()
        self.srv.push("/v1/models", R(200, {"object": "list", "data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "a-model"}, {"id": "bad id!"}, {"nope": 1}]}))
        r = provstore.list_models(self.root, "loc")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["data"], {"ids": ["a-model", "z-model"], "count": 2, "truncated": False, "source": "models"})
        self.assertEqual(self.srv.requests[-1]["headers"]["authorization"], "Bearer " + FAKE)       # ключ ушёл провайдеру, но не в ответ
        self.assertNotIn(FAKE, json.dumps(r))

    def test_anthropic_format_and_alt_shapes(self):
        self.add("an", adapter="anthropic", version_header="2023-06-01")
        self.srv.push("/v1/models?limit=1000", R(200, {"data": [{"id": "claude-x", "type": "model"}, {"id": "claude-a"}], "has_more": False}))
        r = provstore.list_models(self.root, "an")
        self.assertEqual(r["data"]["ids"], ["claude-a", "claude-x"])
        self.assertEqual(self.srv.requests[-1]["headers"]["x-api-key"], FAKE)
        self.add("alt")
        self.srv.push("/v1/models", R(200, {"models": [{"name": "models/gem-1"}, {"id": "gem-2"}]}))
        self.assertEqual(provstore.list_models(self.root, "alt")["data"]["ids"], ["gem-2", "models/gem-1"])

    def test_limit_500_truncated(self):
        self.add()
        self.srv.push("/v1/models", R(200, {"data": [{"id": "m%04d" % i} for i in range(700)]}))
        d = provstore.list_models(self.root, "loc")["data"]
        self.assertEqual((len(d["ids"]), d["count"], d["truncated"]), (500, 700, True))
        self.assertEqual(d["ids"], sorted(d["ids"]))

    def test_401_does_not_leak_key(self):
        self.add()
        self.srv.push("/v1/models", R(401, {"error": {"message": "Incorrect API key provided: %s" % FAKE}}))
        r = provstore.list_models(self.root, "loc")
        self.assertFalse(r["ok"])
        self.assertEqual((r["code"], r["data"]["error"]), ("E_PROVIDER", "auth"))
        self.assertNotIn(FAKE, json.dumps(r, ensure_ascii=False))
        self.assertNotIn("0123456789abcdef", json.dumps(r))

    def test_no_key_and_unknown(self):
        self.add()
        os.remove(os.path.join(self.keys, "loc.env"))
        self.assertEqual(provstore.list_models(self.root, "loc")["code"], "E_NO_KEY")
        self.assertEqual(provstore.list_models(self.root, "nope")["code"], "E_ARGS")
        self.assertEqual(self.srv.count(), 0)

    def test_redirect_is_not_followed(self):
        self.add()
        self.srv.push("/v1/models", R(302, headers={"Location": self.srv.url + "/other"}))
        r = provstore.list_models(self.root, "loc")
        self.assertFalse(r["ok"])
        self.assertEqual(r["data"]["error"], "redirect")
        self.assertEqual(self.srv.count("/other"), 0)

    def test_oversize_and_garbage_body(self):
        self.add()
        self.srv.push("/v1/models", R(200, raw=b"x" * (1024 * 1024 + 10)))
        self.assertEqual(provstore.list_models(self.root, "loc")["data"]["error"], "bad_body")
        self.srv.push("/v1/models", R(200, raw=b"<html>"))
        self.assertEqual(provstore.list_models(self.root, "loc")["data"]["error"], "bad_body")

    def test_ping_ok_and_error(self):
        self.add()
        self.srv.push("/v1/chat/completions", R(200, ds_ok("pong")))
        r = provstore.ping(self.root, "loc")
        self.assertTrue(r["ok"], r)
        d = r["data"]
        self.assertEqual((d["ok"], d["model"], d["paid"], d["text"]), (True, "m", True, "pong"))
        self.assertIn("ms", d)
        self.assertEqual(self.srv.requests[-1]["json"]["max_tokens"], 16)
        self.srv.push("/v1/chat/completions", R(200, ds_ok("x" * 200)))
        self.assertNotIn("text", provstore.ping(self.root, "loc", "m")["data"])           # длинный ответ не возвращается
        self.srv.push("/v1/chat/completions", R(401, {"error": {"message": "key " + FAKE}}))
        e = provstore.ping(self.root, "loc")
        self.assertFalse(e["ok"])
        self.assertEqual(e["data"]["error"], "auth")
        self.assertNotIn(FAKE, json.dumps(e, ensure_ascii=False))
        self.assertEqual(provstore.ping(self.root, "loc", "nomodel")["code"], "E_ARGS")


class TestBaseUrlPolicy(Base):
    """Валидация base_url (netpolicy) и проверка адресов после резолва в транспорте."""

    def test_static_rules(self):
        from tb.llm import netpolicy as N
        ok = ["https://api.openai.com/v1", "http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://[::1]:8080/v1", "http://127.9.9.9/v1", "https://x.example/v1/"]
        for u in ok:
            self.assertIsNone(N.check_url(u), u)
        bad = ["http://169.254.169.254/latest", "https://169.254.1.1/v1", "http://[fe80::1]/v1", "https://metadata.google.internal/v1", "http://0.0.0.0/v1", "http://[::]/v1",
               "https://user:pass@api.openai.com/v1", "https://user@api.openai.com/v1", "https://api.openai.com/v1?k=1", "https://api.openai.com/v1#x", "https://api.openai.com/v1?",
               "http://api.openai.com/v1", "ftp://x.example/v1", "https:///v1", "http://100.100.100.200/v1", "http://[::ffff:169.254.169.254]/v1", "https://a b.example/v1", ""]
        for u in bad:
            self.assertIsNotNone(N.check_url(u), u)
        for u in ("http://10.0.0.5:8000/v1", "http://192.168.1.2/v1", "http://172.16.0.9/v1", "http://[fd12::1]/v1", "https://llm.internal/v1", "http://gpu-box:8000/v1", "https://llm.corp/v1"):
            self.assertIsNotNone(N.check_url(u), u)               # без allow_private нельзя
            self.assertIsNone(N.check_url(u, True), u)            # с флагом можно (в том числе по http)
        for u in ("http://169.254.169.254/x", "http://[fe80::1]/v1", "http://0.0.0.0/v1", "http://metadata.google.internal/x", "http://100.64.0.1/v1"):
            self.assertIsNotNone(N.check_url(u, True), u)         # метаданные и link-local не открывает даже флаг

    def test_registry_rejects_and_accepts(self):
        spec = lambda url, **kw: dict({"adapter": "openai_compat", "base_url": url, "models": {"m": {"name": "m1", "price": {"in": 1, "out": 1}}}}, **kw)
        self.assertFalse(provstore.add(self.root, "p1", spec=spec("http://169.254.169.254/v1"))["ok"])
        self.assertFalse(provstore.add(self.root, "p2", spec=spec("https://u:p@x.example/v1"))["ok"])
        self.assertFalse(provstore.add(self.root, "p3", spec=spec("http://192.168.0.7:8000/v1"))["ok"])
        self.assertTrue(provstore.add(self.root, "p4", spec=spec("http://192.168.0.7:8000/v1", allow_private=True))["ok"])
        self.assertTrue(providers.load(self.root).providers["p4"].allow_private)
        self.assertFalse(provstore.add(self.root, "p5", spec=spec("http://169.254.169.254/v1", allow_private=True))["ok"])
        self.assertFalse(provstore.add(self.root, "p6", spec=spec("https://x.example/v1", allow_private="yes"))["ok"])
        self.assertTrue(provstore.add(self.root, "p7", preset="local")["ok"])           # пресеты loopback проходят
        self.assertTrue(provstore.add(self.root, "p8", preset="lmstudio")["ok"])
        self.assertFalse(provstore.update(self.root, "p4", {"base_url": "https://x.example/v1?a=1"})["ok"])
        self.assertFalse(provstore.update(self.root, "p7", {"beta_url": "http://169.254.169.254/b"})["ok"])
        self.assertFalse(provstore.update(self.root, "p4", {"allow_private": False})["ok"])       # с выключенным флагом приватный адрес уже невалиден
        self.assertEqual(providers.load(self.root).errors, [])
        self.assertEqual(providers.load(self.root).providers["deepseek"].error, None)

    def test_resolved_address_checked_per_connection(self):
        """DNS rebinding: имя публичное, а резолвится во внутренний/служебный адрес -> соединения нет. Резолвер фейковый."""
        from tb.llm.transport import Transport
        from tb.llm.types import LLMError
        srv = FakeLLMServer().start()
        self.addCleanup(srv.stop)
        port = srv.httpd.server_address[1]
        url = "http://rebind.example:%d/v1/models" % port
        srv.default["/v1/models"] = R(200, {"data": [{"id": "x"}]})

        def call(resolver, allow_private=False):
            tr = Transport(resolver=resolver, keepalive=False)
            return tr.request("GET", url, {}, None, {"connect_s": 2, "read_s": 2, "total_s": 5}, {"max": 0}, allow_private=allow_private)

        for ip in ("169.254.169.254", "0.0.0.0", "::", "fe80::1", "100.100.100.200"):
            for ap in (False, True):
                with self.assertRaises(LLMError, msg=ip) as cm:
                    call(lambda h, p, ip=ip: [ip], ap)
                self.assertEqual(cm.exception.code, "config")
        with self.assertRaises(LLMError):
            call(lambda h, p: ["10.1.2.3"])                                    # приватный без allow_private
        with self.assertRaises(LLMError):
            call(lambda h, p: ["127.0.0.1"])                                   # публичное имя на loopback без allow_private
        with self.assertRaises(LLMError):
            call(lambda h, p: ["93.184.216.34", "127.0.0.1"])                  # хотя бы один плохой адрес из набора - отказ
        with self.assertRaises(LLMError):
            call(lambda h, p: ["93.184.216.34"])                               # публичный адрес по http - отказ
        self.assertEqual(srv.count(), 0)                                       # до сервера ни один запрос не дошёл
        self.assertEqual(call(lambda h, p: ["127.0.0.1"], True).status, 200)   # с allow_private: можно
        seq = iter([["127.0.0.1"], ["169.254.169.254"]])                       # «ребиндинг»: первый резолв безопасный, второй - метаданные
        res = lambda h, p: next(seq)
        tr = Transport(resolver=res, keepalive=False)
        h = ({"connect_s": 2, "read_s": 2, "total_s": 5}, {"max": 0})
        self.assertEqual(tr.request("GET", url, {}, None, *h, allow_private=True).status, 200)
        with self.assertRaises(LLMError):
            tr.request("GET", url, {}, None, *h, allow_private=True)
        self.assertEqual(srv.count(), 2)          # дошли только два безопасных запроса (один выше, один здесь)

    def test_loopback_names_must_resolve_to_loopback(self):
        from tb.llm.transport import Transport
        from tb.llm.types import LLMError
        srv = FakeLLMServer().start()
        self.addCleanup(srv.stop)
        srv.default["/x"] = R(200, {})
        url = "http://localhost:%d/x" % srv.httpd.server_address[1]
        tr = Transport(resolver=lambda h, p: ["127.0.0.1"], keepalive=False)
        self.assertEqual(tr.request("GET", url, {}, None, {"connect_s": 2, "read_s": 2, "total_s": 5}, {"max": 0}).status, 200)
        tr = Transport(resolver=lambda h, p: ["10.0.0.1"], keepalive=False)
        with self.assertRaises(LLMError):
            tr.request("GET", url, {}, None, {"connect_s": 2, "read_s": 2, "total_s": 5}, {"max": 0}, allow_private=True)


if __name__ == "__main__":
    unittest.main()
