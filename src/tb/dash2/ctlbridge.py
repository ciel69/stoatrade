"""Мост команд управления для нового дашборда: старт и остановка торгового дня по WebSocket. Владелец: этап C3 фронта.

Принцип: ничего нового в защите не изобретаем и ничего не обходим. Команды идут тем же конвейером, что у старого дашборда: `tb.dash.ctlweb.CtlWeb` (challenge у ctl, подписанная
команда в `state/control/_inbox`, аудит отказов в `state/control/audit.jsonl`, тот же `needs_full` для решения, нужен ли токен). Ключ подписи читает только `tb.control.auth` внутри
`client.build`; в браузер и в ответы он не попадает. Здесь добавлено только то, чего нет у POST-пути:
- сессия «open» на каждое соединение (ни «full», ни «запомнить»: токен этот дашборд не принимает, опасное невозможно); создаётся только при `auth.mode=open_local` и наличии ключа;
- CSRF-токен соединения: выдаётся в `hello` и сверяется константным сравнением в каждой команде (поверх проверки Host/Origin при рукопожатии сокета);
- лимит частоты `max_post_per_min` из config/control.json на соединение (тот же счётчик сессии, что у POST);
- белый список: `day.start` только для paper|testnet|observe и `day.stop` soft; всё остальное (live, риск, KILL, смена мозга, неизвестное) отвечает E_ELEVATE или E_UNKNOWN
  (live-старт: только в расширенном режиме И при control.allow_live_start_from_dash=true, иначе E_ELEVATE / E_DISABLED; ворота H1-H8, предупреждения, nonce и фразы проверяет ctl)
  и пишется в аудит; в команду попадают только поля mode, session, accepted, allow_amber (nonce, фразы, team отбрасываются);
- РАСШИРЕННЫЙ РЕЖИМ (решение владельца, только 127.0.0.1): config/control.json `auth.dash2_full: true` (по умолчанию false) даёт соединению сессию «full» без токена и открывает FULL_ACTIONS
  (кроме platform.shutdown) и запись ключа LLM (op llmkey). Все проверки соединения, аргументы через protocol.validate_args и подтверждения ctl (nonce, фразы, шлюзы) остаются: мост передаёт то,
  что ввёл пользователь, и ничего не подделывает. Аудит из этого дашборда идёт с источником dash2.
- ответ команде идёт тем же сокетом событием `ctl_result`; итог исполнения ctl (outbox) досылается позже.
Блокирующие вызовы (`challenge` ждёт ctl до 4 с) вызывай через `asyncio.to_thread`.
"""
import hmac
import re
import time

from tb.control import auth, client, protocol
from tb.dash import ctlweb, keyweb

SAFE_MODES = ctlweb.SAFE_MODES                       # ("paper", "testnet", "observe"): те же, что считает безопасными needs_full
DAY_KEYS = ("mode", "session", "accepted", "allow_amber")
FOLLOW_S = 180.0                                      # сколько ждём итог команды в outbox
FOLLOW_POLL_S = 0.5
FINAL = ("done", "rejected", "failed", "expired")

# ---- расширенный режим нового дашборда: config/control.json, auth.dash2_full = true (решение владельца; только 127.0.0.1)
# Сессия соединения становится «full» (new_session уровня full), токен не нужен; подтверждения (nonce, фразы, шлюзы) остаются в силе: их проверяет ctl и CtlWeb.
SRC = "dash2"                                         # пометка источника в аудите (audit.append обрезает до 8 символов)
FULL_ACTIONS = ("day.start", "day.stop", "day.resume", "kill.set", "stop.emergency", "risk.set", "mode.set", "carry.decide", "brain.use", "brain.rollback",
                "brain.reload", "brain.role", "brain.profile", "llm.provider", "exchange.set", "notify.set", "notify.test", "intel.refresh", "report.ack",
                "kill.clear")      # снятие KILL (решение владельца 2026-11-02): ctl сам проверяет флаг allow_clear_kill_from_dash, предусловия и подтверждение; нет в BASE (open) и в старом дашборде
# какие ключи аргументов пропускаем до validate_args (лишние отбрасываются; для настроечных и notify.set строгая проверка у самого validate_args)
FULL_KEYS = {
    "day.start": DAY_KEYS, "day.stop": (), "day.resume": (), "kill.clear": (), "kill.set": ("flatten", "reason"), "stop.emergency": ("flatten", "reason"),
    "risk.set": ("level", "accepted"), "mode.set": ("mode",), "carry.decide": ("plan", "decision"),
    "brain.use": ("team", "profile", "agent", "role", "accepted", "to", "from_config", "all"),
    "brain.rollback": ("team", "profile", "agent", "role", "accepted", "to", "from_config", "all"),
    "brain.reload": ("team", "profile", "agent", "role", "accepted", "to", "from_config", "all"),
    "notify.test": (), "intel.refresh": ("source",), "report.ack": ("day_id",),
}
LLMKEY_MAX = 512
RE_PID = keyweb.RE_ID


def clean(x, depth=0):
    """Данные ответа ctl для браузера: строки обрезаны, вложенность и размеры ограничены, только json-типы."""
    if depth > 3:
        return None
    if isinstance(x, bool) or x is None or isinstance(x, (int, float)):
        return x
    if isinstance(x, str):
        return x[:300]
    if isinstance(x, (list, tuple)):
        return [clean(v, depth + 1) for v in list(x)[:24]]
    if isinstance(x, dict):
        return {str(k)[:32]: clean(v, depth + 1) for k, v in list(x.items())[:16] if not any(w in str(k).lower() for w in ("nonce", "mac", "key", "token", "secret"))}
    return None


class Web2(ctlweb.CtlWeb):
    """CtlWeb нового дашборда: дополнительно знает действия только dash2 (protocol.DASH2_ACTIONS, сейчас kill.clear). У экземпляра старого дашборда `_norm` их не принимает
    (E_UNKNOWN), поэтому там такого действия нет. Допускает их только мост, и только в расширенном режиме (allowed_full); остальной конвейер (CSRF, nonce, фразы) общий."""

    def _norm(self, data):
        action = data.get("action")
        if action in protocol.DASH2_ACTIONS:
            ok, _msg, args = client.validate_args(action, data.get("args") if data.get("args") is not None else {})
            return (action, args, None) if ok else (None, None, "E_ARGS")
        return ctlweb.CtlWeb._norm(self, data)


class CtlBridge(object):
    def __init__(self, root=None, environ=None, clock=time.time):
        self.root, self.environ, self.clock = root, environ, clock
        self.web = Web2(root, environ)
        self.sess = {}                  # соединение -> (cookie, сессия CtlWeb)

    # ---- состояние входа
    def availability(self):
        """-> (доступно, причина): управление из нового дашборда включено только при open_local и наличии ключа подписи."""
        if not auth.status(self.environ)["key"]:
            return False, "no_key"
        if self.web.mode() != "open_local":
            return False, "token_mode"
        return True, None

    def open(self, ws):
        """Сессия «open» на соединение. -> то, что уходит в hello: {"enabled", "csrf"?, "reason"?}."""
        ok, why = self.availability()
        if not ok:
            return {"enabled": False, "reason": why}
        full = self.full_enabled()
        ck, s = self.web.new_session(self.clock(), "full" if full else "open")
        self.sess[ws] = (ck, s)
        return {"enabled": True, "csrf": s["csrf"], "full": full, "modes": (list(FULL_ACTIONS) + ["llmkey"]) if full else ["day.start", "day.stop"]}

    def full_enabled(self):
        """Расширенный режим владельца: auth.dash2_full строго true в config/control.json. Нет ключа, не bool или битый файл = выключено (fail closed)."""
        try:
            a = self.web.conf().get("auth")
            return isinstance(a, dict) and a.get("dash2_full") is True
        except Exception:
            return False

    def live_start_enabled(self):
        """Выключатель запуска live из этого дашборда: control.allow_live_start_from_dash строго true (по умолчанию false). Нет ключа, не bool или битый файл = выключено."""
        try:
            return self.web.conf().get("allow_live_start_from_dash") is True
        except Exception:
            return False

    @staticmethod
    def _live_msg(code, action, args):
        """Понятный текст отказа для live-старта; остальное берётся из справочника кодов."""
        if action != "day.start" or not (isinstance(args, dict) and args.get("mode") == "live"):
            return None
        if code == "E_ELEVATE":
            return "запуск live из этого дашборда требует расширенного управления (auth.dash2_full в config/control.json); либо запустите день в старом дашборде"
        if code == "E_DISABLED":
            return "запуск live из этого дашборда выключен владельцем (control.allow_live_start_from_dash); либо запустите день в старом дашборде"
        return None

    def _audit(self, label, action, code, now, args=None, result="rejected", cid="-"):
        """Аудит из нового дашборда: источник dash2, в журнале только хеш аргументов (ни ключа LLM, ни nonce). Не чаще 30 записей в минуту, как у ctlweb."""
        w = self.web
        w.audit_flood = [t for t in w.audit_flood if now - t < 60]
        if len(w.audit_flood) >= 30:
            return
        w.audit_flood.append(now)
        try:
            client.audit_append(self.root, cid or "-", SRC, label, (action or "-")[:24], client.args_digest(action or "-", args if isinstance(args, dict) else {}), result, code, now=now)
        except Exception:
            pass

    def close(self, ws):
        ent = self.sess.pop(ws, None)
        if ent:
            self.web.sessions.pop(ent[0], None)

    # ---- команда
    def _reply(self, cid, kind, action, code, msg=None, **extra):
        out = {"id": cid, "kind": kind, "action": action, "ok": code == "OK", "code": code, "msg": msg or ctlweb.ERR_TEXT.get(code, ctlweb.ERR_TEXT["E_INTERNAL"])}
        out.update(extra)
        return out

    def handle(self, ws, m, now=None):
        """Одна команда из сокета: {"id","csrf","kind":"challenge|command","action","args"}. -> ответ для `ctl_result` (+ поле `accepted_id`, когда команда поставлена в очередь)."""
        now = self.clock() if now is None else now
        cid = str(m.get("id") or "")[:24]
        kind = m.get("kind") if m.get("kind") in ("challenge", "command") else "command"
        action = m.get("action") if isinstance(m.get("action"), str) else ""
        ent = self.sess.get(ws)
        if ent is None:
            return self._reply(cid, kind, action, "E_AUTH", "управление недоступно в этом режиме входа: используйте старый дашборд")
        s = ent[1]
        bad = self._gate(s, m, "ws:" + action[:12], now)
        if bad:
            return self._reply(cid, kind, action, bad)
        full = self._session_full(s)
        if s.get("level") == "full" and not full:          # флаг сняли, пока соединение живо: полномочия пропадают сразу
            self._audit(s["label"], action[:24], "E_AUTH", now)
            return self._reply(cid, kind, action, "E_AUTH", "расширенное управление выключено в настройках: обновите страницу")
        if full:
            args, code = self.allowed_full(action, m.get("args"))
        else:
            args, code = self.allowed(action, m.get("args"))
        if not code and full and action == "day.start" and args.get("mode") == "live" and not self.live_start_enabled():
            code = "E_DISABLED"                                  # выключатель владельца: control.allow_live_start_from_dash (строго true), читается при каждом обращении
        if code:
            self._audit(s["label"], action[:24] or "-", code, now, m.get("args"))
            return self._reply(cid, kind, action, code, self._live_msg(code, action, m.get("args")))
        data = {"action": action, "args": args}
        if full and kind == "command":
            conf, why = self._confirm_of(m)
            if why:
                self._audit(s["label"], action[:24], why, now, args)
                return self._reply(cid, kind, action, why)
            data.update(conf)
        http, obj, _hdr = self.web.challenge(data, s, now) if kind == "challenge" else self.web.command(data, s, now)
        code = str(obj.get("code") or ("OK" if obj.get("ok") else "E_INTERNAL"))
        out = self._reply(cid, kind, action, code, obj.get("msg"))
        out["ok"] = bool(obj.get("ok"))
        if kind == "challenge":
            d = obj.get("data") or {}
            if full:
                out["data"] = clean({k: d.get(k) for k in ("action", "danger", "gates", "warnings", "blocked", "pre_amber", "expires_in", "min_delay", "phrases", "kill")})
                n = d.get("nonce")
                if isinstance(n, str) and 0 < len(n) <= 200:       # nonce нужен клиенту для подтверждения; привязан к соединению и аргументам, в аудит и логи не идёт
                    out["data"]["nonce"] = n
            else:
                out["data"] = clean({k: d.get(k) for k in ("action", "danger", "gates", "warnings", "blocked", "pre_amber", "expires_in")})
            if not out["ok"] and full:
                self._audit(s["label"], action[:24], code, now, args)
        elif obj.get("ok") and obj.get("id"):
            out["accepted_id"] = obj["id"]
            if full:
                self._audit(s["label"], action[:24], "OK", now, args, "ok", obj["id"])
        elif full:
            self._audit(s["label"], action[:24], code, now, args)
        return out

    def _gate(self, s, m, label, now):
        """Проверки соединения: CSRF (константное сравнение), затем лимит частоты. -> код отказа или None."""
        tok = m.get("csrf") if isinstance(m.get("csrf"), str) else ""
        if not hmac.compare_digest(tok.encode("utf-8", "replace"), s["csrf"].encode("ascii")):
            self._audit(s["label"], label, "E_CSRF", now)
            return "E_CSRF"
        s["posts"] = [t for t in s["posts"] if now - t < 60]
        if len(s["posts"]) >= int(self.web.conf()["max_post_per_min"]):
            self._audit(s["label"], label, "E_RATE", now)
            return "E_RATE"
        s["posts"].append(now)
        return None

    def _session_full(self, s):
        """Полномочия full даёт только серверное состояние: уровень сессии, выданный при open(), И включённый флаг сейчас. От клиента уровень не зависит."""
        return s.get("level") == "full" and self.full_enabled()

    @staticmethod
    def _confirm_of(m):
        """Подтверждение от клиента: только nonce (строка) и phrases (до 3 строк); всё остальное отбрасывается. -> ({...}, код ошибки или None)."""
        out = {}
        n, ph = m.get("nonce"), m.get("phrases")
        if n is not None:
            if not (isinstance(n, str) and 0 < len(n) <= 200):
                return {}, "E_BODY"
            out["nonce"] = n
        if ph is not None:
            if not (isinstance(ph, list) and len(ph) <= 3 and all(isinstance(p, str) and len(p) <= 200 for p in ph)):
                return {}, "E_BODY"
            out["phrases"] = list(ph)
        return out, None

    def llmkey(self, ws, m, now=None):
        """Запись ключа LLM-провайдера (write-only): {"id","csrf","provider","value"} -> ответ для `ctl_result` kind='llmkey'. Только в расширенном режиме.
        Значение вынимается из сообщения сразу, нигде не хранится, не логируется, не попадает в аудит, ответ и текст исключений."""
        now = self.clock() if now is None else now
        value = m.pop("value", None) if isinstance(m, dict) else None
        cid = str(m.get("id") or "")[:24]
        pid = m.get("provider")

        def rep(code, msg=None, **extra):
            return self._reply(cid, "llmkey", "llm.key.set", code, msg, **extra)

        try:
            ent = self.sess.get(ws)
            if ent is None:
                return rep("E_AUTH", "управление недоступно в этом режиме входа: используйте старый дашборд")
            s = ent[1]
            bad = self._gate(s, m, "ws:llmkey", now)
            if bad:
                return rep(bad)
            if not self._session_full(s):
                self._audit(s["label"], "llm.key.set", "E_ELEVATE", now)
                return rep("E_ELEVATE", "ввод ключа выключен: включите auth.dash2_full в config/control.json или используйте старый дашборд")
            if not (isinstance(pid, str) and RE_PID.match(pid)):
                self._audit(s["label"], "llm.key.set", "E_ARGS", now)
                return rep("E_ARGS", "провайдер указан неверно")
            if not (isinstance(value, str) and 0 < len(value) <= LLMKEY_MAX and value.isascii() and value.isprintable() and not any(c.isspace() for c in value)):
                self._audit(s["label"], "llm.key.set", "E_ARGS", now, {"provider": pid})
                return rep("E_ARGS", "ключ не принят: печатные символы ASCII без пробелов и переводов строк, не длиннее %d" % LLMKEY_MAX)
            try:
                http, obj, _h = keyweb.handle(self.root, self.environ, {"provider": pid, "value": value}, s["label"], now)
            except Exception:                    # текст исключения может нести значение: наружу и в журнал не отдаём ничего
                self._audit(s["label"], "llm.key.set", "E_INTERNAL", now, {"provider": pid})
                return rep("E_INTERNAL", keyweb.TEXT["E_INTERNAL"], provider=pid)
            ok = http == 200 and bool(obj.get("ok"))
            code = "OK" if ok else str(obj.get("code") or "E_INTERNAL")
            self._audit(s["label"], "llm.key.set", code, now, {"provider": pid}, "ok" if ok else "rejected")
            return rep(code, "ключ сохранён" if ok else keyweb.TEXT.get(code, keyweb.TEXT["E_INTERNAL"]), provider=pid)
        finally:
            value = None

    @staticmethod
    def allowed_full(action, args):
        """Белый список расширенного режима. -> (аргументы, код отказа или None). platform.shutdown всегда E_ELEVATE, неизвестное E_UNKNOWN.
        Ключи аргументов фильтруются по FULL_KEYS, затем строгая protocol.validate_args (у настроечных действий и notify.set она сама отвергает лишние поля)."""
        if action == "platform.shutdown":
            return None, "E_ELEVATE"
        if action not in FULL_ACTIONS:
            return None, "E_UNKNOWN"
        args = args if isinstance(args, dict) else {}
        if action in FULL_KEYS:
            args = {k: args[k] for k in FULL_KEYS[action] if k in args}
        ok, _msg, norm = client.validate_args(action, args)
        if not ok:
            return None, "E_ARGS"
        if action == "day.stop":
            norm = {"mode": "soft"}
        return norm, None

    @staticmethod
    def allowed(action, args):
        """Белый список. -> (аргументы для CtlWeb, код отказа или None)."""
        if action in protocol.DASH2_ACTIONS:
            return None, "E_ELEVATE"                      # только расширенный режим (auth.dash2_full)
        if action not in client.ACTIONS or action == "challenge":
            return None, "E_UNKNOWN"
        args = args if isinstance(args, dict) else {}
        if action == "day.start":
            if args.get("mode") not in protocol.MODES:
                return None, "E_ARGS"
            if args["mode"] not in SAFE_MODES:
                return None, "E_ELEVATE"
            return {k: args[k] for k in DAY_KEYS if k in args}, None
        if action == "day.stop":
            return {"mode": "soft"}, None
        return None, "E_ELEVATE"

    def result_of(self, cid):
        """Итог команды из outbox для досылки клиенту: None, пока ctl не закончил."""
        r = client.read_result(cid, self.root)
        if not r or r.get("status") not in FINAL:
            return None
        return {"id": cid, "status": r.get("status"), "code": r.get("code"), "msg": str(r.get("msg") or "")[:300], "data": clean(r.get("data"))}
