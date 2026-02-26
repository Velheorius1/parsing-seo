-- Gold standard: Supabase table with mandatory RLS
-- Every table MUST have RLS enabled + service-role-only DELETE

CREATE TABLE IF NOT EXISTS example (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  external_id TEXT NOT NULL,
  -- ... columns ...
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(external_id, source)  -- dedup key
);

CREATE INDEX idx_example_source ON example(source);

-- RLS: mandatory on every table
ALTER TABLE example ENABLE ROW LEVEL SECURITY;
CREATE POLICY "example_select" ON example FOR SELECT USING (true);
CREATE POLICY "example_insert" ON example FOR INSERT WITH CHECK (true);
CREATE POLICY "example_update" ON example FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "example_delete_service" ON example FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
