// Типы данных для парсинга SEO

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
  collectedAt: Date;
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

// Ответ Yandex Suggest API: ["запрос", ["подсказка1", "подсказка2", ...]]
export type YandexSuggestResponse = [string, string[]];

// Тендер с площадки (bicotender, etender, xt-xarid)
export interface Tender {
  id: string;
  externalId: string;       // ID на площадке (#251675270)
  title: string;            // Название тендера
  organization: string;     // Заказчик
  price: number | null;     // Сумма в UZS
  priceFormatted: string;   // "2 688 000 UZS"
  currency: string;         // UZS
  deadline: string | null;  // Дедлайн
  dateStart: string | null; // Дата начала
  dateEnd: string | null;   // Дата окончания
  region: string;           // Регион
  categories: string[];     // Категории (полиграфия, упаковка)
  source: string;           // bicotender / etender / xt-xarid
  sourceUrl: string;        // Ссылка на тендер
  status: 'active' | 'closed' | 'cancelled' | 'completed';
  matchedKeywords: string[]; // По каким ключам найден
  collectedAt: Date;
  winner?: string | null;       // Победитель тендера
  winningPrice?: number | null; // Цена победителя
  resultDate?: string | null;   // Дата подведения итогов
  groupId?: string | null;      // Группа связанных тендеров
  daysLeft?: number | null;     // Дней до дедлайна
}

// Избранный тендер
export interface TenderFavorite {
  id: string;
  tenderId: string;
  color: 'red' | 'orange' | 'yellow' | 'green' | 'blue' | 'purple';
  note: string;
  createdAt: Date;
}

// Параметры поиска тендеров
export interface TenderSearchParams {
  keywords: string[];       // Ключевые слова для поиска
  source?: string;          // Фильтр по площадке
  minPrice?: number;
  maxPrice?: number;
  region?: string;
  excludeKeywords?: string[];  // Исключить тендеры с этими словами
  category?: string;           // Фильтр по категории
  deadlineBefore?: string;     // Дедлайн до даты
  deadlineAfter?: string;      // Дедлайн после даты
}

// Результат поиска тендеров
export interface TenderSearchResult {
  tenders: Tender[];
  total: number;
  source: string;
  keyword: string;
  page: number;
}
