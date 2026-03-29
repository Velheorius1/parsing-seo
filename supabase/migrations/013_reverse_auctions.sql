-- Migration 013: Reverse auction support — tender_type + tender_bids table

-- Тип тендера: обычный, встречный аукцион, отбор, конкурс
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS tender_type TEXT DEFAULT 'regular';
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS bid_count INT DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_tenders_tender_type ON tenders(tender_type);

-- Ставки (bids) на встречные аукционы
CREATE TABLE IF NOT EXISTS tender_bids (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  tender_id UUID NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
  bidder_name TEXT NOT NULL,
  bidder_inn TEXT,
  amount NUMERIC NOT NULL,
  currency TEXT DEFAULT 'UZS',
  position INT,
  status TEXT DEFAULT 'submitted'
    CHECK (status IN ('submitted', 'accepted', 'rejected', 'withdrawn')),
  submitted_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ DEFAULT now(),
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tender_id, bidder_name)
);

CREATE INDEX IF NOT EXISTS idx_tender_bids_tender ON tender_bids(tender_id);
CREATE INDEX IF NOT EXISTS idx_tender_bids_bidder ON tender_bids(bidder_name);
CREATE INDEX IF NOT EXISTS idx_tender_bids_amount ON tender_bids(tender_id, amount ASC);

ALTER TABLE tender_bids ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tender_bids_select" ON tender_bids FOR SELECT USING (true);
CREATE POLICY "tender_bids_insert_service" ON tender_bids
  FOR INSERT WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "tender_bids_update_service" ON tender_bids
  FOR UPDATE USING (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  ) WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "tender_bids_delete_service" ON tender_bids FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
