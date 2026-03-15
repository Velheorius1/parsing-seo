# Smart Tender System — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use /team-feature to implement this plan.

**Goal:** Превратить парсер тендеров в интеллектуальную систему: AI-оценка, предиктивная аналитика, конкурент-мониторинг, расширение площадок до уровня TenderZone.

**Architecture:** Qwen через OpenRouter для AI-модулей. Supabase для хранения истории и предсказаний. Python crawler + Next.js дашборд.

**Tech Stack:** Python 3.9, httpx, Supabase, OpenRouter/Qwen, Next.js 14, TypeScript, Tailwind

---

## Task 1: Получить список 21 UZ-площадки из TenderZone API

**Files:**
- Create: `scripts/fetch_tzone_platforms.py`
- Reference: `docs/tzone-api-research.md` (полная документация SBIS RPC протокола)

**Что делаем:** Вызвать TenderZone API `TradingPlatform.GetList` и получить список всех UZ-площадок. Сравнить с нашими 59 enabled sources. Вывести gap — какие площадки мы не покрываем.

**API вызов (SBIS JSON-RPC):**
```python
import httpx
resp = httpx.post('https://tzone.uz/service/?srv=1', headers={
    'Content-Type': 'application/json; charset=utf-8;type=rpc',
    'X-Requested-With': 'XMLHttpRequest',
}, json={
    'jsonrpc': '2.0', 'protocol': 4,
    'method': 'TradingPlatform.GetList',
    'params': {'ДопПоля': [], 'Фильтр': {
        '_type': 'record',
        'd': [['860'], None],  # 860 = Uzbekistan
        's': [
            {'n': 'country_code', 't': {'n': 'Массив', 't': 'Строка'}},
            {'n': 'searchString', 't': 'Строка'}
        ], 'f': 0
    }},
    'id': 1
})
# Parse response → list of UZ platforms with names and URLs
```

**Результат:** Таблица gap-анализа: "Площадка X (Y тендеров) — НЕ покрыта нами. URL: Z"

**Commit:** `research: fetch UZ platforms from TenderZone API, gap analysis`

---

## Task 2: AI Qwen модуль — интеллектуальная оценка парсинга

**Files:**
- Create: `crawler/core/ai_evaluator.py`
- Modify: `crawler/core/runner.py` (вызов после crawl)
- Modify: `crawler/config/settings.py` (новые настройки)

**Что делаем:** После каждого crawl-цикла Qwen анализирует результаты и даёт рекомендации:

1. **Оценка качества данных** — % тендеров без цены, без дедлайна, без организации
2. **False positive rate** — сколько алертов Qwen отклонил vs пропустил
3. **Рекомендации** — "источник X даёт 90% мусора, рассмотреть отключение" или "ключевое слово Y даёт слишком много false positives"
4. **Новые ключевые слова** — Qwen анализирует пропущенные тендеры и предлагает добавить keywords

**AI промпт:**
```
Ты — аналитик системы мониторинга тендеров для типографии в Узбекистане.

Результаты последнего crawl-цикла:
- Всего собрано: {total} тендеров с {sources_count} источников
- Новых: {new_count}
- Алертов отправлено: {alerts_sent}
- AI-фильтром отклонено: {ai_rejected}
- Без цены: {no_price_pct}%
- Без дедлайна: {no_deadline_pct}%

Топ-5 источников по объёму: {top_sources}
Топ-5 отклонённых AI-фильтром: {rejected_examples}

Оцени качество и дай 3 конкретные рекомендации по улучшению.
```

**Результат:** Telegram-сообщение раз в день с AI-отчётом о качестве парсинга.

**Commit:** `feat(ai): Qwen evaluator — daily quality report + recommendations`

---

## Task 3: Предиктивный модуль — "когда компания запустит тендер"

**Files:**
- Create: `crawler/core/predictor.py`
- Create: `supabase/migrations/007_predictions.sql`
- Create: `src/app/api/tenders/predictions/route.ts`
- Create: `src/components/tenders/TenderPredictions.tsx`

**Что делаем:** Анализируем историю тендеров каждой организации и предсказываем когда она запустит следующий.

**Алгоритм:**
1. GROUP BY organization, EXTRACT month → частотная таблица по месяцам
2. Для компаний с 3+ тендерами в истории — выявить сезонность
3. Если компания запускала тендер каждый март и сейчас февраль → алерт "Компания X обычно запускает тендер в марте"
4. Qwen обогащает: "Алокабанк закупал конверты 3 раза: март, июнь, сентябрь. Следующий вероятен в июне 2026"

**Миграция:**
```sql
CREATE TABLE IF NOT EXISTS tender_predictions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    organization TEXT NOT NULL,
    predicted_month INTEGER NOT NULL, -- 1-12
    predicted_year INTEGER NOT NULL,
    confidence NUMERIC, -- 0-100
    basis TEXT, -- "3 тендера за последний год: март, июнь, сентябрь"
    product_hint TEXT, -- "конверты, полиграфия"
    created_at TIMESTAMPTZ DEFAULT now(),
    notified BOOLEAN DEFAULT false
);
ALTER TABLE tender_predictions ENABLE ROW LEVEL SECURITY;
-- ... RLS policies
```

**UI:** Секция "Прогнозы" на /tenders — таблица "Организация | Ожидаемый месяц | Уверенность | Что закупали"

**Commit:** `feat(predictor): seasonal analysis + monthly predictions per organization`

---

## Task 4: Конкурент-мониторинг (Supply Side)

**Files:**
- Modify: `scripts/fetch_cooperation.py` (добавить парсинг offers/lots конкурентов)
- Create: `crawler/core/competitor_monitor.py`
- Modify: `crawler/config/settings.py` (COMPETITOR_KEYWORDS)

**Что делаем:**
1. cooperation.uz `GetAllOffer` — лоты ПОСТАВЩИКОВ (63k total). Фильтр по нашей нише
2. cooperation.uz `GetLotsInTrade` — активные торги (2.5k). Кто выставляет конкурирующие лоты
3. Новый набор ключевых слов COMPETITOR_KEYWORDS — имена конкурентов (типографии УЗ)
4. Алерт: "Конкурент [Типография X] выставил лот: 10000 конвертов по 8000 сум"

**Данные cooperation.uz (уже работает скрипт):**
- `GetAllOffer` — предложения поставщиков: productName, companyName, price, quantity
- `GetLotsInTrade` — лоты на торгах: lotName, sellerCompanyName, startPrice

**Lead generation:**
- Из `GetAllPlanSchedule` — компании ПЛАНИРУЮТ закупку. Если в плане "конверты" + "Алокабанк" → алерт "Потенциальный клиент"

**Commit:** `feat(competitors): monitor competitor listings + lead gen from procurement plans`

---

## Task 5: Добавить недостающие UZ-площадки (из gap-анализа Task 1)

**Files:**
- Modify: `crawler/config/sources.yaml`

**Что делаем:** На основании gap-анализа из Task 1 — добавить площадки которые есть у TenderZone но нет у нас. Для каждой: исследовать API/HTML, добавить конфиг в sources.yaml, протестировать dry-run.

**Вероятные площадки (из исследования):**
- dxarid.uzex.uz (если ожил)
- shop.uzex.uz / eshop.uzex.uz (если имеют контент)
- Новые площадки из TenderZone списка
- goszakup.gov.kz (Казахстан — 5.7M тендеров, если нужен СНГ)

**Commit:** `feat(sources): add missing UZ platforms from TenderZone gap analysis`

---

## Task 6: Деплой на VPS

**Что делаем:**
1. SSH на VPS: `cd /opt/parsing-seo && git pull`
2. Перезапустить cron
3. Добавить OPENROUTER_API_KEY в .env на VPS (для AI-фильтра)
4. Проверить: `python3 -m crawler --dry-run`
5. Проверить логи: `docker logs` или cron output

**Commit:** нет (деплой)

---

## Task 7: Обновить .conventions/ и main.md

**Files:**
- .conventions/ — новые gold standards если появились
- main.md — финальный статус

---

## Порядок выполнения

```
Task 1 (gap-анализ площадок) → независимый, быстрый
Task 2 (AI evaluator)        → независимый
Task 3 (предиктивный)        → зависит от данных в DB
Task 4 (конкуренты)          → независимый
Task 5 (новые площадки)      → зависит от Task 1
Task 6 (деплой)              → после всех
Task 7 (docs)                → финал
```

**Параллельные группы:**
- Group A: Tasks 1→5 (площадки)
- Group B: Tasks 2, 3, 4 (AI модули — независимые)
- Group C: Tasks 6→7 (деплой)

**Оценка: 7 задач, ~1000-1500 строк кода, 2-3 кодера параллельно.**
