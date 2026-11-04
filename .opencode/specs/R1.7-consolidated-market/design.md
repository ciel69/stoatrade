# Design

## Overview

Новый сервис `cmkt` собирает публичные потоки 11 рынков 6 бирж по монетам в фокусе, раз в секунду строит снимок «весь рынок как один» и пишет:

- `runtime/cmkt/snap.json` — последний снимок всех монет (досье, инструмент шлюза, `./tb cmkt`, совместимый `xflow.json`);
- `state/cmkt/YYYYMMDD.sqlite` — сжатые ряды признаков с разрезом по биржам (`deep` 1 с, `light` 5 с), 30 суток.

Потребители читают только через `tb.core.cmktdoc`. Метки и мозг берут признаки одной и той же строки ряда — последней закрытой строго раньше времени сигнала `t0`; `t0` мозг получает из события шины (новое поле `d.t0`). Досье и инструмент читают последний снимок. Сбор полный (все метрики, все биржи); сужаются только меню правил (волны по мощности) и досье (параметр бюджета).

## Решения автора спеки (рассмотренные варианты)

1. **Собственные адаптеры на `marketdata/wsclient.py` (stdlib), а не ccxt.pro.** ccxt.pro отклонён: необязательная зависимость (`CONTRIBUTING.md:20`, `requirements.txt:4–5`), нет управления правилами непрерывности и контрольными суммами, стакан Binance ограничен 20 уровнями (`xstream.py:23`), загрузка рынков через REST ccxt сейчас падает `RequestTimeout` у OKX и Bitget, а прямой WS OKX у сервиса `feed` работает. Цена: 6 адаптеров (по 200–400 строк) и фикстуры форматов; вводятся очередью (Binance, Bybit, OKX, затем Bitget, Gate, Coinbase).
2. **Процесс на рынок (сервис `cmkt@<venue><p|s>`, 11 процессов) и процесс сводки (`cmkt`), а не потоки одного процесса и не процесс на биржу.** Применение дельт стакана стоит 200–500 мкс; в пике × 10 один процесс на биржу (perp и spot) упёрся бы в одно ядро GIL; процесс на рынок держит пик ≤ 0.8 ядра (раздел «Нагрузка») и изолирует сбой (Requirement 11.3). Имя без разделителя (`cmkt@binancep`, `cmkt@coinbases`) выбрано потому, что проходит то же правило, что `exec@<id>` (`^…@[a-z][a-z0-9]{1,11}$`, `platform/services.py:250`); вариант `cmkt@binance_perp` отклонён — пришлось бы менять правило имён и `RE_SERVICE` (`services.py:45`). Внутренний идентификатор рынка — `binance_perp` (файлы, снимок, ряды); соответствие даёт `cmkt_market(name)`.
3. **Обмен сборщик → сводка компактными файлами `runtime/cmkt/ex/<market>.json`, а не локальным сокетом.** Файлы — принятый в проекте способ (`runtime/`), переживают перезапуск сводки, отлаживаются глазами. Сборщик сам считает полосы, стены и окна, поэтому в файл идут только итоговые числа (около 300 Б на монету; монеты `light` — раз в 5 с), без корзин стакана, кроме корзин 25 б.п. у монет `deep`. Стоимость записи — раздел «Нагрузка». Сокет отклонён: больше кода и протокол.
4. **Дисбаланс и полосы — от середины своего рынка; стены — по абсолютной цене уровня биржи (как в роадмапе).** Премия фьючерса к споту и межбиржевой разрыв (у мелких монет десятки б.п., `flow.json`: `xex_bps` до 83 б.п. на 2026-11-04) сдвинули бы стаканы при сложении по абсолютной цене и дали ложный дисбаланс; поэтому ликвидность «у цены» сравнивается относительно своей середины. Стена же — конкретная цена, до которой рынок должен дойти, и её касание проверяется по сделкам того же рынка, поэтому стена хранится по абсолютной цене уровня (в цене за монету), а в сводную объединяются стены разных рынков с ценами в пределах 5 б.п.
5. **Исключать отставший рынок, а не ждать самого медленного.** Ожидание задержало бы весь снимок на худшую биржу; исключение с кодом честно уменьшает `n_ex` и `cov`. Включение — по возрасту публикации и по задержке последнего сообщения, не по p50 (p50 скрывал бы внезапное отставание).
6. **CVD по `t_rx`, а не по `t_ex`.** Отнесение по времени биржи требует ожидания запоздавших сделок; при допуске `lag ≤ 1 с` ошибка отнесения ≤ 1 с на окнах 60–900 с. Для лидерства (`cm_lead_ex`), где важен порядок в пределах секунд, ряды середин строятся по `t_ex − offset`.
7. **Ряды в отдельных файлах на сутки, а не в `state/knowledge.sqlite` и не в одной базе с `DELETE`.** В базе знаний суточные копии с `integrity_check` (R2 Requirement 2.5) копировали бы до 26 ГБ, писатель рядов конкурировал бы с метками, протокол восстановления R2 затрагивал бы ряды. Одна база с `DELETE` старых строк требует `VACUUM` для возврата места; удаление файла суток — O(1).
8. **Ключ ряда `(ts, sym_id)`, а не `(sym_id, ts)`.** Вставка на такте идёт по одному `ts` для 20–140 монет — с ключом `(ts, sym_id)` это дописывание в конец B-дерева (страницы заполнены плотно); с `(sym_id, ts)` — вставки в 140 мест дерева и деление страниц (около 50 % заполнения). Чтение у потребителей — точный ключ (`row_ts`, `sym_id`), обоим вариантам одинаково дёшево. Задача 26 замеряет заполнение страниц.
9. **Признаки для правил по строке строго раньше `t0`, а не через шину и не по времени решения.** Событие шины ограничено 400 байтами (`core/bus.py:80`), 40 признаков в него не входят; расчёт по времени решения (минуты после `t0`) дал бы мозгу другие значения, чем меткам. В шину добавляется только `t0` (Requirement 23): без него мозг знал бы лишь время конверта, а оно у двух публикаторов позже сигнала на разную величину.
10. **Строка признаков — `floor(t0 / step) × step − step` с правилом `wr_ts ≤ row_ts + 3 с` и единым сроком ожидания `row_ts + accept_s + 1`.** Строка `ts = T` содержит данные до `T`; брать строку строго раньше `t0` — запас против сигнала, событие которого пришло в долю секунды `t0`. Правило времени записи и общий срок ожидания делают результат независимым от момента чтения: мозг ждёт строку до срока, захват меток откладывает сигнал до срока (Requirement 14.6), поэтому обе стороны получают одно и то же (или обе `late`). `pending` только у монеты со строкой предыдущего шага: монета, которой в фокусе не было, ждать не заставляет.
11. **Метки берут признаки из ряда, а не из `snap.json` при захвате.** Захват идёт раз в 15 с с отставанием до 120 с (`labels/features.py:21`), снимок к этому моменту уже другой.
12. **Gate, а не MEXC.** См. раздел «Gate или MEXC».
13. **Ярусы различаются источником стакана и шагом ряда, а не набором метрик** (решение владельца 5). Полный стакан по дельтам для 140 монет на 10 рынках стоил бы около 14 000 сообщений в секунду; частичный стакан покрывает полосу ±0.5 % у большинства мелких монет, полоса без покрытия честно `None` (Requirement 6.3).
14. **Coinbase — только `ticker` и `matches`.** Его роль — премия Coinbase и спот-CVD по USD; стакан Coinbase (`level2_batch`) не нужен для этих метрик и добавляет нагрузку и сомнения в публичности канала.
15. **Совместимый `xflow.json` повторяет `xstream` правилами из `tb.logic.cmkt`, а не импортом.** `tb.cmkt` не может импортировать `tb.intel` и `tb.xdata` (Requirement 3.2), поэтому `compat_symbols` и `compat_walls` — копии `xstream.symbol_set` и `xflow.walls` с тестом равенства на фикстуре. Источник `extra` (горячие монеты `intel`) в `runtime/` не пишется; вариант «`intel` пишет свой список в файл» отклонён — новый писатель и связь `cmkt` с `intel`; выбран топ по обороту из фокуса: позиции, планы, запросы структуры и ядро стоят первыми по тому же правилу, а `extra` влияет только на оставшиеся места из 8, и эти монеты гарантированно в фокусе. Стакан Binance обрезается до 20 уровней, как у `xstream`, чтобы поведение сторожа по стенам Binance не изменилось.
16. **Полосы считает сборщик по уровням, корзины 25 б.п. — только для атрибуции у `deep`.** Сводная полоса — сумма полос рынков, поэтому точность полосы не зависит от ширины корзины, а файл обмена остаётся компактным; узкая полоса ±0.25 % добавлена заранее как замена ±0.5 % для ликвидных монет, у которых глубина выдачи 200–1 000 уровней может не покрывать ±0.5 % (Requirement 6.3).
17. **Отдельного файла запросов фокуса нет.** «Запрос скана» владельца уже выражен сигналами в `hot_events.jsonl` и существующим `runtime/structure_req.json` (запросы структуры мозга и сканера, читает `xstream.requested_symbols`, `xstream.py:67–76`); новый писатель не нужен.

## Gate или MEXC

| Критерий | Gate | MEXC |
|---|---|---|
| Формат публичного WS спота | JSON (`wss://api.gateio.ws/ws/v4/`) | protobuf (JSON-каналы спота выведены в 2025) — нужен разборщик вне stdlib |
| Формат WS фьючерсов | JSON (`wss://fx-ws.gateio.ws/v4/ws/usdt`) | JSON (`contract.mexc.com/edge`) |
| Непрерывность стакана | номера `U`/`u` и REST-снимок `with_id=true` | спот: protobuf-версии; фьючерсы: `version` |
| OI и funding | `futures.tickers`: `total_size`, `funding_rate`, марк, индекс | тикер фьючерсов: `holdVol`, `fundingRate` |
| Ликвидации | публичный канал `futures.public_liquidates` (сверяется задачей 2) | публичного канала нет |
| Покрытие монет | один из самых широких листингов спота и фьючерсов | широкий |

Выбор: Gate. Решающее — JSON на обоих рынках (stdlib, правило проекта) и номера обновлений стакана. Если задача 2 покажет, что канал ликвидаций Gate не публичен, это меняет только `liq_cov`, не выбор.

## Architecture

```
src/tb/logic/cmkt.py          чистая логика: полосы по уровням, корзины 25 б.п., дисбаланс, стены по абсолютной цене и их конец, окна CVD,
                              изменение OI в монетах, OI без ликвидаций, funding к 8 ч и взвешенный, сводная середина (медиана/среднее/значение),
                              премии, лидерство, покрытие, CM_FEATURES, FIRST_WAVE, FILL_ORDER, cm_ver, кодирование строки ряда и by_ex,
                              compat_symbols и compat_walls (копии xstream.symbol_set и xflow.walls)
src/tb/cmkt/__init__.py
src/tb/cmkt/conf.py           строгая загрузка config/cmkt.json (E_CONF:<поле>), умолчания = файл
src/tb/cmkt/adapter.py        контракт VenueAdapter, нормализованные события, BookState (снимок + дельты, правила непрерывности, исходные строки
                              уровней для checksum, граница снимка), TapeBuckets (array), MetaCache, SubPlanner, RestBudget
src/tb/cmkt/ex/binance.py     адаптеры (perp + spot в одном модуле биржи), очередь ввода: binance, bybit, okx, затем bitget, gate, coinbase
src/tb/cmkt/ex/bybit.py
src/tb/cmkt/ex/okx.py
src/tb/cmkt/ex/bitget.py
src/tb/cmkt/ex/gate.py
src/tb/cmkt/ex/coinbase.py
src/tb/cmkt/ex/fakex.py       поддельная биржа для тестов контракта
src/tb/cmkt/collector.py      процесс рынка: соединения, пинг, переподключение, смена соединения Binance до 24 ч, стаканы, ленты, время,
                              публикация runtime/cmkt/ex/<market>.json
src/tb/cmkt/focus.py          набор монет и ярусы -> runtime/cmkt/focus.json
src/tb/cmkt/aggregate.py      такт 1 с: чтение состояний рынков, включение/исключение, снимок, snap.json, совместимый xflow.json
src/tb/cmkt/store.py          файлы рядов по суткам: схема, вставка, ротация, карантин повреждённого файла, контроль диска
src/tb/cmkt/service.py        процесс сводки (python -m tb.cmkt.service run); сборщик — python -m tb.cmkt.collector run --market ID
src/tb/platform/services.py   cmkt_market(name), cmkt_services(root), учёт cmkt@* в default_services, parse_services, stop_order, service_cmd, child_env
src/tb/platform/supervisor.py настройки svc для cmkt@* по ключу "cmkt@" (сейчас поиск по точному имени, строка 213)
src/tb/cmkt/cli.py            ./tb cmkt status|show|at|focus
src/tb/core/cmktdoc.py        читатель для потребителей: latest, features_at, brief_block, detail (без импорта tb.cmkt, не бросает)
config/cmkt.json              параметры (раздел «Конфиг»)
runtime/cmkt/                 ex/<market>.json, snap.json, focus.json
state/cmkt/YYYYMMDD.sqlite    ряды
```

Изменяемые модули: `core/bus.py` (`d.t0`), `platform/scansvc.py` и `executor/scanbridge.py` (публикация `t0`), `labels/capture.py` (`ev.cm`, `EV_NUM_FIELDS`), `labels/cli.py` (`stats`, `backfill`), `memory/features.py` (`rule_features(..., cm=None)`), `logic/learn_cond.py` (`RULE_FEATURES`, `MENU_NUM`), `learnloop/menu.py` (K), `brain/cands.py` (`rule_check` берёт `t0` и `cm`), `brain/brief.py` (блок `cmkt`), `gateway/catalog.py` (инструмент), `config/roles/entry_scout.*`, `position_manager.*` (инструмент), `config/roles/day_reviewer.md`, `retro.md` (описание словаря признаков, если он перечислен в тексте), `intel/service.py` (не запускать `XStream` при `compat_xflow`), `intel/xflow.py` (удаление `XFlow`), `platform/services.py`, `platform/status.py`, `tests/test_layering.py`.

### Поток данных

```
биржи (WS, REST) -> cmkt@<venue><p|s>: t_rx, разбор, BookState/TapeBuckets/Ticker/Liq -> runtime/cmkt/ex/<market>.json (раз в 1 с, T_pub; light раз в 5 с)
focus: heat.json, exec_snapshot.json, hot_events.jsonl, structure_req.json -> runtime/cmkt/focus.json (раз в 10 с) -> сборщики (подписки)
cmkt: такт T: ex/*.json -> включение (Requirement 4.4) -> logic.cmkt -> снимок ts=T
      -> runtime/cmkt/snap.json (каждый такт) -> core.cmktdoc.latest -> brief.cmkt, get_market_detail, ./tb cmkt
      -> state/cmkt/YYYYMMDD.sqlite (deep каждый такт, light при ts%5==0, wr_ts) -> core.cmktdoc.features_at(sym, t0)
            -> labels.capture: signals.ev.cm -> learnloop.clusters -> rule_features -> cluster_runs.feats
            -> brain/cands.rule_check (t0 из d.t0 события) -> rule_features(cm=...) -> learn_rules.apply
      -> runtime/intel/xflow.json (compat_xflow=true, раз в 2 с, рынки и данные как у xstream) -> core.flowdoc.xlive_for -> сторож; intel summary
hot_events.jsonl -> scansvc / scanbridge -> scanner_signal{d.t0} -> шина
```

## Components and Interfaces

### Контракт адаптера (`tb.cmkt.adapter`)

```python
class Trade(NamedTuple):       mkt: str; sym: str; t_ex: float; t_rx: float; px: float; coins: float; side: int   # px — цена за монету; +1 агрессор покупает
class BookSnapshot(NamedTuple): mkt; sym; t_ex; t_rx; seq: Optional[int]; bids: list; asks: list   # [(px_str, size_str)] — исходные строки
class BookDelta(NamedTuple):   mkt; sym; t_ex; t_rx; first: Optional[int]; last: Optional[int]; prev: Optional[int]; checksum: Optional[int]; bids; asks
class Ticker(NamedTuple):      mkt; sym; t_ex; t_rx; mark; index; bid; ask; funding; funding_h; oi_coins; turn24_usd   # цены за монету, Optional
class Liquidation(NamedTuple): mkt; sym; t_ex; t_rx; px: float; coins: float; liq_side: str   # "long" | "short" — чья позиция ликвидирована

class VenueAdapter(Protocol):
    venue: str
    markets: tuple                                # ("perp", "spot")
    def hosts(self) -> dict                       # сверяется с белым списком конфига
    def load_meta(self, http) -> dict             # {mkt: {sym: Inst(native, contract_size, coin_mult, tick, funding_h, quote, status)}}
    def plan(self, mkt, syms_deep, syms_light) -> list   # [Sub(conn_key, topic, kind)]; kind: trades|book|book_part|ticker|oi|liq
    def sub_msgs(self, subs, op) -> list          # тексты subscribe/unsubscribe пачками в пределах лимита биржи
    def ping(self) -> str | None                  # текст пинга биржи (живость по ответу)
    def parse(self, mkt, raw, t_rx) -> list       # нормализованные события; pong -> [Pong]; служебные -> []
    def continuity(self, state, delta) -> str     # "ok" | "gap" | "stale"
    def checksum(self, state) -> int | None       # CRC32 по исходным строкам (OKX, Bitget)
    def book_rest(self, http, mkt, sym) -> BookSnapshot | None
    def server_time(self, http) -> float
    def poll(self, http, mkt, syms, now) -> list  # REST-опрос (OI Binance); у остальных []
```

`BookState` хранит уровни словарём `цена_строка → размер_строка` у рынков с контрольной суммой и `цена_float → монеты` у остальных; раскладка по корзинам переводит строки в числа один раз за публикацию. У рынков с REST-снимком хранится граница снимка по сторонам: уровни дальше неё отбрасываются, глубина выдачи = граница. `http` — обёртка `urllib.request` с белым списком хостов, таймаутом 5 с и `RestBudget` (вес в минуту по бирже).

### Таблица бирж (по документации на 2026-11; задача 2 сверяет живым подключением)

| Рынок | Сделки, агрессор | Стакан `deep` / `light`, непрерывность | Тикер, OI, funding | Ликвидации | Пределы (док.) / наши |
|---|---|---|---|---|---|
| binance.perp | `@aggTrade`, `m=true` → продажа | `@depth@100ms` + REST `/fapi/v1/depth?limit=1000` (вес 20): первая дельта `U ≤ lastUpdateId ≤ u`, далее `pu` = прошлый `u`; на время `RESYNC` — `@depth20@100ms` (`book_part`); новое соединение за 10 мин до разрыва 24 ч / `@depth20@500ms` | `@markPrice@1s` (funding, марк, индекс); OI REST `/fapi/v1/openInterest` (вес 1; 10 с / 60 с); интервал `/fapi/v1/fundingInfo` | `!forceOrder@arr` (≤ 1 на символ в секунду, `liq_partial`) | 1024 потока и 10 сообщ./с на соединение, 2400 вес/мин на IP / 200 тем |
| binance.spot | `@aggTrade` | `@depth@100ms` + REST `/api/v3/depth?limit=1000` (вес 50): первая дельта `U ≤ id+1 ≤ u`, далее `U` = прошлый `u` + 1 / `@depth20@1000ms` | `@bookTicker` | — | 1024 потока, 5 сообщ./с, 6000 вес/мин / 200 |
| bybit.perp | `publicTrade` (`S` тейкера) | `orderbook.200` (`orderbook.500`, если по замеру задачи 2 200 уровней не покрывают ±0.5 % у BTC/ETH) / `orderbook.50` (snapshot, delta, `u`; `u=1` — сброс; разрыв — повторная подписка) | `tickers` (`openInterest` в монетах, `fundingRate`, марк, индекс; 100 мс), интервал `instruments-info` | `allLiquidation` | 10 тем на запрос / 200 |
| bybit.spot | `publicTrade` | `orderbook.200` / `orderbook.50` (глубины и смысл `u` у спота — задача 2) | `tickers` | — | то же |
| okx.perp | `trades` (`side` тейкера), `sz × ctVal` | `books` (400 уровней, `seqId`/`prevSeqId`, CRC32 25 уровней по исходным строкам) для обоих ярусов | `tickers`, `mark-price`, `funding-rate`, `open-interest` (`oiCcy`; частота — задача 2) | `liquidation-orders` (`SWAP`; `liq_partial` — задача 2) | 3 соединения/с с IP, 480 подписок в час на соединение, 64 КБ аргументов / 150 |
| okx.spot | `trades` | `books` для обоих ярусов | `tickers` | — | то же |
| bitget.perp | `trade` (`side` тейкера) | `books` (снимок + обновления, CRC32 25 уровней по исходным строкам) / `books15` | `ticker` (`holdingAmount`, `fundingRate`, марк, индекс) | нет публичного | 10 сообщ./с, до 1000 каналов (рекомендация < 50) / 50 |
| bitget.spot | `trade` | `books` / `books15` | `ticker` | — | то же |
| gate.perp | `futures.trades` (`size` > 0 — покупка тейкера), × `quanto_multiplier` | `futures.order_book_update` (`U`,`u`) + REST `order_book?with_id=true` (предел REST — задача 2): первая дельта `U ≤ id+1 ≤ u` / `futures.order_book` (20) | `futures.tickers` (`total_size`, `funding_rate`), интервал из контракта | `futures.public_liquidates` (задача 2) | / 100 |
| gate.spot | `spot.trades` (`side` тейкера) | `spot.order_book_update` (`U`,`u`) + REST / `spot.order_book` (20) | `spot.tickers` | — | / 100 |
| coinbase.spot | `matches` (`side` — сторона мейкера, агрессор противоположный) | стакан не собирается | `ticker` (`best_bid`, `best_ask`); курс `USDT-USD` | — | / 100 |

Смысл поля funding (текущая ставка периода или прогноз следующей) у каждой биржи фиксируется задачей 2; в `cm_fund_w_pct` идёт одна и та же величина — текущая ставка периода.

### Сборщик (`tb.cmkt.collector`)

- Соединения по `conn_key` плана; пинг биржи раз в `ping_s` (10 с); живость — по последнему ответу на пинг или любому сообщению (`silent_s` 15 с ≥ `ping_s` + 5, проверка загрузчика); переподключение 1 → 60 с, ±20 %; у Binance — новое соединение за 10 мин до разрыва 24 ч, закрытие старого после первых сообщений нового.
- Состояние на монету: `BookState`, `TapeBuckets` (`array('d')` на 900 секундных корзин: buy, sell USD, флаг непрерывности), последний `Ticker`, OI-история (`array`, точки раз в 5 с, 960 с), кольцо ликвидаций (3600 с), стены по абсолютной цене (`(сторона, цена) → first_seen, max_usd, last_usd`), журнал концов стен (900 с), ряд середин по `t_ex − offset` (300 точек).
- Время: `t_rx = time.time()` до `json.loads`; `offset` раз в 600 с; `lag` последнего сообщения и p50 30 с.
- Публикация на границе `T` (монеты `deep` каждую секунду, `light` при `T % 5 = 0`, их прошлые значения сводка держит у себя): `runtime/cmkt/ex/<market>.json` — компактный JSON-массив на монету (около 300 Б): статус, `lag_ms`, `offset_ms`, RTT оценки `offset`, середина по `t_rx` и по `t_ex − offset`, полосы ±0.25/0.5/1/2 % (bid, ask USD целыми), глубина выдачи, до 3 стен на сторону (цена, USD, возраст), концы стен за 15 мин по сторонам (число, USD), окна ленты 1/5/15 мин (`buy`, `sell`, `cov`), тикер, OI сейчас и на `T−60/300/900`, ликвидации 5 мин и 1 ч; у монет `deep` дополнительно корзины 25 б.п. (16 на сторону, около 200 Б). Heartbeat: подписки, соединения, восстановления, REST-вес, CPU, RSS, задержка обработки, трафик, запись на диск.

### Сводка (`tb.cmkt.aggregate`)

На такте `T` (по `time.monotonic`, `T_agg = T + 0.3 с`): читает `ex/*.json` (повтор 3 раза по 50 мс при `PermissionError`), применяет правила включения (Requirement 4.4), сопоставление и `MISMATCH` (Requirement 5.8), вызывает функции `logic.cmkt`, собирает снимок, пишет `snap.json`, строки рядов (`wr_ts` = время вставки), совместимый `xflow.json` (при флаге).

### Время сигнала и строка признаков

```
публикатор:   d.t0 = _t0(line.ts)                       # целая секунда UTC, как signals.t0
features_at(root, sym, t0, now = time.time()):          # реальные часы
    for tier, step in ((deep, 1), (light, 5)):
        row_ts = floor(t0 / step) * step - step
        row = SELECT ... WHERE ts = row_ts AND sym_id = ? AND tier = tier
        if row: return row if row.wr_ts <= row_ts + accept_s else {"cm_missing": "late"}
    prev = есть строка монеты с ts = row_ts - step своего яруса (deep, затем light)
    if prev and now < row_ts + accept_s + 1: return "pending"     # срок ожидания строки
    if not prev: return {"cm_missing": "not_focus"}
    return {"cm_missing": "no_row"}; любая ошибка -> {"cm_missing": "db"}
rule_check:  при "pending" — повтор каждые 0.25 с до row_ts + accept_s + 1, затем последний ответ
capture:     при "pending" — сигнал и следующие строки хвоста ждут следующего прохода (курсор не сдвигается)
```

Строка яруса `deep` берётся только с меткой яруса `deep` (у монеты `light` строка `t0 − 1` может существовать при `(t0 − 1) % 5 = 0`, но с меткой `light`; она не берётся первым правилом и не берётся вторым, если не равна `floor(t0/5)×5 − 5`). Так обе стороны выбирают одну строку детерминированно.

### `tb.core.cmktdoc`

```python
def latest(root, now, max_age_s=5.0) -> dict | None                 # snap.json
def features_at(root, sym, t0, now=None, wait=False) -> dict         # {"cm": {"v", "tier", "focus_s", признаки без None}} | {"cm_missing": code}
def brief_block(root, sym, now, cfg) -> dict | None                  # Requirement 16, бюджет brief.max_tokens
def detail(root, sym, parts, now, cfg) -> dict                       # Requirement 21, предел tool.max_bytes
```

Файл суток открывается `file:...?mode=ro` (`uri=True`) на один вызов (ротация на Windows удаляет файлы); при `row_ts` предыдущих суток читается предыдущий файл. Ни одна функция не бросает.

### Изменения потребителей

- `core/bus.py`: `D_FIELDS["scanner_signal"]["t0"] = "num"`, `"t0"` в `D_PROTECTED["scanner_signal"]`; `scansvc` и `scanbridge` ставят `d["t0"]`.
- `labels/capture.poll`: `r = cmktdoc.features_at(root, s["sym"], s["t0"], now)`; при `pending` — остановка разбора хвоста на этом сигнале (курсор остаётся перед ним), иначе `s["ev"].update(r)` до вставки; `EV_NUM_FIELDS += ("wall_usd", "wall_dist_pct")`, `wall_side` в `ev` строкой.
- `memory.features.rule_features(event, cand, cfg, cm=None)`: `src = cm if cm is not None else f.get("cm") or {}`; для каждого признака `cm_` из `RULE_FEATURES` `out[k] = _num8(src.get(k))`.
- `brain/cands.rule_check` (`brain/cands.py:121`): `t0 = d.get("t0")` (нет — `cm_missing=no_t0`), `cm = cmktdoc.features_at(root, sym, t0, now, wait=True).get("cm")`, передача в `rule_features`.
- `learnloop/clusters.py:159`: вызов не меняется (событие меток несёт `ev.cm`).
- `logic/learn_cond.py`: `RULE_FEATURES += FIRST_WAVE`, `MENU_NUM += FIRST_WAVE` (константа из `logic.cmkt`).
- `gateway/catalog.py`: `_reg(Tool("get_market_detail", ..., "read", h_market_detail, out=("sym","ts","age_s","n_ex","cov","warn","book","cvd","oi","liq","funding","venues","trunc")))`; свой счётчик 30 вызовов в час; в модулях шлюза нет названий бирж.

## Data Models

### `CM_FEATURES` (40 признаков, `tb.logic.cmkt`)

| Группа | Признаки | Единица, масштаб в ряду |
|---|---|---|
| Качество | `cm_n_perp`, `cm_n_spot`, `cm_cov` | шт.; доля × 1000 |
| Стакан | `cm_imb_025_pct`, `cm_imb_05_pct`, `cm_imb_1_pct`, `cm_imb_2_pct`, `cm_imb_05_perp_pct`, `cm_imb_05_spot_pct`, `cm_depth_025_musd`, `cm_depth_05_musd`, `cm_depth_1_musd` | % × 10; млн USD × 100 |
| Стены | `cm_wall_bid_musd`, `cm_wall_ask_musd`, `cm_wall_bid_dist_pct`, `cm_wall_ask_dist_pct`, `cm_wall_pull_15m_pct`, `cm_pulled_15m_n` | млн USD × 100; % × 100; % × 10; шт. |
| CVD | `cm_cvd_1m_pct`, `cm_cvd_5m_pct`, `cm_cvd_15m_pct`, `cm_cvd_5m_perp_pct`, `cm_cvd_5m_spot_pct`, `cm_cvd_ps_5m_pct`, `cm_cvd_5m_musd` | % × 10; млн USD × 100 |
| OI | `cm_oi_musd`, `cm_oi_chg_1m_pct`, `cm_oi_chg_5m_pct`, `cm_oi_chg_15m_pct`, `cm_oi_liq_5m_pct` | млн USD × 10; % × 1000 |
| Ликвидации | `cm_liq_5m_long_musd`, `cm_liq_5m_short_musd`, `cm_liq_1h_net_musd`, `cm_liq_cov` | млн USD × 1000; доля × 1000 |
| Funding, премии | `cm_fund_w_pct`, `cm_fund_spread_pct`, `cm_prem_ps_bps`, `cm_prem_cb_bps` | % × 100 000; б.п. × 10 |
| Лидерство | `cm_lead_ex` (индекс рынка), `cm_lead_lag_s` | код 0–10; с |

Определения: `cm_wall_pull_15m_pct = (USD снятых до касания стен bid − ask за 15 мин) / cm_depth_05 × 100` (знаменатель ±0.5 %, при замене Requirement 6.3 — `cm_depth_025`); `cm_oi_liq_5m_pct` — Requirement 7.4; остальные — Requirements 6–10. `cm_ver` — первые 8 hex sha256 списка имён и масштабов.

### Строка ряда

```sql
CREATE TABLE syms(sym_id INTEGER PRIMARY KEY, sym TEXT UNIQUE NOT NULL);
CREATE TABLE mkts(mkt_id INTEGER PRIMARY KEY, mkt TEXT UNIQUE NOT NULL);         -- binance_perp, ...
CREATE TABLE rows(ts INTEGER NOT NULL, sym_id INTEGER NOT NULL, tier INTEGER NOT NULL, wr_ms INTEGER NOT NULL,   -- wr_ts - ts, мс
                  focus_s INTEGER, skew_ms INTEGER, gap_s INTEGER,
                  flags INTEGER NOT NULL,              -- биты: liq_partial, low_cov, clock_back, warmup, mm_unchecked
                  f BLOB NOT NULL,                     -- 40 значений: zigzag varint, 0 — None, иначе значение + 1
                  by_ex BLOB,                          -- по включённым рынкам: mkt_id, cvd5 (тыс. USD), doi5 (монеты × 10^k), liq5 long, liq5 short (тыс. USD), pulled15 (шт.)
                  PRIMARY KEY(ts, sym_id)) WITHOUT ROWID;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);         -- schema_ver, cm_ver, by_ex_ver
```

Значения одним `BLOB`: смена списка признаков меняет `cm_ver`, а не схему; чтение по `cm_ver` файла.

### `ev.cm` в метках

`{"v": "<cm_ver>", "tier": "deep|light", "focus_s": N, "row_ts": N, <признаки без None>}` — до 40 пар по 20–26 байт (около 950 байт в худшем случае, обычно 600–800), предел 1 000 байт (Requirement 14.5); при превышении отбрасываются признаки с конца `CM_FEATURES`, кроме первой волны.

### Снимок (`runtime/cmkt/snap.json`)

`{"schema": "tb.cmkt/1", "ts", "skew_ms", "clock_back", "spot_quote": "USDT", "cols": [имена CM_FEATURES], "ex": {mkt: [code, lag_ms, offset_ms, age_ms]}, "syms": {SYM: [tier, focus_s, [значения по cols], {mkt: code}, bands, walls, by_ex]}}` — компактный, около 1.2 КБ на монету, около 170 КБ на 140 монет; пишется раз в 2 с (досье и инструмент допускают снимок не старше 5 с).

### Конфиг (`config/cmkt.json`, умолчания)

```json
{"schema": "tb.cmkt_cfg/1", "enabled": true, "compat_xflow": false,
 "focus": {"core": ["BTC","ETH","SOL"], "top_n": 30, "deep_top_n": 10, "max_deep": 20, "max_light": 120, "keep_signal_min": 180,
           "struct_req_ttl_min": 30, "hold_s": 600, "warm_s": 300},
 "venues": {"binance": {"enabled": true, "hosts": ["fstream.binance.com","stream.binance.com","fapi.binance.com","api.binance.com"]}, "...": {}},
 "sync": {"tick_s": 1, "lag_max_ms": 1000, "lag_max_ms_venue": {}, "silent_s": 15, "file_stale_s": 1, "accept_s": 3, "ping_s": 10,
          "clock_warn_ms": 500, "time_sync_s": 600, "lead_rtt_max_ms": 100, "lead_offset_max_age_s": 900, "snap_every_s": 2},
 "book": {"bucket_bps": 25, "max_bps": 200, "bands_pct": [0.25, 0.5, 1, 2], "imb_band_first_wave": 0.5, "wall_min_market_usd": 100000, "wall_mult": 6, "wall_min_usd": 400000, "wall_group_bps": 5,
          "wall_persist_s": 30, "touch_bps": 5, "pull_drop_share": 0.7, "pull_window_s": 5, "resync_min_s": 10},
 "cov": {"min_cov": 0.6, "min_n_ex": 2, "liq_min_cov": 0.5, "mismatch_pct": 2.0},
 "limits": {"subs_per_conn": {"default": 200, "bitget": 50, "okx": 150, "gate": 100, "coinbase": 100}, "sub_msgs_per_s": 5, "conn_per_s": 1,
            "backoff_s": 1, "backoff_max_s": 60,
            "rest_weight_per_min": {"binance_perp": 400, "binance_spot": 1100, "bybit": 100, "okx": 60, "bitget": 60, "gate": 60, "coinbase": 30},
            "oi_reserve_per_min": {"binance_perp": 240},
            "oi_poll_s": {"deep": 10, "light": 60}},
 "budget": {"cpu_avg_cores": 1.5, "cpu_p99_cores": 4.0, "rss_mb": 1500, "proc_p99_ms": 300, "net_mbit": 80, "disk_write_gb_day": 30,
            "over_min": 10, "shrink_step": 20, "light_min": 40},
 "store": {"dir": "state/cmkt", "keep_days": 30, "deep_every_s": 1, "light_every_s": 5, "disk_min_gb": 40},
 "brief": {"mode": "compact", "max_tokens": 90, "syms": 1, "experiment": false},
 "tool": {"max_bytes": 2048, "per_hour": 30}}
```

## Монеты в фокусе

Замер по `state/knowledge.sqlite` (только чтение, 2026-11-04, 28 481 сигнал за 6.8 суток, 199 монет, 2 130 заполненных прогонов P2). Доля прогонов P2, у монеты которых был сигнал в окне хранения не позже чем за 5 мин (15 мин) до `t0`, плюс топ-30 по обороту:

| Хранение `keep_signal_min` | Прогрев 300 с | Прогрев 900 с | Размер набора: среднее / p95 |
|---|---|---|---|
| 120 | 0.906 | 0.901 | 88 / 131 |
| 180 | 0.935 | 0.932 | 96 / 140 |
| 240 | 0.953 | 0.950 | 101 / 145 |

Выбрано 180 мин: покрытие 0.935 при p95 набора 140 = `max_deep` + `max_light`; 240 мин дают +0.018 покрытия ценой выхода p95 за предел (вытеснение около 10 % времени). Без динамики топ-30 даёт 0.315 сигналов; 74 % сигналов имеют сигнал той же монеты за предыдущие 30 мин.

## Синхронизация

```
на сообщение:   t_rx = time.time(); lag = t_rx - (t_ex - offset)
раз в 600 с:    offset = server_time - (t_send + t_recv)/2 по запросу с наименьшим RTT из 5
раз в 1 с (T):  сборщик публикует состояние на T (T_pub = T), только события с t_rx <= T
такт сводки:    T_agg = T + 0.3 с; рынок включён, если T - T_pub <= 1 (для light — от последней публикации кратной 5), conn_alive (pong/сообщение <= 15 с),
                lag_last <= lag_max, book.status in (ok, book_part)
                skew_ms = max((T - T_pub)*1000 + lag_ms); ts = T; если ts <= ts_prev -> пропуск, clock_back += 1
```

Допуск «одного момента»: значение включённого рынка относится к моменту не раньше `T − 2.0 с`; типичный `skew_ms` при задержке бирж 50–300 мс — 50–1300 мс (из них до 1 000 мс — шаг публикации). Для монет `light` значения обновляются раз в 5 с: в снимках между публикациями берётся последняя публикация, допуск для них — `T − 6 с`, и ряд пишется только на кратных 5 секундах, где допуск тот же, что у `deep`. Сдвиг локальных часов одинаков для всех процессов машины, поэтому согласованность держится на `t_rx`; `offset` нужен для `lag`, лидерства и `CLOCK_SKEW`; лидерство считается только при погрешности `offset` ≤ 50 мс (RTT ≤ 100 мс) у всех сравниваемых рынков. Расписание — `time.monotonic`, метки — `time.time`.

## REST-бюджет

20 % лимита веса на IP минус оценка других потребителей платформы (`flowwatch` — Binance `bookTicker` всех символов, вес 5 раз в 5 с = 60/мин; `intel/xex` — `fetch_tickers` и funding/OI по 8 монетам раз в 300 с ≈ 20/мин).

`rest_weight_per_min` — общий вес `cmkt` на рынок в минуту; `oi_reserve_per_min` — его часть, закреплённая за опросом OI; снимки стакана, метаданные и время берут только остаток (метаданные и время — около 10 веса в минуту в среднем).

| Рынок | Лимит на IP | 20 % | Другие потребители | `rest_weight_per_min` | `oi_reserve_per_min` | Остаток на снимки | Снимков в минуту | Ресинхронизация 20 стаканов `deep` |
|---|---|---|---|---|---|---|---|---|
| binance.perp | 2400/мин | 480 | 80 | 400 | 240 (20 × 6 + 120 × 1) | 150 | 7 (вес 20) | без мер ниже 172 с; с мерами — 62 с, первые 8 монет (позиции, планы, ядро) — 36 с |
| binance.spot | 6000/мин | 1200 | 0 | 1100 | — | 1090 | 21 (вес 50) | 57 с |
| gate.perp, gate.spot | по задаче 2 | — | 0 | 60 запросов | — | 55 | 55 | 22 с |
| bybit, okx, bitget | снимок по WS | — | — | метаданные, время | — | — | повторная подписка ≤ 5 сообщ./с | ≤ 10 с |

Меры для Requirement 5.5 (Binance `perp` ≤ 150 с для 20 стаканов, ≤ 60 с для первых 8): (1) снимки монет вне позиций, планов и ядра берутся с `limit=500` (вес 10), первые 8 — с `limit=1000` (вес 20): всего 8 × 20 + 12 × 10 = 280 веса; (2) при `RESYNC` больше 4 стаканов Binance `perp` опрос OI яруса `light` (120 из 240 резерва) ставится на паузу до конца восстановления — остаток 270 веса в минуту; итог: первые 8 монет — 160 / 270 мин ≈ 36 с, все 20 — 280 / 270 мин ≈ 62 с. На время паузы признаки OI монет `light` с Binance получают `None` по свежести (Requirement 7.3), остальные биржи их OI дают. Пока стакан в `RESYNC`, Binance держит для него частичный стакан `@depth20@100ms` (`book_part`), остальные рынки монеты работают. Ярус `light` (частичные снимки по WS) REST не требует.

## Волны меню

**Первая волна (6 признаков).** Каждый из своего семейства, без дубля признаков текущего меню; оценка заполнения — доля не-`None` среди заполненных прогонов P2 (база — 0.935 при прогреве 300 с и 0.932 при 900 с по таблице «Монеты в фокусе»; множители — допущения, задача 2 и проверка через 7 суток их уточняют):

| Признак | Семейство | Условия не-`None` | Оценка заполнения |
|---|---|---|---|
| `cm_imb_05_pct` | ликвидность | ≥ 2 рынка с глубиной ≥ 0.5 %: у мелких монет `light` частичные стаканы 15–50 уровней обычно покрывают ±0.5 % (× 0.95); у ликвидных монет `deep` (около 18 % сигналов: ядро и топ-10) 200–1 000 уровней могут не покрывать ±0.5 % — допущение «покрыто у половины» даёт × 0.91 | 0.81; с заменой на ±0.25 % (Requirement 6.3) — 0.88 |
| `cm_cvd_5m_pct` | агрессия | прогрев 300 с, ≥ 2 рынка, покрытие ленты ≥ 0.6, × 0.97 | 0.91 |
| `cm_cvd_ps_5m_pct` | источник спроса | плюс спот к USDT хотя бы на одной бирже с покрытием ≥ 0.6, × 0.88 | 0.82 |
| `cm_prem_ps_bps` | базис | оба вида рынков включены, × 0.88 | 0.82 |
| `cm_oi_liq_5m_pct` | позиционирование | рынки с потоком ликвидаций дают ≥ 0.5 OI (Binance, Bybit, OKX), × 0.90 | 0.84 |
| `cm_wall_pull_15m_pct` | подставные заявки | прогрев 900 с, знаменатель `cm_depth_05` не `None` (то же покрытие, что у `cm_imb_05_pct`) | 0.81; с заменой на ±0.25 % — 0.88 |

Четыре признака близки к порогу 0.8: `cm_cvd_ps_5m_pct`, `cm_prem_ps_bps` (главное допущение — доля монет в фокусе со спотом к USDT) и `cm_imb_05_pct`, `cm_wall_pull_15m_pct` (охват ±0.5 % у ликвидных монет). Задача 2 замеряет фактический охват полос ±0.25/0.5/1/2 % по каждому рынку на BTC, ETH и монете топ-30 (доля времени, когда глубина выдачи покрывает полосу) и долю монет со спотом; по замеру до задачи 19 применяется заранее записанная замена полосы (Requirement 6.3) и, для Bybit `deep`, `orderbook.500` вместо `orderbook.200`. Запасной порядок замены (`FILL_ORDER`) при провале (а) или (б) в проверке через 7 суток: `cm_oi_chg_5m_pct`, `cm_imb_1_pct`, `cm_cvd_5m_perp_pct`. Не в первой волне: `cm_fund_w_pct` (ожидаемая корреляция с `funding_pct` Bybit > 0.7, критерий (в)); `cm_prem_cb_bps` (только монеты Coinbase, заполнение < 0.8); `cm_liq_*` (нули у большинства прогонов, q20 = q80 = 0, критерий (б)); `cm_imb_1/2` (дубль `cm_imb_05`); `cm_lead_ex` (категориальный).

**Когда признаки реально входят в меню.** Окно поиска 14 суток, граница `b` ≤ now − 48 ч 15 мин (`cut_ts`, спека R2); прогоны до запуска сбора в окне имеют `None`. Заполнение окна ≈ (доля окна после запуска) × 0.82–0.91 ≥ 0.8 требует, чтобы после запуска прошло не меньше 0.8 / 0.89 × 14 ≈ 12.6 суток окна (для признаков с заполнением 0.82 — 13.7 суток), то есть now ≥ запуск + 14.6–15.7 суток; меню строится раз в 7 суток, поэтому первое меню с признаками — между 15-ми и 22-ми сутками после запуска. До этого меню само отсекает их кодом `COVERAGE_LOW`. Проверка первой волны командой `./tb learn menu-wave --check` — через 7 суток после запуска (Requirement 15.8).

**Мощность** (метод задачи 9 спеки R2, её замер: медианный поток 24.6 кластера теста в сутки, σ_d = 0.93R, эффект −0.3R, `.opencode/specs/R2-closed-learning-loop/design.md`, строки 250–259). K в таблице: «13» — фактическое число условий меню с n ≥ 88 по замеру R2 (из 17 возможных); «29» — верхняя граница после волны (17 + 6 × 2), взята для консервативной оценки; «25» — ожидаемое K первого меню. Поток условий `cm_` — 0.9 × 24.6 = 22.1 (доля прогонов монет в фокусе). Односторонний порог alpha / K, alpha = 0.05.

| Ступень | Без волны (K = 13) | С волной (K = 29) |
|---|---|---|
| Скрининг, 14 суток | 1.00 (z 2.67, δ√n/σ 5.98) | 0.997 (n 309, z 2.92, δ√n/σ 5.67) |
| Скрининг первого меню, 7 суток | 0.95 | 0.87 (n 155, K 25, z 2.88, δ√n/σ 4.02) |
| Holdout 7 суток | 0.98 | 0.97 |
| Испытание `n_target` 150 | 0.95 | 0.95 |
| Конвейер | 0.93 (первое меню 0.88) | 0.92 (первое меню 0.80) |
| Конвейер при эффекте −0.2R | 0.43 | 0.36 |

Вывод: первая волна снижает мощность медианного условия на 0.01 при −0.3R на установившемся окне 14 суток — критерий Requirement 15.7 (г) выполняется (первое меню с волной идёт на неполном окне и по (г) не проверяется, это исключение записано заранее); цена — эффекты около −0.2R ловятся реже (0.36 против 0.43). Поправка Бонферрони держит долю ложных находок меню на 0.05 при любом K.

## Нагрузка

Подписки при пределах (20 `deep`, 120 `light`; листинг монет `light`: perp 80 %, spot 60 %, Coinbase 30 % — допущение): `deep` 20 × (5 perp × 4 + 5 spot × 2 + Coinbase 2) = 640; `light` 120 × (5 × 0.8 × 4 + 5 × 0.6 × 2 + 0.3 × 2) ≈ 2 710; итого около 3 350 тем, около 35 соединений (Bitget 50 тем на соединение даёт около 12 из них).

| Поток | Сообщ./с в среднем | Стоимость | Ядра в среднем |
|---|---|---|---|
| Дельты стаканов `deep` (10 рынков × 20 монет × 10/с) | 2 000 | 200–500 мкс | 0.4–1.0 |
| Частичные стаканы `light` (OKX `books` дельтами, остальные снимками) | 1 500 | 150–300 мкс | 0.2–0.45 |
| Сделки | 800 | 40 мкс | 0.03 |
| Тикеры, OI, ликвидации | 600 | 40 мкс | 0.02 |
| Сводка (такт 50–100 мс) | — | — | 0.1 |
| Итого | ≈ 4 900 | | 0.75–1.6 |

Пики × 10 (новости, каскады) длятся секунды; самый нагруженный процесс (Binance `perp`) — около 0.08 ядра в среднем и до 0.8 в пике, очередь рассасывается за ≤ 1 с; p99 минутного CPU всех процессов — около 3–4 ядер. Бюджет Requirement 12.3: 1.5 ядра в среднем и 4 ядра p99 — на границе верхней оценки; задача 23 замеряет на записанном трафике, при превышении уменьшаются пределы фокуса. Память: полные стаканы 200 рынков-монет × 2 000 уровней × 100 Б ≈ 40 МБ, ленты (`array`) 140 × 11 × 900 × 24 Б ≈ 33 МБ, процессы Python 12 × 60 МБ — около 800 МБ. Сеть: 4 900 сообщ./с × около 1.2 КБ ≈ 6 МБ/с ≈ 50 Мбит/с в среднем, в пике до 500 Мбит/с на секунды.

Запись на диск (компактные форматы): файлы рынков — на рынок в секунду 20 монет `deep` × (300 + 200) Б + 120 монет `light` / 5 × 300 Б ≈ 17 КБ, на 11 рынков около 190 КБ/с; `snap.json` — 170 КБ раз в 2 с = 85 КБ/с; ряды — 820 МБ в сутки ≈ 10 КБ/с; итого около 285 КБ/с ≈ 25 ГБ в сутки при пределе Requirement 12.3 в 30 ГБ. Для сравнения: прежний вариант с корзинами 5 б.п. у всех монет и снимком 450 КБ каждую секунду дал бы около 180 ГБ в сутки. Задача 23 замеряет запись счётчиками процесса.

## Хранение

Строк в сутки: `deep` 20 × 86 400 = 1.73 млн, `light` 120 × 17 280 = 2.07 млн, итого 3.8 млн. Строка: ключ и служебные поля около 16 Б, `f` 38 значений по 1–3 Б (около 30 % `None` = 1 Б) ≈ 70 Б, `by_ex` — до 10 рынков × (1 + 5 значений по 1–2 Б) ≈ 95 Б, заголовок записи и страницы около 35 Б — около 215 Б. Итого около 820 МБ в сутки, около 25 ГБ за 30 суток (свободно 893 ГБ); порог `disk_min_gb` 40 ГБ — больше 30 суток рядов с запасом. Вставка: 40–160 строк на такт одной транзакцией в конец дерева (ключ `(ts, sym_id)`).

## Миграция

| Модуль | Первая итерация | Вторая итерация |
|---|---|---|
| `marketdata/flowwatch.py` | без изменений: события `book_wall`, `cvd_divergence`, `xex_gap`, `flow.json`, стены Bybit для сторожа | решается спекой второй итерации |
| `xdata/xstream.py` | работает до `compat_xflow=true`, затем не запускается | удаление пакета (Requirement 19.4) |
| `intel/xflow.py` | удаляется класс `XFlow`; `summary`, `book_stats`, `walls`, `oi_changes`, `read` остаются | — |
| `intel/xex.py` | без изменений (`deriv.x`, `xvol.json`) | пересмотр после эксперимента досье |
| `marketdata/feed.py`, `liqwatch.py`, `hotscan.py` | без изменений | — |

Совместимый документ `xflow.json` от `cmkt` в первой итерации повторяет охват `xstream`: монеты `logic.cmkt.compat_symbols` (копия `xstream.symbol_set`, `xstream.py:79–85`: позиции и планы, запросы структуры, BTC/ETH/SOL, затем топ по обороту из фокуса вместо горячих монет `intel`; до 8), рынки `okx.perp`, `bitget.perp`, `binance.perp` под именами `okx`, `bitget`, `binanceusdm`; стакан обрезан до глубины `xstream` (Binance 20 уровней, Bitget 15, OKX без обрезки); полосы и корзины — `logic.cmkt.compat_book_stats` (копия `xflow.book_stats`, `intel/xflow.py:125–157`), стены — `logic.cmkt.compat_walls` (копия `xflow.walls`, `intel/xflow.py:160–172`, корзины 0.1 %, × 5 медианы, ≥ 200 000 USD); ликвидации только `binanceusdm`; ключи `ts_epoch`, `src = "cmkt"`, `ex` (`ok`, `fails`, `err`, `msgs`, `nosup`, `down`), `syms[SYM]`: `cvd` (`w5`, `w15`: `net`, `buy`, `sell`, `cov`), `cvd_anchor`, `ob` (`mid`, `depth_pct`, `bands`), `walls` (`side`, `px`, `usd`, `x`, `dist_pct`, `n_ex`), `live` (`px`, `fund_pct`, `oi_usd`, `oi5_pct`, `liq5_long_usd`, `liq5_short_usd`), `slow` (`liq_long_usd`, `liq_short_usd`, `fundh_pct`, `oi_usd`) — как `XStream.document` (`xstream.py:320–373`). Стены Binance остаются как сейчас (20 уровней). Отличие — работающие OKX и Bitget (сейчас `down`): их стены и полосы появятся, как и было задумано у `xstream`; следствие — больше пробуждений `BOOK_WALL` у сторожа (риск — расход токенов, не торговля; не больше 12 пробуждений в час на позицию). Тест Requirement 17.3 на одной фикстуре проверяет, что коды сторожа и `xflow.summary` у двух производителей совпадают. Поля `ls_x`, `oi1h_x_pct`, `oi24h_x_pct` `xstream` не даёт и сейчас; они остаются отсутствующими.

### Платформа: сервисы сборщиков

По образцу исполнителей бирж (`platform/services.py:237–289`):

```python
RE_CMKT = r"^cmkt@[a-z][a-z0-9]{1,11}$"
def cmkt_market(name) -> tuple | None      # "cmkt@binancep" -> ("binance", "perp"); "cmkt@coinbases" -> ("coinbase", "spot"); иначе None
def cmkt_name(venue, kind) -> str          # ("binance", "perp") -> "cmkt@binancep"
def cmkt_services(root) -> list            # ["cmkt", "cmkt@binancep", "cmkt@binances", ...] по включённым рынкам config/cmkt.json
default_services:  "cmkt" профиля раскрывается в cmkt_services(root)
parse_services:    допускает имена cmkt_market(n) is not None (идут сразу за "cmkt")
stop_order:        сначала "cmkt" (сводка), затем сборщики, затем прочие по прежнему порядку; исполнители последними
service_cmd:       "cmkt" -> [py, -m, tb.cmkt.service, run]; cmkt@* -> [py, -m, tb.cmkt.collector, run, --market <venue>_<kind>]
child_env:         очистка labels_secret для name == "cmkt" или cmkt_market(name) (сейчас точное `name in SCRUBBED_SERVICES`, services.py:358)
supervisor._check_hb: sc = svc.get(name) or (svc.get("cmkt@") if cmkt_market(name) else None) or {}   (сейчас supervisor.py:213)
config/platform.json: "cmkt" в profiles paper/testnet/trade (строки 6–46); svc.cmkt = {hb: file, hb_stale_s: 30, grace_s: 60}; svc["cmkt@"] = {hb: file, hb_stale_s: 30, grace_s: 60}
```

## Correctness Properties

### Property 1: Нет заглядывания вперёд
Для снимка `ts = T` все использованные события имеют `t_rx ≤ T`; строка, выбранная `features_at(sym, t0)`, имеет `ts = floor(t0 / step) × step − step < t0`.
**Validates: Requirements 4.7, 14.1, 15.4**

### Property 2: Исключённый рынок не влияет на агрегаты
Изменение любых данных рынка с кодом исключения не меняет ни одного признака снимка, кроме списка `ex`.
**Validates: Requirements 4.4, 11.2**

### Property 3: Монотонность меток времени
Последовательность `ts` снимков и строк рядов строго возрастает при любом шаге часов назад.
**Validates: Requirements 4.6**

### Property 4: Целостность стакана
Применение снимка и дельт с разрывом номеров (по правилам Binance `perp`, Binance `spot`, Gate) или с неверной контрольной суммой (OKX, Bitget, по исходным строкам) всегда переводит рынок в `RESYNC`; без разрыва состояние совпадает с эталонным стаканом фикстуры; уровни дальше границы REST-снимка отсутствуют.
**Validates: Requirements 5.1, 5.2, 5.3, 5.4**

### Property 5: Инвариантность единиц
Один и тот же стакан, выраженный в контрактах с `contract_size` и в монетах, даёт одинаковые USD корзин, полос и OI (точность 1e-9 относительная); рынок `1000PEPE` (цена за 1000 монет) и рынок `PEPE` с одинаковой ценой за монету дают одинаковые цену за монету, USD и не получают `MISMATCH`.
**Validates: Requirements 5.7, 5.8, 7.1**

### Property 6: Атрибуция и границы
Сумма атрибуции корзины равна её USD; дисбаланс в [−100, 100]; добавление рынка с пустым стаканом не меняет корзин.
**Validates: Requirements 6.2, 6.4**

### Property 7: Изменение OI без влияния цены
Умножение всех цен на константу при неизменных монетах OI не меняет `cm_oi_chg_*` и `cm_oi_liq_5m_pct`; рынок без потока ликвидаций не входит в `cm_oi_liq_5m_pct`.
**Validates: Requirements 7.3, 7.4**

### Property 8: Разложение CVD
`net_all = net_perp + net_spot` в USD для каждого окна; `cm_cvd_ps_5m_pct = perp − spot`.
**Validates: Requirements 8.1, 8.2**

### Property 9: Пороги покрытия
При `n_ex < min_n_ex` или `cov < min_cov` признак — `None`, а не число; при `liq_cov < liq_min_cov` признаки ликвидаций — `None`, а не 0.
**Validates: Requirements 7.3, 8.3, 9.2, 11.2**

### Property 10: Funding и премии
Веса funding неотрицательны и в сумме 1; ставка f за 4 ч даёт 2f в пересчёте на 8 ч; перестановка рынков не меняет `cm_prem_ps_bps`; сводная середина двух рынков — их среднее; при курсе USDT/USD = 1 и равных серединах `cm_prem_cb_bps = 0`.
**Validates: Requirements 10.1, 10.2, 10.3**

### Property 11: Снятие стены классифицируется однозначно
Каждый конец устойчивой стены получает ровно один код из `touched`, `pulled`, `faded`; стена, к которой цена сделок её рынка подошла ближе `touch_bps`, никогда не `pulled`.
**Validates: Requirements 6.5, 6.6**

### Property 12: Паритет признаков мозга и меток
Для одного сигнала `rule_features` по пути меток и по пути мозга дают равные значения всех `RULE_FEATURES` при любом порядке и времени: событие шины позже сигнала на 0–60 с, решение мозга до записи строки, захват меток раньше срока ожидания строки (вставка отложена до следующего прохода, курсор не сдвинут), чтение меток через 15–120 с, строка записана позже `accept_s` (обе стороны — `late`), монета вошла в фокус после `t0` (обе стороны — без признаков).
**Validates: Requirements 14.6, 15.2, 15.3, 15.4, 15.5, 23.4**

### Property 13: Воспроизводимость
Повтор записанного потока сообщений (фикстуры) через сборщики и сводку с подменёнными часами даёт те же значения `f` и `by_ex` строк рядов.
**Validates: Requirements 4.4, 13.2**

### Property 14: Ротация
После прохода ротации нет файлов старше `keep_days`, и всегда есть файлы текущих и предыдущих суток.
**Validates: Requirements 13.3**

### Property 15: Границы ответа
Блок досье не длиннее `brief.max_tokens`, ответ инструмента не длиннее `tool.max_bytes`; в обоих только числа, перечисления и идентификаторы; `ev.cm` не длиннее 1 000 байт и без ключей со значением `None`.
**Validates: Requirements 14.5, 16.1, 16.4, 21.3**

### Property 16: Граница меню
`K_MAX = 29`; условие по признаку с заполнением < 0.8 не попадает в K.
**Validates: Requirements 15.1, 15.6**

### Property 17: Слои
`tb.cmkt` и `tb.core.cmktdoc` не тянут запрещённых модулей прямо и транзитивно; в `tb.cmkt` нет ключей и подписи; в `tb.gateway` нет названий бирж.
**Validates: Requirements 3.1, 3.2, 3.3**

### Property 18: Время сигнала в шине
Для каждой строки `hot_events.jsonl` поле `d.t0` события обоих публикаторов равно `signals.t0` метки; усечение по 400 байтам не удаляет `t0`.
**Validates: Requirements 23.1, 23.2, 23.4**

### Property 19: Допуск момента и лидерство
`skew_ms` снимка ≤ 1 000 + `lag_max_ms`; если RTT оценки `offset` хотя бы одного сравниваемого рынка > `lead_rtt_max_ms` или оценка старше 900 с, признак `cm_lead_ex` — `None`.
**Validates: Requirements 4.5, 10.4**

### Property 20: Совместимость `xflow.json`
Для одной фикстуры рынка документы `XStream.document` и писателя `cmkt` дают одинаковые коды сторожа позиции, одинаковые `xlive_for` и `xflow.summary`; `compat_symbols`, `compat_book_stats`, `compat_walls` равны `xstream.symbol_set`, `xflow.book_stats`, `xflow.walls` на тех же входах; все монеты `compat_symbols` есть в фокусе.
**Validates: Requirements 16.5, 17.2, 17.3, 17.6**

### Property 21: Сервисы сборщиков
`cmkt_market(cmkt_name(v, k)) == (v, k)` для всех 11 рынков; `parse_services` принимает имена сборщиков и отклоняет `cmkt@x_y`; `stop_order` ставит сводку раньше сборщиков; окружение каждого `cmkt@*` без переменных `labels_secret`; супервизор берёт `hb_stale_s` сборщика из `svc["cmkt@"]`.
**Validates: Requirements 18.2, 18.3**

### Property 22: Конфиг живости
Загрузчик отклоняет `silent_s < ping_s + 5` кодом `E_CONF:sync.silent_s`; умолчания (10 и 15 с) проходят.
**Validates: Requirements 4.4**

## Error Handling

| Ситуация | Реакция |
|---|---|
| Биржа недоступна, гео-блок, 403/429 | backoff соединения 1 → 60 с; рынки `DOWN`; REST 429 — пауза 60 с без повтора, вес не расходуется |
| Разрыв номеров или контрольная сумма | `RESYNC`, восстановление ≤ 1 раза в 10 с на рынок, REST-снимки в пределах `rest_weight_per_min` по приоритету монет |
| Формат сообщения изменился | сообщение отбрасывается, счётчик `parse_err`; > 5 % за минуту — рынок `DOWN` с `err=PARSE` |
| `MISMATCH` тикера | рынок исключён для монеты до следующей загрузки метаданных (6 ч) |
| Файл состояния рынка битый | рынок `STALE` на такте |
| Повреждён файл рядов | карантин `*.corrupt-<ts>.sqlite`, новый файл, жёлтый статус на сутки |
| Диск < 40 ГБ | ряды не пишутся, `DISK_LOW`; снимок и метки (`no_row`) продолжаются |
| Бюджет превышен 10 мин | ярус `light` −20 монет за шаг до 40, `BUDGET` |
| Сервис `cmkt` не запущен, файл рядов недоступен | `features_at` → `cm_missing=db`; правила с `cm_` не срабатывают (условие ложно); досье без блока; исключений нет |
| Событие шины без `d.t0` | `cm_missing=no_t0`, признаки `cm_` — `None` |

Асимметрия отказа: `skip_when` с признаком `cm_` при отсутствии данных не отсекает вход, то есть отказ сбора возвращает базовое поведение мозга, а не запрещает торговлю. Доля решений с `None` по признакам активных правил видна в R2 (`feat_missing`).

## Testing Strategy

- **Чистая логика** `tests/test_logic_cmkt.py`: свойства 5–11, 16, 19 (генерация `random.Random(seed)`), эталоны вручную.
- **Адаптеры** `tests/test_cmkt_ex_<venue>.py`: фикстуры живых сообщений задачи 2 (`tests/fixtures_cmkt/<venue>/*.jsonl`, только публичные данные), разбор, единицы, `1000PEPE`, сторона агрессора, правила непрерывности (свойство 4), контракт на `fakex`.
- **Сборщик и сводка** `tests/test_cmkt_collect.py`, `tests/test_cmkt_aggregate.py`: подменённые часы, `WSClient` на `127.0.0.1` (`allow_plain`), свойства 1–3, 13; исключение по `LAG`, `SILENT`, `STALE`; живость по pong; `CLOCK_SKEW`; переподключение; REST-бюджет и порядок восстановления.
- **Хранение** `tests/test_cmkt_store.py`: кодирование `f` и `by_ex`, ротация (свойство 14), карантин, `DISK_LOW`, Windows `PermissionError`, оценка размера строки на синтетических сутках (± 30 % от 215 Б).
- **Потребители**: `tests/test_bus_t0.py` (свойство 18), `tests/test_labels_cm.py` (захват, backfill, покрытие, размер `ev.cm`), `tests/test_rule_features_cm.py` (свойство 12, старые `cluster_runs`), `tests/test_brief_cmkt.py` и `tests/test_gateway_market_detail.py` (свойство 15), `tests/test_xflow_compat.py` (свойство 20).
- **Слои** `tests/test_layering.py`: свойство 17.
- **Нагрузка** (`--slow`): повтор записанного трафика × 4 на 600 с и всплеск × 10 на 10 с; CPU, RSS, задержка обработки, трафик против Requirement 12.3.
- **Живая приёмка** (задача 26): 24 ч на mainnet, гейт этапа 1b, учения выпадения биржи (хост вне белого списка на 10 мин), замер размера суток и заполнения страниц рядов.
