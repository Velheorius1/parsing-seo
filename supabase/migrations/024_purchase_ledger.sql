-- 024: purchase_ledger — журнал закупок по ИНН заказчика.
--
-- Из чего выросло. Данияр 07.10.2026: топ-100 заказчиков нашей продукции и
-- «кто из заказчиков у кого-то заказал, а мы не узнали». Считать это по алертам
-- нельзя (черновик 07.10): у половины алертов нет заказчика, из э-магазинов в
-- заказчики попадают «КИТАЙ» и «без упаковки», один лот лежит в трёх лентах,
-- одна фирма делится на две строки по написанию. Поэтому — по договорам:
-- у сделок etender, прямых закупок UZEX, итогов ВМК-69 и карточек ebirja есть
-- ИНН заказчика.
--
-- Одна строка = один договор/итог ленты. Сырые страницы лежат gzip-квитанциями
-- на VPS (data/purchase-raw/), здесь — только то, что нужно для рейтинга и
-- разбора пропусков.
--
-- Колонки разбора (profile*, entity_id, miss_type, root_stage) пишут следующие
-- фазы; сборщик списков их НЕ передаёт, поэтому повторный upsert списка не
-- затирает ни оценку предмета, ни данные карточки.
--
-- Доступ. Репозиторий кода публичный, а список заказчиков — коммерческая
-- информация: RLS включён и политик нет, anon/authenticated прав не имеют,
-- читает и пишет только service_role (он обходит RLS).

CREATE TABLE IF NOT EXISTS purchase_ledger (
  id                 bigserial PRIMARY KEY,
  feed               text NOT NULL,          -- deals | direct | civil | ebirja_shop | ebirja_auction | ebirja_tender | ebirja_selection
  business_id        text NOT NULL,          -- id договора/итога в своей ленте
  procedure_id       text,                   -- лот / процедура (trade_id, номер лота ebirja)
  contract_number    text,
  status             text,                   -- accepted | rejected | contract_published | contract | ...

  buyer_inn          text,
  buyer_name         text,
  buyer_type         text,                   -- Budget / Korporativ (etender), тип прямой закупки
  winner_inn         text,
  winner_name        text,

  category           text,                   -- раздел классификатора ленты (прямые закупки)
  subject            text,                   -- предмет: название лота или позиции договора
  subject_codes      text[],                 -- коды классификатора позиций (ebirja)
  subject_hash       text,                   -- ключ кэша оценки предмета

  amount             numeric,
  currency           text,
  amount_uzs         numeric,                -- только когда валюта — сум; курс не угадываем
  start_amount       numeric,
  awarded_on         date,

  lot_key            text,                   -- связь с нашими строками tenders (etender /lot/<id>, result-<id>)
  source_url         text,
  details_fetched_at timestamptz,            -- карточка/детали прочитаны

  profile            text CHECK (profile IN ('poly', 'merch', 'none')),
  profile_src        text,                   -- code | keyword | ai | human
  profile_at         timestamptz,
  entity_id          text,                   -- сущность реестра заказчиков
  miss_type          text,
  root_stage         text,
  analysed_at        timestamptz,

  raw_sha256         text,                   -- квитанция страницы, из которой пришла строка
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,

  UNIQUE (feed, business_id)
);

CREATE INDEX IF NOT EXISTS idx_purchase_ledger_buyer ON purchase_ledger (buyer_inn, awarded_on);
CREATE INDEX IF NOT EXISTS idx_purchase_ledger_lot_key ON purchase_ledger (lot_key);
CREATE INDEX IF NOT EXISTS idx_purchase_ledger_subject_hash ON purchase_ledger (subject_hash);
CREATE INDEX IF NOT EXISTS idx_purchase_ledger_details_todo
  ON purchase_ledger (feed, awarded_on) WHERE details_fetched_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_purchase_ledger_profile
  ON purchase_ledger (awarded_on) WHERE profile IN ('poly', 'merch');

ALTER TABLE purchase_ledger ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON purchase_ledger FROM anon, authenticated;
REVOKE ALL ON SEQUENCE purchase_ledger_id_seq FROM anon, authenticated;
GRANT ALL ON purchase_ledger TO service_role;
GRANT USAGE, SELECT ON SEQUENCE purchase_ledger_id_seq TO service_role;

NOTIFY pgrst, 'reload schema';
