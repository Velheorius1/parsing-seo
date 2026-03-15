-- Прогнозы тендеров (сезонные паттерны по организациям)
CREATE TABLE IF NOT EXISTS tender_predictions (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  organization TEXT NOT NULL,
  predicted_month INT NOT NULL CHECK (predicted_month BETWEEN 1 AND 12),
  predicted_year INT NOT NULL,
  confidence NUMERIC NOT NULL DEFAULT 0,
  basis TEXT DEFAULT '',
  product_hint TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  notified BOOLEAN DEFAULT false,
  UNIQUE(organization, predicted_month, predicted_year)
);

CREATE INDEX idx_tender_predictions_org ON tender_predictions(organization);
CREATE INDEX idx_tender_predictions_month_year ON tender_predictions(predicted_month, predicted_year);

-- RLS: обязательно на каждой таблице
ALTER TABLE tender_predictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tender_predictions_select" ON tender_predictions FOR SELECT USING (true);
CREATE POLICY "tender_predictions_insert" ON tender_predictions FOR INSERT WITH CHECK (true);
CREATE POLICY "tender_predictions_update" ON tender_predictions FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "tender_predictions_delete_service" ON tender_predictions FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
