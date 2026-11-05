"""Схема базы знаний (метки), версия 2; статусы и коды. Только данные, без ввода-вывода.

Версия 2 (R4 «Выходы», задача 7; design.md «Data Models»): минутные пути заполненных прогонов `run_paths` (состояние partial -> complete,
`complete_ts`) и сделок мозга `trade_paths` (с `qty`, `qty_step`, `min_qty` инструмента на входе); курсор шины `position_closed` — строка
таблицы `cursor` с `src = CURSOR_BUS_CLOSED` (пишет шаг путей сделок). Миграция только добавляет таблицы: строки v1 не меняются.
"""

SCHEMA_VER = 2
META_VER_KEY = "labels_schema_ver"
DB_NAME = "knowledge.sqlite"

# статусы signals.status
PENDING, DONE, NO_DATA, FAILED, SKIPPED = "pending", "done", "no_data", "failed", "skipped"
STATUSES = (PENDING, DONE, NO_DATA, FAILED, SKIPPED)

# коды (signals.code, policy_runs.code, heartbeat, выход сервиса)
SCHEMA_MIGRATION = "SCHEMA_MIGRATION"
DB_CORRUPT = "DB_CORRUPT"
W_LABELS_SYNC_DIR = "W_LABELS_SYNC_DIR"
FETCH = "FETCH"
NO_LEVEL = "NO_LEVEL"
NO_DIR = "NO_DIR"
NO_ATR_PLAN = "NO_ATR_PLAN"
NO_DATA_CODE = "NO_DATA"           # signals.code у status=no_data
CONFIG = "CONFIG"                   # signals.code: ошибка конфигурации политик/профиля при разметке (метка откладывается)
CODES = (SCHEMA_MIGRATION, DB_CORRUPT, W_LABELS_SYNC_DIR, FETCH, NO_LEVEL, NO_DIR, NO_ATR_PLAN, NO_DATA_CODE, CONFIG)

# версия 1: по одному оператору на элемент (DDL внутри явной транзакции, executescript не используется)
V1 = [
    "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT)",
    "CREATE TABLE cursor(src TEXT PRIMARY KEY, path TEXT, offset INTEGER NOT NULL, head_sha TEXT, ts INTEGER)",
    """CREATE TABLE signals(
  key TEXT PRIMARY KEY, t0 INTEGER NOT NULL, sym TEXT NOT NULL, type TEXT NOT NULL, side TEXT,
  score REAL, price REAL, wake INTEGER, tradable INTEGER, corr_group TEXT, env TEXT, ev_id TEXT,
  ev TEXT NOT NULL, feat TEXT, missing TEXT, levels_src TEXT,
  status TEXT NOT NULL, code TEXT, attempts INTEGER NOT NULL DEFAULT 0,
  mature_ts INTEGER NOT NULL, next_try_ts INTEGER NOT NULL,
  captured_ts INTEGER NOT NULL, labeled_ts INTEGER, gap_min REAL, schema_ver INTEGER NOT NULL)""",
    "CREATE INDEX ix_sig_t0 ON signals(t0)",
    "CREATE INDEX ix_sig_sym ON signals(sym, t0)",
    "CREATE INDEX ix_sig_type ON signals(type, t0)",
    "CREATE INDEX ix_sig_due ON signals(status, next_try_ts)",
    """CREATE TABLE outcomes(
  key TEXT NOT NULL, h INTEGER NOT NULL, side TEXT NOT NULL,
  ret_pct REAL, mfe_pct REAL, mae_pct REAL, mfe_atr REAL, mae_atr REAL, t_mfe_min REAL, t_mae_min REAL,
  cost_rt_pct REAL, net_dir_pct REAL, flags TEXT,
  PRIMARY KEY(key, h, side))""",
    """CREATE TABLE policy_runs(
  key TEXT NOT NULL, policy_id TEXT NOT NULL, policy_ver INTEGER NOT NULL,
  geometry_ver TEXT NOT NULL, ctx_ver TEXT NOT NULL,
  t0 INTEGER NOT NULL, sym TEXT NOT NULL, type TEXT NOT NULL, side TEXT,
  plan TEXT, code TEXT, filled INTEGER NOT NULL, exit_reason TEXT,
  r_gross REAL, r_net REAL, fees_r REAL, mfe_r REAL, mae_r REAL, hold_min REAL, flags TEXT,
  PRIMARY KEY(key, policy_id, policy_ver))""",
    "CREATE INDEX ix_pr_pol ON policy_runs(policy_id, policy_ver, t0)",
    "CREATE INDEX ix_pr_type ON policy_runs(type, t0)",
]

# версия 2 (R4): пути прогонов и сделок мозга для контрфактической оценки выходов
PATH_PARTIAL, PATH_COMPLETE = "partial", "complete"          # run_paths.state
PATH_STATES = (PATH_PARTIAL, PATH_COMPLETE)
FLAG_GAP = "gap"                 # пропуск свечей длиннее bars.GAP_MAX_MIN: путь вне оценок (Requirement 1.5)
FLAG_ADOPTED = "adopted"         # сделка принята командой plan adopt: в оценки мозга не входит (Requirement 1, Edge Cases)
CURSOR_BUS_CLOSED = "bus_closed"  # cursor.src: позиция чтения событий position_closed шины (шаг trade_paths_step)
RUN_PATHS = "run_paths"
TRADE_PATHS = "trade_paths"

V2 = [
    """CREATE TABLE run_paths(
  key TEXT NOT NULL, policy_id TEXT NOT NULL, policy_ver INTEGER NOT NULL, t0 INTEGER NOT NULL, sym TEXT NOT NULL,
  type TEXT NOT NULL, side TEXT NOT NULL, horizon TEXT NOT NULL, entry_ts INTEGER NOT NULL, entry_px REAL NOT NULL, stop REAL NOT NULL,
  tick REAL NOT NULL, max_hold_min REAL NOT NULL, atr5_pct REAL, plan TEXT NOT NULL, rows BLOB NOT NULL, n_rows INTEGER NOT NULL,
  end_ts INTEGER NOT NULL, state TEXT NOT NULL, flags TEXT, aux BLOB, aux_ver TEXT, labeled_ts INTEGER NOT NULL, complete_ts INTEGER,
  PRIMARY KEY(key, policy_id, policy_ver))""",
    "CREATE INDEX ix_rp_t0 ON run_paths(t0)",
    "CREATE INDEX ix_rp_state ON run_paths(state, end_ts)",
    """CREATE TABLE trade_paths(
  journal_id TEXT PRIMARY KEY, plan_id TEXT, sym TEXT NOT NULL, side TEXT NOT NULL, mode TEXT NOT NULL,
  entry_ts INTEGER NOT NULL, entry_px REAL NOT NULL, stop0 REAL NOT NULL, tick REAL NOT NULL, qty REAL NOT NULL, qty_step REAL NOT NULL,
  min_qty REAL NOT NULL, horizon TEXT, max_hold_min REAL, atr5_pct REAL, sig_type TEXT, exit_ver TEXT, policy_h TEXT,
  close_ts INTEGER NOT NULL, r REAL, parts TEXT NOT NULL, timeline TEXT NOT NULL, rows BLOB NOT NULL, end_ts INTEGER NOT NULL,
  flags TEXT, fetched_ts INTEGER NOT NULL)""",
    "CREATE INDEX ix_tp_entry ON trade_paths(mode, entry_ts)",
]
V2_TABLES = (RUN_PATHS, TRADE_PATHS)
V2_INDEXES = ("ix_rp_t0", "ix_rp_state", "ix_tp_entry")

# (версия, [операторы]) по возрастанию
MIGRATIONS = [(1, V1), (2, V2)]
