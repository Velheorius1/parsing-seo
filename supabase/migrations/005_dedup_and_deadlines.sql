-- Группировка тендеров (дедупликация cross-source)
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS group_id UUID;
CREATE INDEX IF NOT EXISTS idx_tenders_group_id ON tenders(group_id);

-- Результаты тендеров
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS winner TEXT;
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS winning_price NUMERIC;
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS result_date TEXT;

-- Обновить CHECK constraint для status (добавить 'completed')
ALTER TABLE tenders DROP CONSTRAINT IF EXISTS tenders_status_check;
ALTER TABLE tenders ADD CONSTRAINT tenders_status_check
  CHECK (status IN ('active', 'closed', 'cancelled', 'completed'));

-- Напоминания о дедлайнах (чтобы не дублировать)
CREATE TABLE IF NOT EXISTS deadline_reminders (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  tender_id UUID NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
  reminder_type TEXT NOT NULL CHECK (reminder_type IN ('3_days', '1_day')),
  sent_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tender_id, reminder_type)
);

ALTER TABLE deadline_reminders ENABLE ROW LEVEL SECURITY;
CREATE POLICY "deadline_reminders_select" ON deadline_reminders FOR SELECT USING (true);
CREATE POLICY "deadline_reminders_insert" ON deadline_reminders FOR INSERT WITH CHECK (true);
CREATE POLICY "deadline_reminders_delete_service" ON deadline_reminders FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
