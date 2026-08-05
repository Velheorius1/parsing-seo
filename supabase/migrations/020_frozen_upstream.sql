-- 020: обнаружение замороженного upstream.
--
-- Дефект, который это закрывает: источник собирается каждый день и по
-- collected_at выглядит образцово живым, но НОВЫХ строк не даёт месяцами —
-- upstream отдаёт один и тот же срез. Так полгода прожили «Cooperation.uz
-- Закупочные планы»: эндпоинт площадки заморожен с 03.02.2026, а мы три раза
-- в сутки перекладывали февральский срез. Проверка 019 смотрит collected_at,
-- который перезаписывается при каждом upsert (db.py:106), и молчала.
--
-- Различает их created_at: он ставится только при ВСТАВКЕ, поэтому
-- max(created_at) — дата последней действительно новой строки.
--
-- Почему агрегатом, а не запросом на источник: первая версия сторожа спрашивала
-- max(created_at) отдельным запросом по каждому из 110 источников. Шесть самых
-- крупных (Cooperation.uz Лоты и Оферты, UZEX Предквалификации, XT-Xarid
-- э-магазин, Ebirja Электронный магазин, Tender.mc.uz) стабильно падали в 57014:
-- ORDER BY created_at по сотням тысяч строк без индекса. То есть проверка молча
-- не покрывала ровно те источники, где цена дефекта выше всего. Здесь это один
-- проход, тем же сканом, что и max(collected_at).
--
-- Смена сигнатуры требует DROP: CREATE OR REPLACE не меняет тип возврата.
-- Единственный потребитель — crawler.scripts.freshness_watchdog.
--
-- NOTE: применяется через Management API (Supabase PAT), как и 019.

DROP FUNCTION IF EXISTS source_freshness();

CREATE FUNCTION source_freshness()
RETURNS TABLE(source text, cnt bigint, last_collected timestamptz,
              last_created timestamptz)
LANGUAGE sql STABLE
SET statement_timeout = '60s'
AS $$
  SELECT source, count(*), max(collected_at), max(created_at)
  FROM tenders GROUP BY source
$$;
