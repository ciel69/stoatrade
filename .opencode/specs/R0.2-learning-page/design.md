# Design

## Overview

Страница «Учится ли мозг» (шаг 1) состоит из трёх частей. (1) Пакет `tb.learn` на Python: чистые функции статистики и по одному модулю на блок, плюс загрузка данных, кеш и сборка отчёта `tb.learning/1`. (2) Операция сокета `learning` в `src/tb/dash2`: клиент просит отчёт за режим, сервер собирает его в потоке и отвечает сообщением `learning_detail` (так уже устроены `exp_detail`, `plan_detail`, `usage_detail`); тот же отчёт отдаёт `api_payload("/api/learning")` через реестр `EXT_API`. (3) Страница фронта на Vue/FSD: сущность `entities/learning`, виджеты `widgets/learning/<блок>`, страница `pages/learning`, вкладка «Учится ли мозг» в разделе «Мозг».

Принципы: числа считает код, а не модель; вердикт определяет правило на данных (n, Уэлч, Бонферрони, защиты), пороги берутся из `expstats` и одного небольшого конфига; режимы не смешиваются ни в одном числе; блок без данных говорит, чего нет и когда появится; расчёт не тормозит дашборд (кеш, single-flight, фоновый тяжёлый блок памяти, SQLite только через `open_ro`); страница только читает.

Рассмотренные и отклонённые варианты:
- Настоящий HTTP `GET /api/learning`: отклонено — у сервера dash2 нет REST (только статика, `/api/link` и сокет), второй путь данных не имеет потребителя; логический путь реализован через `api_payload` и операцию сокета (вопрос Q11).
- Постоянная тема в `TOPICS` с пересчётом по таймеру: отклонено — отчёт тяжелее остальных тем (ряды, бутстреп, SQLite), а данные меняются при закрытии сделки; считаем по запросу клиента с кешем 60 с.
- Новый поток данных (отдельный сервис или файл `state/learning.json`, который пишет фоновая задача): отклонено — ещё один процесс и состояние, которые надо синхронизировать; хватает кеша в памяти процесса dash2.
- Вердикт «лучше нуля» (средний R > 0 с интервалом): отклонено — это вопрос «прибыльно ли», а не «учится ли»; прибыль не доказана и не цель страницы (`docs/RESEARCH.md`). Вердикт сравнивает недавние сделки с прежними.
- Вердикт «подтверждён по скользящему окну 200»: отклонено — окно заполнится только через недели; разбиение «недавние/прежние» работает уже с 60 сделок.
- Сравнение «недавние против прежние» с поправкой на число проверенных окон (K > 1): отклонено — проверяется одна гипотеза (К = 1), окна 50/200 в графиках справочные и не участвуют в вердикте.
- Модель пишет текст вердикта: отклонено — слабые модели и принцип «числа и формулировки считает код»; текст — шаблон по состоянию и причине.
- Пересчёт walk-forward памяти на каждый запрос: отклонено — стоимость растёт квадратично (`advice` ищет похожих среди всех прежних); считаем в фоне, ограничиваем `max_trades`, результат кешируем на 15 минут, на более крупных выборках показываем «по расписанию (этап 3)».
- Парное сравнение мозга и политик по общим сигналам: отложено — в сделках мозга нет ключа сигнала `S-...` (связь появится вместе с R2.9); пока непарный Уэлч с явными оговорками `caveats`.
- Упущенный R в пропусках через типичный стоп: отклонено — выдуманное число; показываем процент хода цены после издержек (Q8).
- Графическая библиотека (Chart.js, ECharts): отклонено — нет зависимости в `frontend/package.json`, плотные SVG-ряды проще и легче, нужны только лента интервала и линия нуля.
- Показ суммы PnL в USDT в экономике: отклонено — раскрывает размер счёта; результат только в R (Q7).
- Серия по дням: отклонено по умолчанию — ~10 сделок в день, шум выше интервала (Q4).
- Интервал PF аналитически (лог-нормальное приближение): отклонено — при n < 100 даёт вводящие в заблуждение границы; бутстреп с фиксированным seed детерминирован.
- Вставлять расчёты в `tb.dash2.topics` напрямую: отклонено — тесты чистых функций без сервера, слой `dash` не должен расти логикой; `topics` лишь вызывает `tb.learn.report`.

## Architecture

```
src/tb/
  learn/                 НОВЫЙ  чистые расчёты + сборка отчёта; не импортирует venues/executor/gateway/control/brain/intel.items/dash/dash2;
    __init__.py                 tb.llm только модулем usage и только из load.py
    conf.py              config/learning.json: загрузка со строгой проверкой, умолчания, предупреждения
    stats.py             t-интервал, Уилсон, Уэлч по сводкам, z пропорций, бутстреп PF, rolling, неделя ISO, est()
    load.py              данные: сделки мозга (expstats + фильтры), случаи (memory.cases.load), расход (llm.usage), подпись источников
    cache.py             кеш отчёта (ttl, min_interval, подпись), single-flight, кеш памяти
    verdict.py           правило вердикта и таблица шаблонов текста
    result.py            блок result
    exits.py             блок exits
    discipline.py        блок discipline
    skips.py             блок skips
    economics.py         блок economics
    vs_policy.py         блок vs_policy (policy_stats через open_ro)
    memory_vs_base.py    блок memory (walk-forward в фоне)
    stages.py            заглушки lessons, versions
    report.py            build(root, mode, now) -> ответ tb.learning/1; изоляция ошибок блоков; sanitize
    dashapi.py           api(path, query, root, now) для EXT_API: /api/learning
  dash/server.py         ИЗМЕНЁН  EXT_API += "tb.learn.dashapi"
  dash2/topics.py        ИЗМЕНЁН  learning_detail(root, mode, now): вызов tb.learn.report + model.scrub
  dash2/server.py        ИЗМЕНЁН  op "learning" -> learning_op; повторное сообщение для блока computing
config/
  learning.json          НОВЫЙ  окна, недели, дни политик, базовые политики, бюджеты, кеш, seed бутстрепа
tests/
  test_learn_stats.py test_learn_load.py test_learn_cache.py test_learn_verdict.py test_learn_blocks_*.py
  test_learn_report.py test_learn_perf.py test_dash2_learning.py ; test_layering.py ИЗМЕНЁН
  fixtures_learning/     НОВЫЙ  payload_full.json, payload_few.json (эталонные ответы)
frontend/src/
  shared/api/live-socket.ts            ИЗМЕНЁН  'learning_detail' в EPHEMERAL
  shared/ui/UiBandChart.vue (+index)   НОВЫЙ     SVG-график: ряд, лента интервала, нулевая линия, подсказка, таблица-альтернатива
  shared/lib/human-pages/learning/     НОВЫЙ     тексты: коды причин, состояния, единицы
  entities/learning/                   НОВЫЙ     model/types.ts, model/store.ts, lib/learning.ts, lib/mock.ts, index.ts
  widgets/learning/{verdict,result,vs-policy,discipline,skips,exits,economics,memory,stages,intro}/   НОВЫЕ
  pages/learning/                      НОВЫЙ     ui/LearningPage.vue, index.ts
  app/router/index.ts                  ИЗМЕНЁН   маршрут /brain/learning
  shared/config/nav.ts, nav.test.ts    ИЗМЕНЁН   вкладка «Учится ли мозг» в «Мозг»
frontend/e2e/lib/harness.mjs           ИЗМЕНЁН   ответ learning_detail фейкового сервера; сценарии страницы
frontend/dist/                         ПЕРЕСОБРАН
```

Поток данных: клиент (`store.request(mode)`) -> сокет `{"op":"learning","mode"}` -> `server.learning_op` (to_thread) -> `topics.learning_detail` -> `tb.learn.cache.get_report` -> (промах) `tb.learn.report.build` -> `load.*` -> блоки -> `scrub` -> `learning_detail`. Блок памяти, не уложившийся в бюджет, возвращается как `computing`, расчёт заканчивается в фоне, `learning_op` ждёт его до 30 с и шлёт повторное `learning_detail`.

## Components and Interfaces

### tb.learn.conf

```python
DEFAULTS = {"schema": "tb.learning_cfg/1",
            "verdict": {"recent_n": 50, "guard_trades_ratio": 0.5},
            "windows": [50, 200], "series_points": 120, "weeks": 8, "policy_days": 14,
            "baseline_policies": ["P5", "P6"],
            "memory": {"max_trades": 600, "budget_s": 3.0, "ttl_s": 900},
            "cache": {"ttl_s": 60, "min_interval_s": 10},
            "pf_bootstrap": {"b": 500, "seed": 20261102, "level": 0.9}}

def load(root=None) -> tuple[dict, list[str]]   # (конфиг со слиянием умолчаний, предупреждения); битый файл = умолчания + предупреждение, не исключение
```

Проверка: неизвестные ключи и значения вне диапазонов (например `recent_n < 30`, `windows` не из целых 30..1000, `guard_trades_ratio` вне 0..1) отбрасываются с предупреждением в `meta.warnings` (`E_CONF:<поле>`). `MIN_N` и `ALPHA` не настраиваются: берутся из `expstats`.

### tb.learn.stats (чистый)

```python
def est(v, lo=None, hi=None, n=0, lvl=0.95) -> dict           # {"v","lo","hi","n","lvl"}; не конечные -> None
def mean_est(xs, lvl=0.95) -> dict                              # среднее ± t-интервал; n<2 -> без интервала
def wilson_est(k, n, lvl=0.95) -> dict                          # доля с интервалом Уилсона
def welch_summary(m1, se1, n1, m2, se2, n2, lvl=0.95) -> dict   # {"diff","se","df","t","p","lo","hi"}; p=None если se==0 или n<2
def two_prop(k1, n1, k2, n2, one_sided=None) -> dict            # {"p1","p2","z","p"} объединённая доля; one_sided: "greater"|"less"|None
def pf_est(pnls, level, b, seed) -> dict                        # точка по сумме, интервал бутстрепом random.Random(seed)
def window_row(trades) -> dict                                   # одна строка expstats.group_stats(по setup="_w") для среза: avg_r, se_r, pf, win_rate, fees_share, mfe_capture, n, small
def rolling(trades, w, fn, min_points=30, max_points=120) -> list   # точки на каждой сделке с индекса min_points, окно растущее до w, прореживание, последняя сохраняется
def iso_week(epoch) -> str                                       # "2026-W44" (UTC, isocalendar)
def week_range(asof, n) -> list[dict]                            # последние n недель: {"week","start","end","partial"}
def iso_epoch(s) -> int | None                                   # "2026-10-27T08:59:35Z" -> секунды
```

`window_row` использует приём `exp_detail`: подставляет в поле `setup` фиксированное значение и вызывает `expstats.group_stats`, поэтому числа совпадают с `./tb exp stats` на том же срезе (свойство 6).

### tb.learn.load

```python
def brain_trades(root, mode) -> tuple[list[dict], dict]   # (сделки по возрастанию ts_close, id; excluded {shadow, manual, no_plan, no_r})
def cases(root, mode) -> list[dict]                        # закрытые сделки-случаи режима (как memory.backtest.closed_trades)
def skip_cases(root, mode) -> tuple[list[dict], str|None]  # случаи skip/idea с whatif режима; причина "mode_unknown" | "no_whatif" | None
def usage_rows(root, since) -> tuple[list[dict], dict]     # tb.llm.usage.read_rows + пометка усечения
def source_signature(root) -> tuple                        # (размер, mtime_ns) trades.jsonl, cases.jsonl, calls-файла, config/learning.json
```

`brain_trades` использует `expstats.load_trades(mode, root, with_rules=False)`; для каждой записи добавляется эпоха закрытия (`iso_epoch(ts_close)`), отбрасываются `shadow`, пустой `plan_id`, теги `manual*`; сортировка по (эпоха, id). `cases` берёт `tb.memory.cases.load(root)` без изменения объектов и конфиг памяти тем же загрузчиком, что использует `./tb memory backtest` (имя уточняется в задаче 2).

### tb.learn.cache

```python
class Cache:
    def get(self, key, sig, loader, ttl, min_interval, now=None) -> tuple[object, bool]   # (значение, из кеша?)
    def clear(self)
class Flight:                                                                              # single-flight для блока памяти
    def start(self, key, fn) -> Future                                                     # один поток на ключ
    def peek(self, key) -> Future | None
REPORT = Cache(); MEMORY = Flight()                                                        # на процесс
```

`get`: значение живо, если `now − t < ttl` и подпись не менялась; при смене подписи пересчёт не чаще `min_interval`. Блокировка на ключ: параллельные вызовы ждут одного. Исключение загрузчика не кладётся в кеш. Время кеша — `time.monotonic` (тесты подставляют `now`).

### tb.learn.verdict (чистый)

```python
def decide(trades, cases_all, cfg, now=None) -> dict
    # trades: сделки мозга по порядку; cases_all: случаи режима (для защиты по нарушениям)
```

Алгоритм (все пороги — `expstats.MIN_N`, `expstats.ALPHA`, K = 1, `cfg["verdict"]`):

```
n = len(R-сделок)
if n < MIN_N:               state=few_data,      reason=n_lt_min
elif n < 2*MIN_N:           state=no_difference, reason=no_baseline        # нет базы для сравнения
else:
    n_recent = min(recent_n, n - MIN_N); recent = T[-n_recent:]; prev = T[:-n_recent]
    w = welch_summary(mean(recent), se(recent), n_recent, mean(prev), se(prev), len(prev), lvl=1-ALPHA/K)
    sig = w.p is not None and w.p < ALPHA/K
    g_viol  = guard_viol(cases_all, t_cut=ts_open(recent[0]))       # ok | провал (доля выросла, p1<0.05) | unknown(<10 с известным rf)
    g_trade = trades_per_day(recent) >= ratio * trades_per_day(prev)
    if w.diff > 0 and sig and g_viol.ok and g_trade.ok: confirmed
    else: no_difference, reason = первое из guard_viol, guard_trades, not_significant
    trend = "down" if w.diff < 0 and sig else "up" if w.diff > 0 and sig else "flat"
```

Текст — таблица шаблонов в `verdict.py` (ключ `(state, reason)` -> строка с `{n}`, `{need}`, `{diff}`, `{lo}`, `{hi}`), русский, до 160 символов. `verdict.params` содержит применённые пороги (`min_n`, `alpha`, `k`, `recent_n`, `guard_trades_ratio`) для аудита.

### Блоки (все: `build(ctx) -> dict` блока; ctx — `Ctx(root, mode, now, cfg, trades, cases, ...)`, общие данные загружает report один раз)

- `result.build(ctx)`: окна `cfg.windows`; `r`, `win`, `pf`, `fees_share`, `n` по `min(W, n)` последним; ряды `rolling(trades, W, fn)`; `all`; `signal`.
- `exits.build(ctx)`: по сделкам с `mfe_r`; `capture` = среднее `r/mfe_r` по выигрышным с `mfe_r > 0` (t-интервал), `mfe` среднее `mfe_r`; ряды от 30-й сделки с путём; `n_path`, `path_share`.
- `discipline.build(ctx)`: недели из `week_range`, группировка случаев по ISO-неделе `ts`; `viol`, `err_any`, `err_top`, `err_total`, `signal` (4 против 4 недель).
- `skips.build(ctx)`: недели; `correct`, `missed_pct`, `trades`; итог; `signal` по доле верных.
- `economics.build(ctx)`: недели; суммы по журналу вызовов; `n_trades`, `r_sum` из сделок мозга; отношения только при положительном знаменателе.
- `vs_policy.build(ctx)`: реестр `tb.labels.policies.load(root)` и `active(...)` для текущих версий и K; `tb.labels.query.policy_stats(root, since, by=("policy_id","policy_ver","ctx_ver"))`; один SQL на самую свежую `ctx_ver` каждой политики через `tb.labels.store.open_ro`; Уэлч по сводкам; `summary`.
- `memory_vs_base.build(ctx)`: `memory.backtest.closed_trades`/`walk_forward` в потоке `Flight`; ожидание `budget_s`; `computing` при превышении; кеш `ttl_s`.
- `stages.build(ctx)`: две заглушки без чтения файлов.

Блок вызывается через `report._safe(id, fn)`: исключение -> `{"id","title","status":"error","reason":"E_BLOCK","note":"не удалось собрать"}`.

### tb.learn.report

```python
ORDER = ("result", "vs_policy", "discipline", "skips", "exits", "economics", "memory", "lessons", "versions")
def build(root=None, mode="paper", now=None) -> dict          # полный ответ tb.learning/1 (до scrub), без кеша
def get(root=None, mode=None, now=None) -> dict               # с кешем (REPORT.get), mode нормализуется
def sanitize(obj) -> obj                                       # допустимые символы строк, обрезка, NaN/inf -> None
```

### tb.learn.dashapi

```python
def api(path, query, root=None, now=None):                    # path == "/api/learning" -> (200, report.get(...)); иначе None
```

### dash2

- `topics.learning_detail(root, mode, now=None)`: нормализует `mode` (`default_mode` -> `paper` при `all`), вызывает `tb.learn.report.get`, оборачивает `model.scrub`; исключение -> `{"ok": False, "mode": mode, "error": "failed"}`.
- `server.learning_op(ws, m)`: по образцу `exp_op`; после ответа, если какой-либо блок `computing`, запускает `asyncio.ensure_future(self._learning_follow(ws, mode))`, который ждёт `MEMORY.peek(...)` до 30 с (в потоке) и шлёт повторный `learning_detail`; закрытое соединение игнорируется.

### Фронтенд

```ts
// entities/learning/model/types.ts
export type LearningMode = 'paper' | 'testnet' | 'live'
export type BlockStatus = 'ok' | 'few_data' | 'no_data' | 'stage' | 'computing' | 'error'
export interface Est { v: number | null; lo: number | null; hi: number | null; n: number; lvl: number }
export interface LearningVerdict { state: 'few_data' | 'no_difference' | 'confirmed'; reason: string; text: string; n: number; n_recent: number | null; n_prev: number | null; diff_r: Est | null; p: number | null; alpha_adj: number; trend: 'up' | 'flat' | 'down' | null; guards: unknown }
export interface LearningBlock { id: string; title: string; status: BlockStatus; note: string; reason?: string; stage?: number; roadmap?: string; [k: string]: unknown }
export interface LearningDetail { schema: 'tb.learning/1'; ok: boolean; mode: LearningMode; asof: number; verdict: LearningVerdict; blocks: Record<string, LearningBlock>; order: string[]; meta: LearningMeta }
// store.ts: useLearningStore() -> { detail, wanted, mocked, pending, request(mode), start() } ; ответ чужого режима отбрасывается; повторное сообщение того же режима заменяет прежнее
```

Точные типы блоков (поля рядов и оценок) задаются в `types.ts` по разделу «Data Models»; сверка с реальным ответом бэкенда — по эталонам `tests/fixtures_learning/*.json` (задача 28).

Состав виджетов (каждый — свой срез `widgets/learning/<имя>` с `ui/*.vue` и `index.ts`): `verdict` (слово, пояснение, n, интервал разности, предупреждение `trend=down`, защиты), `intro` (что значат n, интервал и «данных мало», одна короткая справка), `result` (график скользящего R с лентой и нулём, PF, окна 50/200), `vs-policy` (таблица политик: n, R, разность с интервалом, оговорки), `discipline` (недельные доли нарушений и теги ошибок), `skips` (доля верных и сделки по неделям, упущенный ход в %), `exits` (MFE и доля забранного с рядом), `economics` (токены и USD на сделку и на R по неделям), `memory` (Brier памяти и базы, покрытие, заглушка истории), `stages` (заглушки «появится на этапе N»).

## Data Models

### Ответ `tb.learning/1` (сокращённый образец, `mode=paper`)

```json
{"schema": "tb.learning/1", "ok": true, "mode": "paper", "asof": 1793700000, "min_n": 30, "alpha": 0.05,
 "verdict": {"state": "no_difference", "reason": "no_baseline", "text": "Сделок 38: нужно ещё 22, чтобы сравнить недавние с прежними.",
             "n": 38, "n_recent": null, "n_prev": null, "diff_r": null, "p": null, "alpha_adj": 0.05, "trend": null,
             "guards": {"viol": null, "trades_day": null},
             "params": {"min_n": 30, "alpha": 0.05, "k": 1, "recent_n": 50, "guard_trades_ratio": 0.5}},
 "order": ["result", "vs_policy", "discipline", "skips", "exits", "economics", "memory", "lessons", "versions"],
 "blocks": {
  "result": {"id": "result", "title": "Результат", "status": "ok", "note": "...",
             "windows": {"50": {"n": 38, "r": {"v": -0.15, "lo": -0.51, "hi": 0.21, "n": 38, "lvl": 0.95}, "win": {...}, "pf": {"v": 0.86, "lo": 0.4, "hi": 1.7, "n": 38, "lvl": 0.9}, "pf_inf": false, "fees_share": 0.19, "small": false},
                         "200": {...}},
             "all": {...},
             "series": {"50": [{"i": 30, "ts": 1793000000, "n": 30, "r": {...}, "pf": 0.9}], "200": []},
             "signal": {"50": false, "200": false}},
  "vs_policy": {"status": "ok", "since": 1792490000, "caveats": ["unpaired", "selection", "costs_model", "correlated"], "k": 4, "alpha_adj": 0.0125,
                "brain": {"n": 38, "r": {...}},
                "rows": [{"id": "P6", "ver": 1, "name": "random_side_baseline", "baseline": true, "ctx_changed": false, "n_filled": 410, "fill_rate": 0.97, "r": {...}, "pf": 0.95, "diff": {...}, "p": 0.31, "verdict": "unclear"}],
                "summary": "unclear"},
  "discipline": {"weeks": [{"week": "2026-W44", "partial": true, "n_cases": 14, "viol": {...}, "n_rf": 12, "err_any": {...}, "err_top": [["late_entry", 3]]}], "err_total": [], "signal": null},
  "skips": {"weeks": [{"week": "2026-W44", "n": 50, "correct": {...}, "missed_pct": {"v": 0.35, "n": 17}, "trades": 38}], "total": {...}, "signal": null},
  "exits": {"windows": {"50": {"n_path": 38, "path_share": 1.0, "mfe": {...}, "capture": {...}}}, "series": {"50": []}},
  "economics": {"scope": "platform", "weeks": [{"week": "2026-W44", "calls": 1200, "tok_in": 3000000, "tok_out": 400000, "llm_usd": 0.0, "n_trades": 38, "r_sum": -5.7, "tok_per_trade": 89473, "usd_per_trade": 0.0, "usd_per_r": null}], "signal": null},
  "memory": {"status": "ok", "n_trades": 38, "n_eval": 22, "covered": {...}, "brier_mem": 0.292, "brier_base": 0.270, "d_brier": {"v": -0.022, "lo": -0.08, "hi": 0.04, "n": 22, "lvl": 0.95}, "acc_mem": 0.519, "acc_base": 0.519, "better": false,
             "history": {"status": "stage", "stage": 3, "roadmap": "R3.4", "note": "история по дням появится на этапе 3"}},
  "lessons": {"status": "stage", "stage": 2, "roadmap": "R2.6", "note": "появится на этапе 2"},
  "versions": {"status": "stage", "stage": 2, "roadmap": "R2.6", "note": "появится на этапе 2"}},
 "meta": {"built_ms": 140, "cached": false, "excluded": {"shadow": 0, "manual": 2, "no_plan": 0, "no_r": 0}, "warnings": [], "sources": {"trades": 38, "cases": 120, "labels_db": true}}}
```

Имена полей не содержат `key|secret|token|passw|signature` (после `scrub` ничего не теряется). Числа округляются: R и доли до 4 знаков, проценты до 3, `asof` до 30 с. Число полей фиксировано: поле, не имеющее значения, отдаётся `null`, а не отсутствует.

### `config/learning.json` (новый)

```json
{"schema": "tb.learning_cfg/1",
 "verdict": {"recent_n": 50, "guard_trades_ratio": 0.5},
 "windows": [50, 200], "series_points": 120, "weeks": 8, "policy_days": 14,
 "baseline_policies": ["P5", "P6"],
 "memory": {"max_trades": 600, "budget_s": 3.0, "ttl_s": 900},
 "cache": {"ttl_s": 60, "min_interval_s": 10},
 "pf_bootstrap": {"b": 500, "seed": 20261102, "level": 0.9}}
```

Файл необязателен: без него действуют умолчания. `MIN_N` и `ALPHA` в файл не выносятся.

### Таблица кодов причин для интерфейса (слой `human-pages/learning`)

| Код | Фраза |
|---|---|
| `n_lt_min` | «Закрытых сделок меньше 30: выводов делать рано» |
| `no_baseline` | «Сделок мало, чтобы сравнить недавние с прежними (нужно 60)» |
| `not_significant` | «Разница недавних и прежних сделок в пределах случайного разброса» |
| `guard_viol` | «R вырос, но доля нарушений правил тоже выросла» |
| `guard_trades` | «R вырос, но сделок стало заметно меньше» |
| `no_trades`, `no_cases`, `no_usage`, `no_path`, `no_whatif` | «Данных пока нет: ...» с названием источника |
| `mode_unknown` | «Режим пропусков в журнале не определён, чужие цифры не подмешиваются» |
| `labels_off` | «Метки теневых политик не собираются (сервис labels не работает)» |
| `too_many_for_request` | «Считается по расписанию, появится на этапе 3» |
| `E_BLOCK`, `E_REGISTRY` | «Не удалось собрать блок» |

## Correctness Properties

1. Свойство 1 (режимы не смешиваются): для любого набора сделок и случаев с режимами из `{paper, testnet, live}` результат `report.build(mode=M)` не меняется при добавлении, удалении или изменении любых данных других режимов (`random.Random(seed)`, 20 seed). **Validates: Requirements 2.1, 2.5, 4.2, 11.4**
2. Свойство 2 (фильтр сделок мозга): `brain_trades` не возвращает сделки с `shadow`, без `plan_id`, с тегом `manual*`, из `exclude_trades.json` и без R; число исключённых по причинам сходится с `meta.excluded`; порядок результата не зависит от перестановки строк журнала. **Validates: Requirements 2.2, 2.3, 2.4**
3. Свойство 3 (вердикт `few_data` определяется n): для любых значений R при n < 30 состояние `few_data`; при n ≥ 30 состояние никогда не `few_data`; при 30 ≤ n < 60 состояние `no_difference/no_baseline`. **Validates: Requirements 4.2, 4.3**
4. Свойство 4 (монотонность по данным): если во всех «недавних» сделках R увеличить на δ ≥ 0 (разброс «недавних» не меняется), ранг состояния (`few_data` < `no_difference` < `confirmed`) не убывает при тех же защитах; обратный сдвиг на δ ≥ 0 ранг не повышает. **Validates: Requirements 4.5, 4.6**
5. Свойство 5 (защиты не обходятся): если защита по нарушениям или по числу сделок провалена, состояние не `confirmed` при любых R; если `diff ≤ 0`, состояние не `confirmed`; если `p ≥ alpha/K`, состояние не `confirmed`. **Validates: Requirements 4.6, 4.7, 4.8, 4.9**
6. Свойство 6 (сверка с эталоном): значения `r`, `win`, `pf`, `fees_share` окна равны строке `expstats.group_stats` на том же срезе; `welch_summary` равен `expstats._welch` до 1e-9; `capture` равен `mfe_capture`. **Validates: Requirements 3.3, 5.5, 8.3**
7. Свойство 7 (окна и ряды): для сделок с известными R ряд окна W на точке i равен среднему последних `min(W, i)` значений; число точек не больше `series_points`; последняя точка присутствует; первая точка не раньше 30-й сделки. **Validates: Requirements 5.1, 5.2**
8. Свойство 8 (интервалы): t-интервал и интервал Уилсона содержат точечное значение; ширина не растёт с ростом n при фиксированном разбросе; доля 0 и 1 даёт корректный интервал; n < 2 даёт `lo = hi = None`. **Validates: Requirements 3.1, 3.2, 3.5**
9. Свойство 9 (пустые блоки не падают): при отсутствии любого из источников (нет сделок, нет случаев, нет базы меток, нет журнала вызовов, пустой реестр, битый `learning.json`) `report.build` возвращает `ok: true`, все блоки имеют допустимый `status`, `order` полон, исключений нет. **Validates: Requirements 1.8, 1.9, 12.4, 5.6, 6.7, 7.6, 9.6, 11.9**
10. Свойство 10 (изоляция ошибок блока): исключение в одном блоке (внедряется) даёт `status=error/E_BLOCK` только у него, остальные блоки равны результату без ошибки, текст исключения в ответе отсутствует. **Validates: Requirements 1.8**
11. Свойство 11 (детерминизм): два вызова `build` на одних данных и `now` дают побайтно равный JSON (включая бутстреп PF); перестановка строк журнала не меняет результат. **Validates: Requirements 2.3, 3.4, 3.7**
12. Свойство 12 (недели): для любых моментов `week_range` даёт `weeks` подряд идущих ISO-недель без пропусков, последняя содержит `asof` и помечена `partial`; граница года (53-я неделя, 1 января) не ломает порядок; сумма `n_cases` по неделям равна числу случаев в диапазоне. **Validates: Requirements 6.1, 7.2, 9.1**
13. Свойство 13 (отсутствие секретов и сумм): ни одно имя поля ответа (рекурсивный обход) не входит в запрещённый список и не содержит секретных подстрок; строки соответствуют допустимому алфавиту и длине; на данных с `pnl_usdt`, `risk_usdt` и именами с подстрокой `key` значения не появляются в ответе. **Validates: Requirements 1.7, 9.4, 14.1, 14.5**
14. Свойство 14 (чтение): снимок файлов проекта (`state/`, `config/`, `runtime/`) до и после `report.build` и `learning_op` совпадает; открытие базы меток — только `query_only`. **Validates: Requirements 1.10, 13.1, 14.4**
15. Свойство 15 (сравнение с политиками): для любой пары (мозг, политика) `verdict` равен `few_data` при n < 30 с любой стороны; `better` лишь при `diff > 0` и `p < alpha/K`, K = число включённых политик; версии политик не смешиваются; при смене `ctx_ver` в окне берётся только свежий. **Validates: Requirements 11.2, 11.3, 11.5, 11.6**
16. Свойство 16 (кеш и single-flight): N параллельных вызовов `get` на один ключ выполняют загрузчик один раз; исключение загрузчика не кешируется; смена подписи инвалидирует кеш не чаще `min_interval`. **Validates: Requirements 13.2, 13.3**
17. Свойство 17 (память): блок памяти не вызывает walk-forward при `n_trades > max_trades`; при превышении бюджета отдаёт `computing`, а готовый результат совпадает с прямым вызовом `memory.backtest.walk_forward`. **Validates: Requirements 10.1, 10.4, 10.5**

## Error Handling

| Ситуация | Поведение | Код/статус |
|---|---|---|
| Нет `trades.jsonl` / нет сделок режима | `verdict=few_data`, n=0, блоки `no_data` | `no_trades` |
| Нет `cases.jsonl` или нет случаев режима | блоки дисциплины и пропусков `no_data`; защита по нарушениям `guard_unknown` | `no_cases` |
| Пропуски другого режима (`src.mode`) | не подмешиваются | `mode_unknown` |
| Нет базы меток, нет таблицы `policy_runs`, `sqlite3.DatabaseError` | `vs_policy` `no_data`, остальное работает | `labels_off` |
| Реестр политик не проходит валидацию | `vs_policy` `error` | `E_REGISTRY` |
| Журнал вызовов отсутствует | `economics` `no_data` | `no_usage` |
| Журнал вызовов усечён (> 32 МБ) | значения по хвосту, предупреждение | `usage_truncated` |
| Тяжёлый walk-forward (> `max_trades`) | блок памяти `no_data` | `too_many_for_request` |
| Walk-forward не уложился в бюджет | `computing`, повторное сообщение по готовности | `computing` |
| Исключение в расчёте блока | блок `error`, остальные целы, исключение в журнал сервера без значений | `E_BLOCK` |
| Исключение всей сборки | `{"ok": false, "error": "failed"}` | `failed` |
| Битый `config/learning.json` | умолчания, предупреждение в `meta.warnings` | `E_CONF:<поле>` |
| Недопустимый `mode` в запросе | заменяется на `default_mode` / `paper` | |
| Соединение закрылось до ответа | игнорируется | |
| Поле без значения | `null`, поле сохраняется | |
| Деление на ноль, нулевой разброс | значение/интервал/p равны `null`, без исключения | |
| `capture > 1` из-за округления данных пути | число остаётся, предупреждение | `capture_gt_1` |
| Несколько `rules_key` в окне вердикта | вердикт не блокируется, предупреждение | `mixed_rules` |

Фронтенд: ответ без `schema="tb.learning/1"` или с неизвестным `status` блока — блок показывается как «Не удалось показать блок» без исключения; `ok:false` — состояние «Не удалось собрать отчёт»; запоздавший ответ другого режима отбрасывается.

## Testing Strategy

Бэкенд (unittest, `./tb test`; без сети; синтетические данные на `random.Random(seed)`, каждое свойство минимум на 20 seed):

- `tests/test_learn_stats.py`: свойства 8 и 12, сверка `welch_summary` с `expstats._welch`, `mean_est`, `wilson_est`, бутстреп PF (детерминизм, `pf_inf`), `two_prop`, `rolling` (свойство 7).
- `tests/test_learn_load.py`: свойство 2, фильтры `shadow`/`manual`/`no_plan`/`exclude_trades.json`, режимы, пропуски `mode_unknown`, подпись источников, чтение случаев без изменения объектов.
- `tests/test_learn_cache.py`: свойство 16 (потоки), `min_interval`, исключение не кешируется, монотонное время.
- `tests/test_learn_verdict.py`: свойства 1 (для вердикта), 3, 4, 5; граничные n = 29/30/59/60; защиты, `guard_unknown`; `trend=down`; тексты вердикта до 160 символов.
- `tests/test_learn_blocks_result.py` и аналогичные по блокам (`exits`, `discipline`, `skips`, `economics`, `vs_policy`, `memory`, `stages`): сверка с эталоном (свойство 6), ряды и недели, пустые источники (свойство 9), ленивая загрузка базы через `open_ro` (подмена `open_ro` ловит попытку записи), политики на синтетической базе (`policy_runs` с известными R), `ctx_changed`, версии, K и Бонферрони, память: `too_many_for_request`, `computing`, совпадение с `walk_forward`.
- `tests/test_learn_report.py`: свойства 1, 9, 10, 11, 13, 14; размер ответа ≤ 64 КБ; эталонные ответы пишутся в `tests/fixtures_learning/payload_full.json`, `payload_few.json` (для сверки фронта).
- `tests/test_learn_perf.py`: сборка без памяти ≤ 1,0 с на 500 сделках/5 000 случаев/20 000 строк `policy_runs`; кеш ≤ 20 мс; тест на 2 000/20 000/100 000 — при `TB_SLOW_TESTS=1` (≤ 2,0 с).
- `tests/test_dash2_learning.py` (по образцу `test_dash2_exp.py`): операция `learning` отвечает `learning_detail` только своему соединению; недопустимый режим заменяется; исключение даёт `ok:false` без разрыва; повторное сообщение для `computing`; `api_payload("/api/learning")`; тема не добавлена в `TOPICS`.
- `tests/test_layering.py`: правило для `tb.learn` (прямые и транзитивные импорты), проверка, что `tb.llm` импортируется только как `tb.llm.usage` из `load.py`.

Фронтенд (vitest): `entities/learning/lib/learning.test.ts` (приведение ответа, неизвестный статус, подписи «мало данных», форматирование оценок), `shared/lib/human-pages/learning/human-learning.test.ts` (все коды причин имеют фразу, нет эмодзи и сырых кодов), `model/store.test.ts` (запоздавший ответ другого режима, повторное сообщение, моки), `nav.test.ts` (вкладка), тест сверки с эталонами бэкенда (чтение `tests/fixtures_learning/*.json`, проверка вида).

E2E (`frontend/e2e`): фейковый сервер отвечает `learning_detail` по образцам; сценарии: каждое из трёх слов вердикта, блок-заглушка этапа, блок `computing` и повторное сообщение, переключение режима, ширина 390 px без горизонтальной прокрутки, `prefers-reduced-motion`, простой 20 с без повторяющихся запросов; существующие 62 сценария остаются зелёными.

Приёмка: четыре проверки фронта (typecheck, lint:fsd, test, build), пересборка `frontend/dist`, полный `./tb test`, ручная сверка на копии боевых данных (Requirement 17.6).

## Риски

| Риск | Чем снижаем |
|---|---|
| Фильтр «сделки мозга» пропускает ручные сделки | теги `manual*`, `plan_id`; счётчики `meta.excluded`; сверка на данных в задаче 2 |
| Режим у случаев `skip` не отражает режим дня | блок `no_data/mode_unknown` вместо подмешивания; уточнение источника режима в R2.9 |
| Walk-forward на запросе тормозит дашборд | лимит `max_trades`, фон, бюджет ожидания, кеш на 15 минут |
| Интервалы политик оптимистичны (коррелированные сделки, разные модели издержек) | `caveats`, блок не входит в вердикт, подпись на странице |
| Вердикт подгоняется порогами | `MIN_N`/`ALPHA` из `expstats`, в конфиге только окно и отношение числа сделок, `verdict.params` в ответе |
| Имена полей вырезаются `scrub` (`token`, `key`) | запрет подстрок, тест свойства 13, `tok_in`/`tok_out` |
| Рассинхрон контракта бэкенда и типов фронта | общие эталоны `fixtures_learning`, тест сверки (задача 28) |
