# CLAUDE.md — Parsing SEO

> Читай вместе с PRODUCT.md для полного контекста продукта.

---

## 📍 ТЕКУЩЕЕ СОСТОЯНИЕ (ЧИТАЙ ПЕРВЫМ)

### ✅ Работает (НЕ ЛОМАТЬ)
- Yandex Suggest парсер (`src/lib/parsers/yandexSuggest.ts`)
- Rate limiting 1 req/sec (`src/lib/utils/rateLimiter.ts`)
- API endpoint `/api/suggestions/yandex`
- Zustand store для keywords
- UI: KeywordCollector, KeywordTable, ExportButton
- CSV экспорт с BOM для кириллицы
- Supabase интеграция (keywords сохраняются)
- Парсинг сайта по URL (`src/lib/parsers/siteParser.ts`, `/api/parse`)
- UI: SiteAnalyzer на главной
- **Production:** https://parsing-seo.vercel.app

### ⚠️ ВАЖНО: Фокус на Google, не Yandex
90% трафика в Узбекистане — Google. Yandex Suggest оставляем как опцию, но приоритет — Google.

### 🎯 Следующие работы (по приоритету)
1. **Google Suggest** — подсказки Google вместо/рядом с Yandex
2. **Google SERP** — топ-10 по запросу (кто ранжируется)
3. **Парсинг сайта по URL** — ✅ сделано (одна страница)

### 🚫 НЕ ДЕЛАТЬ СЕЙЧАС
- Авторизация
- Мультипроекты
- Тарифы и оплата

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

## 📁 СТРУКТУРА (актуальная)

```
src/
├── app/
│   ├── layout.tsx              # Root + QueryProvider
│   ├── page.tsx                # Dashboard
│   └── api/
│       ├── keywords/route.ts   # GET из БД
│       ├── parse/route.ts      # POST парсинг URL
│       └── suggestions/yandex/ # POST сбор подсказок
├── components/
│   ├── parsing/
│   │   ├── KeywordCollector.tsx
│   │   ├── KeywordTable.tsx
│   │   └── SiteAnalyzer.tsx    # Анализ сайта по URL
│   └── export/
│       └── ExportButton.tsx
├── lib/
│   ├── parsers/
│   │   ├── yandexSuggest.ts    # ✅ Работает
│   │   └── siteParser.ts      # ✅ Работает
│   ├── supabase/
│   │   ├── client.ts
│   │   ├── keywords.ts
│   │   └── parsedPages.ts
│   ├── store/
│   │   └── keywordStore.ts
│   └── utils/
│       ├── rateLimiter.ts      # ✅ Работает
│       └── csvExport.ts        # ✅ Работает
└── types/
    └── parsing.ts
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

*Версия: 2.0*
*Обновлено: Январь 2026*
