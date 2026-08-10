-- 022 — недельный счётчик в source_freshness().
--
-- Зачем. Две проверки healthcheck считали строки за неделю, вытаскивая их в питон
-- постранично: `select source where collected_at >= week_ago` с растущим offset и
-- капом 200 тыс. Под это условие подходит почти вся активная таблица — строки
-- перечитываются каждым проходом краулера, — то есть сотни страниц с глубокими
-- offset'ами. 10.08 `sources.dead_7d` упёрлась в 57014 три ретрая подряд и
-- отчиталась «Dead-source check failed»; `sources` в тот же прогон дотянула только
-- с ретраями на каждой странице.
--
-- Опасность не в самой поломке, а в её виде: отказ проверки и находка приходят
-- одинаково (оба WARN), то есть слепота неотличима от работы. Кап давал второй
-- способ соврать — молча обрезать выборку и объявить живым источник, чьи строки
-- не влезли в окно.
--
-- dead_7d уже переведена на last_collected из этой же RPC. Здесь добавляется cnt_7d,
-- чтобы и `check_sources` («N активных источников, M строк за неделю», «источники с
-- <5 строк») считалась одной серверной агрегацией.
--
-- Совместимость: существующие потребители (freshness_watchdog, dead_7d) читают поля
-- по имени, лишняя колонка их не трогает. Функция пересоздаётся целиком, потому что
-- менять тип возврата иначе нельзя.
--
-- Применение (PAT в ~/.zshrc):
--   curl -X POST "https://api.supabase.com/v1/projects/oaoehczbycrabkprazts/database/query" \
--     -H "Authorization: Bearer $SUPABASE_ACCESS_TOKEN" -H "Content-Type: application/json" \
--     -d @- <<< '{"query": "<текст ниже одной строкой>"}'
--
-- Проверка:
--   select source, cnt, cnt_7d, last_collected, last_created from source_freshness() limit 5;

DROP FUNCTION IF EXISTS source_freshness();

CREATE FUNCTION source_freshness()
RETURNS TABLE(source text, cnt bigint, cnt_7d bigint,
              last_collected timestamptz, last_created timestamptz)
LANGUAGE sql STABLE
SET statement_timeout = '60s'
AS $$
  SELECT source,
         count(*),
         count(*) FILTER (WHERE collected_at > now() - interval '7 days'),
         max(collected_at),
         max(created_at)
  FROM tenders
  GROUP BY source
$$;
