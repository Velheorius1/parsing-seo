-- Migration 015: extra_info JSONB column on tenders
-- Persists enriched fields (Район, Адрес, Количество, Цена/ед., Срок, Период, ЕНКТ и т.д.)
-- that adapters collect via field_map.extra_info.
-- Previously these lived only in-memory in RawTender and were rendered into TG alerts
-- but lost on DB write. Now persisted for history / analytics / future re-sends.

ALTER TABLE tenders ADD COLUMN IF NOT EXISTS extra_info JSONB DEFAULT '{}'::jsonb;

-- Partial index for tenders that have enriched info (analytics queries)
CREATE INDEX IF NOT EXISTS idx_tenders_extra_info_not_empty
    ON tenders USING GIN (extra_info)
    WHERE extra_info <> '{}'::jsonb;

COMMENT ON COLUMN tenders.extra_info IS
    'Enriched display fields (label -> value) collected via adapters.field_map.extra_info. '
    'Examples: Район, Адрес, Количество, Цена/ед., Срок поставки, Период, ЕНКТ.';
