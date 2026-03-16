-- Fix crawler_settings RLS: restrict INSERT and UPDATE to service_role only.
-- Previously these were open (WITH CHECK (true)), allowing any anon client to write.

-- Drop old open policies
DROP POLICY IF EXISTS "settings_insert" ON crawler_settings;
DROP POLICY IF EXISTS "settings_update" ON crawler_settings;

-- Add service_role-only write
CREATE POLICY "settings_write_service" ON crawler_settings
  FOR INSERT WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
CREATE POLICY "settings_update_service" ON crawler_settings
  FOR UPDATE USING (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  ) WITH CHECK (
    (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
  );
