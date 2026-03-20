-- Add message_type column to classify Telegram messages
-- Values: tender (default), customer_request, info
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS message_type TEXT DEFAULT 'tender';

-- Index for filtering by message type
CREATE INDEX IF NOT EXISTS idx_tenders_message_type ON tenders(message_type);
