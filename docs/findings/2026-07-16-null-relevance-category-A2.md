# A2 — Почему `relevance_category` null на ~53% алертов (аудит 2026-07-16)

**Контекст:** дыра A (verdict-based learning) опиралась на `tenders.relevance_category`.
Разведка показала null на 1430/2708 алертов за 30д. Вопрос: баг персиста или структура?

## Диагноз (из данных)
`null category ⇔ null score` (все 1430 — оба поля пустые → AI-скоринг **не запускался**, а не «запустился, но не сохранил»).

| Источник null | Кол-во (30д) | Почему |
|---|---|---|
| `customer_request` | 968 (68% null) | **По дизайну.** Telegram-лиды гоняют только лёгкий spam-gate (`_ai_lead_is_spam`, notifier.py:1125), НЕ тяжёлый relevance-scorer: heavy-prompt путается на разговорных заказах («сумка 1250шт»→0), документировано 12.06. Kept-лид → `_allow(...)` со `score=None`. |
| `tender` passthrough | ~350 | **Структура.** `E-Birja завершённые сделки`, `ETender Обсуждения`, birja/uzex-стримы идут через annotate-not-gate (notifier.py:1160-1182) — отправляются всегда, скорятся best-effort; при недоскоре остаются null. |
| `tender` AI fail-open | ~110 | Redкий `_ai_check_relevance`→score=None (ошибка/фолбэк). Fail-open guardrail: айтем НЕ дропается молча, но остаётся без score. |

## Вывод: кода НЕ требуется
Null — **не баг**, а сумма by-design (лёгкий gate для лидов) + структурных passthrough + fail-open.
Фикс A1 (`_system_verdict`) корректно обрабатывает все null через фолбэк `'alerted'`:
- показан = релевантен (верный дефолт для лида/passthrough);
- null-вердикт НЕ может сфабриковать ложный recall-guard, т.к. `'alerted' ∉ {ad,irrelevant,weak}` → только `reject`(FP) или `skip`(agreement).

## Честное ограничение (записать)
`customer_request` никогда не получает `relevance_score` → Telegram-лиды **не могут** породить recall-guard (`protect`) сигнал. Защита recall для лидов держится на: (а) лёгком spam-gate, (б) reject-кликах Данияра (работают). Это приемлемо — не чинить, чтобы не гонять heavy-scorer на разговорных заказах (сломает точность лидов + добавит LLM-вызовы + риск дропнуть реальный лид).

## Что мониторить (без действий сейчас)
Если доля null у `tender` вырастет >30% (сейчас 27%) — это может быть рост AI fail-open, не passthrough. Проверять `ai_decision_log` / долю `score IS NULL` по TG-источникам в недельной скоркарте (Задача C).
