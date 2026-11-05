# Архитектура

Пакет `src/tb/`, единая точка запуска `./tb <группа> <команда> [аргументы]` (`./tb help`); на Windows `tb.cmd` / `tb.ps1` (см. `WINDOWS.md`). Только stdlib, Python 3.9.

## Слои (зависимости только сверху вниз)
```
tb (sh) | tb.cmd | tb.ps1  ->  tb/launcher.py (ключ Bybit, прокси, UTF-8)  ->  tb/__main__.py (диспетчер групп)
core/          journal, session, portfolio, guard, pending (ждущие лимиты, state/pending_orders.json), tradectx (снимок рынка на входе, теги ошибок), tradepath (MAE/MFE от сканера, runtime/trade_paths.json) (профиль риска): решения «можно ли входить и каким размером»
venues/        base.Venue (open/close/positions/balance) и реализации: bybit.py (BybitVenue + CLI), bybit_limit.py (ждущие лимитные входы), bybit_stream.py (watch)
marketdata/    feed, wsclient, features, history, funding_history, watch, context, data, collect, patterns (чистые функции фигур/уровней), patterns_scan (`./tb patterns scan`), hotscan (сканер горячих монет по всему рынку Bybit, `./tb bybit read scan`; вывод runtime/hot.json и runtime/prices.json)
cmkt/          сводный рынок (R1.7): conf, focus, adapter и ex/<биржа>, collector, aggregate, store, compat, service, cli; читатель для потребителей — core/cmktdoc.py, чистая математика признаков — logic/cmkt.py
research/      research_*.py (в т.ч. research_patterns: `./tb research patterns`), compare_venues, backtest, paper_donchian (не торгуют)
dash2/         дашборд по умолчанию (`./tb dash`, порт 8765): server.py (статика `frontend/dist` и WebSocket `/ws`), topics.py (темы), ctlbridge.py (мост команд в ctl); фронт Vue в `frontend/`
dash/          слой чтения и управления для dash2 (старый веб-интерфейс удалён 2026-11-02): model.py и model2.py (чтение файлов и расчёты), ctlweb.py (проверки и очередь ctl, база моста), keyweb.py (запись ключей LLM), server.py (только `api_payload` и реестр `EXT_API` без HTTP)
learn/         страница «Учится ли мозг» (R0.2): статистика, блоки и сборка отчёта `tb.learning/1`, только чтение; операция сокета `learning` (раздел ниже «Страница «Учится ли мозг»»)
legacy_engine/ signals, risk, state, tick, sizing: старый автоматический движок, живой торговлей не используется
paths.py       ЕДИНСТВЕННОЕ место с путями: корень от расположения файла или TB_ROOT
launcher.py    платформенная логика входа: env-файл ключа Bybit, снятие прокси, UTF-8 консоль; fsutil.py: os.replace с повторами для Windows
```
`core` не знает про биржи; `venues` зовут `core.guard` и `core.journal`; `marketdata` не знает про торговлю. `cmkt` собирает только публичные данные, не знает торговлю, ключи и мозг; потребители читают его через `core.cmktdoc` или файлы `runtime/`.

## Где что лежит (корень проекта)
| Папка | Что |
|---|---|
| `config/` | risk_profile, config, feed_config, watch_config, context_config, macro_events, patterns_config (только настройки) |
| `state/` | trades.jsonl, sessions.jsonl, portfolio.json, pending_orders.json (ждущие лимиты), state.json/journal.jsonl (старый движок), `plans/` |
| `data/` | CSV свечей, тиков и funding, history_report_*.json |
| `runtime/` | выходы фида и наблюдателей (market.json, events.jsonl, ...), создаются демонами |
| `docs/` | TRADING, HARNESS, RESEARCH, SPEC*, PERMISSIONS, ARCHITECTURE |
| `tests/` | unittest; `_bootstrap.py` добавляет `src/` в путь |

Пути берутся только так: `from tb import paths; paths.state("trades.jsonl")`, `paths.config(...)`, `paths.data(...)`, `paths.runtime(...)`.
Каждая функция принимает `root=` (для `--root` и тестов); `TB_ROOT=/другая/папка` меняет корень для всего процесса.
Руками `os.path.join(HERE, ...)` не писать.

## Как добавить
- Новую биржу: `venues/<имя>.py`, класс-наследник `venues.base.Venue` (open/close/positions/balance, размер и плечо только из `core.guard.evaluate`,
  журнал через `core.journal` с `venue=<имя>`); ключ-профили в `./tb` (ветка группы, если нужен свой env-файл); группа в `SIMPLE` в `__main__.py`;
  тест с поддельным транспортом (как `tests/test_bybit_exec.py`); строка про права в `docs/PERMISSIONS.md`.
- Новую стратегию: исследование в `research/<имя>.py` (+ запись в `RESEARCH.md`), запуск через словарь `RESEARCH` в `__main__.py`.
  В торговлю стратегия попадает только как тезис агента, проверенный `guard`; свои лимиты и формулы риска не заводить.
- Новый тип сессии: только в `config/risk_profile.json:session_types` (max_minutes, budget_frac, лимит сделок). Код не менять;
  описать в `docs/HARNESS.md`.
- Новый источник данных: модуль в `marketdata/`, пути через `paths`, адреса в `config/*.json`.

## v2: план + исполнитель (в разработке, карта модулей)
Спецификация `docs/SPEC_V2.md` и `docs/spec_v2/01..06`, статус пакетов `docs/spec_v2/STATUS.md`. Каркас (WP0) создан, реализации пакетов подключаются позже; пока новые команды печатают «не реализовано» (код 2).
```
core/    plan_schema.py linkid.py (WP2)  filelock.py plan_store.py decisions.py exec_cfg.py (WP4)  bus.py (WP6)  expstats.py (WP7)
logic/   candles.py conds.py rules.py pyramid.py levels.py (WP3)  paper.py (WP7)        чистая логика, без ввода-вывода
executor/ ports.py (VenuePort, MarketFeed, PortError)  core gate actions reconcile protect daemon scanbridge mdsvc main (WP5)  live_port (WP1)  paper_port (WP7)
brain/   plan_cli brief events_cli common (WP6)  exp_cli (WP7)  api_daemon (WP11, отложено)   мозг: без venues/executor и без ключей
venues/  bybit_exec.py bybit_wsx.py (WP1)                                              операции и WS-фид исполнителя
config/  exec_limits.json (только сужающие лимиты)  brief_checklist.json
state/   plans/PL-*/{spec,runtime,revs}  plans/_inbox _outbox _archive  decisions.jsonl  bus/  briefs/  KILL
```
CLI-группы `plan`, `brief`, `events`, `exec`, `exp`, `brain` диспетчер вызывает как `main(argv) -> int` (`tb.__main__.V2`). Для `exec run --profile trade|testnet` launcher грузит ключ и снимает прокси
так же, как для `bybit`; `--profile paper` ключа не требует. Слои проверяет `tests/test_layering.py`: `brain/logic/core/dash` не импортируют `venues` и `executor`, `logic` чист (без `os`, сети, часов, `open`),
`BYBIT_API_SECRET` только в `launcher.py` и модулях `venues/bybit*.py`, `brain` не читает каталог ключей. Контракт порта биржи: `tests/test_port_contract.py` (`PortContract`).

## Тесты
```
./tb test                                   # все тесты, параллельно по модулям (процессов = число процессоров)
./tb test -j 1                              # один процесс, как unittest discover
./tb test --slow                            # и медленный набор (TB_SLOW_TESTS=1: нагрузочные тесты и пороги скорости)
./tb test test_guard test_dash2.ServerTest  # только эти модули/классы/тесты; -k ПОДСТРОКА — по имени теста; --durations N — самые долгие модули
python3 -m unittest discover -s tests       # то же из корня проекта (один процесс)
python3 -m unittest discover -s tests -p "test_guard.py"
```
Параллельный режим (`src/tb/testrun.py`): модуль тестов целиком идёт в одном процессе (модуль дольше 20 с делится на классы), самые долгие первыми (длительности прошлого прогона во временном каталоге ОС); вывод тестов показывается только для упавших модулей; рабочим процессам ставится `TB_PERF_FACTOR=3` (если не задан), пороги времени тестов (бюджет 60 с e2e автономии, перф-пороги `--slow`) берутся с этим запасом — точный замер скорости: `./tb test --slow -j 1`. `test_zz_realstate_guard` (сверка реальных `state/`, `config/`, `runtime/`) идёт последним в основном процессе со снимком, снятым до старта процессов; после каждого модуля проверяется, что тест не оставил `TB_ROOT`. Медленный набор: тесты помечаются `@unittest.skipUnless(os.environ.get("TB_SLOW_TESTS") == "1", ...)` или проверяют пороги времени только при нём; проверки корректности остаются в обычном наборе.
Тесты не должны трогать реальные `state/` и `runtime/`: подменяй `journal.PATH`, `portfolio.PATH`, `session.PATH` или передавай `root=` во временную папку.

## Launcher (кроссплатформенный вход)
Обёртки `tb`, `tb.cmd`, `tb.ps1` только выставляют `PYTHONPATH=src` и зовут `python -m tb.launcher "$@"`; `python -m tb ...` тоже идёт через launcher.
Для `bybit <read|trade|testnet> ...` launcher находит env-файл (`~/.config/traider-bot/` на macOS/Linux; `%USERPROFILE%\.config\traider-bot\`, затем `%APPDATA%\traider-bot\`
на Windows; `TB_KEYS_DIR` переопределяет каталог), кладёт пары KEY=VALUE в окружение ЭТОГО процесса, снимает `HTTP(S)_PROXY`/`ALL_PROXY` любого регистра
и отключает системные прокси urllib (на Windows/macOS они берутся не только из env). Содержимое файла не печатается. Остальные группы окружение не трогают.
Правила для нового кода: пути через `paths`/`os.path`, `open(..., encoding="utf-8")`, замена файла через `fsutil.replace`, без `fcntl`/`os.fork`/`sh`.

## Дашборд (`./tb dash [--port 8765] [--open] [--max-seconds N]`)
С 2026-11-02 по умолчанию новый дашборд (`src/tb/dash2/`, `docs/FRONTEND_PLAN.md`). Один порт на `127.0.0.1`: статика собранного фронта Vue (`frontend/dist`, лежит в репозитории, запуск без Node; пересборка `npm --prefix frontend ci && npm --prefix frontend run build`) и WebSocket `/ws`.
Сервер сам собирает темы из файлов проекта и шлёт клиенту снимок при подключении, дальше только изменившиеся темы (опроса нет; при обрыве клиент переподключается сам). Проверяются Host и Origin, число сокетов ограничено. Команды управления идут тем же сокетом через мост `ctlbridge` в сервис `ctl` (подписанные файлы, аудит; права и флаги `auth.dash2_full`, `allow_*_from_dash`: `docs/CONTROL.md`). Ключей и биржи дашборд не касается.
Платформа поднимает его сервисом `dash` (порт `svc.dash.port`, 8765). Старый дашборд (`dash-old`, порт 8768, `http.server` и статика `dash/static/`) удалён 2026-11-02 по указанию владельца.

### Слой чтения `tb.dash.model` (общий для dash2)
Только чтение. Данные ТОЛЬКО из файлов проекта: `state/{trades,sessions}.jsonl`, `portfolio.json`, `learning.json`,
`runtime/{market,context,hot,health}.json`, `events.jsonl`, `config/risk_profile.json`. Биржу и ключи не трогает (тест `test_dash.py` проверяет исходники и ответы `api_payload` на секреты;
`model.scrub` вырезает поля `*key*/*secret*/*token*/*passw*`).
- Проценты сделки (`model.trade_pcts`, `live_calc`): `move_pct` = движение цены монеты с учётом стороны (long: цена/вход-1, short: 1-цена/вход), без плеча и комиссий; `pnl_pct_margin` = PnL после комиссий / маржа (у открытой: после комиссии входа, плюс `pnl_pct_margin_est` с оценкой комиссии выхода taker). В истории колонки «Цена %», «К марже %», «PnL USDT»; сводка `metrics`: `avg_move_pct`, `avg_margin_pct`, `best/worst_margin_pct`. Нет выхода или маржи -> `null`, на странице «—».
- `server.api_payload` (без HTTP): `/api/state?mode=live|paper|testnet|all&venue=all|platform|bybit` (всё сразу), `/api/market`, `/api/hot`, `/api/health`, `/api/trades`, `/api/ping`. По умолчанию mode=live.
- Журнал разбирается устойчиво: повторный open при открытой записи заменяет её, open после close = новая запись, повторный close правит запись (как поправки по факту биржи),
  close без open и битые строки пропускаются и считаются в предупреждениях, старые записи без полей достраиваются.
- Нереализованный PnL = знак * (цена/вход - 1) * номинал - fee_open (если fee_open нет, оценка 0.09% номинала, помечается). Цена (`model.resolve_price`, для
  сделок, ждущих лимитов и идей): `market.json` (для bybit `prices_by_src.bybit`) -> `runtime/prices.json` (lastPrice ВСЕХ ~820 монет, пишет hotscan каждый скан без новых запросов,
  формат `{"ts_epoch", "prices": {МОНЕТА: цена}, "marks": {...}}`) -> `hot.json`; для площадок кроме bybit запасные источники не берутся. Берётся первая свежая
  (market <= 60 с, prices/hot <= 150 с), иначе самая свежая с пометкой «устарела»; в API `price_src`, `price_age_s`, `price_feed` (сканер жив или нет, подсказка `scan --watch`). Нет цены нигде: «нет цены», PnL не считается.
  Формат цен: значащие цифры по величине (`model.fmt_price` = `price()` в app.js, держать одинаковыми). R сейчас = PnL / risk_usdt. Ликвидация показана ОЦЕНКОЙ
  (`entry * (1 -/+ (1/плечо - mm))`, mm из `maintenance_margin_pct`), реальная цена ликвидации биржи в журнал не пишется.
- Показ: фронт Vue (`frontend/`), темы dash2 (`src/tb/dash2/topics.py`) берут числа из `model`/`model2`. Тесты: `tests/test_dash.py`, `tests/test_dash_v2.py`, `tests/test_dash2*.py`, `tests/test_launcher.py`.

## Шлюз инструментов и MCP (WP11-A, `docs/GATEWAY.md`)
`src/tb/gateway/` (каталог из 22 инструментов поверх brain/core, реестр агентов `config/agents.json` с хэшами токенов, аудит `state/gateway_audit.jsonl`, `gateway.call(agent, tool, args)`) и
`src/tb/mcp/server.py` (`./tb mcp serve --agent ID`, stdio JSON-RPC). Агент видит только каталог: ни ключей, ни файлов, ни shell, ни ордеров; команды планов идут в `_inbox` с `actor=brain:<id>`.
Слои: gateway и mcp не импортируют venues, executor, subprocess, socket, http (`test_layering.py`).

## Платформа: супервизор, диспетчер, сканер-сервис (WP11-B, `docs/RUNBOOK.md`)
`src/tb/platform/`: `supervisor.py` (`./tb up|down|status|logs`: дочерние процессы, pid-файлы, heartbeat, автоперезапуск с экспоненциальной паузой и потолком, блокировка второй копии, `service_down` в шину), `services.py` (конфиг
`config/platform.json`, команды и ЧИСТОЕ окружение сервисов: ключи только у исполнителя), `procs.py` (группы процессов POSIX / CREATE_NEW_PROCESS_GROUP Windows), `logs.py` (ротация), `status.py` (`build_status`: один словарь для
`./tb status`, дашборда `/api/platform`, MCP), `autostart.py` (печать launchd/Windows, не установка), `scansvc.py` (hotscan + ideawatch -> шина `scanner_signal`, белый список, дедупликация), `dispatcher.py` (шина -> подписчики по
`config/subscriptions.json`: очередь, задания API-мозгу, localhost-webhook; коалесцирование, классы приоритета, потолки, курсоры). Платформа не импортирует `tb.executor`/`tb.venues` в слоях brain/core/dash (кроме запуска клиента публичных данных в `scansvc`).

## Уровни риска (WP16, `docs/RISK_LEVELS.md`)
`core/risklevels.py` (чистая stdlib: загрузка `config/risk_levels.json`, `effective(prof, root)`, подпись HMAC файла `state/control/risk_level.json`, `set_level`, `auto_downgrade`, запросы политик) и CLI `control/risk_cmd.py` (`./tb risk ...`). Подключено в `core/guard.py` (`evaluate*`), `core/session.py` (бюджет), `core/exec_cfg.py` (`warnings()`); событие шины `risk_level_changed`. Шлюз, MCP, brain и llm уровень не меняют (тесты `NoAgentPath`).

## Память случаев (WP15a, `docs/spec_v2/08_case_memory.md`)
`src/tb/memory/`: `cases.py` (схема `tb.case/1`, валидатор, `state/cases.jsonl` append-only с хеш-цепочкой `chain.py`, update-строки того же `case_id`; `situation`/`decision` после записи не меняются), `features.py` (признаки из ctx/тегов/идей, неизвестное = null + `missing`, ретроспективные поля в `retro`), `similar.py` + `agg.py` (ЧИСТАЯ логика: сходство Gower с весами и шкалами из `config/memory.json`, жёсткие фильтры режим/версия правил/сторона/возраст/`as_of`, top-k, агрегат n, mean R ± se, win, база, предупреждения INSUFFICIENT n<5 / LOW_N n<30), `lessons.py` (`state/lessons.jsonl`: уроки-гипотезы из шаблонов cond+claim, статусы hyp/confirmed/rejected/expired/merged, затухание, срок годности, противоречия; в guard автоматически НЕ превращаются), `review.py` (`tb.casereview/1`, валидатор `known_at_decision` против самой ситуации), `privacy.py` (очистка текстов, `privacy_scan`), `migrate.py` (миграция и sync из trades/ideas/hot_outcomes/decisions, идемпотентно), `query.py` (сервисный слой чтения для 15b: `find_similar`, `stats`), `cli.py` (`./tb memory similar|stats|add|show|sync|migrate|verify|review-queue|review|lesson|backtest|compare`). Память ничего не разрешает и не запрещает; пакет не импортирует `tb.intel`, биржевой код, executor, gateway (`tests/test_memory_cli.py::TestLayers`). Дашборд («Память») и A/B: позже.
WP15b (память внутри мозга): `blocks.py` (блок `memory` досье <= 450 токенов с порядком усечения, видимость paper/testnet/live по `show_in_live`, полезная нагрузка инструментов шлюза `get_similar_cases`/`list_lessons`, метка `mem.on|off`), `tick.py` (периодический писатель: `migrate(final_only=True)` + пересчёт уроков; вызывается сервисом ctl раз в `tick.interval_s` и раннером перед разбором закрытия), `backtest.py` (`./tb memory backtest|compare`: ретро-проверка «совета похожих» walk-forward и старые/новые, сравнение периодов до/после с разрезом mem), `review.normalize_hint` (шов словарей роли и памяти). Подключения вне пакета: `gateway/catalog.py` (инструменты `get_similar_cases`, `list_lessons`, `add_case_review`; права `memory.read`/`memory.write`; плановый тег `mem.*`), `brain/brief.py` (`memory_block`), `brain/runner.py` (вход ревьюера из памяти, `case_id` по `position_closed`, запись через шлюз), `control/service.py` (тик писателя), `core/expstats.py` (разрез `mem`), `config/agents.json` (права), `config/roles/morning_analyst.json` (`tools_optional`).

## Фабрика меток и база знаний (R1, `.opencode/specs/R1-label-factory/`)
Задача: дешёвые метки на каждом сигнале сканера (признаки на момент сигнала, исходы 15/60/240 мин после издержек, прогон теневых политик), чтобы набирать n для статистики быстрее, чем дают сделки мозга. Метки не торгуют, ордеров, планов и записей журнала сделок не создают.

**База знаний** `state/knowledge.sqlite` (stdlib `sqlite3`, WAL, `synchronous=NORMAL`, `busy_timeout` 5 с; решение владельца Clarifications 2026-11-02: метки лежат здесь, а не в случаях `signal` файла `cases.jsonl`; перенос случаев, уроков и плейбука в эту же базу: блок R2.9). Схема версии 1 (`tb.labels.schema`, миграции применяются одной транзакцией, версия в `meta.labels_schema_ver`, сбой миграции даёт `SCHEMA_MIGRATION` и ничего не меняет):
- `meta`, `cursor` (смещение и sha256 первых 4 КБ в `runtime/hot_events.jsonl`);
- `signals`: ключ `S-<UTC t0>-<SYM>-<type>`, поля события (`side` из `direction`, `score`, `price`, `wake`, `tradable`, `corr_group`, `env`), `ev` (числовые поля события JSON), `feat`/`missing` (признаки и список пустых), `status` (`pending|done|no_data|failed|skipped`), `code`, `attempts`, `mature_ts`/`next_try_ts`, `gap_min`;
- `outcomes` (ключ, горизонт, сторона): `ret_pct`, `mfe_pct`/`mae_pct` (и в ATR), `t_mfe_min`/`t_mae_min`, `cost_rt_pct`, `net_dir_pct`, флаги `gap|halt|no_spread`; у сигнала без направления считаются обе стороны;
- `policy_runs` (ключ, `policy_id`, `policy_ver`): `geometry_ver`, `ctx_ver`, план JSON, `filled`, `exit_reason`, `r_gross`, `r_net`, `fees_r`, `mfe_r`, `mae_r`, `hold_min`. Версии политик не смешиваются, у каждой строки видно, какой геометрией и каким контекстом она получена.
Свечи в базе не хранятся (`rerun` грузит их заново). Ключей и сумм в USDT в базе нет. Объём около 5-10 МБ в сутки.

**Пакет `src/tb/labels/`** (I/O-слой, только stdlib и чистая логика `tb.logic`):
```
schema.py    версия, DDL, статусы и коды (SCHEMA_MIGRATION, DB_CORRUPT, W_LABELS_SYNC_DIR, FETCH, NO_LEVEL, NO_DIR, NO_ATR_PLAN, NO_DATA, CONFIG)
store.py     open_rw (создание, миграции, повреждённый файл -> knowledge.corrupt-<ts>.sqlite и новая база, DB_CORRUPT), open_ro (query_only), tx (BEGIN IMMEDIATE),
             backup (Connection.backup -> state/backups/knowledge-YYYYMMDD.sqlite), sync_dir_warning
capture.py   захват хвоста hot_events.jsonl: курсор offset+head_sha, неполная строка ждёт, INSERT OR IGNORE, срок созревания, снимок fng/dvol/risk_off в момент захвата
klines.py    публичный GET /v5/market/kline без ключа: Limiter (max_rps), повторы 2/5/15 с, RateLimited на 403/429 (пауза 60 с), BtcCache
features.py  признаки на t0 (чистая функция: ряды режутся bars.closed_before, нет данных = None и имя в missing)
policies.py  реестр теневых политик, shadow_ctx и ctx_ver, pick (план для симулятора)
label.py     due, label_one: окна 1м/5м/1ч -> признаки -> исходы -> политики симулятором -> ОДНА транзакция (done, feat, outcomes, policy_runs)
query.py     чтение (open_ro): policy_stats, outcome_stats, coverage, throughput, heartbeat_stats
service.py   цикл сервиса labels
```
**Чистая логика `src/tb/logic/`** (без ввода-вывода, часов и сети; `tests/test_layering.py` проверяет и прямые, и транзитивные импорты):
- `bars.py`: свечи `Row=(open_ts_s,o,h,l,c,v)` из ответа Bybit (`from_bybit`: незакрытая свеча, дубли и мусор отбрасываются), `closed_before`, `agg`, `atr`, `ret_pct`, `range_pct`, `gaps`, `path_stats` (MFE/MAE/возврат по минутам, флаги `gap`/`halt`);
- `costs.py`: `round_trip_pct(prof, venue, maker=False)`, издержки круга из `risk_profile.costs` (раньше расчёт был в `brief.py`, числа прежние);
- `geometry.py`: единая геометрия входа (перенесена из `brain/cands.py`, `validate.py`, `entryplan.py`): `pool`, `candidates` (уровень, стоп за уровнем, цели из встречных уровней, горизонт, отказы), `chase` (momentum_chase), `nearest_ok`, `atr_plan` (профиль `atr` для теней), константы правил входа и `GEOMETRY_VER`; параметры профилей `base|chase|atr` и `cluster_pct` в `config/geometry.json`, умолчания в коде равны файлу;
- `sim.py`: `run(plan, rows_1m, t0_s, costs, manage)`: симулятор плана по минутным свечам (вход market/candle_confirm/candle_confirm_limit/limit, стоп, тейки, таймаут, автозащита, задержка). Порядок внутри свечи консервативный: стоп раньше тейка, гэп за стопом по open с проскальзыванием. Результат: `filled`, `exit_reason` (`sl|tp|be|trail|timeout|ttl|no_fill`), `r_gross`, `r_net`, `fees_r`, `mfe_r`, `mae_r`, `hold_min`, `truncated`. Опирается на `logic.paper` и `logic.rules`.
`brain/cands.py` стала тонкой обёрткой над `logic.geometry` (читает `config/geometry.json`, добавляет в строки `geometry_ver`), `brain/validate.py` реэкспортирует перенесённые константы под прежними именами.

**Поток сигнал -> метка.** Сканер (`hotscan`, сервис `scan`) пишет `runtime/hot_events.jsonl` -> сервис `labels` раз в `capture_every_s` (15 с) читает хвост, отбирает виды из `config/labels.json:capture_types` и вставляет `signals` со статусом `pending` и сроком `mature_ts = t0 + max(240 мин, ttl входа + удержание политики) + grace_min` -> по созревании `label_one` грузит 1м свечи (с `t0 - 30 мин` до срока), 5м и 1ч для признаков и BTC (с кешем по 5-минутным корзинам), считает признаки, исходы трёх горизонтов с `cost_rt_pct` и `net_dir_pct`, для каждой включённой подходящей политики `pick` строит план, `sim.run` прогоняет его, всё пишется одной транзакцией. Нет свечей после `t0`: `no_data`; сеть/конфиг: отсрочка на `retry_min` (30 мин) до `max_attempts` (5), затем `failed`; HTTP 403/429: пауза 60 с без траты попытки. Сигнал с `tradable=false` получает признаки и исходы, но не политики.

**Теневые политики** (`config/shadow_policies.json`, схема `tb.shadow_policies/1`, строгая загрузка: лишние/недостающие поля, дубль `id+ver`, неизвестные значения -> `ValueError`; изменение политики = новая `ver`): сейчас P2 `confirm_follow`, P4 `fade_confirm`, P5 `time_exit_baseline` (без тейков, вход по рынку, профиль `atr`), P6 `random_side_baseline` (сторона по `expstats.assign_variant`). Профиль `base` строит план по уровням метки через `geometry.candidates` (ближайший ok-кандидат стороны, горизонт не выше `max_horizon`), профиль `atr` (вход по рынку) через `geometry.atr_plan`. Контекст `shadow_ctx` берёт уровень риска, тип сессии, потолок горизонта и т. п. из `config/labels.json:shadow_ctx`, горизонты из `risk_profile.session_types` (без поправок уровня риска) и издержки круга; его версия `ctx_ver` (8 символов sha256 входов) пишется в каждую строку, поэтому смена `risk_profile`/`exec_limits` не смешивается со старыми данными. Коды отказа политики: `NO_DIR`, `NO_LEVEL`, `NO_PX`, `NO_ATR_PLAN`, `GEO_*`. Статистика: `query.policy_stats` (n, заполнения, win, средний `r_net` +- se, PF, MFE/MAE в R, доли выходов, p и порог Бонферрони по числу включённых политик).

**Сервис `labels`** (`python -m tb.labels.service run`, запускает супервизор; `platform/services.py`: `ORDER`, `SERVICE_NAMES`, `DEFAULTS`, `service_cmd`, профили paper/testnet/trade). Heartbeat `runtime/platform/labels.hb.json` (поля `captured_24h`, `labeled_24h`, `pending`, `due`, `failed_24h`, `no_data_24h`, `rate_limited_24h`, `req_per_min`, `bad_lines`, `last_error`, `db_mb`, `missing_share_24h`, `warnings`, `enabled`), `wal_checkpoint(PASSIVE)` раз в 10 мин и `TRUNCATE` раз в сутки, блокировка второй копии `runtime/platform/labels.lock` (код выхода 2). Окружение сервиса очищается шире общего: `BYBIT_*`, `ANTHROPIC_*`, `DEEPSEEK_*`, `OPENAI_*`, `*_API_KEY`, `*_SECRET`, `*_TOKEN` (`services.labels_secret`, `child_env`; сам процесс тоже вычищает их при старте). События в шину сервис не публикует; жёлтый статус в `./tb status` ставит `platform/status.py` (`due` > 500 или `failed_24h` > 5% захваченных за 24 ч, `DB_CORRUPT`, `W_LABELS_SYNC_DIR`). Эксплуатация: `docs/RUNBOOK.md`.

**Слои.** `tb.labels` не импортирует `tb.venues`, `tb.executor`, `tb.gateway`, `tb.llm`, `tb.brain`, `tb.control`, `tb.intel.items` ни прямо, ни транзитивно; `tb.logic.bars|geometry|sim|costs` подчиняются правилам чистоты `tb.logic` (`tests/test_layering.py`). Метки читают признаки через `tb.memory.cfg|features` и `tb.logic.candles|levels`, свечи берут сами из публичного REST. Лимиты, `risk_profile.json`, `risk_levels.json`, плечо и размер фабрика не читает на запись и не меняет.

## Сводный рынок (R1.7, `.opencode/specs/R1.7-consolidated-market/`)
Задача: видеть рынок монеты как одно целое со всех 11 рынков 6 бирж (perp и spot) в одну отметку времени: сводный стакан, суммарный OI, CVD, ликвидации, взвешенный funding, премии и лидерство. Признаки снимка пишутся в метки R1 в момент сигнала, первая волна входит в словарь правил R2, мозг видит компактную сводку в досье. Сервис торговлю не ведёт и ключей не видит: только публичные каналы и эндпоинты без подписи; лимиты, плечо, размер и уровень риска он не трогает.

**Процессы.** Сводка `cmkt` (`python -m tb.cmkt.service run`) и сборщик на каждый рынок `cmkt@<биржа><p|s>` (например `cmkt@binancep`, `cmkt@coinbases`; `python -m tb.cmkt.collector run --market <биржа>_<perp|spot>`), всего 11 сборщиков. `platform/services.py`: `cmkt_market`/`cmkt_name`/`cmkt_services` раскрывают сервис `cmkt` профиля в сводку и сборщики включённых рынков `config/cmkt.json`; сервис и сборщики входят в `ORDER`, профили `paper`, `testnet`, `trade` и `default_services`, `stop_order` останавливает сводку раньше сборщиков, окружение чистится от секретов у `cmkt` и любого `cmkt@*`. В `platform/supervisor.py` настройки `svc["cmkt"]` и `svc["cmkt@"]`: `hb: file`, `hb_stale_s` 30 с, `grace_s` 60 с. Падение или блокировка одного сборщика не влияет на остальные: супервизор перезапускает его по heartbeat.

**Конфиг** `config/cmkt.json` (схема `tb.cmkt_cfg/1`, строгая загрузка `tb.cmkt.conf`): `enabled`, `compat_xflow`, `focus` (ядро BTC, ETH, SOL; `top_n` 30, `deep_top_n` 10, `max_deep` 20, `max_light` 120, `keep_signal_min` 180, `struct_req_ttl_min` 30, `hold_s` 600, `warm_s` 300), `venues` (6 бирж, виды рынков и белый список хостов), `sync` (такт 1 с, `lag_max_ms` 1000 с переопределением биржи до 2000, `silent_s` 15, `file_stale_s` 1, `accept_s` 3, `ping_s` 10, `time_sync_s` 600, `snap_every_s` 2), `book` (полосы 0.25/0.5/1/2 %, корзины 25 б.п. до ±200 б.п., стены: порог рынка 100 000 USD или 6× медианы уровней стороны, сводная стена 400 000 USD, группа 5 б.п., жизнь 30 с, касание 5 б.п., снятие ≥ 70 % (0.7) за 5 с), `cov` (`min_cov` 0.6, `min_n_ex` 2, `liq_min_cov` 0.5, `mismatch_pct` 2.0), `limits` (тем на соединение 200, Bitget 50, OKX 150, Gate и Coinbase 100; 5 сообщений подписки в секунду; 1 соединение в секунду; вес REST в минуту по рынку, резерв OI Binance 240; опрос OI `deep` 10 с, `light` 60 с), `budget` (CPU 1.5 и 4 ядра, RSS 1500 МБ, p99 обработки 300 мс, 80 Мбит/с, 30 ГБ в сутки; `over_min` 10, `shrink_step` 20, `light_min` 40), `store` (`state/cmkt`, 30 суток, шаг 1 с и 5 с, `disk_min_gb` 40), `brief` (`compact`, 90 токенов, 1 монета), `tool` (2 048 байт, 30 вызовов в час). Неизвестный ключ, тип, значение вне диапазона или нарушение согласованности дают `E_CONF:<поле>` и сервис не стартует (код 2); файла нет — действуют умолчания в коде, равные поставляемому файлу.

**Сборщик и адаптеры.** `tb.cmkt.adapter` — общая механика: нормализованные события (`Trade`, `BookSnapshot`, `BookDelta`, `Ticker`, `Liquidation`), `BookState` (непрерывность по номерам обновлений биржи, контрольная сумма, если биржа её присылает, перекрёст дольше 2 с, пустая сторона -> `RESYNC`), `TapeBuckets` (лента по `t_rx`, дубли по идентификатору, непрерывность), `MetaCache` (только линейные USDT-бессрочные и спот к USDT, множители `1000*`, `NOT_LISTED`, `MISMATCH` 2 %), `SubPlanner`, `RestBudget` (вес REST в минуту с резервом OI, снимки из остатка, порядок восстановления позиции > планы > ядро > остальные `deep`), `Http` с белым списком хостов (`HOST_DENIED`), `ClockOffset`, `LagMeter`, `Liveness`, `Backoff`, `VenueConn` на WS-клиенте проекта `tb.marketdata.wsclient` (stdlib, без ccxt). Адаптер биржи `tb.cmkt.ex.<биржа>` даёт только форматы, каналы и правила своей биржи; новая биржа — новый модуль и запись `venues.<id>` в конфиге, сводка, логика и хранение не меняются.

**Синхронизация и снимок.** Каждое сообщение получает `t_rx` (локальное время до разбора) и `t_ex` (поле времени биржи; нет поля — `t_ex = t_rx`), `offset` биржи оценивается раз в `sync.time_sync_s` = 600 с по серверному времени (|offset| > 500 мс у 4 и более бирж -> жёлтый `CLOCK_SKEW`), `lag` рынка — по последнему сообщению стакана или сделок. Такт сводки 1 с по `time.monotonic`; снимок `ts = T` строится на `T + 0.3 с` только из сообщений с `t_rx ≤ T`. Рынок входит в агрегаты при `T − T_pub ≤ file_stale_s` (1 с), живом соединении (`silent_s` 15 с при пинге раз в `ping_s` 10 с), `lag ≤ lag_max_ms` и стакане не в `RESYNC`; иначе остаётся в снимке с кодом `OFF | DOWN | SILENT | LAG | RESYNC | STALE | NOT_LISTED | MISMATCH` и `lag_ms` и участвует только в знаменателях покрытия. Снимок несёт `ts`, `skew_ms` (худшее отклонение включённых рынков), число включённых рынков по видам и `clock_back`; `ts` строго растёт. Файл `runtime/cmkt/snap.json` пишется раз в `sync.snap_every_s` = 2 с и всегда, когда ни один рынок не включён; каждый такт пишутся строки рядов.

**Монеты в фокусе.** `tb.cmkt.focus` (пишет только сводка): ядро `focus.core` (BTC, ETH, SOL), топ-`top_n` 30 по межбиржевому обороту 24 ч (`runtime/heat.json`, пересчёт раз в 300 с), позиции и планы (`runtime/exec_snapshot*.json`), сигналы `runtime/hot_events.jsonl` за `keep_signal_min` = 180 мин и запросы структуры `runtime/structure_req.json` (живут 30 мин). Ярус `deep` (не больше 20) — позиции, планы, ядро и топ-10 по обороту; остальные — `light` (не больше 120, после сжатия по бюджету — `light_cap`). Выпавшая из набора монета держится ещё `hold_s` = 600 с; признаки с окнами остаются `None`, пока монета в фокусе меньше окна (60/300/900/3600 с), а `warm_s` = 300 с — порог «монета в фокусе» для покрытия сигналов в метках и `CM_COV`. Результат — `runtime/cmkt/focus.json` (при изменении и не реже раза в 10 с). Нет свежего `heat.json` — `FOCUS_NO_HEAT`, в фокусе остаются ядро и динамика.

**Ряды.** `tb.cmkt.store`: файл на сутки UTC `state/cmkt/YYYYMMDD.sqlite` (WAL, `synchronous=NORMAL`, ключ `(ts, sym_id)`), одна транзакция вставки на такт. `deep` пишется каждую секунду, `light` — при `ts % 5 = 0`. Строка — все 40 признаков `cm_*` в BLOB `f` (масштабированные целые), разрез по рынкам `by_ex` (CVD 5 мин, ΔOI 5 мин в монетах, ликвидации 5 мин по сторонам, снятые до касания стены 15 мин), `wr_ms`, `tier`, `flags`, `gap_s`; версия набора и масштабов — `meta.cm_ver`. Файлы старше `keep_days` = 30 суток удаляются раз в сутки (текущие и предыдущие — никогда), при `PermissionError` повтор в следующий проход. Свободного места меньше `disk_min_gb` = 40 ГБ — ряды не пишутся (`DISK_LOW`), снимок продолжается, проверка раз в 60 с. Повреждённый файл (`SQLITE_CORRUPT`, `SQLITE_NOTADB`) переименовывается в `*.corrupt-<ts>.sqlite`, запись идёт в новый. Сырая лента и стаканы не хранятся; в резервные копии `knowledge` ряды не входят.

**Признаки.** `tb.logic.cmkt` — чистая математика (без файлов, сети и часов): полосы по уровням с покрытием по глубине выдачи, корзины 25 б.п. до ±200 б.п. для атрибуции, стены по абсолютной цене и классификация конца (`touched | pulled | faded`), окна CVD из секундных корзин, изменение OI в монетах, `cm_oi_liq_5m_pct` по одному набору рынков, funding к 8 ч и взвешенный по OI, сводная середина (медиана при ≥ 3 рынках, среднее при 2, значение при 1), премии `ps` и `cb`, лидерство по ряду `t_ex − offset`, покрытие и пороги `None`. `CM_FEATURES` — 40 полей `cm_*`; версия набора и масштабов `cm_ver` — 8 hex sha256 имён и масштабов. Первая волна (`FIRST_WAVE`: `cm_imb_025_pct`, `cm_oi_liq_5m_pct`, `cm_cvd_5m_perp_pct`, `cm_wall_pull_15m_pct`) входит в `RULE_FEATURES` и `MENU_NUM` (`tb.logic.learn_cond`), верхняя граница K меню — 25; остальные признаки копятся в метках и ждут следующих волн.

**Читатель `tb.core.cmktdoc`.** Единственный путь потребителей к сводному рынку (кроме файлов `runtime/`): `latest` (снимок не старше 5 с), `features_at(root, sym, t0)` — строка признаков сигнала (`row_ts = floor(t0 / step) × step − step`, только при `wr_ts ≤ row_ts + accept_s`; ответ `cm` либо `cm_missing = not_focus | no_row | late | db | no_t0`, ожидание `pending` до `row_ts + accept_s + 1`), `brief_block` (вид и бюджет `config/cmkt.json: brief`), `detail` (ответ инструмента шлюза в пределе `tool.max_bytes`). Ряды открываются только на чтение (`mode=ro`) на один вызов, модуль не импортирует `tb.cmkt` и не бросает исключений. Потребители: досье мозга (`brain/brief.py`), правила мозга и шлюза (`brain/cands.rule_check` берёт `t0` из события шины и зовёт `features_at(..., wait=True)`), метки (`labels/capture.py`: `ev.cm` при вставке и `ev.cm_missing`; покрытие — `./tb labels stats`), цикл обучения (`memory.features.rule_features` берёт `cm_` из `ev.cm`), команды `./tb cmkt status|show|at|focus`; шлюз `get_market_detail` читает только через `cmktdoc.detail`.

**Совместимый `xflow.json`.** При `compat_xflow = true` сводка раз в 2 с пишет `runtime/intel/xflow.json` в формате `XStream.document` (`src = "cmkt"`) по охвату `xstream`: рынки `okx_perp`, `bitget_perp`, `binance_perp` под именами `okx`, `bitget`, `binanceusdm`, до 8 монет (`compat_symbols`), стакан обрезан до глубины `xstream` (Binance 20 уровней, Bitget 15, OKX без обрезки), ликвидации только Binance. Сервис `intel` при флаге `XStream` не запускает: у файла один писатель. Потребители прежние (`core.flowdoc.xlive_for`, `xflow.summary`), поведение сторожа позиции не меняется; переключение и откат — `docs/RUNBOOK.md`.

**Слои.** `tb.cmkt` не импортирует `tb.venues`, `tb.executor`, `tb.gateway`, `tb.brain`, `tb.llm`, `tb.control`, `tb.launcher`, `tb.mcp`, `tb.core.secretfile`, `tb.labels`, `tb.learnloop`, `tb.knowledge`, `tb.intel` ни прямо, ни транзитивно; ключей, `hmac` и каталога ключей в пакете нет. `tb.logic.cmkt` чист (без `os`, сети, часов, `open`). `tb.core.cmktdoc` не импортирует `tb.cmkt`; в `tb.gateway` нет названий бирж — они приходят данными из `cmktdoc`. Проверки — `tests/test_layering.py`.

**Наблюдаемость и бюджет.** `./tb cmkt status` (коды рынков, `lag_ms`, `offset_ms`, подписки, соединения, восстановления за запуск, REST-вес, CPU, RSS, p99 обработки, трафик, ярусы, совместимый `xflow.json`), `./tb cmkt show SYM`, `./tb cmkt at SYM TS`, `./tb cmkt focus` — только чтение файлов. Heartbeat `runtime/platform/cmkt.hb.json` и `runtime/platform/cmkt@<биржа><p|s>.hb.json` раз в 5 с, супервизор проверяет его по `hb_stale_s` 30 с. `./tb status` показывает строку `cmkt` и ставит жёлтый (красного нет: торговля от сервиса не зависит) при: снимке старше 5 с (`SNAP_STALE`), 2 и более биржах вне сводки дольше 300 с (`VENUES_OUT`), `CLOCK_SKEW`, доле сигналов монет в фокусе с признаками < 0.9 за 24 ч (`CM_COV`), `DISK_LOW`, `BUDGET`, `FOCUS_NO_HEAT`, битом конфиге (`CONF`). Бюджет процессов — `config/cmkt.json: budget`; превышение `budget.over_min` = 10 мин подряд сжимает `light` на `shrink_step` = 20 монет до `light_min` = 40 и даёт `BUDGET` (после возврата бюджета предел восстанавливается перезапуском сводки). Замер задачи 23 на живом темпе: CPU 1.22 из 1.5 ядра, RSS 1365 из 1500 МБ, p99 обработки 69.4 из 300 мс, трафик 75.5 из 80 Мбит/с, запись 24.95 из 30 ГБ в сутки. Тесты: `tests/test_cmkt_*.py`, `tests/test_logic_cmkt.py`, `tests/test_cmktdoc.py`, `tests/test_brief_cmkt.py`, `tests/test_gateway_market_detail.py`, `tests/test_xflow_compat.py`, `tests/test_labels_cm.py`, `tests/test_rule_features_cm.py`; медленный набор нагрузки — `tests/test_cmkt_load.py` (`TB_SLOW_TESTS=1`).

## Страница «Учится ли мозг» (R0.2, `.opencode/specs/R0.2-learning-page/`)
Задача: показать владельцу одним словом, лучше ли мозг стал работать, и дать числа с n и интервалом. Страница только читает: ордеров, планов, записей журнала и изменений лимитов нет. Числа и тексты вердикта считает код, модель не участвует. Режимы paper, testnet, live не смешиваются ни в одном числе.

**Пакет `src/tb/learn/`** (чистые расчёты плюс сборка отчёта; пишет только в память процесса):
```
conf.py            config/learning.json: строгая загрузка, умолчания, предупреждения E_CONF:<поле>
stats.py           чистая статистика: est (v, lo, hi, n, lvl), t-интервал, Уилсон, Уэлч по сводкам, z пропорций, бутстреп PF (random.Random(seed)), rolling, ISO-недели
load.py            данные: сделки мозга (expstats.load_trades минус shadow, manual*, без plan_id, без R), случаи режима, пропуски, журнал вызовов LLM, подпись источников
cache.py           Cache (ttl, min_interval, блокировка на ключ) и Flight (один фоновый поток на ключ); REPORT и MEMORY на процесс
verdict.py         правило вердикта и таблица шаблонов текста (state, reason) -> строка
result.py exits.py discipline.py skips.py economics.py vs_policy.py memory_vs_base.py stages.py    по одному блоку
report.py          build (без кеша), get (с кешем), sanitize, изоляция ошибок блоков, лимит ответа 64 КБ
dashapi.py         api(path, query, root, now) для EXT_API, detail, follow, is_computing
```
Схема ответа `tb.learning/1`: `verdict` (`state` `few_data|no_difference|confirmed`, `reason`, `text`, `n`, `n_recent`, `n_prev`, `diff_r`, `p`, `alpha_adj`, `trend`, `guards`, `params`), `order` (девять блоков), `blocks` (`result`, `vs_policy`, `discipline`, `skips`, `exits`, `economics`, `memory`, `lessons`, `versions`; у каждого `id`, `title`, `status` `ok|few_data|no_data|stage|computing|error`, `reason`, `note`), `meta` (`built_ms`, `cached`, `excluded`, `warnings`, `sources`). Эталонные ответы: `tests/fixtures_learning/payload_full.json`, `payload_few.json`; по ним фронт сверяет контракт. Имена полей не содержат `key|secret|token|passw|signature` (иначе `model.scrub` вырезал бы их), токены обозначены `tok_in`/`tok_out`. Сумм в USDT в ответе нет: результат только в R.

**Поток данных.** Тема в `TOPICS` не заведена: отчёт тяжелее остальных тем и меняется при закрытии сделки, поэтому считается по запросу клиента.
```
store.request(mode) -> сокет {"op":"learning","mode"} -> dash2.server.learning_op (asyncio.to_thread)
  -> topics.learning_detail -> tb.learn.dashapi.detail -> tb.learn.report.get -> REPORT.get (кеш)
     промах: report.build -> load.* -> блоки -> sanitize -> model.scrub -> сообщение learning_detail (только этому соединению)
```
Недопустимый режим заменяется на `paper`; сбой сборки даёт `{"ok": false, "mode", "error": "failed"}` без разрыва соединения. Тот же отчёт отдаёт `tb.dash.server.api_payload("/api/learning")` через реестр `EXT_API` (запись `tb.learn.dashapi`); настоящего HTTP-пути у dash2 нет, это логический путь поверх тех же функций.

**Кеш и single-flight.** Ключ кеша (корень, режим), подпись источников `load.source_signature` (размер и mtime `trades.jsonl`, `cases.jsonl`, `exclude_trades.json`, журнала вызовов LLM и `config/learning.json`). Отчёт живёт `cache.ttl_s` (60 с); при смене подписи пересчёт не чаще `cache.min_interval_s` (10 с). Параллельные запросы одного ключа ждут один расчёт, исключение загрузчика не кешируется, время кеша монотонное. Ответ отдаётся копией; при попадании в кеш `meta.cached = true`.

**Фоновый блок памяти.** Ретро-тест памяти (`tb.memory.backtest.walk_forward`) растёт квадратично, поэтому идёт в потоке `Flight` по ключу (корень, режим, подпись `cases.jsonl`). `learning_op` ждёт его `memory.budget_s` (3 с); не уложились: блок `memory` получает `status=computing`, такой отчёт в кеш не кладётся (`REPORT.drop`), результат walk-forward кешируется на `memory.ttl_s` (15 мин). После ответа с `computing` сервер запускает `_learning_follow`: в потоке ждёт расчёт до `dashapi.FOLLOW_TIMEOUT_S` (30 с) и шлёт тому же соединению повторное `learning_detail`. Закрытое соединение и упавший расчёт игнорируются, клиент остаётся с «Считается…» и может перезапросить. Больше `memory.max_trades` (600) сделок: walk-forward не запускается, блок `no_data` с причиной `too_many_for_request`.

**Правило вердикта** (`verdict.decide`, пороги `expstats.MIN_N` = 30 и `expstats.ALPHA` = 0.05 из кода, K = 1; в конфиг вынесены только `recent_n` и `guard_trades_ratio`): n < 30 даёт `few_data/n_lt_min`; 30 <= n < 60 даёт `no_difference/no_baseline`; дальше последние `min(recent_n, n - 30)` сделок («недавние») сравниваются с остальными («прежние») тестом Уэлча. `confirmed/improved` только при разности > 0, p < alpha и пройденных защитах: доля нарушений правил (`rule_followed = no`) у недавних не выросла (односторонний тест, p < 0.05; при менее чем 10 известных отметках в любой части защита не блокирует и помечена `guard_unknown`), сделок в сутки у недавних не меньше `guard_trades_ratio` от прежних. Иначе `no_difference` с причиной `guard_viol`, `guard_trades` или `not_significant`. `trend = down` при значимом ухудшении добавляет к тексту предупреждение. Блоки «против политики», «экономика», «память» в вердикт не входят. Применённые пороги лежат в `verdict.params`.

**Фронт** (`frontend/src/`): `entities/learning` (типы, приведение ответа, стор), `widgets/learning/{verdict,intro,result,vs-policy,discipline,skips,exits,economics,memory,stages}`, `pages/learning/ui/LearningPage.vue`, маршрут `/brain/learning` (`app/router/index.ts`), вкладка «Учится ли мозг» в разделе «Мозг» (`shared/config/nav.ts`), `learning_detail` в `EPHEMERAL` (`shared/api/live-socket.ts`: сообщение не кешируется сокетом), график `shared/ui/UiBandChart.vue`, тексты `shared/lib/human-pages/learning/`. Запрос уходит при заходе, смене режима, возвращении связи и росте `trades.total`; таймеров нет. Запоздавший ответ другого режима стор отбрасывает, повторное сообщение того же режима заменяет прежнее.

**Слои.** `tb.learn` не импортирует `tb.venues`, `tb.executor`, `tb.gateway`, `tb.control`, `tb.brain`, `tb.intel.items`, `tb.dash`, `tb.dash2` ни прямо, ни транзитивно; из `tb.llm` берёт только `usage` (журнал вызовов); не пишет на диск и не открывает SQLite на запись (база меток только через `tb.labels.store.open_ro`); сети и процессов нет (`tests/test_layering.py`). Тесты: `tests/test_learn_*.py`, `tests/test_dash2_learning.py`; фронт: vitest рядом с кодом (в том числе сверка с эталонами бэкенда, `pages/learning/ui/contract.test.ts`) и `frontend/e2e/specs/10-learning.mjs`.

## Цикл обучения и rulebook (R2, `.opencode/specs/R2-closed-learning-loop/`)
Задача: замкнуть цикл «метки → кандидат → проверка → урок → испытание → продвижение или откат» кодом, без владельца и без модели. Доказательство даёт выборка меток R1 (прогоны теневой политики `P2`), а не сделки мозга. Правило первой итерации одно: `skip_when` (запрет входа), поэтому код может только ужесточать; размер, плечо, стоп, лимиты и уровень риска цикл не трогает. Числа, пороги и коды причин: `design.md` и `config/learnloop.json` (строгая загрузка `tb.learnloop.conf`; хеш конфига пишется в критерий испытания). Поставка (решение владельца 6): `enabled=true`, `promotion.mode="auto"`.

**Пакеты.**
```
logic/learn_cond.py   чистая: RULE_FEATURES (9 признаков), MENU_NUM, MENU_CAT, cmp_val, matches_features (memory.lessons импортирует отсюда)
logic/learn_rules.py  чистая: RULE_KINDS=("skip_when",), validate_rule, apply (RULE_<id> / E_RULE_<id>), is_tightening, cond_h, block_h
knowledge/            schema2 (DDL v2a, триггеры), store2 (migrate_v2a, append_row, make_backup, restore, verify/stats, чёрный список),
                      markers (файлы-маркеры, is_corruption), rulebook_view (читатель для мозга и шлюза),
                      cli (./tb knowledge), rulebook_cli (./tb rulebook, отклонение урока), learn_cli (./tb learn promotion|gate)
learnloop/            conf, clusters (снимки cluster_runs, выборка окна с cut_ts), stat (парный t), verify, menu, candidates,
                      rules, versions (R-NNNN, promotion_ready), trial, promote, reconcile, watch, gate, hypref, cycle, service
memory/               features.rule_features (одна функция признаков для меток и мозга), lessons (уроки меток src=labels, transition), diagnose
core/hyps.py          словарь гипотез (config/hypotheses.json): family(raw) для тега плана и expstats
```
CLI владельца (`./tb knowledge|rulebook|learn`) живут в `tb.knowledge`, вне `tb.learnloop`: только `learn_cli.set_mode` пишет `config/learnloop.json:promotion.mode`, сам цикл в `config/` не пишет.

**Сервис `learnloop`** (`python -m tb.learnloop.service run`, профили paper/testnet/trade, heartbeat `runtime/platform/learnloop.hb.json`, блокировка `runtime/platform/learnloop.lock`, окружение без ключей как у `labels`). Проходы `learnloop.cycle`: каждый проход `rule_eval_import`; `observe` раз в 15 мин (закрытие кластеров, сигнал повреждения); суточные после `daily_after` UTC (`integrity_check`, копия, меню раз в 7 суток, приём кандидатов, holdout, уроки `hyp`, регистрация испытаний); раз в час вердикты, продвижение, сверка статусов уроков, сторож, `hypref`. Ошибка шага — `STEP_ERROR` и следующий шаг; торговля и мозг от цикла не зависят. Учёт суток и прогрев: Requirements 15.6, 10.6.

**Таблицы v2a** в `state/knowledge.sqlite` (миграция `store2.migrate_v2a` отдельно от `labels.schema`, версия `meta.knowledge_v2_ver`, маркер `state/knowledge.v2`, перед миграцией копия `premigrate`): `cluster_runs` (неизменяемые снимки прогонов закрытого кластера (час, сторона); все расчёты цикла только по ним), `lesson_candidates`, `trials` (критерий и активный набор пишутся до старта, ключевые колонки неизменяемы), `rule_rows` (ревизии правил, ожидание `wait`), `rulebook_versions` (хеш-цепочка `prev`/`h`), `blocklist_rows` (ключ — семейство условия `block_h`), `rule_evals` (из записей `decisions` `kind="note"`, `what="rule_eval"`), `learn_events` (шаги, ошибки, действия `OWNER`). UPDATE/DELETE запрещены триггерами (кроме изменяемых колонок `trials` и `lesson_candidates`). Копии `state/backups/knowledge-<время>-<причина>.sqlite` (`daily`, `premigrate`, `promote`, `rollback`, `owner`), ротация по причинам. Случаи и уроки во второй итерации переносятся в таблицы v2b той же базы (R2.9b, раздел «Вторая итерация» ниже).

**Потоки данных.**
```
labels: policy_runs P2 --observe: закрытие кластера--> cluster_runs (insert-only, один раз)
меню (окно 14 сут, cut_ts) --скрининг--> lesson_candidates --приём <= 3/сут--> holdout 7 сут --> урок hyp (memory.lessons, src=labels)
hyp --> правило trial + trials --выборка окна испытания--> verdict --> promote (ожидание READY|MAX_ACTIVE|LOSS_CAP) --> R-NNNN, копия, событие rulebook_promoted
сторож (точки 50/100/200, затем раз в неделю; TRADE_STARVATION) --> R-NNNN+1 без правила, событие rulebook_rollback; аренда runtime/watch_lease.json
reconcile (раз в час) --> статусы уроков меток по rule_rows/trials (lessons.transition, только вперёд)
мозг: brief.build -> brain/cands.candidates -> rulebook_view.resolve_event -> memory.features.rule_features -> learn_rules.apply -> cands ok=false RULE_<id>
шлюз: submit_plan -> h_submit (после ранних проверок) -> те же функции -> E_RULE_<id>; запись decisions note/rule_eval -> цикл переносит в rule_evals
hypref (раз в час) --> runtime/learn/hyp_ref.json --> досье experiments, ./tb exp, страница «Учится ли мозг»
```
Мозг и шлюз читают rulebook только через `tb.knowledge.rulebook_view` (соединение `labels.store.open_ro` на один вызов, кеш версии в памяти) и применяют его только по единому правилу безопасности (база читается и не восстанавливается, аренда сторожа свежа, `runtime/rules_switch.json` не выключен), иначе режим без применения: `docs/BRAIN.md`, «Правила rulebook». Защита и восстановление базы: `docs/RUNBOOK.md`, «Цикл обучения». При маркере `state/knowledge.v2` `labels.store.open_rw` не уходит в карантин R1 (`knowledge.corrupt-*.sqlite` и пустая база), а поднимает `DbCorruptError`.

**Дашборд.** Вкладка «Память» (`/brain/memory`): операции сокета `memory_cases|memory_lessons|memory_rules|memory_path` (чтение) и `lesson_reject|rule_disable|rules_disable_all` (`tb.dash2.memory_ops`, запись только через `tb.knowledge.rulebook_cli`). Страница «Учится ли мозг»: блоки `lessons`, `versions`, `hyps` (`tb.learn.stages`).

**Слои** (`tests/test_layering.py`). `tb.logic.learn_cond|learn_rules` чисты и импортируют из `tb` только `tb.logic`. `tb.knowledge` и `tb.learnloop` не импортируют мозг, шлюз, MCP, биржу, исполнитель, управление, launcher, файлы ключей и `tb.llm` (кроме `usage`), прямо и транзитивно; `tb.learnloop` не импортирует CLI вне пакета и `core.localcfg`, писателей `config/` в нём нет. Мозг (`brain/cands.py`, `brief.py`, `validate.py`) и шлюз (`gateway/catalog.py`) импортируют `tb.logic.learn_rules` и `tb.knowledge.rulebook_view`, но не `tb.learnloop`; `tb.memory.lessons` импортирует `tb.logic.learn_cond`.

### Вторая итерация (Requirements 3, 12, 17, 18 и размерные правила)
**Пакеты.**
```
knowledge/store2.py     migrate_v2b: таблицы case_rows, lesson_rows (schema2.V2B, ключ meta.knowledge_v2b_ver), append-only, хеш-цепочка seq/prev/h
knowledge/migrate2.py   ./tb knowledge migrate [--dry-run] (проверка остановки PLATFORM_RUNNING, копии, cases.lock/lessons.lock, одна транзакция,
                        сверка fold, откат, *.pre-r29, маркер state/memory.store="sqlite", индекс похожих), export-jsonl, similar_index
knowledge/memstore.py   фасад хранилища для memory.cases / memory.lessons: mode()/mode_info() (маркер state/memory.store корня; без маркера корень
                        проекта - config/memory.json как совместимость, прочие - jsonl; кеш по mtime, читается на каждой записи и чтении),
                        write_marker/ensure_marker (./tb knowledge store-marker), snapshot, append, очередь state/knowledge.spool.jsonl, replay, verify_chain, signature/present
knowledge/similar.py    nearest(query, source=brain|labels|both): мера memory.similar.similarity; migrate_v2s (индекс ix_sig_type_side)
learnloop/port.py       файловый порт заданий модели (L-*.json, kind="learnloop"), available(), clean()
learnloop/candidates.py вход ролей (llm_request: меню, словарь dict.features и dict.ref), submit_review, accept_llm, collect
learnloop/retro.py      retro_digest (сводка недели кодом), шаг retro
learnloop/brainref.py   справочные уроки мозга, справочная проверка кандидатов модели, freeze, violations
brain/learnjobs.py      обработчик заданий learnloop в демоне API-мозга (pending по kind, захват с арендой, roles_status)
logic/learn_rules.py    + size_cap, require_confirm за флагом allow_size_rules
```
**Случаи и уроки в базе (R2.9b).** Где лежат случаи и уроки, решает маркер `state/memory.store` корня данных (`jsonl` | `sqlite`; нет маркера — `jsonl`; на живой платформе с 2026-11-03 `sqlite` после переноса владельцем). Ключ `store` в `config/memory.json` — только переходная совместимость для корня проекта без маркера (жёлтый `STORE_IN_CONFIG`, снимается `./tb knowledge store-marker`); временные корни и копии `state/` от него не зависят. `memory.cases` и `memory.lessons` сохранили имена и структуры; при `store="sqlite"` они пишут и читают только через `tb.knowledge.memstore` и JSONL-файлы не открывают (так же переведены `memory/tick.py`, `./tb memory verify`, `memory/diagnose.py`, `learn/load.py`, `learn/memory_vs_base.py`). Строки `case_rows`/`lesson_rows` — те же строки, что были в JSONL (`seq`, `rev`, `kind`, `ts` сохраняются), цепочка `prev == h` проверяется в транзакции записи. База недоступна (нет файла, восстановление, повреждение, занята, `CHAIN_BROKEN`) — запись уходит в очередь `state/knowledge.spool.jsonl`, чтение идёт из последней проверенной копии плюс очередь; `./tb knowledge replay-spool` применяет очередь идемпотентно по (ключ, `rev`). Возврат на JSONL — только `./tb knowledge export-jsonl`. Правила, версии rulebook и испытания от `store` не зависят. Эксплуатация: `docs/RUNBOOK.md`, «Переключение случаев и уроков на SQLite».

**Поиск похожих `tb.knowledge.similar`.** `nearest` принимает вектор запроса и источник: `brain` — случаи мозга (предотбор: последние `prefilter_cases` = 2000 по времени того же режима и типа, затем прежний `memory.similar.select`, ответ поле в поле совпадает с ним); `labels` — сигналы меток (SQL по `type`, `side`, при запросе `utc_session`, затем последние `prefilter_signals` = 5000 по `t0` через индекс `ix_sig_type_side ON signals(type, side, t0)`; исход — `net_dir_pct` и `r_net` политики); `both` — общий top-k. Ответ ≤ 2 КБ. Источник `labels` работает только при `config/memory.json: similar.labels=true` (по умолчанию `false`, включение — этап 3), иначе `LABELS_OFF`. В досье и инструменты мозга поиск не подключён (этап 3). Замер: `both` на 100 000 сигналов и 20 000 случаев — 116 мс.

**Порт заданий модели.** Цикл не импортирует мозг: он пишет `state/brain/jobs/L-*.json` (`kind="learnloop"`, `deadline_ts = ts + llm_job_ttl_s` 900 с) и ждёт `L-*.result.json` не дольше `llm_wait_s` (600 с). Демон API-мозга (`brain/api_daemon.py`) рядом с `lifejobs.step`/`reset_claims`/`prune` вызывает `learnjobs.step`: захват `L-*.claim` (O_EXCL, аренда — обновление mtime каждые 30 с, брошенный дольше `claim_ttl_s` 120 с берётся снова), проверка готовности роли, вызов `runner.ask_role`, ответ атомарно; после `deadline_ts` — `EXPIRED` без вызова. Готовность ролей (`roles_status`: роль загружена, назначена в действующих назначениях, есть допустимый профиль, бюджет не исчерпан) идёт в heartbeat демона полем `learn_roles`; `port.available` по нему, свежести heartbeat и состоянию «только защита» решает, ставить ли задание. Иначе шаг пишет `LLM_FALSE` с причиной (`NO_DAEMON`, `PROTECT`, `BUDGET`, `NO_ROLE`) и цикл идёт кодом. Вход модели — только числа, перечисления и идентификаторы (`clean` у писателя и у демона). Расход — `state/llm_calls.jsonl` с полем роли, строка `llm_usd_learn_day` в `brief.economics`.

**Роли `day_reviewer` и `retro`** (`config/roles/*.md|json`, `config/brain.json: shared_roles`, `brain/conf.py:ROLE_SCOPE`, `brain/validate.py:SCHEMAS`, `brain/evalrun.py:ROLES`; фикстуры `tests/fixtures_roles/`). Ответы `tb.lesson_cand/1` (до 5 элементов) и `tb.retro/1` (до 3): выбор пункта меню `{"menu":"M3","dir":"lower"}` или условие из словаря `{"cond":[...],"claim":{...}}`. Проверка в два уровня (`candidates.accept_llm`): ответ целиком (схема, `day_id`/`week_id`, лишние поля), элемент (`BAD_SCHEMA`, `NOT_IN_DICT`, `DUPLICATE`, `TOO_MANY`, `BLOCKED`, `TOO_BROAD`, коды скрининга). Принятый элемент проходит скрининг окна поиска (K = K меню + число своих условий модели), `effect` считает код, далее кандидат `src="llm"` идёт тем же путём, что кандидат кода (holdout, урок `hyp`, испытание). Элемент с признаком из `dict.ref` уходит на справочную проверку по сделкам мозга (`brainref.check_candidate`): справочный урок или `N_TOO_SMALL`, кандидатом не становится. Промпты и словарь: `docs/BRAIN.md`, «Цикл обучения и модель».

**Шаги цикла второй итерации** (`learnloop.cycle.default_steps`): `llm_collect` — каждый проход (готовые ответы порта → `accept_llm`, неудачный ответ или просрочка → `LLM_FALSE`, чистка порта); суточные после `register`: `day_review` (задание `day_reviewer` за прошедшие сутки UTC или `LLM_FALSE`), `brain_ref` (справочные уроки мозга), `retro` (раз в неделю ISO UTC: сводка `retro_digest` и задание `retro` или `LLM_FALSE`, иначе `NOT_DUE`). Условие заморозки `FREEZE` добавлено в `versions.promotion_ready`; условие по нарушениям — в вердикт испытания (`trial`, по записям журнала решений с `basis.shadow_hits`). Итоги шагов — `learn_events` (`step` = имя шага, `code` = `LLM_SUBMIT|LLM|LLM_REJECT|LLM_FALSE`, `ref` = `D-YYYYMMDD`/`W-YYYY-WW`).

**`shadow_hits`.** `brain/cands.rule_check` (общая точка `entry_scout` и `h_submit`) считает правила в испытании и активные правила в режиме без применения, которые отсекли бы решение (`rulebook_view.shadow_hits`), и пишет их в поле кандидата и в `basis.shadow_hits` записи `rule_eval`. В таблицу `rule_evals` поле не переносится: условие по нарушениям читает журнал решений.

**Размерные правила.** `learn_rules` знает `size_cap` (`param.size_mult` в (0, 1), только снижение, итог не выше потолка уровня риска) и `require_confirm` (не порождается до парной политики R1.3/R4), испытание `trial.verdict_size`. Всё за флагом `config/learnloop.json: allow_size_rules=false` (решение владельца 3): виды не создаются и при чтении отклоняются (`blocked`), `rules.derive` порождает только `skip_when`; мозг и шлюз (`rulebook_view`, `brain/cands`, `gateway`) читают правила без флага, поэтому даже при `allow_size_rules=true` размерное правило до отдельного подключения к размеру плана (guard/Gate) на торговлю не влияет.

**Слои.** `tb.learnloop` по-прежнему не импортирует `tb.brain`, `tb.gateway`, `tb.llm` (кроме `usage`); `brain/learnjobs.py` не импортирует `tb.learnloop` (константы протокола совпадают, проверяет тест).

## Политика выхода и сопровождение (R4, `.opencode/specs/R4-exits/`)
Задача: проверить на будущих данных и применить выход лучше текущего `auto_protect`, не давая коду ослабить границу владельца. Пол защиты (`exitpol.floor` из `exec_limits.auto_protect`) — нижняя граница; действующая версия `X-NNNN` (политика `tb.exit_policy/1`) оценивается на путях, проходит испытание и продвигается или откатывается автоматически либо возвращается владельцем. Цикл выходов живёт в сервисе `learnloop`, в торговлю не вмешивается: исполнитель читает только снимок и аренду (`tb.core.exitdoc`), мозг — только досье; в `config/` ни один путь не пишет (единственное исключение — `promotion.mode` через `./tb exits promotion`).

**Пакеты.**
```
logic/exitpol.py            чистая: схема tb.exit_policy/1 (компоненты be, lock, partial, trail_atr, stall, decay, timeout, near_tp,
                            min_profit_r, scope), floor/normalize/validate/weaker_than/policy_hash, compile (политика ∘ пол ->
                            правила auto_be/auto_lock/xp_*), keep_at, apply_state, summary для досье
logic/exitsim.py            чистая: контрфактический симулятор run/pair/replay_brain/path_mfe; правила считает rules.evaluate_manage,
                            доли — qty.close_qty; порядки S и T, признак amb и d_lo, издержки taker/maker/стоп с гэпом, taker_only
                            (ROBUST_COST), cut, конец пути; SIM_VER = "xs1"
logic/qty.py                чистая: floor_step, close_qty (перенесена из executor/core.py): одна доля закрытия у исполнителя,
                            симулятора и сравнения с фактом
labels/paths.py             run_paths/trade_paths: encode/decode (OHLC разностями в тиках, zigzag-varint, zlib), run_path_row в
                            транзакции label_one, extend_step (догрузка окна, state=complete), backfill, trade_paths_step (курсор
                            position_closed, окно после закрытия, qty/qty_step/min_qty, части закрытия, хронология вмешательств,
                            adopted, trade_retry_h), rotate (paths.keep_days), load_cfg/enabled
core/exitdoc.py             читатель для исполнителя: снимок runtime/exit_policy.json, аренда runtime/exit_lease.json, выключатель
                            runtime/exits_switch.json, срок lease.lease_h; причины LEASE/SNAPSHOT/HASH/SWITCH, при любой ошибке —
                            пол; кеш по подписи файла, выключатель читается каждый раз
learnloop/exits_conf.py     строгая загрузка config/exits.json (схема tb.exits_cfg/1): меню (23 варианта: normalize/validate/
                            weaker_than при загрузке), согласованность (paths.hold_mult >= max timeout.hold_frac, search.min_clusters
                            = ceil(0.75 × trial.n_target), brain.min_n <= brain.n), config_hash; битый файл -> DISABLED
learnloop/exits_eval.py     шаг eval_pass (eval.every_s): пути run/trade -> оценки вариантов в exit_evals (ord S/T, px_mode
                            fee/taker, block по unit.block_h и стороне, path_mfe_r), таблица near_tp, очередь eval_backlog,
                            ротация меню keep_days_menu, справочный срез pass_active
learnloop/exits_search.py   поиск раз в search.every_days: окно window_days, матрица блоков × меню, max-T (Westfall–Young,
                            B = search.perm_b, зерно — хеш выборки), отбор по alpha/min_effect_r/min_clusters, ROBUST_COST, хвост;
                            exit_search, near_tp по путям раньше окна, чёрный список, NOOP
learnloop/exits_trial.py    критерий (неизменяемый, criterion_h, триггер tr_exit_trials_crit), register, шаг register_candidates
                            (кандидаты exit_search -> испытания, trial.max_parallel), чистый verdict (MEAN,
                            ROBUST_COST, TAIL, ROBUST, BRAIN), шаг verdicts, закрытие COST_CHANGED/SIM_CHANGED/FLOOR_CHANGED/
                            BASE_CHANGED, чёрный список на cooldown_days, exit_trials
learnloop/exits_versions.py версии X-NNNN (INIT, PROMOTE, ROLLBACK, FLOOR_CHANGED, OWNER_ROLLBACK), готовность promotion_ready
                            (свежий сторож, проверенная копия не старше 24 ч), promote, публикация снимка и аренды одной
                            операцией, floor_changed, rollback, события exit_policy_promoted/exit_policy_rollback
learnloop/exits_watch.py    сторож: точки watch.looks (2/4/8 блоков) и недельная, откат по путям (d_w) и по сделкам мозга (d_b
                            через replay_brain), EXIT_SIM_GAP и пауза продвижения, аренда by="watch", meta.exits_watch
learnloop/exits_report.py   R4.2 after и write_after (необязательные поля случая result.after_mfe_r, result.after_t1), R4.3 brain
                            (d_b, разрезы по вмешательствам и кодам пробуждения, стоимость токенов), gate, page_block/run_block
knowledge/exits_cli.py      ./tb exits: status|versions|search|trials|brain|after|gate|apply|rollback|promotion — чтение и
                            аварийные средства владельца
learn/exits.py              блок «Выходы» страницы «Учится ли мозг»: R0.2 (MFE, доля забранного) и раздел r4
```
Конфиг `config/exits.json`: `paths` (пути и окна), `unit.block_h` = 24 (сутки UTC и сторона), `eval` (раз в час, до 20 000 оценок за проход, меню живёт 28 суток), `maker` (1 тик, 2 б.п., `wait_s` 60), `search` (раз в 7 суток, окно 14, `max_k` 24, B = 2000, alpha 0.05, эффект 0.03R, минимум 23 блока), `trial` (30 блоков, 2 параллельно, 21 сутки), `tail` (MFE 1.5R, keep 0.8, n 30), `robust.tol_r` 0.02, `brain` (60 сделок, минимум 10, допуск 0.08R), `promotion` (`auto`, ожидание 7 суток, свежесть сторожа 3 ч), `watch` (точки 2/4/8, недельное окно 8, откат от −0.02R, сделки мозга от 50 при −0.05R, `EXIT_SIM_GAP` 0.1R), `lease.lease_h` 6, `cooldown_days` 14, `menu_ver` 1 и меню 23 вариантов. Числовых умолчаний в коде нет.

**Таблицы x1** (`knowledge/schema2.py`, миграция `store2.migrate_x1`, ключ версии `meta.knowledge_x1_ver`, копия `premigrate`; база v2a без них остаётся зелёной): `exit_variants` (вариант меню, нормализованная политика, `policy_h`), `exit_evals` (оценка пути на варианте: `src` `run|trade`, ключ пути, `var_id/var_ver`, `ord` `S|T`, `px_mode` `fee|taker`, `block`, `r_net`, `r_gross`, `fees_r`, `mfe_r`, `path_mfe_r`, `cost_h`, `sim_ver`, `policy_h`, `labeled_ts`), `exit_versions` (append-only, хеш-цепочка `prev_h`/`row_h`), `exit_search`, `exit_trials` (триггер неизменности критерия), `exit_blacklist`. Оценки читают поиск, испытание и сторож; версии — исполнитель через снимок и отчёты.

**Потоки данных.**
```
labels: run_paths (partial) --extend_step--> complete; trade_paths (position_closed, окно close_ts + post_close_min)
exits_eval: complete-пути × варианты (действующая, предыдущая, испытания; меню — только в окне поиска) -> exit_evals, eval_backlog -> 0
exits_search: блоки × меню, max-T -> кандидаты в exit_search.results и learn_events
exits_register (раз в час): кандидаты последней строки exit_search с кодом OK (политика с таблицей near_tp) -> не больше
                             trial.max_parallel испытаний по убыванию среднего d -> exit_variants и exit_trials (open)
exits_verdicts (раз в час): n_target блоков, brain.n сделок, tail.min_n путей хвоста -> verdict, закрытие испытания
exits_promote (раз в час): promote -> X-NNNN -> runtime/exit_policy.json + runtime/exit_lease.json, событие exit_policy_promoted
exits_watch (раз в час): точки d_w и d_b -> ROLLBACK; EXIT_SIM_GAP (пауза продвижения); аренда by="watch"
exits_after (раз в сутки): поля «после закрытия» в случаях-сделках
исполнитель: exitdoc.current -> rt.pos.exit на входе -> limits_for(exit_policy, exit_ctx) -> evaluate_manage каждый тик -> rt.close.rule,
             поле случая exit_rule, журнал XP:*
мозг: brief.exit (exitpol.summary) -> position_manager; amend с правилом xp_*/auto_* -> E_EXIT_POLICY
страница: tb.learn.exits -> exits_report.run_block; кнопка «Выходы: вернуть границу» -> dash2.memory_ops.exit_rollback
владелец: ./tb exits ... (база только на чтение; apply пишет выключатель без базы)
```
Кандидатов последнего успешного поиска (строка `exit_search` с кодом `OK`, поле `results`) регистрирует шаг `exits_register` между поиском и вердиктами: политика варианта собирается из меню и таблицы `near_tp` той же строки, поэтому в испытание уходит ровно та политика (и тот `policy_h`), что прошла поиск. Порядок — по убыванию среднего `d`, не больше `trial.max_parallel` открытых испытаний; чёрный список, вариант с уже существующим испытанием и вариант, чей `policy_h` уже стал версией, пропускаются — повторный проход на той же строке ничего не создаёт.

**Шаги цикла.** `exits_eval` — период `pass` с собственным интервалом `eval.every_s`; `exits_search`, `exits_register`, `exits_verdicts`, `exits_promote`, `exits_watch` — часовые (периодичность поиска проверяет сам модуль, `exits_search.due`, регистрация идемпотентна); `exits_after` — суточный. Ошибка шага выходов пишется `STEP_ERROR` и не останавливает шаги R2; `EXIT_SIM_GAP` останавливает только продвижение. Файлы состояния: `runtime/exit_policy.json`, `runtime/exit_lease.json`, `runtime/exits_switch.json`, `meta.exits_watch`, `meta.eval_backlog`; аренда сторожа R2 — отдельный `runtime/watch_lease.json`, версии и `ctx_ver` R2 не меняются.

**Исполнитель.** `_exit_doc` пишет `rt.pos.exit` в `_finalize_entry` и `adopt_position`; `limits_for` передаёт `exit_policy` и `exit_ctx` (продление `lifecycle.max_hold_min` их не меняет); причина закрытия правилом — `rt.close.rule = xp_*`, компонент — в поле случая `exit_rule`; сверка лесенки распознаёт заявки `tbx-xp-<план>-<правило>` и снимает осиротевшие; стоп на бирже заявки политики не трогают. Статус отката к полу виден в `runtime/exec_health.json` и `./tb status`. Мозг и шлюз: `brief.exit` (≤ 60 токенов) и `E_EXIT_POLICY` для `amend_plan` по правилам кода. CLI владельца: `docs/RUNBOOK.md`, «Политика выхода: команды `./tb exits`».

**Слои** (`tests/test_layering.py`). `tb.logic.exitpol|exitsim|qty` импортируют из `tb` только `tb.logic`; `tb.core.exitdoc` — только `tb.paths` и `tb.logic.exitpol` (без `learnloop`, `knowledge`, `labels`, `venues`, `executor`); `tb.learnloop.exits_*` не импортируют мозг, шлюз, MCP, биржу, исполнитель, управление и CLI и не пишут в `config/`; писатель `config/exits.json` ровно один — `tb.knowledge.exits_cli.set_mode`. `tb.learn` читает базу выходов только через `exits_report` (`open_ro`).

## Фигуры и уровни (опционально)
`marketdata/patterns.py` (чистые функции) -> `patterns_scan.py` (`./tb patterns scan --sym ETH [--tf 1h|4h|1d]`, только чтение `data/` и `runtime/market.json`).
События для наблюдателя: `config/watch_config.json` ключ `"patterns_events": true` (по умолчанию false, ключа в файле нет; пороги `config/patterns_config.json`). События `pattern` помечены `unvalidated`: фигуры не прошли проверку (`RESEARCH.md`).
