-- 021 — индекс по created_at (первое появление лота у нас).
--
-- Зачем. `collected_at` перезаписывается КАЖДЫМ upsert'ом, то есть отвечает на
-- вопрос «когда видели в последний раз». На вопрос «когда увидели ВПЕРВЫЕ»
-- отвечает только `created_at` (вставка, дальше не трогается) — и именно от него
-- считается запас до дедлайна и задержка обнаружения (crawler/scripts/first_seen_report.py).
--
-- Почему это не было сделано раньше. Индекса на created_at нет с рождения таблицы:
-- есть idx_tenders_collected_at (btree DESC) и составной (source, collected_at),
-- а по created_at — ничего. Любая выборка «за окно по первому появлению» поверх
-- 740 тыс. строк упирается в statement timeout (57014). Отчёт по алертам
-- проскакивает через tenders_alert_seq_key, а полная выборка — нет.
--
-- CONCURRENTLY: краулер пишет непрерывно, обычный CREATE INDEX подержал бы запись
-- на время построения. Команду нельзя выполнять внутри транзакции — применять
-- отдельным запросом, не в общем блоке миграций.
--
-- Применение (PAT в ~/.zshrc):
--   curl -X POST "https://api.supabase.com/v1/projects/oaoehczbycrabkprazts/database/query" \
--     -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" \
--     -d '{"query":"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tenders_created_at ON tenders (created_at DESC)"}'
--
-- Проверка:
--   select indexname from pg_indexes where tablename='tenders' and indexname='idx_tenders_created_at';
--
-- Откат (не требуется, индекс аддитивный):
--   DROP INDEX CONCURRENTLY IF EXISTS idx_tenders_created_at;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tenders_created_at
    ON tenders (created_at DESC);
