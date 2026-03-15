# CLAUDE.md — Parsing SEO

> Читай вместе с PRODUCT.md для полного контекста продукта.

---

## ТЕКУЩЕЕ СОСТОЯНИЕ
Фаза: Production — 99 источников (64 enabled), cron 3x/день, AI Qwen фильтр + evaluator
Дата обновления: 15 марта 2026

### Что работает
- **Crawler на VPS** (46.62.155.190): 64 enabled sources, cron 06:00/14:00/22:00
- **cooperation.uz** — Mac launchd каждые 8ч (376k планов, geo-blocked с VPS)
- **AI-фильтр Qwen** — отсекает false positives (набор реагентов ≠ подарочный набор)
- **AI Evaluator** — ежедневный отчёт качества парсинга через Qwen
- **Предиктивный модуль** — сезонные паттерны компаний → "X запустит тендер в апреле"
- **Конкурент-мониторинг** — алерты когда конкурент выставляет лот (#конкурент)
- **Lead generation** — закупочные планы cooperation.uz → "Алокабанк планирует конверты" (#лид)
- **Дедупликация** — один алерт вместо 3 за один тендер с разных площадок
- **Дедлайн-трекер** — напоминания за 3д и 1д
- **Мониторинг результатов** — UZEX CivilContracts (5000+ сделок, победители+цены)
- **Дашборд /tenders** — фильтры (20+), Excel экспорт, аналитика, избранное, DeadlineBadge
- **Админ-панель /tenders/settings** — управление keywords, конкурентами, порогом цены, тоглами
- **Мин порог 10M сум** — мелкие тендеры не алертятся

### Что не работает / в процессе
- **E-IMZO регистрация** (Данияр) — ebirja.uz, hayotbirja.uz (PFX ключ готов)
- **Международные**: UNICEF (403), ADB (Cloudflare), JICA (404)
- **Beeline UZ, Bnect** — SPA, нужен Playwright

### Последние обновления (15 марта 2026)
- **Smart Tender System** — AI evaluator, predictor, competitor monitoring, lead gen
- **TenderZone parity** — фильтры, Excel, аналитика, избранное (1775 строк)
- **Gap analysis** — 21 UZ-площадка у TenderZone, 9 gaps, 6 добавлены (99 total)
- **Админ-панель** — /tenders/settings (keywords, competitors, price, toggles)
- **Миграции 005-008** — все применены в Supabase
- **VPS задеплоен** — все модули загружены, 9505 тендеров

### Следующие шаги
- [ ] **Регистрация E-IMZO** (Данияр) — ebirja.uz + hayotbirja.uz
- [ ] Проверить /tenders и /tenders/settings в браузере
- [ ] Расширение СНГ (Казахстан goszakup.gov.kz) если нужен
- [ ] Интеграция в Brain Bot (скилл `/тендеры`)

### Регистрация на площадках (TODO Данияр)

Все госплощадки требуют **E-IMZO (ЭЦП)**. Скачать: https://e-imzo.soliq.uz/download/

| # | Площадка | Ссылка на регистрацию | Авторизация | Приоритет | Что даст |
|---|----------|----------------------|-------------|-----------|----------|
| 1 | **ebirja.uz** (госзакупки) | https://xarid.ebirja.uz/ru/auth/register | E-IMZO | **Высокий** | Buyer-side лоты, API `shop/product/view`, торги |
| 2 | **ebirja.uz** (биржа) | https://app.ebirja.uz/ru/auth/register | E-IMZO | Средний | Биржевые торги |
| 3 | **hayotbirja.uz** | https://hayotbirja.uz/#/register | E-IMZO | **Высокий** | API-токен → тендеры 169, аукционы 2558, э-магазин 52k |
| 4 | **xt-xarid.uz** | https://xt-xarid.uz/#/register | E-IMZO | Средний | Обратные аукционы через кабинет |
| 5 | **cooperation.uz** | https://cooperation.uz/login (рег. сломана, вход через E-IMZO) | E-IMZO + ESI.uz | Низкий | Расширенные данные (уже парсим публичный API) |
| 6 | **TenderZone** | https://trade.tzone.uz/auth/register | Телефон/пароль | Низкий | Агрегатор 157k+ (7 дней бесплатно) |

---

## 📋 План: Мониторинг тендеров Узбекистана

**Цель:** не пропускать релевантные тендеры (упаковка, полиграфия, гофра, коробка, печать, этикетка) и быстро реагировать.

**Закон:** ЗРУ-684 от 22.04.2021 «О государственных закупках» — все госзакупки обязаны публиковаться на электронных площадках.

---

### Карта площадок (исследование 25.02.2026)

#### Приоритет 1 — Госзакупки (основной объём)

| Площадка | URL | Что парсить | Примечание |
|----------|-----|-------------|------------|
| **XT-Xarid** | xt-xarid.uz | Прямые закупки бюджетных организаций | Основная площадка госзакупок, заменила старый xarid.uz |
| **UZEX Тендер** | etender.uzex.uz | Конкурсы через товарную биржу | Часть UZEX ecosystem |
| **UZEX E-Аукцион** | e-auksion.uzex.uz | Электронные аукционы | Обратные аукционы — цена снижается |
| **UZEX DXarid** | dxarid.uzex.uz | Госзакупки через биржу | Прямые закупки |
| **UZEX EXarid** | exarid.uzex.uz | Электронные закупки | Каталог товаров |
| **UZEX Shop** | shop.uzex.uz | Онлайн-магазин биржи | B2G каталог |
| **UZEX EShop** | eshop.uzex.uz | Электронный магазин | Малые закупки |
| **Cooperation.uz** | cooperation.uz | Кооперация и закупки | Новая площадка, растёт |
| **E-Birja** | e-birja.uz | Электронная биржа | Торговая площадка |

#### Приоритет 2 — Международные организации (высокие бюджеты)

| Организация | URL тендеров | Сфера |
|-------------|-------------|-------|
| **UNDP** | procurement-notices.undp.org (фильтр UZ) | Развитие, инфраструктура |
| **UNICEF** | supply.unicef.org | Образование, здравоохранение |
| **World Bank** | projects.worldbank.org/en/projects-operations/procurement | Крупные инфраструктурные проекты |
| **ADB** | adb.org/projects/tenders | Азиатский банк развития |
| **EBRD** | ecepp.ebrd.com | Европейский банк реконструкции |
| **IsDB** | isdb.org/procurement | Исламский банк развития |
| **GIZ** | ausschreibungen.giz.de | Германское агентство (офис в Ташкенте) |
| **JICA** | jica.go.jp/english/procurement | Японское агентство (офис в Ташкенте) |
| **KOICA** | koica.go.kr | Корейское агентство |
| **USAID** | sam.gov (+ usaid.gov) | Американское агентство |
| **EU** | ted.europa.eu | Тендеры Евросоюза |

**Агрегаторы международных тендеров:**
- UNGM (ungm.org) — единая система закупок ООН
- dgMarket (dgmarket.com) — агрегатор тендеров развивающихся стран
- DevelopmentAid (developmentaid.org) — тендеры + гранты
- TendersInfo (tenders.info) — глобальный агрегатор

#### Приоритет 3 — Крупные компании (прямые закупки)

| Компания | Сектор | Тендеры на сайте |
|----------|--------|-----------------|
| **АГМК** (agmk.uz) | Горнодобыча | Активные тендеры |
| **Uzbekistan Airways** (uzairways.com) | Авиация | 80+ тендеров |
| **Узбекнефтегаз** (ung.uz) | Нефтегаз | Регулярные закупки |
| **НМАК Узбекистон** (uzbekistanairways.com) | Авиация | Тендеры |
| **Навоийский ГМК** (ngmk.uz) | Горнодобыча | Крупные закупки |
| **Узтрансгаз** (uztransgaz.uz) | Газ | Тендеры |
| **Нацэлектросети** (npes.uz) | Энергетика | 22+ тендера |
| **LUKOIL Uzbekistan** (lukoil.uz) | Нефтегаз | Тендеры |
| **Узхимпром** (uzkimyosanoat.uz) | Химия | Закупки |
| **Узметкомбинат** (uzmetkombinat.uz) | Металлургия | Тендеры |
| **Узбекистон темир йуллари** (railway.uz) | ЖД | Регулярные закупки |
| **UzAuto Motors** (uzautomotors.com) | Автопром | Тендеры |
| **Artel** (artelelectronics.com) | Электроника | Закупки |
| **Uztelecom** (uztelecom.uz) | Телеком | Тендеры |
| **COSCOM/Ucell** (ucell.uz) | Телеком | Закупки |
| **Beeline Uzbekistan** (beeline.uz) | Телеком | Тендеры |

#### Приоритет 4 — Агрегаторы и частные площадки

| Площадка | URL | Объём |
|----------|-----|-------|
| **TenderZone** | tenderzone.uz | 157K+ тендеров, агрегатор |
| **Bicotender** | bicotender.uz | 20K+ по УЗ, 106 полиграфия |
| **zakupki.prom.uz** | zakupki.prom.uz | Промышленные закупки |
| **tender.uz** | tender.uz | Информационный портал |

**Telegram-каналы (мониторить):**
- @prom_zakupki — промышленные закупки
- @newtenderzone_bot — бот TenderZone
- @tenders_uzbekistan — общий канал тендеров

#### Информационные порталы (вторичные)

| Портал | URL | Что там |
|--------|-----|---------|
| **openbudget.uz** | openbudget.uz | Открытый бюджет — планы закупок |
| **data.gov.uz** | data.gov.uz | Открытые данные — статистика |
| **norma.uz** | norma.uz | Законодательство о закупках |
| **lex.uz** | lex.uz | Нормативные акты |

---

### Ключевые слова для фильтрации

```
упаковка, полиграфия, гофра, коробка, печать, этикетка,
типография, книга, каталог, брошюра, блокнот, календарь,
packaging, printing, cardboard, label, box, qadoqlash, bosma
```

---

### Фазы реализации

**Фаза 1 — Парсер приоритетных площадок**
- xt-xarid.uz + etender.uzex.uz + e-auksion.uzex.uz + cooperation.uz
- Ежечасный cron: парсинг новых лотов
- Фильтр по ключевым словам
- Supabase: хранение лотов (ID, тема, заказчик, сумма, дедлайн, ссылка, площадка)

**Фаза 2 — Telegram-алерты**
- Новый тендер → моментальный алерт в Telegram
- Формат: площадка, тема, сумма, заказчик, дедлайн, ссылка
- Интеграция в Brain Bot (скилл `/тендеры`)

**Фаза 3 — Расширение на международные + крупные компании**
- UNDP, UNICEF, World Bank — парсинг по фильтру Uzbekistan
- Сайты АГМК, Uzbekistan Airways, НМАК — парсинг разделов тендеров
- TenderZone/Bicotender как бэкап-агрегаторы

**Фаза 4 — Дашборд с историей + аналитикой**
- Архив тендеров: кто выигрывал, по каким ценам
- Аналитика конкурентов
- Фильтры по площадкам, категориям, суммам

**Фаза 5 — Интеграция в Newcalc**
- Тендер пришёл → кнопка → автосоздание расчёта с параметрами из лота

**Фаза 6 — Еженедельный PDF-дайджест**
- Автосборка отчёта по релевантным тендерам за неделю
- Отправка в Telegram для планёрки

**Фаза 7 (идея) — AI-скоринг «стоит ли участвовать»**
- Модель анализирует тендер + прошлые победы/поражения
- Оценка шансов + рекомендация по цене

---

## 🎯 КЛЮЧЕВОЙ ПРИНЦИП: ЛЮБОЙ URL

```
❌ НЕПРАВИЛЬНО: Фиксированный список конкурентов (micros.uz, print.uz)
✅ ПРАВИЛЬНО: Пользователь вводит ЛЮБОЙ URL → получает ключевые слова
```

**Job Story (по Замесину):**
```
КОГДА я вижу что конкурент выше меня в поиске
  → я не знаю какие ключевые слова он использует
  → у меня нет доступа к Ahrefs ($99/мес)

ХОЧУ ввести URL его сайта и получить список ключевых слов
  → быстро, за 1-2 минуты
  → в понятном формате

ЧТОБЫ использовать эти слова на своём сайте
  → и подняться в поиске
```

---

## ⚡ QUICK START

```bash
cd ~/Desktop/Parsing\ seo
npm run dev   # localhost:3000
```

**Production:** https://parsing-seo.vercel.app
**GitHub:** https://github.com/Velheorius1/parsing-seo
**Supabase:** https://supabase.com/dashboard/project/oaoehczbycrabkprazts

---

## 🤖 АВТОНОМНЫЙ РЕЖИМ

### Claude ДЕЛАЕТ сам:
- ✅ Создание/редактирование файлов
- ✅ npm install
- ✅ Git (add, commit, push в feature-ветки)
- ✅ Выбор технических решений
- ✅ Исправление ошибок
- ✅ Запуск тестов

### Claude СПРАШИВАЕТ только:
- ❓ Удаление из production БД
- ❓ Push в main
- ❓ Платные API (Apify)
- ❓ Изменение .env

### Стиль работы
```
ДЕЛАЙ, НЕ СПРАШИВАЙ.
Ошибся → исправь сам.
Готово → коммить + PR ссылка.
```

**После каждой задачи:**
```
✅ PR: https://github.com/Velheorius1/parsing-seo/compare/main...feat/xxx
```

---

## 🛠 ТЕХНИЧЕСКИЙ СТЕК

- **Framework:** Next.js 14 (App Router)
- **Language:** TypeScript (strict)
- **Styling:** Tailwind CSS
- **State:** Zustand
- **Validation:** Zod
- **DB:** Supabase (PostgreSQL)
- **Parsing:** cheerio, axios, bottleneck
- **Hosting:** Vercel

### API для Google
- **Google Suggest:** `https://suggestqueries.google.com/complete/search?client=firefox&q={query}&hl=ru`
- **Google SERP:** Apify `apify/google-search-scraper` или SerpAPI

---

## Ключевые файлы
> Читай напрямую через Read/Grep. НЕ запускай Explore агентов.

| Файл | Что там |
|------|---------|
| `crawler/config/sources.yaml` | **Конфиг всех источников** (добавь строку = новый источник) |
| `crawler/main.py` | Entrypoint crawler (CLI + cron) |
| `crawler/core/runner.py` | Загрузка YAML, dispatch адаптеров, asyncio.gather |
| `crawler/core/models.py` | Pydantic: SourceConfig, RawTender, AdapterType |
| `crawler/core/db.py` | Supabase upsert (service_role, batch 500) |
| `crawler/adapters/api.py` | API адаптер (httpx, pagination, field_map) |
| `crawler/adapters/html.py` | HTML адаптер (BS4, CSS selectors) |
| `crawler/adapters/spa.py` | SPA адаптер (Playwright headless) |
| `crawler/adapters/telegram_adapter.py` | Telegram адаптер (Telethon) |
| `src/lib/supabase/tenders.ts` | queryTenders + saveTenders (TS frontend) |
| `src/lib/store/tenderStore.ts` | Zustand (GET + refresh) |
| `src/app/api/tenders/route.ts` | API (GET from Supabase, POST refresh) |

## 📁 СТРУКТУРА (актуальная)

```
src/
├── app/
│   ├── layout.tsx              # Root + QueryProvider
│   ├── page.tsx                # Dashboard (+ навигация на /tenders)
│   ├── tenders/page.tsx        # Мониторинг тендеров (dark theme)
│   └── api/
│       ├── keywords/route.ts   # GET из БД
│       ├── parse/route.ts      # POST парсинг URL
│       ├── tenders/route.ts    # POST поиск + GET сохранённые
│       └── suggestions/yandex/ # POST сбор подсказок
├── components/
│   ├── parsing/
│   │   ├── KeywordCollector.tsx
│   │   ├── KeywordTable.tsx
│   │   └── SiteAnalyzer.tsx
│   ├── tenders/
│   │   ├── TenderKeywords.tsx  # Чипы выбора ключевых слов
│   │   └── TenderTable.tsx     # Таблица результатов
│   └── export/
│       └── ExportButton.tsx
├── lib/
│   ├── parsers/
│   │   ├── yandexSuggest.ts
│   │   ├── siteParser.ts
│   │   └── tenderParser.ts     # UZEX API (прямой REST)
│   ├── supabase/
│   │   ├── client.ts
│   │   ├── keywords.ts
│   │   ├── parsedPages.ts
│   │   └── tenders.ts          # saveTenders, getTenders
│   ├── store/
│   │   ├── keywordStore.ts
│   │   └── tenderStore.ts      # Zustand + 27 ключевых слов
│   └── utils/
│       ├── rateLimiter.ts      # + tenderLimiter
│       └── csvExport.ts
└── types/
    └── parsing.ts              # + Tender, TenderSearchResult
```

---

## ⚡ КОМАНДЫ

```bash
npm run dev      # localhost:3000
npm run build    # Проверка сборки
npm run lint     # ESLint
```

---

## 🔐 ДОСТУПЫ (.env.local)

```bash
NEXT_PUBLIC_SUPABASE_URL=https://oaoehczbycrabkprazts.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=...
APIFY_API_TOKEN=...  # Для будущего парсинга
```

---

## ✍️ СТИЛЬ КОДА

| Что | Как |
|-----|-----|
| Комментарии | Русский |
| Переменные | camelCase, английский |
| Компоненты | PascalCase |
| Константы | UPPER_SNAKE_CASE |
| Commits | Английский, Conventional |

```bash
feat: add site parser
fix: rate limit for cheerio
```

---

## 🚨 RATE LIMITING (обязательно)

```typescript
// Всегда через limiter!
import { yandexLimiter, siteLimiter } from '@/lib/utils/rateLimiter';

// Yandex Suggest: 1 req/sec
// Site parsing: 1 req/2sec
```

---

## 🔒 БЕЗОПАСНОСТЬ

```
🚫 Не коммитить .env
🚫 Не парсить без rate limiting
🚫 Не пушить в main напрямую
```

---

## 💡 ЗАМЕТКИ ДЛЯ CLAUDE

```
КРИТИЧЕСКИ ВАЖНО:

1. ЭТО ПРОДУКТ — смотри PRODUCT.md
2. ЛЮБОЙ URL — не фиксированный список
3. НЕ ЛОМАЙ работающее — расширяй
4. Я не программист — объясняй просто
5. PR ссылка после каждой задачи
```

---

## 📚 ДОПОЛНИТЕЛЬНО

Полное продуктовое видение, JTBD, сегменты, user flows → **PRODUCT.md**

---

*Версия: 3.0*
*Обновлено: 25 февраля 2026*
