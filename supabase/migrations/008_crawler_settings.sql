-- Crawler settings: key-value store for admin panel
-- Manages: alert keywords, competitor keywords, min price, feature toggles

CREATE TABLE IF NOT EXISTS crawler_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- RLS: mandatory
ALTER TABLE crawler_settings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "settings_select" ON crawler_settings FOR SELECT USING (true);
CREATE POLICY "settings_insert" ON crawler_settings FOR INSERT WITH CHECK (true);
CREATE POLICY "settings_update" ON crawler_settings FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "settings_delete_service" ON crawler_settings FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);

-- Default settings
INSERT INTO crawler_settings (key, value) VALUES
  ('alert_keywords', '["полиграфия","упаковка","печать","этикетка","коробка","стенд","блокнот","баннер","каталог","ежедневник","визитка","флаер","брошюра","POS","дизайн","типография","переплёт","ламинация","плёнка","тара"]'),
  ('competitor_keywords', '["CHINOZ PACK PRINT","Printech","Turon Print"]'),
  ('min_price', '10000000'),
  ('ai_filter_enabled', 'true'),
  ('lead_gen_enabled', 'true'),
  ('deadline_reminders_enabled', 'true')
ON CONFLICT (key) DO NOTHING;
