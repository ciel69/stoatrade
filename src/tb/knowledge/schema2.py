"""Схема цикла обучения v2a (и v2b второй итерации: case_rows, lesson_rows) в state/knowledge.sqlite (design.md, «SQLite state/knowledge.sqlite, схема v2a»): DDL, триггеры, коды, лимиты. Только данные.

Версия хранится в meta.knowledge_v2_ver отдельно от labels_schema_ver; миграция - knowledge.store2.migrate_v2a, а не labels.schema.MIGRATIONS.
"""

V2A_VER = 1
META_VER_KEY = "knowledge_v2_ver"
META_COPY_TS = "copy_ts"                 # время копии, из которой база восстановлена (epoch, строка)
META_RESTORE_TS = "restore_ts"           # время подстановки копии (epoch, строка)
META_RESTORE_MODE = "restore_mode"       # auto | manual
META_RESTORE_SRC = "restore_src"         # имя файла копии
META_EVENTS_DAY = "learn_events_day"     # {"day", "n", "skipped"}: счётчик строк learn_events без ошибок за сутки UTC

# ---- коды (design.md, «Коды причин»)
DB_CORRUPT = "DB_CORRUPT"
CHAIN_BROKEN = "CHAIN_BROKEN"
SRC_CORRUPT = "SRC_CORRUPT"
NOT_READY = "NOT_READY"
AUTO_RESTORED = "AUTO_RESTORED"
EVENTS_THROTTLED = "EVENTS_THROTTLED"
STEP_ERROR = "STEP_ERROR"
OWNER = "OWNER"
RESTORING = "RESTORING"                  # статус: идёт протокол восстановления (runtime/knowledge.restoring)
RESTORE_FAILED = "RESTORE_FAILED"        # статус: протокол прерван (таймаут подтверждений, повторы, -wal/-shm), нужен ручной restore
SCHEMA_MISMATCH = "SCHEMA_MISMATCH"      # статус: нет индекса или триггера схемы v2a
KNOWLEDGE_MIGRATION = "KNOWLEDGE_MIGRATION"

# строки этих кодов (и любые действия владельца) не ограничиваются events_per_day
ERROR_CODES = frozenset((STEP_ERROR, CHAIN_BROKEN, DB_CORRUPT, SRC_CORRUPT, "SAMPLE_CHANGED", "RECONCILE_MISMATCH", AUTO_RESTORED, EVENTS_THROTTLED,
                         KNOWLEDGE_MIGRATION, RESTORE_FAILED))

# коды записей чёрного списка (колонка «Чёрный список» таблицы кодов) и снятие владельцем
BLOCK_CODES = frozenset(("NO_OOS_SIGN", "NOT_SIGNIFICANT", "N_TOO_SMALL", "SAMPLE_CHANGED", "TRIAL_REJECT", "NO_VERDICT", "UNREACHABLE_N", "ROLLBACK",
                         "TRADE_STARVATION", "BOUNDS_BLOCKED", "OWNER_REJECT", "OWNER_DISABLE"))
UNBLOCK = "UNBLOCK"
ACTORS = ("code", OWNER)

# ---- резервные копии
BACKUP_REASONS = ("daily", "premigrate", "promote", "rollback", "owner")
BACKUP_KEEP = {"daily": 7, "premigrate": 3, "promote": 3, "rollback": 3, "owner": 3}

# ---- значения по умолчанию (рабочие берёт config/learnloop.json, задача 5)
RESTORE_ACK_TIMEOUT_S = 60.0
EVENTS_PER_DAY = 500
AUTO_RESTORED_YELLOW_S = 7 * 86400

# ---- лимиты размера строки (канонический JSON строки, байты UTF-8): правило <= 2 КБ, версия <= 4 КБ
ROW_MAX = {"rule_rows": 2048, "rulebook_versions": 4096, "learn_events": 4096, "blocklist_rows": 1024, "trials": 16384,
           "lesson_candidates": 16384, "cluster_runs": 4096, "rule_evals": 4096}
ROW_MAX_DEFAULT = 4096

# поля-хеши и ключи меток: privacy_scan их не проверяет (длинная hex-строка - не утечка)
HASH_FIELDS = frozenset(("h", "prev", "cond_h", "criterion_h", "key", "sample_h"))

APPEND_ONLY = ("rule_rows", "rulebook_versions", "blocklist_rows", "learn_events")
INSERT_ONLY = ("cluster_runs", "rule_evals")
CHAIN_TABLES = ("rulebook_versions",)
TABLES = ("cluster_runs", "rule_rows", "rulebook_versions", "lesson_candidates", "trials", "blocklist_rows", "rule_evals", "learn_events")
TRIALS_IMMUTABLE = ("trial_id", "rule_id", "lesson_id", "criterion", "criterion_h", "start_ts", "deadline_ts", "env_norm", "policy_id", "policy_ver", "ctx_ver")
# изменяемые таблицы: (ключ, изменяемые колонки)
MUTABLE = {"trials": ("trial_id", ("state", "verdict", "numbers", "cut_ts", "closed_ts")),
           "lesson_candidates": ("id", ("status", "code", "p_screen", "holdout_start", "holdout_end", "evidence", "lesson_id"))}
INDEXES = ("ix_cr_t0", "ix_re_ts_rule", "ix_re_ts")


def _guard(table, what, ev):
    return "CREATE TRIGGER tr_%s_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, '%s %s'); END" % (
        table, what, ev, table, "INSERT_ONLY" if table in INSERT_ONLY else "APPEND_ONLY", table)


V2A = [
    """CREATE TABLE cluster_runs(cluster TEXT NOT NULL, hour INTEGER NOT NULL, side TEXT NOT NULL, key TEXT NOT NULL, t0 INTEGER NOT NULL, type TEXT NOT NULL,
  policy_id TEXT NOT NULL, policy_ver INTEGER NOT NULL, ctx_ver TEXT NOT NULL, env_norm TEXT NOT NULL, filled INTEGER NOT NULL, r_net REAL,
  feats TEXT NOT NULL, flags TEXT, closed_ts INTEGER NOT NULL, PRIMARY KEY(policy_id, policy_ver, key))""",
    "CREATE INDEX ix_cr_t0 ON cluster_runs(policy_id, ctx_ver, env_norm, t0)",
    """CREATE TABLE rule_rows(seq INTEGER PRIMARY KEY, rule_id TEXT NOT NULL, rev INTEGER NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
  wait TEXT, wait_since_ts INTEGER, from_lesson TEXT NOT NULL, env_norm TEXT NOT NULL, rulebook_ver_in TEXT, rulebook_ver_out TEXT, reason_code TEXT,
  body TEXT NOT NULL, ts TEXT NOT NULL, UNIQUE(rule_id, rev))""",
    """CREATE TABLE rulebook_versions(seq INTEGER PRIMARY KEY, ver TEXT NOT NULL UNIQUE, parent TEXT, reason_code TEXT NOT NULL, trial_id TEXT, rule_ids TEXT NOT NULL,
  numbers TEXT, ts TEXT NOT NULL, prev TEXT, h TEXT NOT NULL)""",
    """CREATE TABLE lesson_candidates(id INTEGER PRIMARY KEY, menu_id TEXT NOT NULL, src TEXT NOT NULL, cond TEXT NOT NULL, claim TEXT NOT NULL, env_norm TEXT NOT NULL,
  ctx_ver TEXT NOT NULL, k_space INTEGER NOT NULL, p_screen REAL, status TEXT NOT NULL, code TEXT, holdout_start INTEGER, holdout_end INTEGER,
  evidence TEXT, lesson_id TEXT, ts INTEGER NOT NULL, UNIQUE(menu_id, src, cond, claim))""",
    """CREATE TABLE trials(trial_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL UNIQUE, lesson_id TEXT NOT NULL, env_norm TEXT NOT NULL, criterion TEXT NOT NULL, criterion_h TEXT NOT NULL,
  policy_id TEXT NOT NULL, policy_ver INTEGER NOT NULL, ctx_ver TEXT NOT NULL, start_ts INTEGER NOT NULL, deadline_ts INTEGER NOT NULL, state TEXT NOT NULL,
  verdict TEXT, numbers TEXT, cut_ts INTEGER, closed_ts INTEGER)""",
    "CREATE TABLE blocklist_rows(seq INTEGER PRIMARY KEY, cond_h TEXT NOT NULL, code TEXT NOT NULL, until_ts INTEGER, actor TEXT NOT NULL, ts TEXT NOT NULL)",
    """CREATE TABLE rule_evals(rec_seq INTEGER NOT NULL, rule_id TEXT NOT NULL, ts INTEGER NOT NULL, role TEXT, event_seq INTEGER, no_event INTEGER NOT NULL,
  hit INTEGER NOT NULL, evaluated INTEGER NOT NULL, missing INTEGER NOT NULL, rulebook_ver TEXT, plan TEXT, PRIMARY KEY(rec_seq, rule_id))""",
    "CREATE INDEX ix_re_ts_rule ON rule_evals(ts, rule_id)",
    "CREATE INDEX ix_re_ts ON rule_evals(ts)",
    "CREATE TABLE learn_events(seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, step TEXT NOT NULL, code TEXT, actor TEXT NOT NULL, ref TEXT, body TEXT)",
]
for _t in APPEND_ONLY + INSERT_ONLY:
    V2A.append(_guard(_t, "nu", "UPDATE"))
    V2A.append(_guard(_t, "nd", "DELETE"))
V2A.append("CREATE TRIGGER tr_trials_nu BEFORE UPDATE OF %s ON trials BEGIN SELECT RAISE(ABORT, 'IMMUTABLE trials'); END" % ", ".join(TRIALS_IMMUTABLE))
V2A.append("CREATE TRIGGER tr_trials_nd BEFORE DELETE ON trials BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY trials'); END")

TRIGGERS = tuple(["tr_%s_%s" % (t, w) for t in APPEND_ONLY + INSERT_ONLY for w in ("nu", "nd")] + ["tr_trials_nu", "tr_trials_nd"])

# ---- v2b (вторая итерация, R2.9b, Requirement 3.1): случаи и уроки в базе. Отдельный ключ версии meta.knowledge_v2b_ver: v2a-база без таблиц
# v2b остаётся зелёной (ready/TABLES/TRIGGERS v2a их не требуют). Строка таблицы = строка прежнего JSONL: body - канонический JSON строки без
# seq/prev/h, а seq/prev/h - колонки; h = sha256(prev + канон. JSON строки с seq и prev)[:16] (как memory.chain), поэтому цепочка базы совпадает
# с цепочкой целого файла, а export-jsonl воспроизводит прежний формат.
V2B_VER = 1
META_V2B_KEY = "knowledge_v2b_ver"
MEM_TABLES = ("case_rows", "lesson_rows")
MEM_KEY = {"case_rows": "case_id", "lesson_rows": "lesson_id"}
MEM_LOCK = {"case_rows": "cases.lock", "lesson_rows": "lessons.lock"}          # те же блокировки, что у memory.cases / memory.lessons
MEM_FILE = {"case_rows": "cases.jsonl", "lesson_rows": "lessons.jsonl"}
SPOOL_NAME = "knowledge.spool.jsonl"     # очередь записей при недоступной базе (Requirement 3.7)
SPOOL_LOCK = "knowledge.spool.lock"
PRE_SUFFIX = ".pre-r29"                  # прежние JSONL после переноса
STORES = ("jsonl", "sqlite")
STORE_MARKER = "memory.store"            # state/memory.store: источник правды о store корня ({"store", "ts", "by"}); нет файла -> "jsonl"
STORE_SRC = ("marker", "config-compat", "default")   # откуда взят store (config-compat - переходное: store из config/memory.json корня проекта)

# коды второй итерации
STORE_IN_CONFIG = "STORE_IN_CONFIG"      # store корня проекта взят из config/memory.json без маркера (жёлтый в verify): ./tb knowledge store-marker
PLATFORM_RUNNING = "PLATFORM_RUNNING"
SPOOL_PENDING = "SPOOL_PENDING"
JSONL_APPEARED = "JSONL_APPEARED"        # store=sqlite, а в state/ появился cases.jsonl / lessons.jsonl (жёлтый в verify, отчёт migrate)
FOLD_MISMATCH = "FOLD_MISMATCH"

V2B = [
    """CREATE TABLE case_rows(seq INTEGER PRIMARY KEY, case_id TEXT NOT NULL, rev INTEGER NOT NULL, kind TEXT NOT NULL, ts TEXT, t_ts INTEGER, type TEXT,
  mode TEXT, body TEXT NOT NULL, prev TEXT NOT NULL, h TEXT NOT NULL, UNIQUE(case_id, rev))""",
    "CREATE INDEX ix_case_rows_mt ON case_rows(mode, type, t_ts)",
    """CREATE TABLE lesson_rows(seq INTEGER PRIMARY KEY, lesson_id TEXT NOT NULL, rev INTEGER NOT NULL, kind TEXT NOT NULL, ts TEXT, status TEXT, src TEXT,
  body TEXT NOT NULL, prev TEXT NOT NULL, h TEXT NOT NULL, UNIQUE(lesson_id, rev))""",
]
for _t in MEM_TABLES:
    V2B.append("CREATE TRIGGER tr_%s_nu BEFORE UPDATE ON %s BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY %s'); END" % (_t, _t, _t))
    V2B.append("CREATE TRIGGER tr_%s_nd BEFORE DELETE ON %s BEGIN SELECT RAISE(ABORT, 'APPEND_ONLY %s'); END" % (_t, _t, _t))
V2B_INDEXES = ("ix_case_rows_mt",)

# ---- v2e (причина события записей rule_eval): колонки rule_evals.event_why (ok | no_event | out_of_scope | stale; NULL у строк до миграции —
# прежний no_event) и event_kind (вид события). Свой ключ версии meta.knowledge_re_why_ver; ALTER TABLE ADD COLUMN идемпотентно
# (knowledge.store2.migrate_re_why), зовёт шаг rule_eval_import. База без колонок читается (rulebook_view.rule_eval_why: все строки legacy).
RE_WHY_VER = 1
META_RE_WHY_KEY = "knowledge_re_why_ver"
RE_WHY_COLS = (("event_why", "TEXT"), ("event_kind", "TEXT"))
V2B_TRIGGERS = tuple("tr_%s_%s" % (t, w) for t in MEM_TABLES for w in ("nu", "nd"))
