-- Competitor monitoring: track competitor wins and activity
-- Source: UZEX CivilContracts API (provider_name matching)

CREATE TABLE IF NOT EXISTS competitor_activity (
  id SERIAL PRIMARY KEY,
  competitor_name TEXT NOT NULL,
  tender_title TEXT,
  tender_price NUMERIC,
  currency TEXT DEFAULT 'UZS',
  customer_name TEXT,
  source TEXT DEFAULT 'uzex',
  external_id TEXT,
  source_url TEXT,
  activity_date TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(external_id, competitor_name)
);

-- RLS: mandatory
ALTER TABLE competitor_activity ENABLE ROW LEVEL SECURITY;
CREATE POLICY "comp_activity_select" ON competitor_activity FOR SELECT USING (true);
CREATE POLICY "comp_activity_insert" ON competitor_activity FOR INSERT WITH CHECK (true);
CREATE POLICY "comp_activity_update" ON competitor_activity FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "comp_activity_delete_service" ON competitor_activity FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
