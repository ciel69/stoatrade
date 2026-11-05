"""Коды недоступной базы знаний и режимы торговли отчётов цикла выходов (R4, задача 22).

Отдельный лёгкий модуль: его читают CLI владельца (`tb.knowledge.exits_cli`) и кнопка «Вернуть границу» в дашборде
(`tb.dash2.memory_ops`), которым нельзя тянуть `tb.llm` через `exits_report` (слои дашборда запрещают tb.llm).
Тексты причин — те же, что показывает `./tb exits status`.
"""
MODES = ("paper", "testnet", "live")
NO_DB, RESTORING, DB_ERROR = "NO_DB", "RESTORING", "DB_ERROR"
REASONS_RU = {NO_DB: "базы знаний нет (state/knowledge.sqlite не создана)",
              RESTORING: "база знаний восстанавливается (runtime/knowledge.restoring): повторите после восстановления",
              DB_ERROR: "база знаний не читается"}
