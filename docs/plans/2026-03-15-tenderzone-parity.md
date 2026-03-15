# TenderZone Parity — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Достичь функционального паритета с TenderZone (tzone.uz) — добавить расширенные фильтры, экспорт, аналитику, избранное и "осталось X дней" в наш Next.js дашборд тендеров.

**Architecture:** Все данные уже в Supabase (таблица `tenders`). Добавляем: (1) новые API endpoints для аналитики/экспорта, (2) расширенные фильтры в Zustand store + UI, (3) новые компоненты (аналитика, избранное). Новая таблица `tender_favorites` для избранного. Стиль — dark theme (gray-950), акцент amber-400/500.

**Tech Stack:** Next.js 14 App Router, TypeScript, Tailwind CSS, Zustand, Supabase (PostgreSQL), Zod, xlsx (для Excel экспорта)

**Текущий стек файлов:**
- Page: `src/app/tenders/page.tsx`
- Components: `src/components/tenders/TenderTable.tsx`, `TenderKeywords.tsx`
- API: `src/app/api/tenders/route.ts`
- Store: `src/lib/store/tenderStore.ts`
- Types: `src/types/parsing.ts`
- DB: `src/lib/supabase/tenders.ts`

---

## Task 1: Тип Tender — добавить новые поля

**Files:**
- Modify: `src/types/parsing.ts`

**Что делаем:** Добавляем поля `winner`, `winningPrice`, `resultDate`, `groupId`, `daysLeft` в тип Tender. Добавляем тип `TenderFavorite`. Расширяем `status` на `'completed'`.

**Код:**

```typescript
// В interface Tender добавить:
status: 'active' | 'closed' | 'cancelled' | 'completed';
winner?: string | null;
winningPrice?: number | null;
resultDate?: string | null;
groupId?: string | null;
daysLeft?: number | null; // вычисляется из deadline

// Новый интерфейс:
export interface TenderFavorite {
  id: string;
  tenderId: string;
  color: 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple';
  note: string;
  createdAt: Date;
}

// Расширить TenderSearchParams:
export interface TenderSearchParams {
  keywords: string[];
  excludeKeywords?: string[];
  source?: string;
  status?: string;
  minPrice?: number;
  maxPrice?: number;
  region?: string;
  category?: string;
  deadlineBefore?: string;
  deadlineAfter?: string;
}
```

**Commit:** `feat(types): extend Tender with winner, favorites, advanced search params`

---

## Task 2: "Осталось X дней" — вычисление и отображение

**Files:**
- Modify: `src/components/tenders/TenderTable.tsx`

**Что делаем:** Добавляем функцию `calcDaysLeft(deadline)` и бейдж "Осталось X дней" с цветовой кодировкой (красный <2д, оранжевый <5д, зелёный >5д). Заменяем сырой deadline на человекочитаемый формат.

**Код:**

```typescript
function calcDaysLeft(deadline: string | null): number | null {
  if (!deadline) return null;
  const patterns = [
    /(\d{4})-(\d{2})-(\d{2})/,           // 2026-03-20
    /(\d{2})\.(\d{2})\.(\d{4})/,           // 20.03.2026
  ];
  for (const p of patterns) {
    const m = deadline.match(p);
    if (m) {
      const dateStr = p === patterns[0]
        ? `${m[1]}-${m[2]}-${m[3]}`
        : `${m[3]}-${m[2]}-${m[1]}`;
      const diff = Math.ceil((new Date(dateStr).getTime() - Date.now()) / 86400000);
      return diff;
    }
  }
  return null;
}

function DeadlineBadge({ deadline }: { deadline: string | null }) {
  const days = calcDaysLeft(deadline);
  if (days === null) return <span className="text-gray-500">—</span>;
  if (days < 0) return <span className="text-gray-500 line-through">Истёк</span>;

  const color = days <= 1 ? 'text-red-400 bg-red-500/10'
    : days <= 3 ? 'text-orange-400 bg-orange-500/10'
    : days <= 7 ? 'text-yellow-400 bg-yellow-500/10'
    : 'text-green-400 bg-green-500/10';

  return (
    <div className="flex flex-col gap-0.5">
      <span className={`text-xs px-1.5 py-0.5 rounded ${color}`}>
        {days === 0 ? 'Сегодня' : days === 1 ? 'Завтра' : `${days}д`}
      </span>
      <span className="text-[10px] text-gray-500">{deadline}</span>
    </div>
  );
}
```

Заменить в `TenderRow` колонку дедлайна на `<DeadlineBadge deadline={tender.deadline} />`.

**Commit:** `feat(ui): add "days left" badge with color coding to tender table`

---

## Task 3: Расширенные фильтры — Store + UI

**Files:**
- Modify: `src/lib/store/tenderStore.ts`
- Create: `src/components/tenders/TenderFilters.tsx`
- Modify: `src/app/tenders/page.tsx`

**Что делаем:** Панель расширенных фильтров (как у TenderZone): регион, категория, диапазон цен, статус, площадка, исключающие слова. Сворачиваемая панель "Расширенный поиск".

**Store — новые поля и действия:**

```typescript
// Добавить в TenderState:
filterStatus: string | null;
filterCategory: string | null;
excludeKeywords: string[];
showAdvancedFilters: boolean;

// Добавить действия:
setFilterStatus: (status: string | null) => void;
setFilterCategory: (category: string | null) => void;
setExcludeKeywords: (keywords: string[]) => void;
toggleAdvancedFilters: () => void;
resetFilters: () => void;
```

**TenderFilters.tsx — компонент:**

Сворачиваемая панель с:
- Регион (кнопки-чипы из уникальных регионов в данных)
- Категория (кнопки-чипы)
- Диапазон цен (два input: от / до)
- Статус (active / closed / completed)
- Площадка (уже есть SourceFilter, перенести сюда)
- Исключающие слова (input + чипы)
- Кнопка "Сбросить фильтры"

Стиль: `bg-gray-900 rounded-xl p-4 border border-gray-800`

**Commit:** `feat(filters): advanced search panel with region, category, price range, status`

---

## Task 4: API расширенных фильтров

**Files:**
- Modify: `src/app/api/tenders/route.ts`
- Modify: `src/lib/supabase/tenders.ts`

**Что делаем:** GET endpoint принимает новые query params: `region`, `category`, `status`, `minPrice`, `maxPrice`, `excludeKeywords`, `deadlineBefore`. Supabase query фильтрует серверно.

**Новые query params:**

```
?keywords=упаковка&region=Ташкент&category=Бумага&minPrice=1000000&maxPrice=50000000&status=active&exclude=мебель,еда
```

**В queryTenders добавить фильтры:**

```typescript
if (region) query = query.eq('region', region);
if (status) query = query.eq('status', status);
if (minPrice) query = query.gte('price', minPrice);
if (maxPrice) query = query.lte('price', maxPrice);
```

**Commit:** `feat(api): server-side filtering by region, price, status, category`

---

## Task 5: Экспорт Excel

**Files:**
- Create: `src/app/api/tenders/export/route.ts`
- Create: `src/components/tenders/ExportButton.tsx`
- Modify: `src/app/tenders/page.tsx`

**Что делаем:** Кнопка "Экспорт Excel" скачивает .xlsx с текущими фильтрами. Используем библиотеку `xlsx` (SheetJS). Колонки: Название, Заказчик, Сумма, Валюта, Дедлайн, Осталось дней, Статус, Регион, Площадка, Ссылка, Победитель, Цена победителя.

**API endpoint `/api/tenders/export`:**

```typescript
import * as XLSX from 'xlsx';

export async function GET(request: NextRequest) {
  // Те же фильтры что и /api/tenders GET
  // Формирует workbook, возвращает как application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.json_to_sheet(rows);
  XLSX.utils.book_append_sheet(wb, ws, 'Тендеры');
  const buf = XLSX.write(wb, { type: 'buffer', bookType: 'xlsx' });
  return new Response(buf, {
    headers: {
      'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      'Content-Disposition': `attachment; filename="tenders_${date}.xlsx"`,
    },
  });
}
```

**ExportButton.tsx:**

```typescript
// Кнопка с иконкой Excel, строит URL с текущими фильтрами из store
// window.open(`/api/tenders/export?${params}`)
```

**Зависимость:** `npm install xlsx`

**Commit:** `feat(export): Excel export with current filters`

---

## Task 6: Аналитика заказчиков

**Files:**
- Create: `src/app/api/tenders/analytics/route.ts`
- Create: `src/components/tenders/TenderAnalytics.tsx`
- Modify: `src/app/tenders/page.tsx`

**Что делаем:** Секция "Аналитика" под таблицей тендеров:
1. **Топ-10 заказчиков** — кто больше всего тендеров размещает (bar chart или таблица)
2. **Средний % снижения цены** — `(cost - result_cost) / cost * 100` из CivilContracts
3. **Статистика по регионам** — pie chart или таблица (количество + сумма)
4. **Статистика по категориям** — аналогично

**API `/api/tenders/analytics`:**

```typescript
// SELECT organization, COUNT(*) as count, SUM(price) as total_sum
// FROM tenders WHERE status = 'active'
// GROUP BY organization ORDER BY count DESC LIMIT 10

// SELECT region, COUNT(*) as count, SUM(price) as total_sum
// FROM tenders GROUP BY region ORDER BY count DESC

// SELECT AVG((price - winning_price) / NULLIF(price, 0) * 100) as avg_discount
// FROM tenders WHERE winning_price IS NOT NULL AND price > 0
```

**Компонент:**

Табы: "Заказчики" | "Регионы" | "Категории" | "Снижение цен"

Каждый таб — компактная таблица с количеством тендеров и суммой. Стиль dark theme.

**Commit:** `feat(analytics): buyer analytics, region stats, price reduction %`

---

## Task 7: Избранное (Favorites)

**Files:**
- Create: `supabase/migrations/006_favorites.sql`
- Create: `src/lib/supabase/favorites.ts`
- Create: `src/app/api/tenders/favorites/route.ts`
- Create: `src/components/tenders/FavoriteButton.tsx`
- Modify: `src/components/tenders/TenderTable.tsx`

**Что делаем:** Звёздочка на каждом тендере. Клик = добавить в избранное (с выбором цвета). Заметка к тендеру. Фильтр "Только избранное". Данные в Supabase таблице `tender_favorites`.

**Миграция:**

```sql
CREATE TABLE IF NOT EXISTS tender_favorites (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  tender_id UUID NOT NULL REFERENCES tenders(id) ON DELETE CASCADE,
  color TEXT DEFAULT 'yellow' CHECK (color IN ('red','orange','yellow','green','blue','purple')),
  note TEXT DEFAULT '',
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(tender_id)
);
ALTER TABLE tender_favorites ENABLE ROW LEVEL SECURITY;
CREATE POLICY "favorites_select" ON tender_favorites FOR SELECT USING (true);
CREATE POLICY "favorites_insert" ON tender_favorites FOR INSERT WITH CHECK (true);
CREATE POLICY "favorites_update" ON tender_favorites FOR UPDATE USING (true) WITH CHECK (true);
CREATE POLICY "favorites_delete" ON tender_favorites FOR DELETE USING (true);
```

**FavoriteButton:**

```typescript
// Звёздочка: пустая (не в избранном) / заполненная цветом (в избранном)
// Клик → toggle. Долгий клик → выбор цвета + заметка (попап)
```

**В TenderRow:** добавить `<FavoriteButton tenderId={tender.id} />` первой колонкой.

**Commit:** `feat(favorites): star button with color tags and notes`

---

## Task 8: Результаты тендеров в UI

**Files:**
- Modify: `src/components/tenders/TenderTable.tsx`

**Что делаем:** Для тендеров со статусом `completed` — показываем победителя и цену. Бейдж "Завершён" + строка с победителем. StatusBadge расширяем на `completed`.

**Код:**

```typescript
// В StatusBadge добавить:
completed: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
// В labels:
completed: 'Завершён',

// В TenderRow, после title, если есть winner:
{tender.winner && (
  <div className="text-[10px] text-blue-400 mt-1">
    Победитель: {tender.winner}
    {tender.winningPrice && ` | ${formatPrice(tender.winningPrice, tender.currency)}`}
  </div>
)}
```

**Commit:** `feat(ui): show winner info for completed tenders`

---

## Task 9: Подсветка ключевых слов

**Files:**
- Modify: `src/components/tenders/TenderTable.tsx`

**Что делаем:** В title тендера подсвечиваем совпавшие ключевые слова жёлтым (как у TenderZone).

**Код:**

```typescript
function HighlightedTitle({ title, keywords }: { title: string; keywords: string[] }) {
  if (!keywords.length) return <>{title}</>;

  const pattern = keywords
    .map(kw => kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
    .join('|');
  const regex = new RegExp(`(${pattern})`, 'gi');
  const parts = title.split(regex);

  return (
    <>
      {parts.map((part, i) =>
        regex.test(part)
          ? <mark key={i} className="bg-amber-500/30 text-amber-300 rounded px-0.5">{part}</mark>
          : part
      )}
    </>
  );
}
```

**Commit:** `feat(ui): highlight matched keywords in tender titles`

---

## Task 10: Обновить page.tsx — собрать всё вместе

**Files:**
- Modify: `src/app/tenders/page.tsx`

**Что делаем:** Добавляем секции: TenderFilters (расширенный поиск), ExportButton, TenderAnalytics. Обновляем header со статистикой "93 площадки, 12000+ тендеров".

**Структура страницы:**

```
Header (название + статистика)
├── TenderKeywords (ключевые слова)
├── TenderFilters (расширенные фильтры, сворачиваемые)
├── Action bar (ExportButton + количество + сортировка)
├── TenderTable (результаты)
└── TenderAnalytics (аналитика, табы)
```

**Commit:** `feat(page): assemble all components — filters, export, analytics`

---

## Task 11: Деплой на VPS + Vercel

**Files:**
- Modify: `src/app/tenders/page.tsx` (финальные правки)

**Что делаем:**
1. `npm run build` — проверить сборку
2. Git push (Vercel авто-деплой или `vercel --prod`)
3. SSH на VPS: `cd /opt/parsing-seo && git pull && python3 -m crawler --dry-run` — проверить crawler
4. Открыть в браузере и проверить все фичи

**Commit:** `chore: build verification before deploy`

---

## Порядок выполнения

```
Task 1 (типы)           → фундамент
Task 2 (дни)            → быстрая победа, видимый результат
Task 3 (фильтры UI)     → ← зависит от Task 1
Task 4 (фильтры API)    → ← зависит от Task 3
Task 5 (Excel)          → независимый
Task 6 (аналитика)      → независимый
Task 7 (избранное)      → независимый
Task 8 (результаты UI)  → ← зависит от Task 1
Task 9 (подсветка)      → независимый
Task 10 (сборка)        → ← зависит от всех
Task 11 (деплой)        → финал
```

**Параллельные группы:**
- Group A: Tasks 1→2→3→4→8 (типы → UI → фильтры)
- Group B: Tasks 5, 6, 7, 9 (независимые)
- Group C: Task 10→11 (сборка → деплой)

**Оценка: 8-10 коммитов, ~1500-2000 строк кода.**
