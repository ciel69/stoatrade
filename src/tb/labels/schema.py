"""Схема базы знаний (метки), версия 1; статусы и коды. Только данные, без ввода-вывода."""

SCHEMA_VER = 1
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

# (версия, [операторы]) по возрастанию
MIGRATIONS = [(1, V1)]
