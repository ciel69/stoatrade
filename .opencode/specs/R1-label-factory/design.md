# Design

## Overview

Фабрика меток — отдельный сервис `labels` с таблицами в новой базе знаний `state/knowledge.sqlite`. Он читает хвост `runtime/hot_events.jsonl` (тот же источник, что `scansvc`), сразу сохраняет сигнал с полями события, а после созревания догружает свечи публичным REST, считает признаки на t0, исходы и прогоняет теневые политики чистым симулятором. Кандидатов входа и для теней, и для мозга строит один модуль `tb.logic.geometry`, перенесённый из `brain/cands.py` без изменения чисел. Модель выбирает кандидата номером, а цены в развёрнутый объект входа подставляет код. Память случаев получает кеш с дочитыванием хвоста и починку признаков.

Рассмотренные и отклонённые варианты:
- Сигналы в `state/cases.jsonl` (спека 08 `signal`): отклонено — тысячи строк в сутки в журнале с хеш-цепочкой, полное перечитывание на каждое обращение мозга, смешение разметки с решениями мозга во всех местах чтения.
- Тени через исполнитель (`expstats.shadow_variants`): отклонено — потолок `paper.max_active=20`, полный guard и слоты, привязка к плану мозга; не тянет тысячи прогонов в сутки и задним числом.
- Доработка `marketdata/outcomes.py` (точка lastPrice раз в 30 с): отклонено — путь цены между опросами не виден; `outcomes.py` не трогается, он нужен статистике идей.
- MongoDB/DuckDB: отклонено — внешние зависимости. SQLite из stdlib.
- Хранение свечей в базе: отклонено — объём; `rerun` перекачивает.
- Событие шины `labels_tick` и ошибки разметчика в шину: отклонено — событие `error` будит мозг (`PRI_WAKE`), агрегаты уже есть в heartbeat.
- Новые профили геометрии для мозга в R1: отложено — регрессия кандидатов мозга должна быть точной; новые профили вводятся тенями и продвижением (R2.7).
- Теги `geom.`/`prof.`/`brief.` в плане: отклонено — лимит 5 тегов (`plan_schema`, `gateway/catalog.py:639`); `brief_id` уже передаётся отдельным полем, `geometry_ver` пишется в `decisions.basis`.

## Architecture

```
src/tb/
  logic/
    bars.py          НОВЫЙ  свечи: нормализация ответа Bybit, отсев незакрытой, closed_before, agg 1м->5м, atr, ret/range, gaps, path_stats
    geometry.py      НОВЫЙ  перенос brain/cands.py: pool, stop_floor_pct, candidates(levels, px, ctx, profiles), chase
    sim.py           НОВЫЙ  симулятор плана по 1м OHLC
  labels/            НОВЫЙ  I/O-пакет; не импортирует venues/executor/gateway/llm/brain/control/intel.items
    __init__.py
    schema.py        DDL версий, статусы, коды
    store.py         open_rw/open_ro, миграции, tx, backup, проверка папки синхронизации, порча файла
    capture.py       хвост hot_events.jsonl -> signals (курсор, неполная строка, capture_types)
    klines.py        публичный GET /v5/market/kline, Limiter, повторы, пауза на 403/429, кеш BTC 5м
    features.py      признаки на t0 (logic.bars, logic.levels.key_levels на CandleBook из закрытых свечей), контекст
    policies.py      реестр config/shadow_policies.json, выбор кандидата для политики
    label.py         разметка одной созревшей метки (одна транзакция)
    service.py       цикл: capture каждые 15 с, label очереди due, heartbeat, checkpoint, блокировка
    query.py         чтение: policy_stats, outcome_stats, coverage, throughput
    cli.py           ./tb labels ...
  brain/
    cands.py         ИЗМЕНЁН  обёртка над logic.geometry.candidates; + geometry_ver
    validate.py      ИЗМЕНЁН  stop_floor_pct из logic.geometry; схема ответа entry_reply/1; PRICE_KEY, CAND_NOT_OK, NO_CANDIDATE; ENTRY (развёрнутый) остаётся
    runner.py        ИЗМЕНЁН  cands в ctx до предпроверки; NO_CANDIDATES без модели; JSON-путь через _entry_obj; basis.cand
    entryplan.py     без изменений логики (получает развёрнутый объект)
  memory/
    cases.py         ИЗМЕНЁН  кеш с дочитыванием хвоста; _exists/get по кешу
    tick.py          ИЗМЕНЁН  пропуск migrate при неизменных источниках
    features.py      ИЗМЕНЁН  brief_id из refs, confirmed из плана, рыночные поля из ctx/события
    cli.py           ИЗМЕНЁН  ./tb memory coverage
  gateway/catalog.py ИЗМЕНЁН  при подаче плана сохраняет досье по brief_id, если его нет в state/briefs/
  marketdata/hotscan.py, liqwatch.py, flowwatch.py  ИЗМЕНЁН  id события с датой
  platform/services.py  ИЗМЕНЁН  сервис labels, расширенная очистка окружения
  platform/status.py    ИЗМЕНЁН  жёлтый статус labels по порогам
  __main__.py           ИЗМЕНЁН  группа labels
config/
  geometry.json        НОВЫЙ
  shadow_policies.json НОВЫЙ
  labels.json          НОВЫЙ
  platform.json        ИЗМЕНЁН  сервис labels в профилях paper/testnet/trade
  roles/entry_scout.md|.json  ИЗМЕНЁН  ответ entry_reply/1, версия +1
tests/  (см. Testing Strategy)
```

Поток:

```
hotscan/liqwatch/flowwatch -> runtime/hot_events.jsonl --capture(15 с)--> signals(status=pending, mature_ts)
                                                                               | mature_ts <= now
                                                                               v
label_one: klines 1м [t0-30м, t0+240м+maxhold], 5м x300 и 1ч x200 (<= t0), BTC 5м (кеш корзины)
           -> features(t0) -> outcomes(15/60/240) -> для политики: geometry.candidates -> выбор -> sim.run
           -> одна транзакция: signals.status=done + feat + outcomes + policy_runs

мозг: brief -> cands.py -> logic.geometry.candidates -> brief.cands -> модель: candidate n -> runner._entry_obj -> tb.entry/1 (цены из кандидата) -> validate -> entryplan.build
```

## Components and Interfaces

### tb.logic.bars

```python
Row = tuple  # (open_ts_s:int, o, h, l, c, v)
def from_bybit(list_rows, tf_s, now_s) -> list[Row]   # сортировка по возрастанию, отсев незакрытой свечи
def closed_before(rows, t0_s, tf_s) -> list[Row]      # open_ts + tf_s <= t0_s
def agg(rows_1m, tf_min) -> list[Row]                 # выравнивание по UTC
def atr(rows, n=14) -> float | None
def ret_pct(rows, minutes, tf_min) -> float | None
def range_pct(rows, minutes, tf_min) -> float | None
def gaps(rows_1m, max_gap_min=3) -> list[tuple]
def path_stats(rows_1m, start_s, px0, side, minutes, atr_pct) -> dict
#  -> ret_pct, mfe_pct, mae_pct, mfe_atr, mae_atr, t_mfe_min, t_mae_min, gap(bool), halt(bool)
```

### tb.logic.geometry

```python
# перенесено из brain/validate.py и brain/entryplan.py с прежними сигнатурами (validate/entryplan импортируют отсюда):
def stop_floor_pct(ctx, entry_type) -> float
def exec_stop_pct(...) ; def _entry_rr(...) ; def chase_impulse(...) ; def _loose(ctx) -> bool
ENTRY_MAX_LEVEL_DIST_PCT, ENTRY_MAX_LEVEL_DIST_LOOSE_PCT, ENTRY_MIN_RR, ENTRY_SIZE_MULT_MAX, LOOSE_RISK_LEVEL,
CHASE_MIN_LEVEL, CHASE_RANGE_ATR, CHASE_SETUP, SESSION_ORDER, MAX_HOLD_MIN   # полный перечень — обходом зависимостей cands.py
# перенесено из brain/cands.py:
def pool(levels: list[float], cluster_pct: float) -> list[float]
def candidates(levels: list[float], ctx: dict, profiles: dict) -> list[dict]
#  ctx — словарь вида validate.entry_context (те же ключи: px, a5_pct, spread_bps, range_5m_pct, cost_rt_pct, cost_rt_maker_pct,
#        min_rr, max_stop_pct, horizons, max_horizon, off_plan_ok, risk_level, day_size_mult, surge_x, ret_5m, stop_streak,
#        session_type, no_confirm_allowed, no_confirm_max, ...)
#  -> строки в формате brain/cands.py (n, ok, fail, side, setup, level, zone, stop, tp[], horizon, confirm, entry_type, rr, ...)
# новое, только для теней:
def nearest_ok(cands: list[dict], side: str, px: float, include_chase: bool = False) -> dict | None   # ok-кандидат стороны с минимальным |level - px|; momentum_chase пропускается (вход по рынку, level == px выигрывал бы всегда)
def atr_plan(side: str, ctx: dict, profile: dict) -> dict | None       # профиль atr: стоп max(stop_floor_pct(ctx,"market"), k_atr*a5), тейк tp_r R; None при нет цены/неверной стороне -> код NO_ATR_PLAN
```
Перенос механический: константы `cands.py` читаются из профилей `base`/`chase`, порядок и формат строк не меняется. Регрессионные фикстуры снимаются со старого `cands.candidates` до переноса.

### tb.logic.sim

```python
def run(plan: dict, rows_1m: list, t0_s: int, costs: dict, manage: dict) -> dict
# plan: side, entry{kind: market|candle_confirm|candle_confirm_limit|limit, level, ttl_min}, stop, tps[], fracs[], max_hold_min, tick
# costs: fee, maker_fee, slip (доли, как risk_profile.costs.bybit), stop_slip_bps, spread_bps (сигнала или 2)
# manage: latency_s + весь блок exec_limits.auto_protect (be_r, min_profit_r, trail_activate_r, trail_lock_frac, ...)
# -> filled, entry_ts, entry_px, exit_reason, parts[{ts, px, frac, why}], r_gross, r_net, fees_r, mfe_r, mae_r, hold_min, truncated
```
Внутри свечи: подтверждение → вход → (стоп с учётом гэпа) → тейки по порядку → обновление автозащиты на close (действует со следующей свечи). Цены исполнения — через `tb.logic.paper.fill_market/stop_trigger/limit_touch` с синтетическими bid/ask = цена ∓ спред/2; уровни переноса стопа — через `tb.logic.rules.profit_floor` и пороги `_auto_rules`. Выход по `MAX_HOLD_MIN[horizon]` — соглашение симулятора (исполнитель срок сам не закрывает).

### tb.labels.store

```python
def open_rw(root) -> sqlite3.Connection    # isolation_level=None, WAL, synchronous=NORMAL, journal_size_limit, busy_timeout=5000, migrate()
def open_ro(root) -> sqlite3.Connection | None   # обычное открытие + PRAGMA query_only=ON; нет файла -> None
@contextmanager
def tx(conn)                                # BEGIN IMMEDIATE / COMMIT / ROLLBACK
def backup(root, dst=None) -> str
def sync_dir_warning(root) -> str | None
```

### tb.labels.capture

```python
def sig_key(t0_s: int, sym: str, typ: str) -> str
def parse_event(line: str) -> dict | None   # ts ISO -> t0, direction -> side
def poll(conn, root, cfg, now_s) -> dict    # {"new", "bad_lines", "offset"}
```
Курсор: `cursor(src='hot_events', path, offset, head_sha, ts)`.

### tb.labels.klines

```python
class Limiter:
    def __init__(self, rps: float, clock, sleep): ...
    def wait(self) -> None
def fetch(sym, interval, start_s, end_s, limiter, http=None, now_s=None) -> list   # пагинация по 1000, повторы 2/5/15, RateLimited на 403/429
class BtcCache: def get(self, t0_s) -> list    # 5м BTCUSDT по корзине t0 // 300
```

### tb.labels.features / policies / label / query

```python
features.compute(rows_1m, rows_5m, rows_1h, btc_5m, t0_s, sym, ev, ctx_snapshot) -> (feat: dict, missing: list)
policies.load(root) -> list[dict]
policies.shadow_ctx(root, cfg, feat, ev) -> dict        # ctx вида entry_context из shadow_ctx + признаков; ctx_ver
policies.pick(policy, sig, feat, ctx, geom_cfg) -> (plan | None, code | None)   # base: nearest_ok -> вход policy.entry на уровне кандидата; atr: atr_plan, market
label.due(conn, now_s, limit) -> list[row]
label.label_one(conn, root, row, cfg, policies, geom_cfg, fetch, btc_cache, now_s) -> str   # итоговый статус
query.policy_stats(root, since=None, by=("policy_id","policy_ver"), kinds=None) -> list[dict]
query.outcome_stats(root, since=None, by=("type",), horizon=60) -> list[dict]
query.coverage(root, since=None) -> dict
query.throughput(root) -> dict
```

### Мозг

- `validate.ENTRY_REPLY` (`entry_reply/1`): ответ модели; `validate.ENTRY` (`tb.entry/1`): развёрнутый объект, как сейчас; `output_schema` роли остаётся `entry/1`, новое поле роли `reply_schema: "entry_reply/1"`.
- JSON-путь `entry_scout`: `response_format`/проверка сырого ответа по `reply_schema` → `PRICE_KEY` → `runner._entry_obj` (прежние коды `NO_CANDIDATE:n_OF:m`, `CANDIDATE_FAILED`) → прежняя проверка `entry/1` и `semantic`. Режим инструментов не меняется.
- `entry_precheck`: `NO_CANDIDATES` — последней проверкой, только если список `ctx["cands"]` не `None` (при занятой монете он `None`, тогда сработает `SYM_BUSY`).
- Слияние тегов: теги кандидата, затем модели, без повторов, не больше 2.
- `decisions.basis` для решения входа: `cand: {n, geometry_ver}`.

### Память

- `cases._Cache`: `{size, mtime, head_sha, offset, raw_rows, folded, ids}`; `load()` дочитывает хвост; `append_new`/`append_update` под блокировкой обновляют кеш и проверяют `case_id in ids`.
- `tick`: снимок `{path: [size, mtime]}` источников в `runtime/memory/tick_state.json`; без изменений `migrate` не вызывается.
- `features`: `brief_id` из `refs.brief_id`; `fng`/`dvol`/`risk_off`/`blackout` из блока `snap` сохранённого досье; `confirmed` из `plan.spec` + `decisions` `trigger_fired`; рыночные поля из `ctx` или события шины по `refs.event_seq`.
- `brain/brief.save`: блок `snap` (≤ 300 байт) сохраняется всегда, в том числе в урезанной версии.

## Data Models

### SQLite `state/knowledge.sqlite`, схема меток v1

```sql
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);                       -- labels_schema_ver
CREATE TABLE cursor(src TEXT PRIMARY KEY, path TEXT, offset INTEGER NOT NULL, head_sha TEXT, ts INTEGER);
CREATE TABLE signals(
  key TEXT PRIMARY KEY, t0 INTEGER NOT NULL, sym TEXT NOT NULL, type TEXT NOT NULL, side TEXT,
  score REAL, price REAL, wake INTEGER, tradable INTEGER, corr_group TEXT, env TEXT, ev_id TEXT,
  ev TEXT NOT NULL, feat TEXT, missing TEXT, levels_src TEXT,
  status TEXT NOT NULL, code TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  mature_ts INTEGER NOT NULL, next_try_ts INTEGER NOT NULL,
  captured_ts INTEGER NOT NULL, labeled_ts INTEGER, gap_min REAL, schema_ver INTEGER NOT NULL);
CREATE INDEX ix_sig_t0 ON signals(t0);
CREATE INDEX ix_sig_sym ON signals(sym, t0);
CREATE INDEX ix_sig_type ON signals(type, t0);
CREATE INDEX ix_sig_due ON signals(status, next_try_ts);
CREATE TABLE outcomes(
  key TEXT NOT NULL, h INTEGER NOT NULL, side TEXT NOT NULL,
  ret_pct REAL, mfe_pct REAL, mae_pct REAL, mfe_atr REAL, mae_atr REAL, t_mfe_min REAL, t_mae_min REAL,
  cost_rt_pct REAL, net_dir_pct REAL, flags TEXT,
  PRIMARY KEY(key, h, side));
CREATE TABLE policy_runs(
  key TEXT NOT NULL, policy_id TEXT NOT NULL, policy_ver INTEGER NOT NULL,
  geometry_ver TEXT NOT NULL, ctx_ver TEXT NOT NULL,
  t0 INTEGER NOT NULL, sym TEXT NOT NULL, type TEXT NOT NULL, side TEXT,
  plan TEXT, code TEXT, filled INTEGER NOT NULL, exit_reason TEXT,
  r_gross REAL, r_net REAL, fees_r REAL, mfe_r REAL, mae_r REAL, hold_min REAL, flags TEXT,
  PRIMARY KEY(key, policy_id, policy_ver));
CREATE INDEX ix_pr_pol ON policy_runs(policy_id, policy_ver, t0);
CREATE INDEX ix_pr_type ON policy_runs(type, t0);
```
Статусы `signals.status`: `pending`, `done`, `no_data`, `failed`, `skipped` (тип вне `capture_types` после смены конфига).

### `config/geometry.json`

```json
{"geometry_ver": "g1", "cluster_pct": 0.15,
 "profiles": {
   "base":  {"stop_buf_atr": 0.1, "tp1_min_r": 1.0, "tp1_max_r": 1.5, "scalp_min_tp_costs": 3.0, "max_ok": 3, "max_fail": 2},
   "chase": {"zone_pct": 0.3, "stop_atr": 0.25, "tp_r": 2.5, "surge_x": "<из CHASE_SURGE_X>", "range_atr": "<из CHASE_RANGE_ATR>"},
   "atr":   {"k_atr": 1.5, "tp_r": 1.5}}}
```
`candidates(levels, ctx, profiles)` принимает `profiles` и как весь `config/geometry.json`, и как словарь профилей; пропущенные ключи берутся из `DEFAULT_PROFILES` в коде (тест проверяет равенство файла и `DEFAULT_PROFILES`). Пороги импульса chase живут в профиле `chase`, их изменение требует нового `geometry_ver`. Ключи и значения задача 2 сверяет с фактическими константами `brain/cands.py` (в том числе неупомянутыми здесь) и дополняет так, чтобы регрессия была точной.

### `config/shadow_policies.json`

```json
{"schema": "tb.shadow_policies/1", "policies": [
 {"id": "P2", "ver": 1, "name": "confirm_follow", "kinds": ["range_5m","turnover_surge","hot_enter","oi_spike","liq_cascade"], "side": "with_dir", "entry": "candle_confirm_limit", "profile": "base", "no_tp": false, "max_horizon": "intraday", "entry_ttl_min": 30, "enabled": true},
 {"id": "P4", "ver": 1, "name": "fade_confirm", "kinds": ["range_5m","liq_cascade","oi_flush"], "side": "against_dir", "entry": "candle_confirm", "profile": "base", "no_tp": false, "max_horizon": "intraday", "entry_ttl_min": 30, "enabled": true},
 {"id": "P5", "ver": 1, "name": "time_exit_baseline", "kinds": ["*"], "side": "with_dir", "entry": "market", "profile": "atr", "no_tp": true, "max_horizon": "scalp", "entry_ttl_min": 2, "enabled": true},
 {"id": "P6", "ver": 1, "name": "random_side_baseline", "kinds": ["*"], "side": "random", "entry": "market", "profile": "atr", "no_tp": false, "max_horizon": "scalp", "entry_ttl_min": 2, "enabled": true}]}
```
Для профиля `base` горизонт берётся у кандидата, но не выше `max_horizon` политики (`max_horizon` передаётся в ctx геометрии); созревание по самой длинной паре `entry_ttl_min + MAX_HOLD_MIN[max_horizon]` (intraday: 30 + 240 мин) — около 4 ч 40 мин после t0.

### `config/labels.json`

```json
{"enabled": true, "base_url": "https://api.bybit.com", "max_rps": 5, "grace_min": 10, "retry_min": 30, "max_attempts": 5,
 "horizons": [15, 60, 240], "latency_s": 60, "capture_every_s": 15, "label_batch": 20, "checkpoint_min": 10,
 "capture_types": ["range_5m","turnover_surge","hot_enter","oi_spike","oi_flush","funding_extreme","liq_cascade","cvd_divergence","book_wall","xex_gap"],
 "warn": {"due": 500, "failed_share": 0.05},
 "shadow_ctx": {"risk_level": 3, "session_type": "day", "max_horizon": "intraday", "off_plan_ok": true, "day_size_mult": 1.0, "stop_streak": 0, "no_confirm_allowed": false},
 "default_spread_bps": 2,
 "coverage_fields": ["atr5_pct","range_15m_pct","ret_1h_pct","btc_ret_1h_pct","dist_sup_pct","dist_res_pct"]}
```

## Correctness Properties

1. **Ключ сигнала устойчив к повторам.** Повторное чтение, перезапуск и чтение с начала дают то же множество ключей. **Validates: Requirements 1.3, 1.4, 1.5**
2. **Неполная строка не теряется.** Строка без `\n` в конце не читается и подхватывается целиком в следующем цикле. **Validates: Requirements 1.6**
3. **Нет заглядывания в будущее.** Замена любых свечей с закрытием после t0 на произвольные не меняет `feat`. **Validates: Requirements 3.1, 3.2, 3.3, 3.7, 2.4**
4. **Исход согласован со свечами.** `mfe_pct >= max(0, направленный ret)`, `mae_pct <= min(0, направленный ret)`, `0 <= t_mfe_min <= h`. **Validates: Requirements 5.1, 5.3**
5. **Регрессия кандидатов мозга.** Новый `cands.candidates` == прежний на фикстурах. **Validates: Requirements 6.6, 6.7, 6.8**
6. **Пороги геометрии.** Для любого `ok`-кандидата: стоп по нужную сторону, стоп не ближе пола и не дальше `max_stop_pct`, `tp1 >= tp1_min_r × R`, RR после издержек не ниже `min_rr`. **Validates: Requirements 6.1, 6.2**
7. **Консервативный и детерминированный симулятор.** Правила 7.4 и 7.5 соблюдаются, одинаковый вход даёт одинаковый результат, ручные сценарии совпадают с эталоном. **Validates: Requirements 7.3, 7.4, 7.5, 7.6, 7.8**
8. **Тени не торгуют.** Прогон разметки не меняет `state/plans/`, `state/trades.jsonl`, `state/decisions*`, `state/bus/` и не пишет событий шины. **Validates: Requirements 8.7, 12.4**
9. **Модель не задаёт цену.** Ответ с ценовым полем отклоняется; level/stop/tp развёрнутого объекта равны полям выбранного кандидата. **Validates: Requirements 9.1, 9.2, 9.3**
10. **Кеш памяти эквивалентен полному чтению и не портится вызывающими.** **Validates: Requirements 10.1, 10.2, 10.5, 10.6**
11. **Атомарность.** Сбой посреди разметки сигнала не оставляет частичных строк; сбой миграции оставляет прежнюю схему. **Validates: Requirements 4.4, 5.5**
12. **Окружение без ключей.** У процесса `labels` нет переменных из списка 14.3. **Validates: Requirements 14.3, 2.1**

## Error Handling

| Ситуация | Реакция | Видимость |
|---|---|---|
| Битая строка или нет `ts`/`sym`/`type` | пропуск, `bad_lines++` | heartbeat |
| Неполная последняя строка | ждём следующий цикл | — |
| Сеть/5xx/10002 | 3 повтора 2/5/15 с, затем `next_try_ts += retry_min`, после `max_attempts` `failed/FETCH` | heartbeat |
| 403/429 | пауза 60 с всем запросам | heartbeat `rate_limited_24h` |
| Нет свечей | `no_data` | heartbeat |
| Пропуск > 3 мин | флаг `gap`, `gap_min` | outcomes/policy_runs.flags |
| Нет кандидата для политики | `filled=0`, код | `labels stats` |
| База повреждена | переименование, новая база, `last_error=DB_CORRUPT` | heartbeat, статус жёлтый |
| Миграция упала | откат, выход `SCHEMA_MIGRATION` | `./tb status` |
| Вторая копия | выход с кодом 2 | лог |
| Ответ модели с ценой | `PRICE_KEY`, повтор формата, затем прежний механизм запасного профиля | `decisions` |

## Testing Strategy

- `logic/bars|geometry|sim`: юнит-тесты, свойства на `random.Random(seed)`; регрессионные фикстуры кандидатов снимаются со старого `cands.candidates` первым шагом задачи 2; 15+ ручных сценариев симулятора с эталоном.
- `labels/store`: временный `TB_ROOT`, миграции и откат, читатель `query_only` параллельно писателю, backup, порча файла, предупреждение о папке синхронизации, скорость агрегата на 1 000 000 строк (`executemany`, порог 300 мс).
- `labels/capture`: неполная строка, усечение, изменение начала файла, перезапуск, дубли, битые строки, фильтр типов, `direction` → `side`.
- `labels/klines`: фейковый HTTP — пагинация, обратный порядок, незакрытая свеча, повторы, 403/429, ограничитель, кеш BTC; отсутствие обращения к каталогу ключей.
- `labels/features`: свойство 3, новая монета, нет уровней, устаревший `context.json`.
- `labels/policies`: P2/P4/P5/P6 на фикстурах, `NO_DIR`, отказ геометрии, невалидный реестр, версии не смешиваются.
- `labels/label`, `service`: полный цикл на фейковом fetch, атомарность (исключение посреди записи), свойство 8 (снимок файлов до/после), пороги статуса, блокировка второй копии, свойство 12.
- Мозг: фикстуры `tests/fixtures_roles/` для `entry_reply/1`, `PRICE_KEY`, `CAND_NOT_OK`, `NO_CANDIDATE`, `NO_CANDIDATES` без вызова модели, равенство плана из `es_take` и JSON-пути.
- Память: свойство 10, скорость на 20 000 случаях, пропуск `migrate`, `memory coverage`, признаки из `brief_id`/плана/события.
- Слои: `test_layering.py` для `tb.labels` и новых модулей `tb.logic`.
- Полный `./tb test` зелёный; сеть не используется.
