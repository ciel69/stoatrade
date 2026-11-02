# Implementation Plan: R1-label-factory

## Overview

Реализация блоков R1.1–R1.6 роадмапа. Волна 0 — чистая логика и независимые правки (файлы не пересекаются). Волна 1 — хранилище, захват, загрузка свечей. Волна 2 — признаки, политики, мозг на кандидатах. Волна 3 — разметка и сервис. Волна 4 — CLI, догрузка, признаки случаев, документация, полный прогон.

## Tasks

- [x] 1. Чистые функции свечей `tb.logic.bars`
  - `from_bybit` (сортировка, отсев незакрытой свечи), `closed_before`, `agg`, `atr`, `ret_pct`, `range_pct`, `gaps`, `path_stats`
  - тесты `tests/test_logic_bars.py`: обратный порядок и незакрытая свеча, агрегация 1м→5м по UTC, ATR на известном ряде, пропуски, MFE/MAE и время до них (свойство 4)
  - _Requirements: 2.4, 2.9, 3.1, 5.1, 5.2, 5.3_

- [x] 2. Перенос геометрии в `tb.logic.geometry`
  - первым шагом снять регрессионные фикстуры: не меньше 20 досье (chase, отказы, оба типа входа) → вывод текущего `brain/cands.candidates` в `tests/fixtures_geometry/`
  - обойти зависимости `brain/cands.py` и перенести в `tb.logic.geometry` алгоритм (`pool`, `candidates`, `_horizon`, chase) и всё, что он берёт из `brain/validate.py` и `brain/entryplan.py` (как минимум `stop_floor_pct`, `exec_stop_pct`, `_entry_rr`, `chase_impulse`, `_loose`, `ENTRY_MAX_*`, `ENTRY_MIN_RR`, `ENTRY_SIZE_MULT_MAX`, `LOOSE_RISK_LEVEL`, `CHASE_*`, `SESSION_ORDER`, `MAX_HOLD_MIN`) с прежними сигнатурами; `validate.py` и `entryplan.py` импортируют их под прежними именами
  - константы `cands.py` в `config/geometry.json` (`geometry_ver: g1`, профили `base`, `chase`; профиль `atr` для теней)
  - `brain/cands.py` → обёртка (сбор `level_pool` и `ctx` вида `entry_context`, вызов, поле `geometry_ver`)
  - `nearest_ok(cands, side, px)` и `atr_plan(side, ctx, profile)` для теней
  - тесты `tests/test_logic_geometry.py`: регрессия поле в поле (свойство 5), пороги (свойство 6), `atr_plan`, отсутствие импорта `tb.brain`; прежние тесты `validate`/`cands`/`entryplan` проходят без правок
  - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

- [x] 3. Симулятор `tb.logic.sim`
  - входы `market`, `candle_confirm`, `candle_confirm_limit`, `limit` с `latency_s` и `entry_ttl_min`; порядок внутри свечи по 7.4; гэпы по 7.5; частичные тейки; автозащита через `logic.rules.profit_floor` и пороги `_auto_rules` (весь блок `auto_protect`); выход по `MAX_HOLD_MIN[horizon]`; цены через `logic.paper` с синтетическими bid/ask
  - тесты `tests/test_logic_sim.py`: 15+ сценариев с эталоном (каждый `exit_reason`, каждый пункт 7.4, гэп за стопом и за тейком), из них ≥ 5 посчитаны вручную в тексте теста; детерминизм, `truncated` (свойство 7)
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

- [x] 4. Скорость памяти случаев
  - кеш в `memory/cases.py` с дочитыванием хвоста, сбросом по размеру/первым 4 КБ; `_exists`/`get` по кешу под `cases.lock`; найти и перевести на копии вызывающих, которые изменяют результат `load`
  - `memory/tick.py`: снимок источников, пропуск `migrate` без изменений, пересчёт уроков как сейчас
  - тесты `tests/test_memory_cache.py`: свойство 10 (перемешанные new/update, изменение результата не портит кеш), скорость 20 000 случаев (повтор ≤ 50 мс, первое ≤ 3 с), пропуск `migrate`
  - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

- [x] 5. Уникальные id событий
  - дата `YYYYMMDD` в id: `hotscan.make_event`, `hotscan.make_deriv_event`, `liqwatch.make_event`, `flowwatch.make_event`; совместимость чтения старых id в `outcomes.py`, `ideawatch.py`, `scansvc.py`, `memory/migrate.py`
  - тесты в `tests/test_hotscan.py` и соседних: id разных месяцев различаются, старые id читаются
  - _Requirements: 1.8_

- [x] 6. База знаний и схема меток `tb.labels.store`, `tb.labels.schema`
  - `open_rw` (`isolation_level=None`, WAL, `synchronous=NORMAL`, `journal_size_limit`, `busy_timeout=5000`, `BEGIN IMMEDIATE`), `open_ro` (`query_only`), миграции v1, `tx`, `backup` с повторами, `sync_dir_warning`, обработка порчи файла
  - тесты `tests/test_labels_store.py`: миграция и откат (свойство 11), читатель параллельно писателю, backup, порча, предупреждение о папке синхронизации, скорость агрегата (100 000 строк ≤ 150 мс; 1 000 000 строк ≤ 300 мс при `TB_SLOW_TESTS=1`)
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9_

- [x] 7. Захват сигналов `tb.labels.capture`
  - `sig_key`, `parse_event` (`ts` → t0, `direction` → `side`), фильтр `capture_types`, курсор (`offset`, `head_sha`), неполная последняя строка, `INSERT OR IGNORE`, `mature_ts`
  - тесты `tests/test_labels_capture.py`: свойства 1 и 2, усечение, изменение начала файла, перезапуск, дубли, битые строки, `tradable=false`, сигнал без направления
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 8. Загрузка свечей `tb.labels.klines`
  - публичный GET `/v5/market/kline`, пагинация, `Limiter` (`max_rps`), повторы 2/5/15 с, пауза 60 с на 403/429, `BtcCache` по 5-минутным корзинам
  - тесты `tests/test_labels_klines.py` на фейковом HTTP: пагинация, повторы, 403/429, ограничитель, кеш BTC, отсутствие обращения к каталогу ключей
  - _Requirements: 2.1, 2.3, 2.5, 2.6, 2.7_

- [x] 9. Признаки на t0 `tb.labels.features`
  - окна свечей по 2.2; признаки монеты; уровни `key_levels(book, now_ms=t0×1000)` на `CandleBook(now=t0)` только из закрытых свечей, развёрнутые в список цен, снимок уровней; контекст BTC; время и `classify_coin`; `fng = sources.fear_greed.value`, `dvol = sources.dvol_ETH.value`, `risk_off` из `runtime/context.json` при свежести; `missing`
  - тесты `tests/test_labels_features.py`: свойство 3, новая монета, нет уровней, устаревший `context.json`
  - _Requirements: 2.2, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 10. Теневые политики `tb.labels.policies`
  - `config/shadow_policies.json` (P2, P4, P5, P6); `load` со строгой валидацией; `shadow_ctx` из `config/labels.json` + признаков (горизонты из `risk_profile.session_types`, издержки расчётом `brief.py`), `ctx_ver`
  - `pick`: профиль `base` — `geometry.candidates` на уровнях метки, `nearest_ok` стороны политики, горизонт кандидата не выше `max_horizon`, вход по `entry` на уровне кандидата; профиль `atr` — `atr_plan`, вход по рынку; P6 через `expstats.assign_variant`; `NO_DIR`
  - тесты `tests/test_labels_policies.py`: ожидаемые планы на фикстурах, `NO_DIR`, отказ геометрии, лишние поля реестра, версии, `ctx_ver` меняется при изменении любого входа
  - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.9_

- [x] 11. Мозг на кандидатах
  - схема ответа `entry_reply/1` в `validate.py`, проверка `PRICE_KEY`; `output_schema` роли остаётся `entry/1`, новое поле роли `reply_schema` и его поддержка в `brain/roles_runtime.py`/`conf.py`
  - `runner.py`: JSON-путь `entry_scout` — `response_format` и проверка сырого ответа по `reply_schema`, затем `_entry_obj` (прежние коды `NO_CANDIDATE:n_OF:m`, `CANDIDATE_FAILED`), затем прежняя проверка `entry/1`; `NO_CANDIDATES` последней проверкой `entry_precheck` при `cands` не `None`; слияние тегов (кандидат, затем модель, ≤ 2); `decisions.basis.cand = {n, geometry_ver}`
  - `config/roles/entry_scout.md|.json` (версия +1, `reply_schema`), описания `es_take`/`es_watch`, сценарии `tests/fixtures_roles/`, обновление `test_roles_c3.py` и `test_brain_*` с указанием причины
  - тесты: свойство 9, равенство плана из `es_take` и JSON-пути, порядок `SYM_BUSY` раньше `NO_CANDIDATES`, слияние тегов
  - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

- [x] 12. Разметка, сервис, чтение
  - `tb.labels.label`: `due`, `label_one` (загрузка окон, признаки, исходы 15/60/240 с `cost_rt_pct` и `net_dir_pct`, флаги `gap`/`halt`, прогон политик симулятором, одна транзакция, `no_data`/`failed`, `ctx_ver`)
  - `tb.labels.service`: цикл захвата и разметки, heartbeat с полями 12.2, `wal_checkpoint`, блокировка второй копии; без публикации в шину
  - `platform/services.py`: `ORDER`, `SERVICE_NAMES`, `DEFAULTS`, `service_cmd`, расширенная очистка окружения для `labels`; `config/platform.json` (paper/testnet/trade); жёлтый статус в `platform/status.py`
  - `tb.labels.query`: `policy_stats`, `outcome_stats`, `coverage`, `throughput`
  - тесты `tests/test_labels_label.py`, `tests/test_labels_service.py`: полный цикл на фейковом fetch, атомарность, свойство 8 (снимок файлов), свойство 12, пороги статуса, вторая копия
  - _Requirements: 2.2, 2.6, 2.8, 2.9, 3.8, 5.1, 5.2, 5.3, 5.4, 5.5, 8.3, 8.6, 8.7, 12.1, 12.2, 12.3, 12.4, 12.6, 12.7, 14.3_

- [x] 13. CLI, догрузка, признаки случаев мозга
  - группа `labels` в `__main__.py`: `status|stats|coverage|show|backfill|rerun|backup|verify` (вывод и коды по 12.5); `stats --by policy` с Бонферрони; `backfill --since` и `rerun --policy --since` только ставят сигналы в очередь (`pending`) при работающем сервисе, прогресс в `status`
  - случаи мозга: блок `snap` в `brain/brief.save` (всегда, в том числе в урезанной версии); чтение `snap` в `migrate._brief`/`features.apply_brief`; `confirmed` из плана; рыночные поля из ctx/события; `./tb memory coverage`
  - тесты `tests/test_labels_cli.py`, `tests/test_memory_coverage.py` (в том числе `snap` в урезанном досье)
  - _Requirements: 4.3, 8.8, 11.1, 11.2, 11.3, 11.4, 12.5, 13.1, 13.2, 13.3, 13.4_

- [~] 14. Слои, документация, полный прогон (код и документация готовы, полный прогон `./tb test` и сверка конфигов лимитов за главным агентом)
  - `tests/test_layering.py`: `tb.labels` без venues/executor/gateway/llm/brain/control/intel.items; `tb.logic.bars|geometry|sim` под правилами чистоты
  - документация: `docs/ARCHITECTURE.md` (база знаний, пакет labels, logic.geometry), `docs/BRAIN.md` (кандидаты вместо цен), `docs/RUNBOOK.md` (сервис labels, backup, папка синхронизации), `docs/ROADMAP.md` (статус R1.x, ссылка на спеку)
  - полный `./tb test` зелёный; проверка, что `risk_profile.json`, `risk_levels.json` и лимиты не изменены
  - _Requirements: 14.1, 14.2, 14.4, 14.5_

- [ ] 15. Проверка после слияния на живых данных
  - на копии `TB_ROOT`: `./tb labels backfill --since <дата>`, сверка 5 меток с графиком биржи (цена t0, MFE/MAE, исход политики), `./tb labels verify` = 0 нарушений
  - после суток работы сервиса: `./tb labels coverage` (порог 3.8) и `./tb memory coverage` (порог 11.5); отчёт с числами в `docs/spec_v2/` или в описании PR
  - _Requirements: 3.8, 11.5_

## Task Dependency Graph

{"waves": [{"id": 0, "tasks": ["1", "2", "3", "4", "5"]}, {"id": 1, "tasks": ["6", "7", "8"]}, {"id": 2, "tasks": ["9", "10", "11"]}, {"id": 3, "tasks": ["12"]}, {"id": 4, "tasks": ["13", "14"]}, {"id": 5, "tasks": ["15"]}]}

## Notes

- Задача 7 использует таблицы задачи 6; в волне 1 их лучше вести одному исполнителю последовательно.
- Задачи 2 и 11 обе трогают `brain/validate.py` (2 — перенос функций и констант в logic с реэкспортом, 11 — схема ответа); волны разные, конфликта нет.
- Задача 11 меняет поведение мозга; причина правок прежних тестов фиксируется в отчёте.
- Сервис включается в профили сразу; по решению владельца слияние ветки — после выпуска дашборда v2 (R0.1).
- Задача 15 выполняется после слияния и не входит в CI.
