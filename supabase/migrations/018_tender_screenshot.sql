-- Migration 018: tender preview screenshot
-- Добавляет колонки для хранения URL скриншота лота из Supabase Storage.
-- Используется для broken SPA источников (UZEX prequalification/competitions/direct,
-- Cooperation, Hayotbirja, XT-Xarid) где deep-link не работает извне.

ALTER TABLE tenders ADD COLUMN IF NOT EXISTS preview_screenshot_url TEXT;
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS preview_screenshot_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_tenders_screenshot_at
    ON tenders(preview_screenshot_at)
    WHERE preview_screenshot_url IS NOT NULL;
