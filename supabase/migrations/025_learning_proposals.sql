-- 025: learning_proposals — предложения правок фильтра (фаза 5 плана топ-100).
--
-- Из чего выросло. Детектор пропусков (customer_miss, фаза 4) первым прогоном
-- 07.10.2026 нашёл 33 покупки топ-100 нашего профиля, чей лот лежал у нас в базе
-- и умер на ключевых словах: «чоп этиш» кириллицей, «нашр», «matbaa», «sovg'a».
-- Решение Данияра: система ПРЕДЛАГАЕТ правку с бэктестом, включает он кнопкой
-- «да»; сама система ничего не включает.
--
-- Одна строка = одно предложение. UNIQUE (kind, key): отклонённое слово второй
-- раз не предлагается — «нет» система помнит.
--   kind keyword — слово в alert_keywords; включается коммитом с тестом, статус
--                  applied ставит propose_fixes --sync, когда слово в живом словаре;
--   kind channel — площадка или вид закупки без объявления (каталог э-магазина,
--                  несобираемая площадка); «да» — взять в работу.
--
-- Доступ. Доказательства — покупки заказчиков топ-100, это коммерческая
-- информация, а репозиторий публичный: RLS включён, политик нет, anon и
-- authenticated прав не имеют; читает и пишет только service_role.

CREATE TABLE IF NOT EXISTS learning_proposals (
  id                  bigserial PRIMARY KEY,
  kind                text NOT NULL CHECK (kind IN ('keyword', 'channel')),
  key                 text NOT NULL,           -- слово (нижний регистр) / лента
  payload             jsonb NOT NULL DEFAULT '{}'::jsonb,
  evidence            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- что пропустили
  backtest            jsonb NOT NULL DEFAULT '{}'::jsonb,   -- что изменится
  status              text NOT NULL DEFAULT 'proposed'
                      CHECK (status IN ('proposed', 'approved', 'rejected', 'applied', 'retired')),
  telegram_message_id bigint,
  decided_by          text,
  decided_at          timestamptz,
  applied_at          timestamptz,
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (kind, key)
);

CREATE INDEX IF NOT EXISTS idx_learning_proposals_status ON learning_proposals (status);

ALTER TABLE learning_proposals ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON learning_proposals FROM anon, authenticated;
REVOKE ALL ON SEQUENCE learning_proposals_id_seq FROM anon, authenticated;
GRANT ALL ON learning_proposals TO service_role;
GRANT USAGE, SELECT ON SEQUENCE learning_proposals_id_seq TO service_role;
