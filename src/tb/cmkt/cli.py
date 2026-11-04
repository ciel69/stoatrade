"""`./tb cmkt status|show|at|focus` — наблюдение за сводным рынком (R1.7, задача 17; Requirement 18.1). Только чтение файлов.

- `status [--json]` — сводка (`runtime/platform/cmkt.hb.json`) и рынки: код в последнем снимке, `lag_ms`, `offset_ms`, подписки,
  соединения (живых/всего), восстановления стаканов (переподключений и RESYNC с запуска процесса), использованный REST-вес, CPU, RSS,
  p99 задержки обработки, трафик (из `runtime/platform/cmkt@<venue><p|s>.hb.json`); бюджет процессов и размеры ярусов фокуса;
- `show SYM [--json]` — последний снимок монеты (`runtime/cmkt/snap.json`, разбор `logic.cmkt.snap_sym`);
- `at SYM TS [--json]` — строка ряда на момент сигнала `TS` (секунды эпохи или ISO-время) через `tb.core.cmktdoc.features_at`;
- `focus [--json]` — набор монет и причины (`runtime/cmkt/focus.json`).
Коды: 0 — данные есть, 1 — нет данных (нет снимка, монеты, фокуса), 2 — ошибка использования.
"""
import argparse
import datetime
import json
import os
import time

from tb import paths
from tb.cmkt import aggregate as AG
from tb.cmkt import conf
from tb.cmkt import focus as FO
from tb.cmkt.collector import service_name
from tb.logic import cmkt as L


def _root(a):
    return os.path.abspath(a.root) if a.root else paths.root()


def _hb(name, root, now):
    from tb.platform import services as S
    age, d = S.read_hb(name, root, now)
    return age, (d if isinstance(d, dict) else {})


def _f(x, fmt="%s"):
    return "-" if x is None else fmt % x


def parse_ts(s):
    """'1790900000' | '1790900000.5' | ISO ('2026-11-04T10:00:00Z', без зоны — UTC) -> секунды эпохи (float); иначе ValueError."""
    s = str(s or "").strip()
    try:
        return float(s)
    except ValueError:
        pass
    d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


# ---------------------------------------------------------------- status
def status_doc(root, now=None):
    """Состояние сбора одним словарём (для status и --json)."""
    now = time.time() if now is None else now
    cfg, errs = conf.load(root)
    age, hb = _hb(AG.SERVICE, root, now)
    snap = AG.read_snap(root)
    ex = (snap or {}).get("ex") or {}
    fdoc = FO.read(root)
    mkts = []
    for v, k in conf.markets(cfg or conf.defaults(), enabled_only=False):
        mkt = "%s_%s" % (v, k)
        enabled = conf.venue_enabled(cfg or {}, v)
        cage, c = _hb(service_name(v, k), root, now)
        row = dict(zip(L.SNAP_EX_COLS, ex.get(mkt) or ())) if isinstance(ex.get(mkt), list) else {}
        conns = c.get("conns")
        mkts.append({"mkt": mkt, "service": service_name(v, k), "enabled": enabled, "code": row.get("code") if row else ("OFF" if not enabled else None),
                     "in_snap": bool(row), "lag_ms": row.get("lag_ms"), "off_ms": row.get("off_ms") if row.get("off_ms") is not None else c.get("off_ms"),
                     "state": c.get("state"), "err": c.get("err"), "hb_age_s": None if cage is None else round(cage, 1),
                     "subs": c.get("subs"), "conns": conns, "alive": c.get("alive"), "reconnects": c.get("reconnects"), "resyncs": c.get("resyncs"),
                     "rest": c.get("rest"), "cpu_cores": c.get("cpu_cores"), "rss_mb": c.get("rss_mb"), "proc_ms_p99": c.get("proc_ms_p99"),
                     "net_mbit": c.get("net_mbit"), "coins": c.get("coins")})
    tiers = {"deep": len((fdoc or {}).get("deep") or ()), "light": len((fdoc or {}).get("light") or ()),
             "light_cap": (fdoc or {}).get("light_cap")} if fdoc else None
    return {"ts": round(now, 3), "conf_errors": errs, "enabled": None if cfg is None else cfg.get("enabled"),
            "service": {"hb_age_s": None if age is None else round(age, 1), "state": hb.get("state"), "snap_ts": (snap or {}).get("ts"),
                        "snap_age_s": round(now - snap["t_wr"], 1) if snap and isinstance(snap.get("t_wr"), (int, float)) else None,
                        "n_ex": (snap or {}).get("n_ex"), "warn": (snap or {}).get("warn") or hb.get("warn") or [], "skew_ms": (snap or {}).get("skew_ms"),
                        "tick_ms_p99": hb.get("tick_ms_p99"), "cpu_cores": hb.get("cpu_cores"), "rss_mb": hb.get("rss_mb"),
                        "store_code": hb.get("store_code"), "venues_out": hb.get("venues_out") or {}, "budget": hb.get("budget")},
            "markets": mkts, "tiers": tiers}


def status_text(d):
    s = d["service"]
    out = []
    if d["conf_errors"]:
        out.append("config/cmkt.json не годен: %s" % ", ".join(d["conf_errors"]))
    if d.get("enabled") is False:
        out.append("сводный рынок выключен (config/cmkt.json: enabled=false)")
    n = s.get("n_ex") or {}
    out.append("сводка: %s, heartbeat %s с; снимок ts %s (возраст %s с), рынков в сводке perp %s / spot %s, skew %s мс, такт p99 %s мс" % (
        s.get("state") or "нет heartbeat", _f(s.get("hb_age_s")), _f(s.get("snap_ts")), _f(s.get("snap_age_s")), n.get("perp", "-"), n.get("spot", "-"),
        _f(s.get("skew_ms")), _f(s.get("tick_ms_p99"))))
    if s.get("warn"):
        out.append("предупреждения: %s" % ", ".join(s["warn"]))
    if s.get("venues_out"):
        out.append("вне сводки: %s" % ", ".join("%s с %s" % (v, time.strftime("%H:%M:%S", time.localtime(t))) for v, t in sorted(s["venues_out"].items())))
    b = s.get("budget") or {}
    if b:
        out.append("бюджет (%s процессов): CPU %s ядра, p99 CPU %s, RSS %s МБ, обработка p99 %s мс, трафик %s Мбит/с, запись %s ГБ/сут%s" % (
            b.get("n"), _f(b.get("cpu_cores")), _f(b.get("cpu_p99_cores")), _f(b.get("rss_mb")), _f(b.get("proc_p99_ms")), _f(b.get("net_mbit")),
            _f(b.get("disk_gb_day")), ("; ПРЕВЫШЕНО: " + ", ".join(b["over"])) if b.get("over") else ""))
    t = d.get("tiers")
    out.append("ярусы: deep %s, light %s (предел light %s)" % (t["deep"], t["light"], _f(t.get("light_cap"))) if t else "ярусы: нет focus.json")
    out.append("%-14s %-10s %-7s %-7s %-6s %-5s %-7s %-6s %-6s %-6s %-6s %-6s %-6s %s" % (
        "рынок", "код", "lag_ms", "off_ms", "подп", "соед", "восст", "REST", "CPU", "RSS", "p99мс", "Мбит/с", "hb_с", "сборщик"))
    for m in d["markets"]:
        code = "OFF" if not m["enabled"] else (m["code"] or ("ok" if m["in_snap"] else "-"))
        conns = "%s/%s" % (_f(m["alive"]), _f(m["conns"])) if m["conns"] is not None else "-"
        rec = "%s/%s" % (_f(m["reconnects"]), _f(m["resyncs"])) if m["reconnects"] is not None or m["resyncs"] is not None else "-"
        out.append("%-14s %-10s %-7s %-7s %-6s %-5s %-7s %-6s %-6s %-6s %-6s %-6s %-6s %s" % (
            m["mkt"], code, _f(m["lag_ms"]), _f(m["off_ms"]), _f(m["subs"]), conns, rec, _f(m["rest"]), _f(m["cpu_cores"]), _f(m["rss_mb"]),
            _f(m["proc_ms_p99"]), _f(m["net_mbit"]), _f(m["hb_age_s"]), "%s%s" % (m["state"] or "-", (" " + str(m["err"])) if m["err"] else "")))
    out.append("восст = переподключения/RESYNC с запуска сборщика")
    return "\n".join(out)


# ---------------------------------------------------------------- show / at / focus
def show_text(r):
    out = ["%s: ts %s, ярус %s, в фокусе %s с, n_ex %s, cov %s, mid %s, skew %s мс, флаги %s" % (
        r.get("sym"), r.get("ts"), r.get("tier"), _f(r.get("focus_s")), _f(r.get("n_ex")), _f(r.get("cov")), _f(r.get("mid")), _f(r.get("skew_ms")),
        ",".join(r.get("flags") or []) or "-")]
    mk = r.get("mk") or {}
    if mk:
        out.append("рынки: " + ", ".join("%s %s" % (m, (v or {}).get("code") or "ok") for m, v in sorted(mk.items())))
    f = r.get("f") or {}
    vals = [(k, v) for k, v in f.items() if v is not None]
    out.append("признаки (%d из %d не None):" % (len(vals), len(f)))
    for k, v in vals:
        out.append("  %-26s %s" % (k, v))
    if r.get("lead"):
        out.append("лидер: %s" % r["lead"])
    return "\n".join(out)


def focus_text(d, now):
    out = ["фокус: ts %s (возраст %s с), deep %d, light %d, предел light %s%s" % (
        _f(d.get("ts")), _f(round(now - d["ts"], 1) if isinstance(d.get("ts"), (int, float)) else None), len(d.get("deep") or ()), len(d.get("light") or ()),
        _f(d.get("light_cap")), ("; предупреждения: " + ", ".join(d["warn"])) if d.get("warn") else "")]
    why, since = d.get("why") or {}, d.get("since") or {}
    for tier in ("deep", "light"):
        for s in d.get(tier) or ():
            t = since.get(s)
            out.append("  %-6s %-12s %-7s в фокусе %s с" % (tier, s, why.get(s) or "-", _f(int(now - t) if isinstance(t, (int, float)) else None)))
    return "\n".join(out)


def _dump(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True, default=str))


def build_parser():
    p = argparse.ArgumentParser(prog="tb cmkt", description="сводный рынок: состояние сбора, снимок монеты, строка ряда, фокус (только чтение)")
    p.add_argument("--root", default=None, help="корень проекта (по умолчанию TB_ROOT)")
    sub = p.add_subparsers(dest="cmd")
    for name, hlp in (("status", "биржи и рынки: код, задержка, offset, подписки, соединения, восстановления, REST-вес, ресурсы, ярусы"),
                      ("focus", "набор монет и причины")):
        sp = sub.add_parser(name, help=hlp)
        sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("show", help="последний снимок монеты")
    sp.add_argument("sym")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("at", help="строка ряда на момент TS (секунды эпохи или ISO)")
    sp.add_argument("sym")
    sp.add_argument("ts")
    sp.add_argument("--json", action="store_true")
    return p


def _sym(s):
    s = str(s or "").strip().upper()
    return s[:-4] if s.endswith("USDT") and len(s) > 4 else s


def main(argv, root=None):
    p = build_parser()
    try:
        a = p.parse_args(list(argv))
    except SystemExit as e:
        return 0 if e.code == 0 else 2
    if not a.cmd:
        p.print_help()
        return 2
    root = root or _root(a)
    now = time.time()
    if a.cmd == "status":
        d = status_doc(root, now)
        _dump(d) if a.json else print(status_text(d))
        return 0
    if a.cmd == "focus":
        d = FO.read(root)
        if d is None:
            print("нет runtime/cmkt/focus.json: сводка не запущена (./tb status)")
            return 1
        _dump(d) if a.json else print(focus_text(d, now))
        return 0
    if a.cmd == "show":
        snap = AG.read_snap(root)
        r = L.snap_sym(snap, _sym(a.sym)) if snap else None
        if r is None:
            print("нет снимка монеты %s (%s)" % (_sym(a.sym), "монеты нет в фокусе" if snap else "нет runtime/cmkt/snap.json"))
            return 1
        _dump(r) if a.json else print(show_text(r))
        return 0
    if a.cmd == "at":
        try:
            t0 = parse_ts(a.ts)
        except ValueError:
            print("TS: секунды эпохи или ISO-время, например 2026-11-04T10:00:00Z")
            return 2
        from tb.core import cmktdoc                     # читатель потребителей (задача 16): ряды только на чтение
        d = cmktdoc.features_at(root, _sym(a.sym), t0, now=now, wait=False)
        _dump(d)                                        # {"cm": {...}} | {"cm_missing": код} | {"pending": True, "row_ts", "until"}
        return 0 if isinstance(d, dict) and isinstance(d.get("cm"), dict) else 1
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
