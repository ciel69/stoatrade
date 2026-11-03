"""База знаний v2 (R2.9a, спека .opencode/specs/R2-closed-learning-loop): таблицы цикла обучения в state/knowledge.sqlite и защита базы.

schema2  DDL v2a, триггеры append-only/insert-only, коды и лимиты (только данные);
markers  маркеры state/knowledge.v2, runtime/knowledge.restoring, регистрация долгоживущих писателей, подтверждения, сигнал повреждения, is_corruption;
store2   migrate_v2a, запись строк (privacy_scan, лимиты, цепочка rulebook_versions), learn_events, чёрный список, verify, make_backup, протокол восстановления;
cli      `./tb knowledge verify|backup|restore|stats|disable-v2|migrate-v2a`; вторая итерация: `migrate [--dry-run]|export-jsonl|replay-spool`;
memstore (R2.9b) бэкенд фасада memory.cases/lessons при store="sqlite": таблицы case_rows/lesson_rows, очередь state/knowledge.spool.jsonl;
migrate2 (R2.9b) перенос JSONL -> база (dry-run, проверка остановки платформы, сверка fold, откат, *.pre-r29) и export-jsonl.
Пакет не импортирует venues/executor/gateway/llm/brain/control.
"""
