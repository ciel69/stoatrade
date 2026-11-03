"""Исполнение настроечных действий ctl: llm.provider, brain.profile, brain.role, exchange.set (WP-X). Вызывается из handlers.Ctl.dispatch/requirements.

Защита: те же рамки, что у остальных действий (подпись, срок, nonce, аудит каждой команды без значений). Секретов здесь нет. Смена основного профиля live-роли в режиме live идёт
через tb.brain.swap (гейты, проба, фраза, предупреждения), включение live-биржи требует фразы и nonce, смена активной биржи и режима биржи возможна только при остановленной платформе.
"""
from tb.control import brain_iface

def _out(ctl, cmd, r, extra=None):
    """Результат операции {"ok","code","msg","data"} -> ответ ctl."""
    data = dict(r.get("data") or {})
    if r.get("msg"):
        data["detail"] = r["msg"]
    data.update(extra or {})
    if r["ok"]:
        return ctl.finish(cmd, "done", "OK", data)
    return ctl.finish(cmd, "rejected", r["code"] if r["code"].startswith("E_") else "E_ARGS", data)


NET_MIN_INTERVAL_S = 3.0          # models/ping ходят в сеть: не чаще одного вызова на провайдера за 3 с
PING_TIMEOUT_S = 20.0


def _warn(wid, code, msg, **numbers):
    return {"id": wid, "code": code, "msg": msg, "numbers": numbers}


def _host(url):
    rest = (url or "").split("://", 1)[-1]
    return rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1][:80]


def _provider_requirements(root, args, req):
    """llm.provider: allow_private=true - опасное действие (nonce) с предупреждением; смена base_url/beta_url/ключа у провайдера с ключом - предупреждение
    «ключ уйдёт на новый адрес» (принимается списком accepted, как остальные предупреждения). models/ping по рискам безопасны (частота ограничивается в handle)."""
    from tb.llm import keystore, providers, provstore
    op = args["op"]
    if op not in ("add", "update"):
        return
    try:
        reg = providers.load(root)
    except providers.ProviderError:
        reg = None
    cur = reg.providers.get(args["id"]) if reg else None
    if op == "update":
        patch = args.get("patch") or {}
        new = dict(cur.raw) if cur else {}
        new.update(patch)
        old = {"base_url": cur.base_url if cur else None, "beta_url": cur.beta_url if cur else None, "key_file": cur.key_file if cur else None,
               "key_env": cur.key_env if cur else None, "allow_private": cur.allow_private if cur else False}
    else:
        base = {}
        if args.get("preset"):
            base = dict(provstore.presets(root).get(args["preset"]) or {})
        base.update(args.get("spec") or {})
        new = base
        old = {"base_url": None, "beta_url": None, "key_file": None, "key_env": None, "allow_private": False}
    if new.get("allow_private") is True and not old["allow_private"]:
        req["danger"] = True
        req["warnings"].append(_warn("llm.allow_private", "ALLOW_PRIVATE", "Разрешаются приватные и внутренние адреса (RFC1918, внутренние имена): ключ и запросы могут уйти во внутреннюю сеть. "
                                     "Включайте только для своего сервера. Link-local и метаданные облака остаются запрещены."))
    kf, ke = new.get("key_file"), new.get("key_env")
    changed = [k for k in ("base_url", "beta_url") if (new.get(k) or "").rstrip("/") != (old[k] or "").rstrip("/")]
    if op == "update" and (kf, ke) != (old["key_file"], old["key_env"]):
        changed.append("key")
    if op == "add":         # из шаблона без своего адреса ключ идёт на официальный адрес шаблона: предупреждаем только о собственном base_url
        pre = (provstore.presets(root).get(args.get("preset")) or {}) if args.get("preset") else {}
        changed = ["base_url"] if (args.get("spec") or {}).get("base_url") and (args["spec"]["base_url"] or "").rstrip("/") != (pre.get("base_url") or "").rstrip("/") else []
    if changed and (kf or ke):
        try:
            present = bool(keystore.status(kf, ke)["present"])
        except Exception:
            present = False
        if present:
            req["warnings"].append(_warn("llm.key_new_url", "KEY_NEW_URL", "Ключ провайдера уйдёт на адрес %s. Убедитесь, что это нужный сервис." % _host(new.get("base_url")),
                                         host=_host(new.get("base_url")), changed=changed))


def _net_gate(ctl, pid):
    """-> секунд до следующего разрешённого вызова или 0 (и запоминает время вызова)."""
    now = ctl.now()
    last = getattr(ctl, "_llm_net_last", None)
    if last is None:
        last = ctl._llm_net_last = {}
    prev = last.get(pid)
    if prev is not None and 0 <= now - prev < NET_MIN_INTERVAL_S:
        return NET_MIN_INTERVAL_S - (now - prev)
    last[pid] = now
    if len(last) > 64:
        for k in sorted(last, key=last.get)[:32]:
            last.pop(k, None)
    return 0


def requirements(ctl, action, args):
    """-> {"danger","phrases","gates","warnings","blocked"} или None, если действие не требует подтверждений."""
    req = {"danger": False, "phrases": [], "gates": [], "warnings": [], "blocked": False}
    root, now = ctl.root, ctl.now()
    if action == "llm.provider":
        _provider_requirements(root, args, req)
    elif action == "brain.role" and args["op"] == "chain" and args.get("primary"):
        from tb.brain import swap
        try:
            tier = swap.role_tier(root, args["role"])
        except Exception:
            tier = 0
        if tier >= 1:
            c = brain_iface.classify(root, {"role": args["role"], "profile": args["primary"], "accepted": args.get("accepted", [])}, now, "use")
            if c is not None:
                req["gates"], req["warnings"], req["blocked"], req["danger"] = c["gates"], c["warnings"], c["blocked"], bool(c["danger"])
                req["phrases"] = list(c["phrases"]) + (["я принимаю риск"] if c["warnings"] else [])
    elif action == "exchange.set":
        from tb.core import exchcfg
        op, eid = args["op"], args.get("id")
        ex = (exchcfg.load(root)["exchanges"].get(eid) or {}) if eid else {}
        live = (op == "mode" and args.get("mode") == "live") or (op in ("activate", "enable") and ex.get("mode") == "live")
        if op in ("activate", "mode", "enable", "disable", "allowed") and ctl.platform().get("state") != "stopped":
            req["gates"] = [{"id": "E_STATE", "ok": False, "msg": "биржи меняются только при остановленной платформе"}]
            req["blocked"] = True
        if live:
            req["danger"] = True
            req["phrases"] = ["ВКЛЮЧАЮ LIVE НА БИРЖЕ %s" % str(eid).upper()]
    return req


def handle(ctl, cmd, phrase_ok=False):
    a, args = cmd["action"], cmd["args"]
    root, now = ctl.root, ctl.now()
    op = args["op"]
    if a == "llm.provider":
        from tb.brain import profiles
        from tb.llm import keystore, provstore, providers
        pid = args["id"]
        if op == "add":
            r = provstore.add(root, pid, preset=args.get("preset"), spec=args.get("spec"))
        elif op == "update":
            r = provstore.update(root, pid, args["patch"])
        elif op == "remove":
            r = provstore.remove(root, pid)
        elif op == "enable":
            r = provstore.set_enabled(root, pid, True)
        elif op == "disable":
            use = [row for row in profiles.profile_rows(root)["profiles"] if row["provider"] == pid and profiles.live_roles_using(root, row["id"])]
            if use:
                return _out(ctl, cmd, {"ok": False, "code": "E_LIVE_IN_USE", "msg": "провайдера использует live-роль (профиль %s)" % use[0]["id"], "data": {}})
            r = provstore.set_enabled(root, pid, False)
        elif op in ("models", "ping"):
            wait = _net_gate(ctl, pid)
            if wait:
                return ctl.finish(cmd, "rejected", "E_RATE", {"retry_after_s": round(wait, 1), "detail": "не чаще одного сетевого вызова на провайдера в %d с" % NET_MIN_INTERVAL_S})
            if op == "models":
                r = provstore.list_models(root, pid)
            else:
                r = provstore.ping(root, pid, args.get("model"))
            if r["code"] in ("E_ARGS", "E_NO_KEY"):
                return _out(ctl, cmd, r)
            data = dict(r["data"], ok=bool(r["ok"]))
            if not r["ok"]:
                data["detail"] = data.get("detail") or r["msg"]
            return ctl.finish(cmd, "done", "OK", data)           # отказ провайдера/сети - данные отчёта (ok=false), как у check
        elif op == "model_set":
            r = provstore.model_set(root, pid, args["model"], args["spec"])
        elif op == "model_remove":
            r = provstore.model_remove(root, pid, args["model"])
        elif op == "key_clear":
            reg = None
            try:
                reg = providers.load(root)
            except providers.ProviderError:
                pass
            p = reg.providers.get(pid) if reg else None
            if p is None or not p.key_file:
                r = {"ok": False, "code": "E_ARGS", "msg": "у провайдера нет ключа", "data": {}}
            elif p.enabled and profiles.profile_rows(root) and any(row["provider"] == pid and row["used_by"] for row in profiles.profile_rows(root)["profiles"]):
                r = {"ok": False, "code": "E_IN_USE", "msg": "провайдер используется ролями: сначала выключите его или смените профили", "data": {}}
            else:
                try:
                    st = keystore.clear_key(p.key_file, p.key_env)
                    r = {"ok": True, "code": "OK", "msg": "ключ удалён", "data": {"key": st}}
                except keystore.SecretError as e:
                    r = {"ok": False, "code": "E_ARGS", "msg": str(e), "data": {}}
        else:
            r = provstore.check(root, pid, args.get("model"), live=False)
            if not r["ok"]:      # проверка не «ошибка команды»: отчёт возвращается как данные
                return ctl.finish(cmd, "done", "OK", dict(r["data"], ok=False, detail=r["msg"]))
            return ctl.finish(cmd, "done", "OK", dict(r["data"], ok=True))
        extra = None
        if r["ok"]:
            profiles.signal_reload(root, "ctl", now)
            if op == "update" and "base_url" in (args.get("patch") or {}):
                try:
                    p = providers.load(root).providers.get(pid)
                    if p and (p.key_file or p.key_env) and keystore.status(p.key_file, p.key_env)["present"]:
                        extra = {"send_warning": {"code": "KEY_NEW_URL", "host": _host(p.base_url), "msg": "ключ провайдера теперь уходит на %s" % _host(p.base_url)}}
                except providers.ProviderError:
                    pass
        return _out(ctl, cmd, r, extra)
    if a == "brain.profile":
        from tb.brain import profiles
        r = profiles.profile_set(root, args["id"], args.get("provider"), args.get("model"), args.get("params")) if op == "set" else profiles.profile_delete(root, args["id"])
        if r["ok"]:
            profiles.signal_reload(root, "ctl", now)
        return _out(ctl, cmd, r)
    if a == "brain.role":
        from tb.brain import profiles
        if op == "chain":
            res = brain_iface.swap_action(root, "set_chain", dict(args, role=args["role"]), now, phrase_ok=phrase_ok)
            return _out(ctl, cmd, res)
        r = profiles.role_opts_set(root, args["role"].split(".")[-1], args.get("params"), args.get("budget"), clear=(op == "opts_clear"))
        if r["ok"]:
            profiles.signal_reload(root, "ctl", now)
        return _out(ctl, cmd, r)
    if a == "exchange.set":
        from tb.core import exchcfg
        try:
            if op == "activate":
                exchcfg.set_active(args["id"], root)
            elif op == "enable":
                exchcfg.set_enabled(args["id"], True, root)
            elif op == "disable":
                exchcfg.set_enabled(args["id"], False, root)
            elif op == "mode":
                exchcfg.set_mode(args["id"], args["mode"], root)
            elif op == "allowed":
                exchcfg.set_allowed(args["ids"], root)
            else:
                exchcfg.set_limits(args["id"], args["max_open_positions"], root)
        except exchcfg.ExchangeError as e:
            return ctl.finish(cmd, "rejected", "E_ARGS", {"detail": str(e)})
        return ctl.finish(cmd, "done", "OK", {"exchanges": exchcfg.load(root)["active"], "detail": "сохранено; действует при следующем старте платформы"})
    return ctl.finish(cmd, "rejected", "E_UNKNOWN")
