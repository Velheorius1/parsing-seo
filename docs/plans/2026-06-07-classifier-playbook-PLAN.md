# План: Classifier Playbook Loop — редирект на Phase 2 (relevance) + фикс маховика фидбека

## Context (зачем)

Данияр написал ТЗ «Classifier Playbook Loop» (самообучение классификатора из человеческого
фидбека, инъекция обобщённых принципов в AI-промпт по образцу Nyro). Задача: изучить, оценить
критически насколько поможет парсеру, составить план внедрения, сделать скоринг до/после.

**Критическая оценка (evidence-based, на данных VPS) — переворачивает приоритет ТЗ:**

| Находка | Данные |
|---|---|
| **Phase 0 gate провален** | `alert_feedback` = **15 коррекций всего**, все 28.03–15.04, **2 месяца ни одной новой**. ТЗ §4.0: <30 → «не строить полный контур». |
| **ТЗ Phase 1 мисфокусирован** | **0 из 15 коррекций про GROUP-классификатор** (цель Phase 1). Все 15 = tender-**relevance**: E-Birja товары/сделки (8), Beeline (1), None (5), test (1). Playbook Phase 1 учить НЕ НА ЧЕМ. |
| **Реальный баг (почему фидбек заглох)** | `feedback_bot.py:129` зовёт `record_feedback(alert_seq, corrected_label)` **без `message_text`/`source`**. `get_few_shot_examples` фильтрует `message_text IS NOT NULL` → **каждый клик по inline-кнопке создаёт строку, невидимую для обучения** (5/15 строк = NULL). Маховик сломан на СБОРЕ, не на алгоритме. |
| **Узкий охват** | playbook ≠ recall-пайплайн (cooperation/UZEX), который улучшали 06.06. |

**Вывод:** playbook Phase 1 (GROUP) сейчас даёт ≈0 (нет данных, premature, против собственного
gate ТЗ). Решение Данияра: **redirect на Phase 2 (relevance)** — туда где коррекции реально есть
и что совпадает с product-scope работой 06.06 (Стенд/Табличка/Бейдж IN, Баннер/Папка OUT). Плюс
сначала **починить feedback_bot** (разблокировать маховик) и **накопить данные**, и заложить
**baseline-скоринг (до/после)**.

---

## Рекомендованный подход (4 шага, по возрастанию объёма)

> Дисциплина веток (CLAUDE.md): `git switch -c parsing-seo-playbook-loop origin/main` от свежего
> main в parsing-seo репо (код на VPS `/opt/parsing-seo`). Доки (ТЗ + этот план) — в Second_Brain
> на `main` через worktree. **Шаг 0 при исполнении: коммит ТЗ-файла + плана** (Данияр просил
> «коммит перед»).

### Шаг 1 — Фикс feedback_bot (THE пререкизит, ~5 строк, высокий leverage)
**Проблема:** кнопочный фидбек невидим обучению.
**Фикс:** в `crawler/scripts/feedback_bot.py::process_callback` перед `record_feedback` сделать
lookup тендера по `alert_seq` (переиспользовать логику из `parsing_feedback_cli.py:50-66` —
`tenders.select(external_id,source,title,message_type).eq(alert_seq)`), передать `message_text=title`,
`source=source`, `original_label=message_type`.
**Файлы:** `crawler/scripts/feedback_bot.py` (+ возможно вынести lookup-хелпер в `feedback.py`,
чтобы CLI и bot не дублировали — `feedback.py::lookup_tender_for_feedback(alert_seq)`).
**Verify:** клик по кнопке тест-алерта → строка в `alert_feedback` с непустыми message_text/source;
`get_few_shot_examples()` её видит.

### Шаг 2 — Baseline-скоринг харнес (до/после, переиспользуемый)
Новый `crawler/scripts/score_relevance.py`:
- Берёт labeled-сет: коррекции из `alert_feedback` где есть ground-truth label И title/message_text
  (по `tender_id` дотянуть `tenders.title` для строк без message_text → ~15) + **зашить мини product-scope
  golden-set из 06.06** (Стенд→client, Табличка→client, Бейдж→client, Бланк→client, Баннер→irrelevant,
  Папка→irrelevant, цемент→irrelevant — известный ground-truth, уже verified).
- Прогоняет через **текущий** `notifier._ai_check_relevance` (полный путь, не изолированно),
  маппит score≥70→relevant иначе→irrelevant, сравнивает с label.
- Метрики: accuracy, FP (AI relevant / human ad-irrelevant), FN (AI irrelevant / human client),
  per-class. Флаг `--playbook on|off` для до/после.
- **Честная оговорка в выводе:** n мал (~20) → индикативно, не статзначимо (ТЗ §5).
**Verify:** запустить `--playbook off` → зафиксировать **BASELINE (до)** число в отчёт.

### Шаг 3 — Phase 2 playbook-инфра под relevance (по ТЗ, адаптировано; gated, dormant)
Цель-пайплайн: `crawler/core/notifier.py` (`_RELEVANCE_PROMPT`, `_ai_check_relevance`), НЕ
`telegram_adapter` (GROUP).
1. **Миграция `supabase/migrations/020_classifier_playbook.sql`** (latest = 019, не 017 как в ТЗ §4.1):
   таблица `classifier_playbook` точно по ТЗ §2.1 (taxonomy/principle/example/signal_key/status/
   support_count/retired_reason) + RLS (`supabase-security`: SELECT/INSERT/UPDATE true, DELETE
   service_role), `unique(signal_key)`. Применить через Management API/PAT.
2. **`feedback.py::get_relevance_playbook() -> str`**: читает `status='active'`, форматирует, кэш
   как `_FEW_SHOT_TTL` (2ч), лимит ≤20.
3. **`notifier.py`**: плейсхолдер `{playbook}` в `_RELEVANCE_PROMPT` (override-слой над текущим
   МЫ ДЕЛАЕМ/НЕ НАШЕ блоком, с явной precedence «принципы playbook важнее при конфликте»);
   проброс в `_ai_check_relevance`/`_ai_call_one`. Грейсфул при пустом (как `{few_shot}` в адаптере).
4. **`crawler/scripts/playbook_refine.py`** (по ТЗ §2.2): переиспользовать `fetch_feedback` из
   `refine_patterns.py`; **relevance-таксономия** (`ad-as-client` FP, `relevant-rejected` FN —
   in-scope зарезан как Стенд до C3, `irrelevant-niche` out-of-scope принят, `wrong-score`,
   `trivial`); детерминированный `signal_key=taxonomy+slug` из закрытого набора; дедуп по UNIQUE;
   промоут candidate→active только `support_count>=2` или ручной апрув; противоречие→retired;
   системный пробел→`prompt_proposals` отчёт (TG --send). Модель `deepseek-v4-pro` (уже дефолт).
   Линтер «без имён собственных» (ТЗ §2.4).
5. **Cron**: `playbook_refine --send` weekly (другое время чем `refine_patterns` Mon 9:00, чтобы
   не конкурировать за чтение); `refine_patterns` **оставить read-only** (ТЗ §2.6 — канал для
   prefilter-FN, который playbook не достаёт).
6. **Bootstrap** на 15 исторических: прогнать, **всё садить candidate** (none auto-active — лечит
   laundering одиночного шума, ТЗ Атака A).

### Шаг 4 — Gate / накопление / после
- Playbook остаётся candidate-доминантным (dormant в промпте) **пока не накопится ≥30 свежих
  relevance-коррекций** (теперь текут через починенные кнопки шага 1).
- Когда active-принципов ≥3 (support≥2, ревью Данияра, **0 имён собственных**) → запустить
  `score_relevance.py --playbook on` → **AFTER (после)**, сравнить дельту с baseline.
- **Anti-scope:** не строить полный контур «на холостом» если за разумный срок фидбек не пошёл —
  тогда стоп на шагах 1-2 (фикс + скоринг), playbook не активировать.

---

## Скоринг до/после (явная методология — Данияр просил)

| | Что | Когда |
|---|---|---|
| **ДО (baseline)** | `score_relevance.py --playbook off` на labeled-сете (15 коррекций + golden product-scope) через полный `_ai_check_relevance`. Метрики accuracy/FP/FN. | **сейчас** (шаг 2) — даёт число сегодня |
| **ПОСЛЕ** | тот же сет/харнес `--playbook on` с ≥3 active-принципами. Засчитывается **дельта** (исправил X ранее-ошибочных), не абсолют (ТЗ §5). | после накопления ≥30 свежих коррекций |

**Честные ограничения (не прятать):** n≈20 → индикативно. Чистый train/test split на 15 примерах
невозможен → «после» на bootstrap-принципах из того же сета = leakage; валидное «после» только на
свежих held-out коррекциях. Это в отчёте явно.

---

## Критические файлы
- **Чиним:** `crawler/scripts/feedback_bot.py` (lookup+message_text), `crawler/core/feedback.py`
  (хелпер lookup + `get_relevance_playbook`), `crawler/core/notifier.py` (`{playbook}` в `_RELEVANCE_PROMPT`).
- **Новые:** `crawler/scripts/score_relevance.py`, `crawler/scripts/playbook_refine.py`,
  `supabase/migrations/020_classifier_playbook.sql`.
- **Переиспользуем:** `parsing_feedback_cli.py:50-66` (tender lookup), `refine_patterns.py::fetch_feedback`,
  `notifier._ai_check_relevance`/`_ai_call_one`, существующую `alert_feedback` (только читаем, ТЗ §3).
- **НЕ трогаем:** `telegram_adapter` GROUP-путь (Phase 1 отложен), `_DEMAND_PATTERNS`/`_AD_FILTER`,
  `ai_evaluator.py`, схему `alert_feedback`, модель (deepseek-v4-pro уже стоит).

## Verification (end-to-end)
1. **feedback_bot:** тест-клик → `alert_feedback` строка с message_text/source ≠ NULL.
2. **Baseline:** `score_relevance.py --playbook off` → число accuracy/FP/FN зафиксировано.
3. **Миграция:** Supabase Security Advisor чисто, `unique(signal_key)` работает.
4. **Промпт:** лог `_ai_check_relevance` в `parsing-seo-ai-decisions.jsonl` содержит текст принципов
   (когда active≥1).
5. **Идемпотентность:** повторный `playbook_refine` не плодит дубли (signal_key UNIQUE);
   противоречие→retired+reason (не delete).
6. **Латентность:** дельта p95 AI-вызова с ≤20 принципами vs без — не ухудшать значимо.
7. **После накопления:** `--playbook on` → дельта vs baseline; ревью Данияра каждого active-принципа.

## Риски (сверх ТЗ §6)
- **Главный: данных может так и не накопиться** (фидбек заглох на 2мес). Митигация: gate на шаге 4 —
  если за разумный срок <30, playbook не активировать, остановиться на фикс+скоринг (не строить
  костыль на холостом).
- playbook→«рельсы в БД»: граница ТЗ §2.0 (принцип=«как думать», не «что блокировать»), ревью,
  детерм. signal_key, candidate-gate.
- Leakage в «после»: валидное измерение только на held-out свежих коррекциях.
