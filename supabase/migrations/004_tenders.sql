-- Таблица тендеров (crawler results)
CREATE TABLE IF NOT EXISTS tenders (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  organization TEXT DEFAULT '',
  price NUMERIC,
  price_formatted TEXT DEFAULT '',
  currency TEXT DEFAULT 'UZS',
  deadline TEXT,
  date_start TEXT,
  date_end TEXT,
  region TEXT DEFAULT '',
  categories TEXT[] DEFAULT '{}',
  source TEXT NOT NULL,
  source_url TEXT DEFAULT '',
  status TEXT DEFAULT 'active' CHECK (status IN ('active', 'closed', 'cancelled')),
  matched_keywords TEXT[] DEFAULT '{}',
  search_text TEXT DEFAULT '',
  collected_at TIMESTAMPTZ DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(external_id, source)
);

CREATE INDEX idx_tenders_source ON tenders(source);
CREATE INDEX idx_tenders_status ON tenders(status);
CREATE INDEX idx_tenders_collected_at ON tenders(collected_at DESC);

-- RLS: обязательно на каждой таблице
ALTER TABLE tenders ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenders_select" ON tenders FOR SELECT USING (true);
CREATE POLICY "tenders_insert" ON tenders FOR INSERT WITH CHECK (true);
CREATE POLICY "tenders_update" ON tenders FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "tenders_delete_service" ON tenders FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
