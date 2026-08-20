-- 023: alert_outcome — чем кончился алерт.
--
-- Из чего выросло. Разбор 20.08.2026: за полгода система выдала 7553 алерта на
-- 981 млрд сум, и в ней НЕТ НИ ОДНОЙ записи о том, что по ним произошло.
-- tender_bids/tender_favorites/tender_predictions пусты; колонка `winner`
-- заполнена у 2963 строк, но НИ ОДНА из них не алертилась — results_tracker
-- собирает прямые договоры (CivilContracts), это другая вселенная лотов.
-- Следствие: невозможно отличить «система работает вхолостую» от «Данияр
-- участвует мимо системы». Любая оптимизация точности при этом — выбор между
-- двумя числами, ни одно из которых не связано с выручкой.
--
-- Две РАЗНЫЕ оси, поэтому две колонки, а не один enum:
--   our_action  — подали ли МЫ заявку. Знает только человек.
--   lot_result  — чем кончился ЛОТ. Часто знает площадка.
-- Лот может быть выигран конкурентом при том, что мы не подавали: смешать это
-- в одно поле — потерять именно ту разницу, ради которой таблица заводится.
--
-- Автоисточник lot_result — уже собранные нами фиды (замер 20.08):
--   ETender Сделки (победители)   → 177 из 361 алерченного etender-лота
--   ETender Несостоявшиеся (лиды) →  91
-- то есть 74% исхода лежало в нашей же базе несшитым. Победитель приходит в
-- extra_info->>'Победитель', а не в колонку winner — отсюда «ноль победителей».

CREATE TABLE IF NOT EXISTS alert_outcome (
  alert_seq      integer primary key,       -- номер алерта (tenders.alert_seq)
  tender_id      text,                      -- tenders.id на момент записи
  lot_key        text,                      -- id лота из source_url (/lot/NNN) — ключ авто-сшивки

  -- ось 1: наше действие. Только человек, только кнопкой.
  our_action     text CHECK (our_action IN ('bid', 'passed')),
  action_at      timestamptz,

  -- ось 2: судьба лота. Авто или человек; человек всегда главнее (см. outcome.merge).
  lot_result     text CHECK (lot_result IN ('won_by_us', 'won_by_other', 'no_deal')),
  result_source  text,                      -- 'auto:etender-deals' | 'auto:etender-notdealed' | 'button'
  winner         text,
  result_price   numeric,
  participants   integer,
  result_at      timestamptz,

  note           text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Сшивка по лоту и выборка «подали, а исход неизвестен» — два единственных
-- горячих запроса (nudge и отчёт).
CREATE INDEX IF NOT EXISTS idx_alert_outcome_lot_key ON alert_outcome (lot_key);
CREATE INDEX IF NOT EXISTS idx_alert_outcome_pending
  ON alert_outcome (our_action) WHERE lot_result IS NULL;

ALTER TABLE alert_outcome ENABLE ROW LEVEL SECURITY;
-- supabase-security.md: SELECT/INSERT/UPDATE true; DELETE только service_role.
CREATE POLICY alert_outcome_select ON alert_outcome FOR SELECT USING (true);
CREATE POLICY alert_outcome_insert ON alert_outcome FOR INSERT WITH CHECK (true);
CREATE POLICY alert_outcome_update ON alert_outcome FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY alert_outcome_delete_service ON alert_outcome FOR DELETE USING (
  (current_setting('request.jwt.claims', true)::jsonb ->> 'role') = 'service_role'
);
