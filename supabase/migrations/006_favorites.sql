-- Избранные тендеры
CREATE TABLE IF NOT EXISTS tender_favorites (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  tender_id UUID NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
  color TEXT DEFAULT 'yellow' CHECK (color IN ('red', 'orange', 'yellow', 'green', 'blue', 'purple')),
  note TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tender_id)
);

CREATE INDEX idx_tender_favorites_tender_id ON tender_favorites(tender_id);

-- RLS: обязательно на каждой таблице
ALTER TABLE tender_favorites ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tender_favorites_select" ON tender_favorites FOR SELECT USING (true);
CREATE POLICY "tender_favorites_insert" ON tender_favorites FOR INSERT WITH CHECK (true);
CREATE POLICY "tender_favorites_update" ON tender_favorites FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "tender_favorites_delete_service" ON tender_favorites FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
