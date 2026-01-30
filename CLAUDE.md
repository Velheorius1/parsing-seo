# CLAUDE.md — Проект Parsing SEO (анализ конкурентов и сбор семантики)

> Этот файл автоматически загружается Claude Code при старте сессии.
> Основан на официальных best practices Anthropic (апрель 2025).

---

## ⚡ QUICK START (читай первым)

```bash
# Локальный путь проекта
cd ~/Desktop/Parsing\ seo
# или
cd "/Users/doniersalahutdinov/Рабочий стол/Parsing seo"

# Клонировать (если ещё не создан)
git clone https://github.com/Velheorius1/parsing-seo-seo.git
cd parsing-seo
npm install
cp .env.example .env.local  # Заполнить API ключи
npm run dev                  # localhost:3000
```

**Claude Code — базовые команды:**
```
claude                       # Запустить сессию
/init                       # Сгенерировать CLAUDE.md автоматически
/clear                      # Очистить контекст (делай часто!)
/permissions                # Настроить разрешения
Escape                      # Прервать Claude
Escape + Escape             # Вернуться в историю
```

**Главное правило:** После завершения задачи Claude ВСЕГДА выводит:
```
✅ PR готов: https://github.com/Velheorius1/parsing-seo/compare/main...{{branch}}
```

---

## 📋 О ПРОЕКТЕ

**Название:** Parsing SEO
**Локальный путь:** `~/Desktop/Parsing seo`
**Репозиторий:** https://github.com/Velheorius1/parsing-seo
**Production URL:** https://parsing-seo.vercel.app
**Описание:** SEO-инструмент для анализа конкурентов, сбора ключевых слов и исследования поисковой выдачи

### Цели проекта
1. Парсинг сайтов конкурентов (micros.uz, print.uz) — URL, title, H1, meta
2. Сбор поисковых подсказок Google и Yandex
3. Анализ SERP (топ-10 результатов)
4. Парсинг Wordstat для оценки частотности
5. Выгрузка данных в удобном формате (CSV, JSON)

---

## 🛠 ТЕХНИЧЕСКИЙ СТЕК

### Core
- **Framework:** Next.js 14+ (App Router)
- **Language:** TypeScript (strict mode)
- **Styling:** Tailwind CSS
- **Package Manager:** npm

### Парсинг и API
```bash
# Apify SDK для управления акторами
npm install apify-client

# HTTP клиент для API запросов
npm install axios

# Парсинг HTML
npm install cheerio

# Работа с CSV
npm install papaparse
npm install @types/papaparse -D

# Rate limiting для API
npm install bottleneck
```

### Рекомендуемые библиотеки
```bash
# Валидация данных
npm install zod

# State management
npm install zustand

# UI компоненты
npx shadcn@latest init
```

### Backend/Infrastructure
- **Database:** Supabase (PostgreSQL) — хранение результатов парсинга
- **Hosting:** Vercel
- **Analytics:** PostHog
- **Parsing:** Apify (акторы для веб-скрейпинга)

---

## 🔗 ВНЕШНИЕ API И СЕРВИСЫ

### 1. Apify Actors (платный, есть free tier)

**Console:** https://console.apify.com/
**Docs:** https://docs.apify.com/

```typescript
// Инициализация клиента
import { ApifyClient } from 'apify-client';

const client = new ApifyClient({
  token: process.env.APIFY_API_TOKEN,
});
```

**Используемые акторы:**

| Actor | Назначение | Actor ID |
|-------|------------|----------|
| Website Content Crawler | Парсинг сайтов конкурентов | `apify/website-content-crawler` |
| Google Search Results Scraper | SERP анализ | `apify/google-search-scraper` |
| Google Search Suggestions | Подсказки Google | `apify/google-suggest-scraper` |

### 2. Yandex Suggest API (бесплатный)

```typescript
// Эндпоинт
const YANDEX_SUGGEST_URL = 'https://suggest.yandex.ru/suggest-ff.cgi';

// Пример запроса
async function getYandexSuggestions(query: string): Promise<string[]> {
  const response = await fetch(
    `${YANDEX_SUGGEST_URL}?part=${encodeURIComponent(query)}&uil=ru&v=4&sn=5&lr=10335`
  );
  const data = await response.json();
  return data[1] || []; // Массив подсказок
}

// lr=10335 — Ташкент
// lr=213 — Москва
```

### 3. Yandex Wordstat (через браузерную автоматизацию)

```typescript
// Требует авторизации в Яндексе
// Используем Puppeteer или Apify actor
// Actor: "jancurn/wordstat-scraper" или кастомный

// Альтернатива: сервисы с API
// - keys.so
// - spywords.ru
// - serpstat.com
```

### 4. Google Search (через Apify или SerpAPI)

```typescript
// Вариант 1: Apify (дешевле)
const run = await client.actor("apify/google-search-scraper").call({
  queries: ["типография Ташкент", "печать визиток"],
  maxPagesPerQuery: 1,
  resultsPerPage: 10,
  countryCode: "uz",
  languageCode: "ru",
});

// Вариант 2: SerpAPI (надёжнее, дороже)
// https://serpapi.com/
```

---

## 📁 СТРУКТУРА ПРОЕКТА

```
src/
├── app/
│   ├── layout.tsx
│   ├── page.tsx                    # Dashboard
│   ├── competitors/                # Анализ конкурентов
│   │   └── page.tsx
│   ├── keywords/                   # Сбор ключевых слов
│   │   └── page.tsx
│   ├── serp/                       # SERP анализ
│   │   └── page.tsx
│   └── api/
│       ├── parse/
│       │   └── route.ts            # Запуск парсинга
│       ├── suggestions/
│       │   ├── google/route.ts
│       │   └── yandex/route.ts
│       ├── serp/
│       │   └── route.ts
│       └── wordstat/
│           └── route.ts
├── components/
│   ├── ui/                         # shadcn компоненты
│   ├── parsing/
│   │   ├── CompetitorTable.tsx
│   │   ├── KeywordList.tsx
│   │   └── SerpResults.tsx
│   └── export/
│       └── ExportButton.tsx        # Экспорт в CSV/JSON
├── lib/
│   ├── apify/
│   │   ├── client.ts               # Apify клиент
│   │   └── actors.ts               # Конфиг акторов
│   ├── parsers/
│   │   ├── yandexSuggest.ts
│   │   ├── googleSuggest.ts
│   │   └── wordstat.ts
│   ├── utils/
│   │   ├── rateLimiter.ts          # Rate limiting
│   │   └── csvExport.ts
│   └── supabase/
│       └── client.ts
├── types/
│   ├── parsing.ts                  # Типы для парсинга
│   └── supabase.ts
└── hooks/
    └── useParsingJob.ts            # Хук для отслеживания задач
```

---

## ⚡ КОМАНДЫ

```bash
# Разработка
npm run dev          # Запуск dev сервера (localhost:3000)
npm run build        # Production сборка
npm run start        # Запуск production сборки

# Качество кода
npm run lint         # ESLint проверка
npm run typecheck    # TypeScript проверка (tsc --noEmit)
npm test             # Запуск тестов

# Парсинг (CLI скрипты)
npm run parse:competitors   # Парсинг конкурентов
npm run parse:suggestions   # Сбор подсказок
npm run parse:serp          # Анализ SERP
```

---

## 📊 СХЕМА ДАННЫХ (Supabase)

### Таблица: `competitors`
```sql
CREATE TABLE competitors (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  domain TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  title TEXT,
  h1 TEXT,
  meta_description TEXT,
  meta_keywords TEXT[],
  parsed_at TIMESTAMPTZ DEFAULT NOW(),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_competitors_domain ON competitors(domain);
```

### Таблица: `keywords`
```sql
CREATE TABLE keywords (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  keyword TEXT NOT NULL,
  source TEXT NOT NULL, -- 'google_suggest', 'yandex_suggest', 'wordstat', 'competitor'
  search_volume INTEGER, -- Из Wordstat
  competition TEXT, -- 'low', 'medium', 'high'
  base_query TEXT, -- Исходный запрос для подсказок
  created_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(keyword, source)
);

CREATE INDEX idx_keywords_keyword ON keywords(keyword);
CREATE INDEX idx_keywords_source ON keywords(source);
```

### Таблица: `serp_results`
```sql
CREATE TABLE serp_results (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  query TEXT NOT NULL,
  position INTEGER NOT NULL,
  url TEXT NOT NULL,
  title TEXT,
  description TEXT,
  domain TEXT,
  parsed_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(query, position, parsed_at::DATE)
);

CREATE INDEX idx_serp_query ON serp_results(query);
```

### Таблица: `parsing_jobs`
```sql
CREATE TABLE parsing_jobs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  type TEXT NOT NULL, -- 'competitors', 'suggestions', 'serp', 'wordstat'
  status TEXT DEFAULT 'pending', -- 'pending', 'running', 'completed', 'failed'
  input JSONB,
  output JSONB,
  error TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 🎯 ЦЕЛЕВЫЕ ЗАПРОСЫ ДЛЯ ПАРСИНГА

### Базовые запросы (seed keywords)
```typescript
const SEED_KEYWORDS = [
  // Упаковка
  'картонная упаковка',
  'упаковка на заказ',
  'коробки с логотипом',
  'подарочная упаковка',
  'крафт упаковка',
  
  // Полиграфия
  'типография Ташкент',
  'печать визиток',
  'печать буклетов',
  'печать каталогов',
  'широкоформатная печать',
  
  // Специфичные
  'упаковка для еды',
  'упаковка для косметики',
  'бумажные пакеты',
  'этикетки на заказ',
];
```

### Конкуренты для анализа
```typescript
const COMPETITORS = [
  { name: 'Micros', domain: 'micros.uz', startUrl: 'https://micros.uz/' },
  { name: 'Print.uz', domain: 'print.uz', startUrl: 'https://print.uz/' },
  // Добавить больше по необходимости
];
```

---

## 🔐 ДОСТУПЫ И ИНТЕГРАЦИИ

### Environment Variables (.env.local)
```bash
# Supabase
NEXT_PUBLIC_SUPABASE_URL=https://oaoehczbycrabkprazts.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=your-anon-key
SUPABASE_SERVICE_ROLE_KEY=your-service-key  # Для серверных операций

# Apify
APIFY_API_TOKEN=your-apify-token

# PostHog Analytics
NEXT_PUBLIC_POSTHOG_KEY=your-posthog-key
NEXT_PUBLIC_POSTHOG_HOST=https://app.posthog.com

# Опционально: SerpAPI (альтернатива Apify для Google)
SERPAPI_KEY=your-serpapi-key
```

### Где получить ключи

| Сервис | URL | Free tier |
|--------|-----|-----------|
| Apify | https://console.apify.com/sign-up | $5/мес бесплатно |
| Supabase | https://supabase.com/dashboard | 500MB бесплатно |
| SerpAPI | https://serpapi.com/ | 100 запросов/мес |
| PostHog | https://posthog.com/ | 1M событий бесплатно |

### Dashboard Links
- **Vercel:** https://vercel.com/velheorius1/parsing-seo
- **Supabase:** https://supabase.com/dashboard/project/oaoehczbycrabkprazts
- **GitHub:** https://github.com/Velheorius1/parsing-seo
- **Apify:** https://console.apify.com/

---

## ✍️ СТИЛЬ КОДА

### Naming Conventions
- **Переменные/функции:** camelCase (`parseCompetitor`, `keywordData`)
- **Компоненты:** PascalCase (`KeywordTable`, `SerpChart`)
- **Константы:** UPPER_SNAKE_CASE (`SEED_KEYWORDS`, `API_URL`)
- **Типы:** PascalCase с суффиксом (`KeywordResult`, `ParseJob`)

### Язык
- **Комментарии в коде:** русский
- **Названия переменных:** английский
- **Git commits:** английский (Conventional Commits)

### TypeScript типы для проекта
```typescript
// src/types/parsing.ts

export interface CompetitorPage {
  url: string;
  domain: string;
  title: string | null;
  h1: string | null;
  metaDescription: string | null;
  metaKeywords: string[];
  parsedAt: Date;
}

export interface Keyword {
  keyword: string;
  source: 'google_suggest' | 'yandex_suggest' | 'wordstat' | 'competitor';
  searchVolume?: number;
  competition?: 'low' | 'medium' | 'high';
  baseQuery?: string;
}

export interface SerpResult {
  query: string;
  position: number;
  url: string;
  title: string;
  description: string;
  domain: string;
}

export interface ParsingJob {
  id: string;
  type: 'competitors' | 'suggestions' | 'serp' | 'wordstat';
  status: 'pending' | 'running' | 'completed' | 'failed';
  input: Record<string, unknown>;
  output?: Record<string, unknown>;
  error?: string;
  startedAt?: Date;
  completedAt?: Date;
}
```

---

## 🔀 GIT WORKFLOW

### Branch Naming
```
feat/competitor-parser       # Новая функциональность
fix/yandex-suggest-encoding  # Исправление бага
chore/update-apify-client    # Обслуживание
refactor/optimize-parsing    # Рефакторинг
```

### Commit Convention (Conventional Commits)
```
feat: добавить парсер Yandex Suggest
fix: исправить кодировку в подсказках
chore: обновить Apify SDK
refactor: оптимизировать rate limiting
```

### PR Workflow
**ВАЖНО:** Claude Code ВСЕГДА выводит ссылку для мержа:
```
PR готов к мержу: https://github.com/Velheorius1/parsing-seo/compare/main...feat/competitor-parser
```

---

## 🤖 ИНСТРУКЦИИ ДЛЯ CLAUDE CODE

### Приоритеты
1. **Безопасность** — не превышать лимиты API, не спамить сервисы
2. **Качество** — типизация, обработка ошибок, rate limiting
3. **Полезность** — собирать релевантные данные

### Workflow: Explore → Plan → Code → Verify
```
1. EXPLORE: Изучи API документацию, проверь лимиты
2. PLAN: Опиши план из 3-5 шагов, подожди подтверждения
3. CODE: Реализуй с rate limiting и error handling
4. VERIFY: Тестируй на малых выборках сначала
5. COMMIT: Используй Conventional Commits
6. PR: Выведи ссылку на PR для мержа
```

### Extended Thinking
- `"think"` — базовый анализ
- `"think hard"` — глубокий анализ (для сложной логики парсинга)
- `"ultrathink"` — для архитектурных решений

### Запрещено без явного разрешения
```
❌ Запускать массовый парсинг без rate limiting
❌ Хранить API ключи в коде
❌ Парсить сайты без robots.txt проверки
❌ Превышать free tier лимиты API
❌ Пушить напрямую в main
```

### Разрешено автоматически
```
✅ Создавать новые файлы
✅ Тестировать API на единичных запросах
✅ Запускать тесты и линтеры
✅ Создавать PR
```

---

## 📝 ТИПИЧНЫЕ ЗАДАЧИ

### Добавить новый источник подсказок
```bash
# Claude Code выполнит:
1. Создаст src/lib/parsers/[source].ts
2. Добавит типы в src/types/parsing.ts
3. Создаст API route в src/app/api/suggestions/[source]/route.ts
4. Добавит rate limiting
5. Напишет тесты
```

### Парсинг нового конкурента
```bash
# Claude Code выполнит:
1. Добавит домен в COMPETITORS константу
2. Проверит robots.txt
3. Настроит Apify actor
4. Запустит тестовый парсинг (10 страниц)
5. Сохранит результаты в Supabase
```

### Экспорт данных в CSV
```typescript
// Уже реализовано в src/lib/utils/csvExport.ts
import { exportToCSV } from '@/lib/utils/csvExport';

const csv = exportToCSV(keywords, {
  columns: ['keyword', 'source', 'searchVolume'],
  filename: 'keywords-export.csv'
});
```

---

## 🚨 RATE LIMITING (КРИТИЧЕСКИ ВАЖНО)

### Лимиты API
```typescript
// src/lib/utils/rateLimiter.ts
import Bottleneck from 'bottleneck';

// Yandex Suggest — 1 запрос в секунду
export const yandexLimiter = new Bottleneck({
  minTime: 1000,
  maxConcurrent: 1,
});

// Google через Apify — по тарифу
export const apifyLimiter = new Bottleneck({
  minTime: 2000,
  maxConcurrent: 2,
});
```

### Использование
```typescript
// Всегда через limiter!
const suggestions = await yandexLimiter.schedule(() => 
  getYandexSuggestions(query)
);
```

---

## 🔧 CUSTOM SLASH COMMANDS

### `.claude/commands/parse-competitor.md`
```markdown
Парсить сайт конкурента: $ARGUMENTS

Шаги:
1. Проверь robots.txt сайта
2. Настрой Apify Website Content Crawler
3. Запусти на 10 страниц для теста
4. Сохрани результаты в Supabase
5. Выведи статистику (сколько страниц, keywords)
```

### `.claude/commands/collect-keywords.md`
```markdown
Собрать ключевые слова по теме: $ARGUMENTS

Шаги:
1. Получи подсказки Yandex Suggest
2. Получи подсказки Google Suggest
3. Удали дубликаты
4. Сохрани в Supabase
5. Экспортируй в CSV
```

---

## 🔒 БЕЗОПАСНОСТЬ

### Неснимаемые ограничения
```
🚫 Не коммитить API ключи в git
🚫 Не парсить без rate limiting
🚫 Не игнорировать robots.txt
🚫 Не хранить персональные данные без согласия
```

### .env.example
```bash
# Скопируй в .env.local и заполни
NEXT_PUBLIC_SUPABASE_URL=
NEXT_PUBLIC_SUPABASE_ANON_KEY=
SUPABASE_SERVICE_ROLE_KEY=
APIFY_API_TOKEN=
NEXT_PUBLIC_POSTHOG_KEY=
NEXT_PUBLIC_POSTHOG_HOST=
```

---

## 🆘 TROUBLESHOOTING

### Apify ошибки
```bash
# Проверить статус актора
npx apify actors call apify/website-content-crawler --input='{"startUrls":[{"url":"https://example.com"}],"maxPagesPerCrawl":1}'
```

### Yandex Suggest не отвечает
```bash
# Проверить вручную
curl "https://suggest.yandex.ru/suggest-ff.cgi?part=тест&uil=ru&v=4"
```

### Rate limit exceeded
```bash
# Увеличить задержку в rateLimiter.ts
# Или подождать сброса лимитов (обычно 1 час)
```

---

## 💡 ЗАМЕТКИ ДЛЯ CLAUDE

```
ПОМНИ:
- Я не программист, объясняй простым языком
- Всегда используй rate limiting для API
- Тестируй на малых выборках сначала (10 записей)
- Всегда показывай PR ссылку после завершения
- Комментарии на русском
- Сначала проверь robots.txt перед парсингом
```

---

## 🎯 JOBS TO BE DONE

### Основной Job
"Собрать полную семантику для SEO-продвижения полиграфического бизнеса без ручной работы"

### Под-Jobs
- "Понять какие страницы есть у конкурентов"
- "Найти все варианты поисковых запросов по теме"
- "Узнать кто в топе по целевым запросам"
- "Оценить частотность ключевых слов"

---

*Последнее обновление: Январь 2026*
*Версия: 1.0*
*Основано на: Anthropic Claude Code Best Practices (April 2025)*
### Инструкция для Claude
```
АВТОНОМНЫЙ РЕЖИМ ВКЛЮЧЁН.

ТЫ ДОЛЖЕН:
- Принимать решения самостоятельно, не спрашивая подтверждения
- Выбирать лучший подход сам, без вариантов "А или Б?"
- Исправлять ошибки сразу, не спрашивая "исправить?"
- Продолжать работу до полного завершения задачи
- Писать код сразу, без "хотите чтобы я написал?"

НЕ СПРАШИВАЙ:
- "Хотите продолжить?"
- "Какой вариант предпочитаете?"
- "Могу ли я создать файл?"
- "Подтвердите что мне сделать X"
- "Вы хотите чтобы я..."

ПРОСТО ДЕЛАЙ. Если что-то пойдёт не так — я скажу.

ИСКЛЮЧЕНИЯ (спроси только если):
- Удаление данных из production БД
- Изменение .env с секретами
- Действие стоит денег (API с оплатой за запрос)
- Пуш в main ветку