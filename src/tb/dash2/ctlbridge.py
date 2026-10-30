"""Мост команд управления для нового дашборда: старт и остановка торгового дня по WebSocket. Владелец: этап C3 фронта.

Принцип: ничего нового в защите не изобретаем и ничего не обходим. Команды идут тем же конвейером, что у старого дашборда: `tb.dash.ctlweb.CtlWeb` (challenge у ctl, подписанная
команда в `state/control/_inbox`, аудит отказов в `state/control/audit.jsonl`, тот же `needs_full` для решения, нужен ли токен). Ключ подписи читает только `tb.control.auth` внутри
`client.build`; в браузер и в ответы он не попадает. Здесь добавлено только то, чего нет у POST-пути:
- сессия «open» на каждое соединение (ни «full», ни «запомнить»: токен этот дашборд не принимает, опасное невозможно); создаётся только при `auth.mode=open_local` и наличии ключа;
- CSRF-токен соединения: выдаётся в `hello` и сверяется константным сравнением в каждой команде (поверх проверки Host/Origin при рукопожатии сокета);
- лимит частоты `max_post_per_min` из config/control.json на соединение (тот же счётчик сессии, что у POST);
- белый список: `day.start` только для paper|testnet|observe и `day.stop` soft; всё остальное (live, риск, KILL, смена мозга, неизвестное) отвечает E_ELEVATE или E_UNKNOWN
  и пишется в аудит; в команду попадают только поля mode, session, accepted, allow_amber (nonce, фразы, team отбрасываются);
- ответ команде идёт тем же сокетом событием `ctl_result`; итог исполнения ctl (outbox) досылается позже.
Блокирующие вызовы (`challenge` ждёт ctl до 4 с) вызывай через `asyncio.to_thread`.
"""
import hmac
import time

from tb.control import auth, client, protocol
from tb.dash import ctlweb

SAFE_MODES = ctlweb.SAFE_MODES                       # ("paper", "testnet", "observe"): те же, что считает безопасными needs_full
DAY_KEYS = ("mode", "session", "accepted", "allow_amber")
FOLLOW_S = 180.0                                      # сколько ждём итог команды в outbox
FOLLOW_POLL_S = 0.5
FINAL = ("done", "rejected", "failed", "expired")


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


class CtlBridge(object):
    def __init__(self, root=None, environ=None, clock=time.time):
        self.root, self.environ, self.clock = root, environ, clock
        self.web = ctlweb.CtlWeb(root, environ)
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
        ck, s = self.web.new_session(self.clock(), "open")
        self.sess[ws] = (ck, s)
        return {"enabled": True, "csrf": s["csrf"]}

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
        tok = m.get("csrf") if isinstance(m.get("csrf"), str) else ""
        if not hmac.compare_digest(tok.encode("utf-8", "replace"), s["csrf"].encode("ascii")):
            self.web.audit(None, s["label"], "ws:" + action[:12], "E_CSRF", now)
            return self._reply(cid, kind, action, "E_CSRF")
        s["posts"] = [t for t in s["posts"] if now - t < 60]
        if len(s["posts"]) >= int(self.web.conf()["max_post_per_min"]):
            self.web.audit(None, s["label"], "ws:" + action[:12], "E_RATE", now)
            return self._reply(cid, kind, action, "E_RATE")
        s["posts"].append(now)
        args, code = self.allowed(action, m.get("args"))
        if code:
            self.web.audit(None, s["label"], action[:24] or "-", code, now, m.get("args") if isinstance(m.get("args"), dict) else {})
            msg = "live запускается в старом дашборде: нужны токен и подтверждение фразами" if (code == "E_ELEVATE" and action == "day.start") else None
            return self._reply(cid, kind, action, code, msg)
        data = {"action": action, "args": args}
        http, obj, _hdr = self.web.challenge(data, s, now) if kind == "challenge" else self.web.command(data, s, now)
        out = self._reply(cid, kind, action, str(obj.get("code") or ("OK" if obj.get("ok") else "E_INTERNAL")), obj.get("msg"))
        out["ok"] = bool(obj.get("ok"))
        if kind == "challenge":
            d = obj.get("data") or {}
            out["data"] = clean({k: d.get(k) for k in ("action", "danger", "gates", "warnings", "blocked", "pre_amber", "expires_in")})
        elif obj.get("ok") and obj.get("id"):
            out["accepted_id"] = obj["id"]
        return out

    @staticmethod
    def allowed(action, args):
        """Белый список. -> (аргументы для CtlWeb, код отказа или None)."""
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
