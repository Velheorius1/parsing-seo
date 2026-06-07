-- 020: classifier_playbook — self-learning relevance principles (Phase 2 of the
-- classifier playbook loop, docs/plans/2026-06-07-classifier-playbook-loop-TZ.md).
-- Generalized principles (NO proper names) distilled from human feedback, injected as
-- a soft override layer into notifier._RELEVANCE_PROMPT. Dormant until status='active'.
-- (Already applied to prod via Management API on 2026-06-07.)

CREATE TABLE IF NOT EXISTS classifier_playbook (
  id              bigint generated always as identity primary key,
  taxonomy        text not null,            -- relevant-rejected | ad-as-client | irrelevant-niche | wrong-score | trivial
  principle       text not null,            -- generalized, NO proper names (TZ §2.4 linter)
  example         text,                     -- "(пример: ...)"
  signal_key      text not null unique,     -- deterministic dedup key: taxonomy + ":" + slug
  status          text not null default 'candidate',  -- candidate | active | retired
  support_count   int  not null default 1,  -- corroborating corrections; active only at >=2 (TZ Атака A)
  retired_reason  text,
  created_at      timestamptz default now(),
  updated_at      timestamptz default now()
);

ALTER TABLE classifier_playbook ENABLE ROW LEVEL SECURITY;
-- supabase-security.md: SELECT/INSERT/UPDATE true; DELETE only service_role (never delete — retire).
CREATE POLICY classifier_playbook_select ON classifier_playbook FOR SELECT USING (true);
CREATE POLICY classifier_playbook_insert ON classifier_playbook FOR INSERT WITH CHECK (true);
CREATE POLICY classifier_playbook_update ON classifier_playbook FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY classifier_playbook_delete_service ON classifier_playbook FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
