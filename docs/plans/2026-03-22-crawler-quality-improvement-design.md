# Улучшение парсера тендеров — Design Document

## Оглавление
1. [Обзор](#обзор)
2. [Ключевые решения](#ключевые-решения)
3. [Детальный анализ](#детальный-анализ)
4. [План реализации](#план-реализации)
5. [Метрики успеха](#метрики-успеха)

---

## Обзор

Crawler собирает 9500+ тендеров из 64 источников, но AI Evaluator показывает низкое качество: 100% без цены/дедлайна, 19/23 "ошибок". Анализ 6 экспертов выявил, что **система работает хорошо, но мониторинг врёт**, а потенциал AI enrichment не используется.

**Три корневые проблемы:**
1. **Evaluator считает "0 результатов за цикл" = ошибка** → ложная тревога на 23 TG-каналах
2. **67 из 99 источников не получают AI enrichment** → пустые поля (цена, дедлайн, заказчик)
3. **Баги в field_map** → `xarid-competitions` (3811 тендеров) без организации из-за `organization: ""`

---

## Ключевые решения

| # | Аспект | Решение | Уверенность |
|---|--------|---------|-------------|
| 1 | Качество данных | Fix field_map + source-aware stats | High |
| 2 | Telegram каналы | Прунинг 3 мёртвых + observability layer | High |
| 3 | AI Enrichment | Post-adapter enrichment для ВСЕХ источников + JSON output | High |
| 4 | Новые источники | Сначала починить сломанные (SPA, гео-блок), потом новые | High |
| 5 | AI Evaluator | Query Supabase для daily truth вместо per-cycle stats | High |
| 6 | Архитектура | Docker HEALTHCHECK + flock + crawl_runs таблица | High |

---

## Детальный анализ

### 1. Качество данных (цена, дедлайн, заказчик)
**Эксперт:** Raymond Hettinger

**Решение:** Fix field_map gaps + Source-aware quality stats

**Проблема:** `xarid-competitions` — второй по объёму источник (3811 тендеров) — имеет `organization: ""` в field_map. API вероятно возвращает `customer_name` (sibling-источник `xarid-direct` маппит его корректно). Аналогично `ebirja-eshop`, `ebirja-natshop`.

**Действия:**
- curl запрос к xarid API → найти правильное поле для organization
- Исправить field_map для 3-4 источников
- Обновить evaluator: считать "без цены" только для источников где цена ДОЛЖНА быть

**Альтернативы:** Detail-page fetching (дорого, N запросов), AI extraction (решается в пункте 3)

**Риски:**
- API xarid может не возвращать customer_name → проверить curl-ом
- Per-source stats усложняют Telegram-сообщение (лимит 4096 символов)

---

### 2. Telegram каналы — стратегия
**Эксперт:** Martin Kleppmann

**Решение:** Отключить 3 мёртвых + добавить observability

**Контекст:** 23 TG-канала, все возвращают 0 в отдельном цикле — это НОРМАЛЬНО (incremental mode). 17 из 23 активно производят тендеры. Стоимость опроса всех 23 = ~8 секунд, $0.

**Классификация:**

| Tier | Каналов | Тендеров всего | Действие |
|------|---------|---------------|----------|
| HIGH (>100) | 7 | 1456 (79%) | Оставить |
| MED (20-100) | 7 | 349 (19%) | Оставить |
| LOW (<20) | 3 | 32 (2%) | Оставить |
| DEAD (0) | 3 | 0 | **Отключить** (tg-mift, tg-davlatxaridlar, tg-tender-uzbekistan) |
| POSTING BUT 0 | 3 | 0 | Расширить парсер для news/signals |

**Дополнительно:**
- Добавить `last_tender_at` warning: если канал 0 тендеров 14+ дней → WARNING в логах
- Примонтировать `/app/crawler/cache/` как Docker volume (защита от потери last_id при rebuild)

**Риски:**
- Отключённый канал может ожить → mitigation: observability layer поймает
- Cache persistence при docker rebuild → volume mount решает

---

### 3. AI Enrichment pipeline
**Эксперт:** Andrej Karpathy

**Решение:** Post-adapter AI enrichment + structured JSON output + parallel calls

**Текущее состояние:**
- AI используется только для Telegram (demand extraction + intent verification) и relevance filtering
- **67 из 99 источников не получают AI enrichment** → пустые поля
- Модель: `qwen/qwen3-30b-a3b` (MoE, 30B total / 3B active) — очень дешёвая

**Расчёт стоимости:**
- 500 тендеров/день × ~200 tokens input = 100k tokens/день
- Qwen3-30B-A3B: $0.10/M input → **$0.003/день = $0.09/месяц**
- Это фактически бесплатно

**Действия:**
1. Добавить pipeline stage после адаптеров, перед DB upsert: для RawTender с missing price/deadline/org → отправить title+search_text в Qwen
2. Переключить на `response_format: {"type": "json_object"}` вместо line-based parsing
3. Параллелизировать relevance checks через `asyncio.gather` (max 10 concurrent)
4. Добавить semaphore для rate limiting OpenRouter

**Альтернативы:** Model upgrade до Qwen3-235B (~3x дороже, но всё равно $0.30/мес)

**Риски:**
- OpenRouter rate limits при 500 concurrent → semaphore max 10
- JSON mode может не поддерживаться для qwen3-30b-a3b → fallback к line parsing
- +30-60 секунд к каждому crawl cycle → приемлемо для cron

---

### 4. Новые источники и покрытие
**Эксперт:** Sam Newman

**Решение:** Починить сломанное ПЕРЕД добавлением нового

**Приоритеты починки (по ожидаемому yield):**

| # | Источник | Проблема | Fix | Ожидаемый yield |
|---|----------|----------|-----|----------------|
| 1 | SPA sources (xt-xarid, hayotbirja, ebirja-auction) | Playwright fails on VPS | Найти REST API за SPA | 3000-5000 |
| 2 | Geo-blocked (cooperation.uz) | VPS в России заблокирован | Cloudflare Workers proxy | 500-1000 |
| 3 | 12 corporate HTML (aab, fnpz, saneg...) | Broken selectors | Batch investigation | 50-100 |

**Новые источники ПОСЛЕ починки:**

| # | Источник | Тип | Ценность |
|---|----------|-----|----------|
| 1 | TenderZone (157k+) | SPA (configured, disabled) | Мега-агрегатор = backup покрытие |
| 2 | sam.gov (USAID) | API | Бесплатный API key, US тендеры в УЗ |
| 3 | openbudget.uz | API/HTML | Планы закупок = ранний сигнал |
| 4 | Bicotender (106 полиграфия) | HTML | Конкурентная разведка |

**Риски:**
- SPA sources могут требовать headless Chrome → уже установлен в Docker (Playwright)
- Geo-proxy добавляет latency и точку отказа

---

### 5. AI Evaluator — правдивые метрики
**Эксперт:** Kent C. Dodds

**Решение:** Query Supabase для daily truth

**Корневая проблема:** Evaluator считает stats из одного цикла (5 тендеров) и показывает "19/23 ошибок" хотя система здорова. `sources_fail = v == 0` — но 0 = нормально для incremental TG каналов.

**Действия:**
1. **Заменить `_compute_stats`** → query Supabase: `SELECT count(*), source FROM tenders WHERE collected_at >= today`
2. **3 bucket classification:** `sources_ok` (>0 сегодня), `sources_idle` (0 но нормально), `sources_error` (Exception или 0 при историческом avg >0)
3. **Добавить baselines в Qwen prompt** → "etender обычно 600/день, сегодня 665" вместо голых цифр
4. **Формат:** "9547 тендеров в БД (23 новых за цикл)" вместо "5 тендеров"

**Альтернативы:**
- A: Просто разделить empty vs error (минимальный fix, не решает проблему "5 тендеров")
- B: Аккумулировать stats за день в JSON файл (сложнее, менее надёжно чем Supabase query)

**Риски:**
- Дополнительный Supabase query при каждой оценке → 1 запрос, ничтожная нагрузка
- Нужно определить "исторический средний" для каждого источника

---

### 6. Архитектура crawler
**Эксперт:** Kelsey Hightower

**Решение:** Docker HEALTHCHECK + flock + crawl_runs таблица

**Текущие проблемы:**
- Нет health check → если cron умрёт, контейнер "работает" (tail -f)
- Нет защиты от overlap → если crawl >2ч, два процесса параллельно
- Нет исторических метрик → только Docker logs с ротацией
- Cron in Docker — anti-pattern (env export hack, silent failures)

**Действия (Phase 1 — safety):**
```dockerfile
HEALTHCHECK --interval=300s --timeout=5s --retries=3 \
  CMD python -c "import os,time; assert time.time()-os.path.getmtime('/tmp/last_crawl_ok')<10800"
```
```cron
0 */2 * * * flock -n /tmp/crawler.lock cd /app && python -m crawler.main
```

**Действия (Phase 2 — observability):**
- Таблица `crawl_runs`: timestamp, duration_seconds, sources_ok, sources_failed, tenders_found, tenders_new, alerts_sent, errors (JSONB)
- Записывать после каждого crawl cycle
- Evaluator query-ит эту таблицу вместо in-memory stats

**Альтернативы:**
- Systemd timer вместо cron-in-Docker (чище, но ломает docker-compose)
- Circuit breaker на адаптерах (преждевременная оптимизация при 2ч cron)

**Риски:**
- `/tmp/last_crawl_ok` теряется при restart → но healthcheck просто пропустит первую проверку
- crawl_runs таблица требует миграцию → стандартный процесс

---

## План реализации

### Phase 1: Quick Wins (1 день)
- [ ] Fix field_map: `xarid-competitions` organization → проверить curl, исправить
- [ ] Fix field_map: `ebirja-eshop`, `ebirja-natshop` organization
- [ ] Отключить 3 мёртвых TG канала (tg-mift, tg-davlatxaridlar, tg-tender-uzbekistan)
- [ ] Docker HEALTHCHECK + flock в cron
- [ ] Volume mount для `/app/crawler/cache/`

### Phase 2: Evaluator Fix (0.5 дня)
- [ ] Evaluator: query Supabase вместо per-cycle stats
- [ ] 3-bucket source classification (ok/idle/error)
- [ ] crawl_runs таблица + миграция

### Phase 3: AI Enrichment (1 день)
- [ ] Post-adapter enrichment stage в runner.py
- [ ] Structured JSON output (response_format)
- [ ] Parallel relevance checks (asyncio.gather + semaphore)
- [ ] Тест: enrichment cost за 1 цикл

### Phase 4: Source Recovery (1-2 дня)
- [ ] Investigate SPA sources: найти REST API за xt-xarid, hayotbirja
- [ ] Geo-proxy для cooperation.uz (Cloudflare Workers)
- [ ] Batch investigation 12 broken HTML sources
- [ ] last_tender_at observability для TG каналов

### Phase 5: New Sources (по мере готовности)
- [ ] TenderZone (enable + fix SPA)
- [ ] sam.gov (API key registration)
- [ ] openbudget.uz (procurement plans)

---

## Метрики успеха

| Метрика | Сейчас | Цель (Phase 3) | Цель (Phase 5) |
|---------|--------|----------------|----------------|
| Источники с >0 результатов/день | ~35/64 (55%) | 45/64 (70%) | 55/70 (78%) |
| Тендеры без organization | ~40% | <15% | <10% |
| Тендеры без price (где цена есть в источнике) | ~60% | <20% | <10% |
| Тендеры без deadline | ~30% | <10% | <5% |
| Evaluator false alarms | 19/23 "ошибок" | 0 false alarms | 0 |
| AI enrichment стоимость | $0/мес | <$0.10/мес | <$0.30/мес |
| Общий объём тендеров/день | ~9500 | ~10000 | ~15000-20000 |

---

*Документ создан: 22 марта 2026*
*Метод: /deep-think — 6 параллельных экспертных анализов*
*Эксперты: Raymond Hettinger, Martin Kleppmann, Andrej Karpathy, Sam Newman, Kent C. Dodds, Kelsey Hightower*
