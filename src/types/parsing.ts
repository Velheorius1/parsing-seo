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
