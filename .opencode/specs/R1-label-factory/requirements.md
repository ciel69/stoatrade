# Requirements Document

## Introduction

Блоки R1.1–R1.6 роадмапа (`docs/ROADMAP.md`, этап 1 «Фабрика дешёвых меток»). Цель: превратить каждый сигнал сканера (около 3,5 тыс. в сутки) в размеченный пример без участия LLM: признаки на момент сигнала, исходы цены на 15/60/240 мин с MFE/MAE по минутным свечам и результаты набора теневых детерминированных политик. Это даёт объём данных для этапов 2–4 (уроки, память, выходы), в сотни раз больше ~10 сделок мозга в сутки. Попутно: стопы и тейки считает один алгоритм кода в слое `tb.logic` (мозг и тени используют одни формулы), модель больше не называет цены; память случаев мозга перестаёт перечитывать весь файл на каждое обращение; заполняются пустующие признаки случаев.

Вне охвата: использование меток мозгом (этап 3), перенос случаев и уроков в базу знаний (R2.9), автоматическое предложение уроков (R2.0), продвижение политик (R2.7), страница «Учится ли мозг» (R0.2; эта спека даёт ей функции чтения), новые профили геометрии для мозга, изменение лимитов риска, исполнителя и торговли.

## Glossary

- **Сигнал** — строка события в `runtime/hot_events.jsonl` (пишут `hotscan`, `liqwatch`, `flowwatch`, `ideawatch`) одного из типов `capture_types` из `config/labels.json` (по умолчанию `range_5m`, `turnover_surge`, `hot_enter`, `oi_spike`, `oi_flush`, `funding_extreme`, `liq_cascade`, `cvd_divergence`, `book_wall`, `xex_gap`; типы `idea_*` и `patrol` не захватываются).
- **t0** — момент сигнала: поле `ts` события (ISO-8601, UTC, точность до секунды), переведённое в секунды эпохи.
- **Направление сигнала** — поле `direction` события: `up` → `long`, `down` → `short`, пусто или иное → нет направления.
- **База знаний** — файл SQLite `state/knowledge.sqlite` (stdlib `sqlite3`, режим WAL). В этой спеке в ней появляются только таблицы меток; перенос случаев, уроков и плейбука — блок R2.9.
- **Метка** — запись о сигнале в таблицах меток (`signals`, `outcomes`, `policy_runs`): признаки на t0, исходы, результаты политик.
- **Разметчик** — сервис платформы `labels`, который захватывает сигналы и размечает их после созревания.
- **Созревание** — момент `t0 + max(240 мин, max(entry_ttl_min) + MAX_HOLD_MIN[max_horizon]) по включённым политикам + grace_min`.
- **Геометрия** — расчёт кандидатов входа (сторона, уровень, стоп, тейки, доли, горизонт, тип входа) из уровней и контекста по профилю; чистый модуль `tb.logic.geometry`, перенесённый из `brain/cands.py` вместе с функциями и константами `brain/validate.py` и `brain/entryplan.py`, от которых он зависит.
- **Профиль геометрии** — набор параметров геометрии в `config/geometry.json`: `base` и `chase` с числами, равными текущим константам кода (используются мозгом и тенями), и `atr` (только для теней с рыночным входом).
- **Контекст теней** — фиксированный набор входов геометрии для теней (`config/labels.json:shadow_ctx`: уровень риска, тип сессии, максимальный горизонт и др.), заменяющий то, что у мозга приходит из досье.
- **Версия контекста `ctx_ver`** — первые 8 символов sha256 канонического JSON из `costs.bybit`, `shadow_ctx`, горизонтов сессии, `auto_protect`, `latency_s`, `stop_slip_bps`; меняется при любом изменении условий симуляции.
- **Политика** — детерминированное правило входа на сигнал (сторона, тип входа, профиль, максимальный горизонт), версия в реестре `config/shadow_policies.json`.
- **Симулятор** — чистая функция `tb.logic.sim.run`, прогоняющая план по минутным OHLC-свечам.
- **Кандидат** — строка `brief.cands` с готовыми числами геометрии; модель выбирает кандидата номером.
- **Развёрнутый объект входа** — внутренний объект `tb.entry/1` с ценами, который код строит из кандидата (`runner._entry_obj`); модель его не пишет.
- **MFE/MAE** — максимальный ход цены в сторону сигнала / против него за горизонт, в % и в единицах ATR(5м); для политик — в R.
- **Случай** — запись `tb.case/1` в `state/cases.jsonl` (решения мозга; спека меняет только скорость чтения и заполнение признаков).

## Requirements

### Requirement 1: Захват сигналов

**User Story:** As a платформа, I want каждый сигнал сканера сохранять в базе знаний в момент его появления, so that ни один пример не теряется и признаки события берутся на t0.

#### Acceptance Criteria

1. WHEN в `runtime/hot_events.jsonl` появляется новая завершённая строка (оканчивается `\n`) с типом из `capture_types` THE разметчик SHALL записать сигнал в таблицу `signals` не позднее чем через 60 с.
2. THE разметчик SHALL хранить для сигнала: ключ, `t0`, `sym`, `type`, `side` (из `direction`), `score`, `price`, JSON числовых полей события (`range_5m_pct`, `ret_5m_pct`, `turn_surge_5m`, `chg_24h_pct`, `turn_24h_musd`, `spread_bps`, `funding_pct`, а также `oi_*`, `liq_usd`, `cvd_pct`, `xex_bps`, если есть), `wake`, `tradable`, `corr_group`, `env`, исходный `id` события и версию схемы.
3. THE ключ сигнала SHALL выводиться детерминированно из (`t0`, `sym`, `type`) в формате `S-YYYYMMDDHHMMSS-SYM-TYPE`; повторное чтение той же строки SHALL не создавать дубль.
4. WHEN разметчик перезапущен THE разметчик SHALL продолжить чтение с сохранённого смещения и обработать строки, появившиеся за время простоя.
5. WHEN файл событий стал короче сохранённого смещения или изменились его первые 4 КБ THE разметчик SHALL читать файл с начала; дубли SHALL отсекаться ключом.
6. IF последняя строка файла не оканчивается `\n` THEN THE разметчик SHALL не читать её до следующего цикла и не сдвигать смещение за её начало.
7. IF строка не разбирается как JSON, в ней нет `sym`, `ts` или `type`, либо `ts` не разбирается THEN THE разметчик SHALL пропустить её и увеличить счётчик `bad_lines`.
8. THE id событий, создаваемые `hotscan.make_event`, `hotscan.make_deriv_event`, `liqwatch.make_event` и `flowwatch.make_event`, SHALL включать дату `YYYYMMDD`, а прежние id без даты SHALL продолжать читаться потребителями (`outcomes`, `ideawatch`, `scansvc`, `memory.migrate`).

#### Edge Cases

- Две строки с одинаковыми `t0`, `sym`, `type` — одна метка.
- Сигнал без направления — сохраняется; исходы считаются для обеих сторон (Requirement 5.3), политики с привязкой к направлению его пропускают (Requirement 8.4).
- `tradable=false` — сохраняется, политики не прогоняются; нет поля `tradable` (события `liqwatch`/`flowwatch`) — считается `true`.
- Нет `spread_bps` — издержки исходов считаются без спреда с флагом `no_spread`, симулятор берёт спред по умолчанию 2 bps.
- Четыре процесса пишут в файл без блокировки — недописанная строка ждёт следующего цикла (критерий 1.6).

### Requirement 2: Загрузка свечей

**User Story:** As a разметчик, I want получать минутные и 5м/1ч свечи любой монеты публичным REST Bybit, so that исходы и признаки считаются точно и без ключей.

#### Acceptance Criteria

1. THE разметчик SHALL загружать свечи только публичным эндпоинтом `/v5/market/kline` без ключа и без чтения каталога ключей.
2. WHEN метка созрела THE разметчик SHALL загрузить для монеты: 1м свечи за [t0 − 30 мин, момент созревания − grace_min], 300 свечей 5м и 200 свечей 1ч, закрытых не позже t0.
3. THE разметчик SHALL брать 5м свечи BTCUSDT из кеша, общего для всех сигналов одной 5-минутной корзины t0, и загружать их не чаще одного раза на корзину.
4. THE разметчик SHALL отбрасывать текущую незакрытую свечу ответа (время открытия + длительность > времени запроса) и сортировать свечи по возрастанию времени (Bybit отдаёт их в обратном порядке).
5. THE разметчик SHALL ограничивать частоту своих запросов к бирже значением `max_rps` (по умолчанию 5 запросов/с).
6. IF запрос вернул ошибку сети, HTTP 5xx или код Bybit 10002 THEN THE разметчик SHALL повторить его до 3 раз с паузой 2, 5, 15 с; затем отложить метку на `retry_min` (по умолчанию 30 мин); после `max_attempts` (по умолчанию 5) отложений пометить метку `status=failed, code=FETCH`.
7. IF биржа ответила HTTP 403 или 429 THEN THE разметчик SHALL остановить все свои запросы на 60 с и отметить `rate_limited_24h` в heartbeat.
8. IF у монеты нет свечей за окно THEN THE разметчик SHALL пометить метку `status=no_data`.
9. IF в 1м свечах окна есть пропуск длиннее 3 минут THEN THE разметчик SHALL записать `gap_min` и пометить затронутые исходы и прогоны политик флагом `gap`.

#### Edge Cases

- Биржа недоступна часами — очередь созревших меток растёт и обрабатывается по возвращении в порядке t0.
- Разметчик делит IP с другими сервисами (сканер раз в 30 с, структура) — при `max_rps=5` суммарная нагрузка остаётся ниже 10% лимита Bybit 600 запросов / 5 с.

### Requirement 3: Признаки на момент сигнала без заглядывания в будущее

**User Story:** As a разработчик и будущий мозг, I want признаки каждой метки считать только из данных, известных до t0, so that статистика по меткам честная.

#### Acceptance Criteria

1. THE разметчик SHALL вычислять из свечей монеты, закрытых не позже t0: `atr5_pct` (ATR(14) на 5м, в % цены), `range_15m_pct`, `ret_15m_pct`, `ret_1h_pct`, `ret_4h_pct`, `vol_rel` (ATR(14) 5м / медиана ATR(14) по 300 свечам 5м).
2. THE разметчик SHALL вычислять уровни функцией `tb.logic.levels.key_levels(book, now_ms=t0×1000)` на `CandleBook`, заполненной только свечами, закрытыми до t0, с `now=t0` (тогда незакрытой свечи в книге нет), разворачивать результат в список цен уровней и записывать `dist_sup_pct`, `dist_res_pct`, `dist_sup_atr`, `dist_res_atr`, `n_levels_3atr` и снимок уровней `levels` (не больше 12 цен).
3. THE разметчик SHALL вычислять контекст BTC из 5м свечей BTCUSDT, закрытых не позже t0: `btc_ret_1h_pct`, `btc_ret_4h_pct`, `btc_atr5_pct`.
4. THE разметчик SHALL записывать `utc_hour`, `dow`, `utc_session` из t0 и `coin_class` функцией `tb.memory.features.classify_coin` с конфигом `config/memory.json`.
5. WHEN сигнал захвачен не позднее чем через 120 с после t0 и `runtime/context.json` моложе 15 мин (`ts_epoch`) THE разметчик SHALL записать `fng = sources.fear_greed.value`, `dvol = sources.dvol_ETH.value`, `risk_off = risk_off` из этого файла; иначе SHALL записать `null`.
6. THE разметчик SHALL записывать список `missing` с именами признаков, равных `null`.
7. THE ни один признак SHALL не зависеть от свечи, время закрытия которой больше t0 (тест: замена всех свечей после t0 на экстремальные не меняет признаки).
8. WHEN разметчик работает сутки при доступной бирже THE доля меток со `status=done`, у которых пусто хотя бы одно из полей `coverage_fields` (`atr5_pct`, `range_15m_pct`, `ret_1h_pct`, `btc_ret_1h_pct`, `dist_sup_pct`, `dist_res_pct`), SHALL быть ниже 10% (проверка после слияния командой `./tb labels coverage`).

#### Edge Cases

- У монеты меньше 300 свечей 5м — признаки по имеющимся, недостающие в `missing`.
- Уровней нет — расстояния `null`; политики с уровневым входом пишут отказ `NO_LEVEL`.

### Requirement 4: Хранилище меток в базе знаний

**User Story:** As a платформа, I want хранить метки в одном локальном SQLite-файле с индексами, so that запись тысяч меток в сутки и выборки быстрые, без внешних зависимостей.

#### Acceptance Criteria

1. THE хранилище SHALL использовать только stdlib `sqlite3`, файл `state/knowledge.sqlite`, `journal_mode=WAL`, `synchronous=NORMAL`, `journal_size_limit` 64 МБ, `isolation_level=None` с явными транзакциями `BEGIN IMMEDIATE` у писателя и `busy_timeout` не меньше 5000 мс.
2. THE хранилище SHALL иметь таблицы `meta`, `cursor`, `signals`, `outcomes`, `policy_runs` и индексы по `signals(t0)`, `signals(sym, t0)`, `signals(type, t0)`, `signals(status, next_try_ts)`, `policy_runs(policy_id, policy_ver, t0)`, `policy_runs(type, t0)`.
3. THE разметку (признаки, исходы, прогоны политик) SHALL выполнять только сервис `labels`; команды `./tb labels backfill|rerun` SHALL только ставить сигналы в очередь (вставка `pending` или перевод в `pending`) транзакцией `BEGIN IMMEDIATE` и работать при запущенном сервисе; читатели SHALL открывать базу с `PRAGMA query_only=ON`.
4. WHEN версия схемы в коде новее значения в `meta` THE хранилище SHALL применить миграции по порядку в одной транзакции; IF миграция падает THEN транзакция SHALL откатиться, а сервис SHALL завершиться с кодом `SCHEMA_MIGRATION`.
5. THE агрегат по одной политике за 30 дней (n, средний `r_net`, win) SHALL вычисляться не дольше 300 мс на базе с 1 000 000 строк `policy_runs` (тест на синтетической базе запускается при `TB_SLOW_TESTS=1`; в обычном `./tb test` — 100 000 строк и порог 150 мс).
6. THE команда `./tb labels backup` SHALL копировать базу через `sqlite3.Connection.backup` в `state/backups/knowledge-YYYYMMDD.sqlite`, не останавливая сервис, с повтором до 3 раз при `PermissionError`.
7. IF путь к `state/` содержит маркер папки облачной синхронизации (`OneDrive`, `Dropbox`, `iCloud`, `Google Drive`, без учёта регистра) или начинается с `\\` THEN THE сервис SHALL записать предупреждение `W_LABELS_SYNC_DIR` в heartbeat.
8. THE база знаний SHALL не содержать ключей и сумм капитала, баланса или позиций в USDT; только цены, проценты, R и идентификаторы.
9. THE сервис SHALL раз в `checkpoint_min` (по умолчанию 10 мин) выполнять `wal_checkpoint(PASSIVE)`, а раз в сутки — `wal_checkpoint(TRUNCATE)`.

#### Edge Cases

- База повреждена (`sqlite3.DatabaseError` при открытии) — файл переименовывается в `knowledge.corrupt-<ts>.sqlite`, создаётся новый, в heartbeat `last_error=DB_CORRUPT`; курсор начинается заново, дубли отсекает ключ.
- Рост: около 5–10 МБ в сутки; порог пересмотра хранения — 5 ГБ (решается в R2.9).

### Requirement 5: Исходы сигналов

**User Story:** As a разработчик и будущий мозг, I want знать для каждого сигнала, куда и насколько ушла цена за 15/60/240 мин, so that базовая частота «что бывает после такого сигнала» известна точно.

#### Acceptance Criteria

1. WHEN метка созрела и свечи загружены THE разметчик SHALL для горизонтов 15, 60 и 240 мин записать `ret_pct`, `mfe_pct`, `mae_pct`, `mfe_atr`, `mae_atr`, `t_mfe_min`, `t_mae_min`.
2. THE цена t0 SHALL быть open первой 1м свечи, начинающейся не раньше t0.
3. IF у сигнала есть направление THEN MFE/MAE SHALL считаться в его сторону; IF направления нет THEN SHALL записываться строки для `long` и для `short`.
4. THE разметчик SHALL записывать `cost_rt_pct = 2 × (fee + slip) × 100` по `config/risk_profile.json:costs.bybit` плюс `spread_bps / 100` и `net_dir_pct` = направленный `ret_pct − cost_rt_pct`.
5. THE исходы, признаки, прогоны политик и статус `done` одного сигнала SHALL записываться одной транзакцией.

#### Edge Cases

- Горизонт попал на пропуск свечей — флаг `gap` у этого горизонта, остальные пишутся.
- Нулевой объём больше 30 мин подряд внутри горизонта — флаг `halt`.

### Requirement 6: Единая геометрия стопов и тейков в `tb.logic`

**User Story:** As a мозг и тени, I want один алгоритм кода для уровня, стопа, тейков, долей и горизонта, so that тени и мозг считают по одним формулам, а параметры можно улучшать данными.

#### Acceptance Criteria

1. THE модуль `tb.logic.geometry` SHALL содержать перенесённый из `brain/cands.py` алгоритм целиком: склейку уровней (`pool`), перебор уровней по обе стороны, стоп за уровнем с буфером ATR, тейки по уровням и кратным R, выбор горизонта (`_horizon`), выбор типа входа по двум полам стопа, отказы (прежние коды `cands`), ветку `momentum_chase`.
2. THE в `tb.logic.geometry` SHALL переехать все функции и константы `brain/validate.py` и `brain/entryplan.py`, которые использует `brain/cands.py` (как минимум `stop_floor_pct`, `exec_stop_pct`, `_entry_rr`, `chase_impulse`, `_loose`, `ENTRY_MAX_LEVEL_DIST_PCT`, `ENTRY_MAX_LEVEL_DIST_LOOSE_PCT`, `ENTRY_MIN_RR`, `ENTRY_SIZE_MULT_MAX`, `LOOSE_RISK_LEVEL`, `CHASE_MIN_LEVEL`, `CHASE_RANGE_ATR`, `CHASE_SETUP`, `SESSION_ORDER`, `MAX_HOLD_MIN`; полный перечень — обходом зависимостей в задаче 2) с прежними сигнатурами (`stop_floor_pct(ctx, entry_type)` и т. д.); `brain/validate.py` и `brain/entryplan.py` SHALL импортировать их из `tb.logic.geometry` под прежними именами, так что прочий код не меняется.
3. THE функция `tb.logic.geometry.candidates(levels, ctx, profiles) -> list[dict]` SHALL принимать `ctx` того же вида и с теми же ключами, что строит `validate.entry_context` (включая `px`, `a5_pct`, `spread_bps`, `range_5m_pct`, `cost_rt_pct`, `cost_rt_maker_pct`, `min_rr`, `max_stop_pct`, `horizons`, `max_horizon`, `off_plan_ok`, `risk_level`, `day_size_mult`, `surge_x`, `ret_5m`, `stop_streak`, `session_type`, `no_confirm_allowed`, `no_confirm_max`), а `levels` — список цен уровней.
4. THE модуль SHALL не импортировать `tb.brain` и подчиняться правилам чистоты `tb.logic` (`tests/test_layering.py`).
5. THE параметры профилей `base` и `chase` SHALL храниться в `config/geometry.json` с версией `geometry_ver` и SHALL быть равны текущим константам кода; изменение параметров SHALL требовать новой версии. THE профиль `atr` (только для теней с рыночным входом) SHALL задавать стоп `max(stop_floor_pct(ctx, "market"), k_atr × a5_pct)` с `k_atr = 1.5` и тейк `tp_r = 1.5` R; его расчёт — функция `tb.logic.geometry.atr_plan(side, ctx, profile)`.
6. THE `brain/cands.py` SHALL стать обёрткой: собрать `level_pool` и `ctx` из досье, вызвать `tb.logic.geometry.candidates`, вернуть строки прежнего формата плюс поле `geometry_ver`.
7. THE кандидаты, которые строит новый `brain/cands.py`, SHALL совпадать с кандидатами прежней реализации на наборе фикстур досье (не меньше 20, включая chase, отказы и оба типа входа) поле в поле (регрессионный тест, снятый до переноса).
8. IF допустимого кандидата нет THEN строка кандидата SHALL иметь `ok=false` и код причины прежнего словаря `cands`.

#### Edge Cases

- Уровни мозга (`validate.level_pool`: структура, уровни исполнителя, идеи) и уровни теней (`key_levels` по свечам) приходят из разных источников; формулы общие, источник уровней записывается в метку (`levels_src`).

### Requirement 7: Симулятор плана по минутным свечам

**User Story:** As a тени, I want чистый симулятор плана по 1м OHLC с правилами исполнителя, so that результат политики близок к тому, что дал бы paper-исполнитель.

#### Acceptance Criteria

1. THE симулятор SHALL быть чистой функцией `tb.logic.sim.run(plan, rows_1m, t0_s, costs, manage) -> dict` в `tb.logic`.
2. THE симулятор SHALL поддерживать входы: `market` (open первой 1м свечи после `t0 + latency_s`, по умолчанию 60 с); `candle_confirm` (закрытие 5м свечи, собранной из 1м, за уровнем, затем market на следующей минуте); `candle_confirm_limit` (после такого же подтверждения post-only лимит на уровне, как у мозга в scalp); `limit` (лимит на уровне); каждый с `entry_ttl_min`.
3. THE лимит SHALL считаться заполненным целиком, только если цена прошла уровень насквозь хотя бы на 1 тик (`tb.logic.paper.limit_touch`), с комиссией maker; частичные заполнения не моделируются.
4. THE порядок событий внутри одной 1м свечи SHALL быть консервативным: (а) стоп и тейк в одной свече — стоп; (б) заполнение лимита и стоп в одной свече — заполнение, затем стоп; (в) тейк и перенос в безубыток в одной свече — безубыток действует со следующей свечи; (г) свеча подтверждения не проверяется на стоп, проверка начинается со свечи входа.
5. WHEN свеча открывается гэпом за стопом THE стоп SHALL исполняться по open плюс `stop_slip_bps`; WHEN гэп за тейком THE тейк SHALL исполняться по цене тейка.
6. THE симулятор SHALL применять частичные тейки по долям и автозащиту `config/exec_limits.json:auto_protect`, вычисляя уровни переноса стопа функциями `tb.logic.rules` (`profit_floor` с издержками, `stop_slip_bps`, `min_profit_r`; пороги `be_r`, `trail_activate_r`, `trail_lock_frac` как в `_auto_rules`), и закрывать остаток по `MAX_HOLD_MIN[horizon]` по close свечи (в исполнителе срок удержания позицию сам не закрывает; в симуляторе закрывает, чтобы у метки был конец — расхождение принято).
7. THE симулятор SHALL вычитать комиссии и проскальзывание через функции `tb.logic.paper` (`fill_market`, `stop_trigger`, `limit_touch`), подставляя синтетические bid/ask = цена ∓ спред/2 (спред сигнала, при его отсутствии 2 bps), и возвращать `filled`, `entry_ts`, `entry_px`, `exit_reason` (`sl|tp|be|trail|timeout|ttl|no_fill`), `parts`, `r_gross`, `r_net`, `fees_r`, `mfe_r`, `mae_r`, `hold_min`, `truncated`.
8. THE симулятор SHALL быть детерминирован и SHALL давать на ручных сценариях (не меньше 15: каждый `exit_reason`, каждый пункт 7.4, гэпы) значения из таблицы эталонов, из которых не меньше 5 посчитаны вручную в тексте теста (формула и числа), а не вызовом `tb.logic.paper`/`tb.logic.rules`.

#### Edge Cases

- Вход не случился до `entry_ttl_min` — `exit_reason=ttl`, `filled=false`.
- Свечей не хватило до `max_hold` — выход по последней свече, `truncated=true`.

### Requirement 8: Теневые политики

**User Story:** As a платформа, I want прогонять заранее зафиксированный набор детерминированных политик на каждом созревшем сигнале, so that есть базовая линия для решений мозга и сырьё для уроков.

#### Acceptance Criteria

1. THE реестр `config/shadow_policies.json` SHALL описывать политику полями `id`, `ver`, `name`, `kinds` (типы сигналов или `*`), `side` (`with_dir|against_dir|random`), `entry` (`market|candle_confirm|candle_confirm_limit`), `profile` (`base|atr`), `no_tp` (bool), `max_horizon`, `entry_ttl_min`, `enabled`; лишние поля SHALL отклоняться.
2. THE стартовый реестр SHALL содержать 4 политики: P2 `confirm_follow` (с направлением, `candle_confirm_limit`, `base`, `max_horizon` intraday), P4 `fade_confirm` (против направления, `candle_confirm`, `base`, intraday; типы `range_5m`, `liq_cascade`, `oi_flush`), P5 `time_exit_baseline` (с направлением, `market`, `atr`, без тейков, scalp), P6 `random_side_baseline` (сторона `expstats.assign_variant(ключ сигнала, ["long","short"], exp="P6")`, `market`, `atr`, scalp).
3. WHEN метка созрела и свечи загружены THE разметчик SHALL для каждой включённой политики, подходящей по типу сигнала, построить план: для профиля `base` — ближайший к цене `ok`-кандидат стороны политики из `tb.logic.geometry.candidates` (уровни метки, контекст теней), горизонт и тип подтверждения кандидата, вход по полю `entry` на уровне кандидата; для профиля `atr` — `atr_plan` с входом по рынку и горизонтом `max_horizon`; и прогнать план симулятором. Строка `policy_runs` SHALL содержать `policy_id`, `policy_ver`, `geometry_ver`, `ctx_ver`, план (сторона, уровень, стоп, тейки, доли, горизонт, вход) и результат.
4. IF у сигнала нет направления, а политика `with_dir`/`against_dir` THEN строка SHALL записываться с `filled=0, code=NO_DIR`; IF подходящего кандидата нет THEN `filled=0` и код геометрии.
5. THE изменение любого поля политики SHALL требовать нового `ver`; THE статистика SHALL по умолчанию не смешивать версии.
6. IF `tradable=false` THEN политики по сигналу SHALL не прогоняться.
7. THE прогон политик SHALL не создавать планов, ордеров, записей `state/trades.jsonl`, `state/decisions*` и событий шины.
8. THE `./tb labels stats --by policy` SHALL выводить по политике и версии: n, долю заполнений, win, средний `r_net` ± se, PF, MFE/MAE в R, доли `exit_reason`, p против нуля и порог Бонферрони по числу включённых политик (`expstats.bonferroni`).
9. THE контекст теней SHALL задаваться в `config/labels.json:shadow_ctx` (по умолчанию `risk_level` 3, `session_type` "day", `max_horizon` "intraday", `off_plan_ok` true, `day_size_mult` 1.0, `stop_streak` 0, `no_confirm_allowed` false), горизонты сессии — из `config/risk_profile.json:session_types` без поправок уровня риска, `cost_rt_pct` и `cost_rt_maker_pct` — тем же расчётом, что `brain/brief.py` для досье; остальные ключи `ctx` (`px`, `a5_pct`, `spread_bps`, `range_5m_pct`, `surge_x`, `ret_5m`) — из признаков и события метки.

#### Edge Cases

- Монета совпала с открытой сделкой мозга — тень прогоняется (тени не делят слоты).
- Новая политика добавлена позже — `./tb labels rerun --policy ID --since D` прогоняет её на размеченных сигналах, загружая свечи заново.

### Requirement 9: Модель не называет цены

**User Story:** As a владелец, I want чтобы модель выбирала готового кандидата, а все цены считал код, so that решения воспроизводимы, а слабая локальная модель не ошибается в числах.

#### Acceptance Criteria

1. THE схема ответа модели `entry_reply/1` SHALL допускать для `propose` только `candidate` (1..10), `setup`, `thesis`, `conf`, `why`, необязательные `size_mult` (не выше текущего потолка), `tags`, `hypothesis`; для `skip` — `skip_code` и необязательный `watch: {candidate}`.
2. THE `output_schema` роли `entry_scout` SHALL остаться `entry/1` (развёрнутый объект; все сравнения `output_schema == "entry/1"` в `runner.py` и проверка `schema_mod.validate(role.schema, obj)` не меняются); схема ответа модели SHALL указываться отдельным полем роли `reply_schema: "entry_reply/1"`, которое JSON-путь использует для `response_format` и проверки сырого ответа, после чего ответ разворачивается тем же `runner._entry_obj`, что и в режиме инструментов.
3. IF ответ модели содержит поля `level`, `zone`, `stop`, `tp`, `price`, `sl`, `horizon`, `confirm`, `entry_type` THEN THE проверка ответа SHALL отклонить его кодом `PRICE_KEY` с просьбой повтора.
4. IF номер кандидата вне списка или кандидат имеет `ok=false` THEN THE проверка SHALL отклонить `propose` прежними кодами `NO_CANDIDATE:<n>_OF:<m>` или `CANDIDATE_FAILED`.
5. WHEN предпроверка не нашла других причин отказа (`NO_SESSION`, `NO_DATA`, `SYM_BUSY`, `NO_SLOTS`, паузы и т. п.), а список `ctx["cands"]` не пуст и не содержит ни одного `ok`-кандидата THE раннер SHALL не вызывать модель и записать решение `skip` с кодом `NO_CANDIDATES`.
6. THE промпт `config/roles/entry_scout.md`, его `.json` (версия роли +1, поле `reply_schema`) и описания инструментов `es_take`/`es_watch` SHALL соответствовать схеме ответа.
7. THE запись решения мозга о входе (`decisions` `basis`) SHALL содержать `cand: {n, geometry_ver}`; теги плана при этом SHALL не расширяться (лимит 5 тегов не превышается).
8. THE теги развёрнутого объекта SHALL собираться так: сначала теги кандидата, затем теги модели, без повторов, не больше 2 (лимит схемы `entry/1`).

#### Edge Cases

- Модель без инструментов (JSON-режим) — отвечает `{"decision":"propose","candidate":2,...}`; обработчик тот же.

### Requirement 10: Скорость памяти случаев

**User Story:** As a мозг, I want чтобы досье и инструменты памяти не перечитывали весь `cases.jsonl` на каждое обращение, so that память не тормозит при росте.

#### Acceptance Criteria

1. THE `tb.memory.cases` SHALL кешировать свёрнутые случаи в процессе и при следующем чтении дочитывать только хвост файла от сохранённого смещения, если размер не уменьшился и первые 4 КБ не изменились; иначе SHALL перечитывать файл полностью.
2. THE проверка существования случая при записи SHALL использовать множество `case_id` из кеша, обновлённого под блокировкой `cases.lock`.
3. THE `memory.tick` SHALL пропускать миграцию, если размеры и mtime файлов-источников не изменились с прошлого успешного прогона; пересчёт уроков SHALL вызываться как сейчас (только при записанных случаях).
4. THE повторное `cases.load` без изменений файла SHALL выполняться не дольше 50 мс, первое — не дольше 3 с на синтетическом файле из 20 000 случаев (запас на медленные машины; сейчас каждое обращение — полное чтение).
5. THE результат `cases.load` с кешем SHALL совпадать с полным перечитыванием для любой последовательности записей new/update (тест свойства).
6. THE кеш SHALL отдавать вызывающим объекты, изменение которых не портит кеш: вызовы, изменяющие результат, переводятся на копии (перечень находится и правится в задаче), что проверяется тестом «изменил результат — следующий `load` прежний».

#### Edge Cases

- Файл переписан другим процессом — первые 4 КБ изменились, кеш сбрасывается.
- Несколько процессов пишут — блокировка прежняя, каждый дочитывает чужие строки.

### Requirement 11: Полнота признаков случаев мозга

**User Story:** As a мозг, I want чтобы случаи сделок получали признаки, которые сейчас пустуют, so that поиск похожих и уроки работают по реальным данным.

#### Acceptance Criteria

1. THE `brain/brief.save` SHALL всегда сохранять (в том числе в урезанной до 12 КБ версии) компактный блок `snap` не больше 300 байт: `fng = sources.fear_greed.value`, `dvol = sources.dvol_ETH.value`, `risk_off` из `runtime/context.json` и `blackout` из `market` досье; THE писатель случаев SHALL находить досье по `brief_id`, который шлюз уже передаёт при подаче плана, и заполнять из `snap` признаки `fng`, `dvol`, `risk_off`, `blackout` (существующие `migrate._brief` и `features.apply_brief` дополняются чтением `snap`).
2. THE писатель случаев SHALL выводить `confirmed`/`confirm_kind` из плана (`plan_schema.has_confirm` и событие `trigger_fired` с условием `candle`), а не только из тега `no-confirm`.
3. THE писатель случаев SHALL брать `range_5m_pct`, `spread_bps`, `funding_pct`, `turn_surge_5m`, `chg_24h_pct` из `ctx` входа, а при их отсутствии — из события сканера по `refs.event_seq`.
4. THE команда `./tb memory coverage [--since D]` SHALL выводить по каждому признаку долю пустых значений.
5. WHEN изменения работают сутки в paper THE доля пустых `confirmed`, `range_5m_pct`, `spread_bps` среди новых случаев `trade` SHALL быть ниже 20% (проверка после слияния).

#### Edge Cases

- Старые случаи без `brief_id` — признаки остаются `null`.

### Requirement 12: Сервис, команды и наблюдаемость

**User Story:** As a владелец, I want разметчик как обычный сервис платформы с понятным статусом, so that он работает сам и сбои видны без шума в чате.

#### Acceptance Criteria

1. THE сервис `labels` SHALL быть добавлен в `platform/services.py` (`ORDER`, `SERVICE_NAMES`, `DEFAULTS`, `service_cmd`) и в `config/platform.json` для профилей `paper`, `testnet`, `trade`, под супервизором с heartbeat `runtime/platform/labels.hb.json`.
2. THE heartbeat SHALL содержать `captured_24h`, `labeled_24h`, `pending`, `due`, `failed_24h`, `no_data_24h`, `rate_limited_24h`, `req_per_min`, `bad_lines`, `last_error`, `db_mb`, `missing_share_24h`, `warnings`.
3. IF `due` больше 500 или `failed_24h` больше 5% захваченных за 24 ч THEN THE `./tb status` SHALL показывать сервис жёлтым с причиной.
4. THE разметчик SHALL не публиковать событий в шину (ошибки и агрегаты видны в heartbeat и `./tb status`, мозг ими не будится).
5. THE CLI SHALL предоставлять `./tb labels status|stats|coverage|show KEY|backfill --since D|rerun --policy ID --since D|backup|verify`; `show KEY` SHALL печатать JSON сигнала с признаками, исходами и прогонами политик (код 0, нет ключа — код 1); `verify` SHALL выполнять `PRAGMA integrity_check` и проверять, что у каждого сигнала `done` есть исходы трёх горизонтов и строки всех подходящих включённых политик текущих версий, печатать число нарушений и возвращать 0 только при нуле нарушений.
6. THE модуль `tb.labels.query` SHALL предоставлять функции только чтения `policy_stats(root, since, by)`, `outcome_stats(root, since, by, horizon)`, `coverage(root, since)`, `throughput(root)` для страницы «Учится ли мозг».
7. WHEN запускается вторая копия сервиса THE она SHALL завершиться с кодом 2 (блокировка `runtime/platform/labels.lock`).

### Requirement 13: Догрузка истории

**User Story:** As a платформа, I want разметить сигналы, уже накопленные в `hot_events.jsonl`, so that база стартует не с нуля.

#### Acceptance Criteria

1. THE команда `./tb labels backfill --since YYYY-MM-DD` SHALL захватить сигналы из `runtime/hot_events.jsonl` начиная с даты и поставить созревшие в очередь разметки.
2. THE догрузка SHALL соблюдать `max_rps`, SHALL быть возобновляемой без дублей и SHALL показывать прогресс в `./tb labels status`.
3. THE свечи SHALL не храниться в базе; `rerun` SHALL загружать их заново.
4. THE признаки контекста, недоступные задним числом (`fng`, `risk_off`), SHALL при догрузке записываться `null`.

#### Edge Cases

- В истории около 21 тыс. сигналов за 6 дней: при 5 запросах/с и около 3 запросах на сигнал (BTC из кеша) догрузка занимает около 3,5 ч.

### Requirement 14: Слои и безопасность

**User Story:** As a владелец, I want чтобы фабрика меток не могла торговать и не трогала ключи, so that её ошибки не стоят денег.

#### Acceptance Criteria

1. THE пакет `tb.labels` SHALL не импортировать `tb.venues`, `tb.executor`, `tb.gateway`, `tb.llm`, `tb.brain`, `tb.control`, `tb.intel.items` (проверка в `tests/test_layering.py`).
2. THE модули `tb.logic.geometry`, `tb.logic.sim`, `tb.logic.bars` SHALL подчиняться правилам чистоты `tb.logic`.
3. THE окружение сервиса `labels` SHALL не содержать переменных с ключами: `BYBIT_*`, `ANTHROPIC_*`, `DEEPSEEK_*`, `OPENAI_*` и любых, оканчивающихся на `_API_KEY`, `_SECRET`, `_TOKEN` (расширение очистки `platform/services.child_env` для этого сервиса, тест).
4. THE изменения SHALL не менять `config/risk_profile.json`, `config/risk_levels.json`, лимиты guard, размер, плечо и уровень риска.
5. THE `./tb test` SHALL оставаться зелёным; прежние тесты меняются только там, где спека меняет поведение (схема ответа `entry_scout`, кандидаты), с указанием причины в отчёте задачи.

## Clarifications

### Session 2026-11-02

- Q: Где хранить метки: в `state/cases.jsonl` (как задумывала спека 08) или отдельно? → A: отдельно, в локальной базе. MongoDB отклонена (внешний сервер и драйвер нарушают правило «только stdlib», усложняют упаковку); выбран SQLite (stdlib `sqlite3`).
- Q: Переносить ли все знания мозга в SQLite? → A: да, как цель; файл сразу называется `state/knowledge.sqlite`, в этой спеке в нём только метки, перенос случаев, уроков и плейбука — блок R2.9.
- Q: Убирать ли путь, где модель сама называет цены (JSON-режим)? → A: убрать; модель только выбирает кандидата, цены считает код. Цель — максимум прибыли и обучаемости.
- Q: Включать ли запись сигналов и теневые политики сразу? → A: да, сразу после дашборда v2.
- Q: Кто проверяет корректность политик и уроков? → A: не владелец (нет трейдерской экспертизы); проверка только данными. Стартовый набор политик выбран агентом, изменение — новой версией.

## Assumptions

- Публичный `/v5/market/kline` Bybit отдаёт 1м историю минимум за 30 дней назад по линейным контрактам.
- Разрешения 1 мин достаточно для скальп-горизонта; порядок событий внутри минуты неизвестен, поэтому принят консервативный порядок (Requirement 7.4); частичные заполнения лимитов не моделируются.
- Издержки — оценка по `config/risk_profile.json:costs.bybit` плюс спред события, не факт исполнения.
- Объём: около 3,5 тыс. сигналов и до 14 тыс. строк `policy_runs` в сутки, 5–10 МБ базы в сутки.
- Стартовые 4 политики выбраны по `docs/RESEARCH.md` и текущим сетапам; задача — базовая линия и сырьё для уроков, а не прибыль. Политики `momentum_market` (chase) и `level_limit_follow` добавляются следующей версией реестра после первых данных.
- Уровни теней считаются `key_levels` по свечам, уровни мозга берутся из досье; это разные источники при общих формулах.
