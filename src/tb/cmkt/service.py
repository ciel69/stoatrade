"""Процесс сводки сводного рынка: сервис платформы `cmkt` (R1.7, задача 17; Requirements 3.1, 11.3, 11.4, 12.3, 12.5, 18.2, 18.3).

Запуск: `python -m tb.cmkt.service run [--root R] [--max-seconds N]`; сборщики рынков — отдельные процессы
`python -m tb.cmkt.collector run --market <venue>_<kind>` (сервисы `cmkt@<venue><p|s>`, их поднимает супервизор по
`platform.services.cmkt_services`). Обёртка над `tb.cmkt.aggregate.Aggregator` (такт, снимок, ряды, фокус) с дополнениями heartbeat
`runtime/platform/cmkt.hb.json` (раз в 5 с, супервизор проверяет его по `svc.cmkt`: `hb: file`, `hb_stale_s` 30 с):

- `venues_out` — {биржа: с какого времени исключена}: у биржи все включённые рынки в снимке с кодом исключения (`OFF` — выключенные
  владельцем — не считаются). `./tb status` ставит жёлтый, если таких бирж 2 и больше дольше 300 с (Requirement 11.4);
- `cpu_cores`, `rss_mb` своего процесса и `budget` — сумма по процессам `cmkt` (сводка и свежие heartbeat сборщиков): CPU, p99 CPU,
  RSS, p99 задержки обработки, трафик, запись на диск; `over` — превышенные пределы `config/cmkt.json:budget`. Превышение передаётся
  фокусу (`Focus.budget`): `budget.over_min` минут подряд — ярус `light` −`shrink_step` монет до `light_min` и жёлтый `BUDGET`
  (Requirement 12.3).

Битый config/cmkt.json -> код выхода 2 (причина в heartbeat), супервизор не перезапускает. Выключенный сводный рынок (`enabled: false`)
— процесс не выходит (выход с кодом 0 супервизор считал бы падением), а пишет heartbeat `state: OFF` до остановки.
Окружение процесса чистится от секретов (`collector.scrub_env`); ключей, подписи и сети здесь нет.
"""
import argparse
import sys
import time
from collections import deque

from tb.cmkt import aggregate as AG
from tb.cmkt import conf
from tb.cmkt import focus as FO
from tb.cmkt.collector import rss_mb, scrub_env, service_name, write_hb

SERVICE = AG.SERVICE
EXIT_CONF = AG.EXIT_CONF
HB_FRESH_S = 30.0                     # heartbeat сборщика старше — в сумму бюджета не входит
OFF_HB_S = 5.0
# сумма по процессам -> предел config/cmkt.json:budget
BUDGET_KEYS = (("cpu_cores", "cpu_avg_cores"), ("cpu_p99_cores", "cpu_p99_cores"), ("rss_mb", "rss_mb"), ("proc_p99_ms", "proc_p99_ms"),
               ("net_mbit", "net_mbit"), ("disk_gb_day", "disk_write_gb_day"))


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def venues_out(ex, prev, now):
    """Биржи вне сводки. ex — {рынок '<venue>_<kind>': код | None} последнего снимка; prev — прежний результат {биржа: с какого времени}.
    -> {биржа: since}: у биржи все рынки, кроме выключенных (`OFF`), с кодом исключения. Время входа сохраняется, пока биржа вне сводки."""
    by = {}
    for mkt, code in (ex or {}).items():
        v, _, kind = str(mkt).rpartition("_")
        if not v or code == "OFF":
            continue
        by.setdefault(v, []).append(code)
    out = {}
    for v, codes in by.items():
        if codes and all(c is not None for c in codes):
            t = (prev or {}).get(v)
            out[v] = t if isinstance(t, (int, float)) and t <= now else now
    return out


def budget_totals(own, collectors, bud):
    """Сумма бюджета по процессам cmkt. own — числа сводки, collectors — heartbeat сборщиков, bud — config/cmkt.json:budget.
    CPU, RSS, трафик и запись складываются; p99 задержки обработки — наибольший по процессам; p99 CPU — сумма p99 процессов (оценка сверху).
    -> {"cpu_cores", "cpu_p99_cores", "rss_mb", "proc_p99_ms", "net_mbit", "disk_gb_day", "n": процессов в сумме, "over": [имена пределов]}."""
    out = dict((k, None) for k, _ in BUDGET_KEYS)
    procs = [own or {}] + [h for h in collectors or () if isinstance(h, dict)]
    for h in procs:
        for k, _ in BUDGET_KEYS:
            v = _num(h.get(k))
            if v is None:
                continue
            if k == "proc_p99_ms":
                out[k] = v if out[k] is None else max(out[k], v)
            else:
                out[k] = v if out[k] is None else out[k] + v
    for k, _ in BUDGET_KEYS:
        if out[k] is not None:
            out[k] = round(out[k], 3)
    out["n"] = len(procs)
    out["over"] = [lim for k, lim in BUDGET_KEYS if out[k] is not None and _num((bud or {}).get(lim)) is not None and out[k] > float(bud[lim])]
    return out


class Summary(AG.Aggregator):
    """Сводка платформы: Aggregator + биржи вне сводки и бюджет процессов в heartbeat."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.out_since = {}
        self._cpu = deque(maxlen=64)
        self.budget = None

    def _own(self, now):
        self._cpu.append((now, time.process_time()))
        old = next((r for r in self._cpu if now - r[0] <= 60.0), None)
        cpu = None
        if old is not None and now - old[0] >= 1.0:
            cpu = round(max(0.0, (self._cpu[-1][1] - old[1]) / (now - old[0])), 3)
        return {"cpu_cores": cpu, "rss_mb": rss_mb()}

    def collector_hbs(self, now):
        """Свежие heartbeat сборщиков включённых рынков (того же корня)."""
        from tb.platform import services as S
        out = []
        for v, k in conf.markets(self.cfg, enabled_only=True):
            age, d = S.read_hb(service_name(v, k), self.root, now)
            if age is not None and age <= HB_FRESH_S and isinstance(d, dict) and not d.get("stopped"):
                out.append(d)
        return out

    def heartbeat(self, now=None, extra=None):
        now = self.clock() if now is None else now
        snap = self.last or {}
        if snap:
            ex = dict((m, v[0] if isinstance(v, list) and v else None) for m, v in (snap.get("ex") or {}).items())
            self.out_since = venues_out(ex, self.out_since, now)
        own = self._own(now)
        try:
            cols = self.collector_hbs(now)
        except Exception:                                    # noqa: BLE001 — чтение чужих heartbeat не роняет сводку
            cols = []
        self.budget = budget_totals(own, cols, self.cfg.get("budget") or {})
        stopped = bool((extra or {}).get("stopped"))
        if self.focus is not None and not stopped:
            try:
                self.focus.budget(bool(self.budget["over"]), now)
            except Exception:                                # noqa: BLE001
                pass
        e = dict(own)
        e.update({"venues_out": dict(self.out_since), "budget": self.budget,
                  "markets": ["%s_%s" % m for m in conf.markets(self.cfg, enabled_only=True)],
                  "light_cap": getattr(self.focus, "light_cap", None)})
        e.update(extra or {})
        return super().heartbeat(now, e)


def idle_off(root, max_seconds=0.0, clock=time.time, sleep=time.sleep):
    """Сводный рынок выключен: heartbeat `state: OFF` раз в 5 с до остановки (без выхода с кодом 0 и перезапусков супервизора)."""
    t_end = clock() + max_seconds if max_seconds else None
    try:
        while True:
            write_hb(SERVICE, {"state": "OFF", "enabled": False}, root)
            if t_end is not None and clock() >= t_end:
                break
            sleep(OFF_HB_S if t_end is None else max(0.01, min(OFF_HB_S, t_end - clock())))
    except KeyboardInterrupt:
        pass
    write_hb(SERVICE, {"state": "OFF", "enabled": False, "stopped": True}, root)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tb.cmkt.service")
    ap.add_argument("cmd", nargs="?", default="run", choices=("run",))
    ap.add_argument("--root", default=None)
    ap.add_argument("--max-seconds", type=float, default=0.0, dest="max_seconds")
    a = ap.parse_args(argv)
    scrub_env()
    root = a.root or None
    cfg, errs = conf.load(root)
    if errs:
        write_hb(SERVICE, {"state": "E_CONF", "errors": errs}, root)
        sys.stderr.write("cmkt.service: %s\n" % ", ".join(errs))
        return EXIT_CONF
    if cfg.get("enabled") is False:
        return idle_off(root, a.max_seconds)
    svc = Summary(root, cfg, focus=FO.Focus(root, cfg))
    try:
        svc.run(max_seconds=a.max_seconds)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
