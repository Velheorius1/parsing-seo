import type { ParsedPage } from '@/lib/parsers/siteParser';

export interface SeoFieldAnalysis {
  text: string;
  matches: string[];
  missing: string[];
}

export interface SeoAnalysis {
  query: string;
  queryWords: string[];
  title: SeoFieldAnalysis;
  h1: SeoFieldAnalysis;
  description: SeoFieldAnalysis;
  score: number; // 0-100
  recommendations: string[];
}

const STOP_WORDS = new Set([
  'и', 'в', 'на', 'с', 'по', 'для', 'из', 'от', 'к', 'о', 'у', 'за', 'не',
  'но', 'а', 'или', 'что', 'как', 'это', 'при', 'так', 'все', 'его', 'уже',
  'the', 'a', 'an', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is',
  'and', 'or', 'not', 'but', 'as', 'it', 'from',
]);

function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .replace(/[^a-zа-яёa-z0-9\s]/gi, ' ')
    .split(/\s+/)
    .filter((w) => w.length > 1);
}

function getQueryWords(query: string): string[] {
  return tokenize(query).filter((w) => !STOP_WORDS.has(w));
}

function analyzeField(fieldText: string | null, queryWords: string[]): SeoFieldAnalysis {
  const text = fieldText || '';
  const lowerText = text.toLowerCase();
  const matches: string[] = [];
  const missing: string[] = [];

  for (const word of queryWords) {
    if (lowerText.includes(word)) {
      matches.push(word);
    } else {
      missing.push(word);
    }
  }

  return { text, matches, missing };
}

function calculateScore(
  title: SeoFieldAnalysis,
  h1: SeoFieldAnalysis,
  description: SeoFieldAnalysis,
  queryWords: string[],
): number {
  if (queryWords.length === 0) return 0;

  // Title — 40%, H1 — 35%, Description — 25%
  const titleScore = (title.matches.length / queryWords.length) * 40;
  const h1Score = (h1.matches.length / queryWords.length) * 35;
  const descScore = (description.matches.length / queryWords.length) * 25;

  return Math.round(titleScore + h1Score + descScore);
}

function generateRecommendations(
  title: SeoFieldAnalysis,
  h1: SeoFieldAnalysis,
  description: SeoFieldAnalysis,
): string[] {
  const recs: string[] = [];

  if (!title.text) {
    recs.push('Title отсутствует — добавьте title с ключевыми словами запроса');
  } else if (title.missing.length > 0) {
    recs.push(`Добавьте в Title слова: ${title.missing.join(', ')}`);
  }

  if (!h1.text) {
    recs.push('H1 отсутствует — добавьте заголовок H1 с ключевыми словами');
  } else if (h1.missing.length > 0) {
    recs.push(`Добавьте в H1 слова: ${h1.missing.join(', ')}`);
  }

  if (!description.text) {
    recs.push('Meta description отсутствует — добавьте описание с ключевыми словами');
  } else if (description.missing.length > 0) {
    recs.push(`Добавьте в Description слова: ${description.missing.join(', ')}`);
  }

  if (title.text && title.missing.length === 0 && h1.missing.length === 0 && description.missing.length === 0) {
    recs.push('Все ключевые слова присутствуют в основных мета-тегах');
  }

  return recs;
}

export function analyzeSeoOptimization(query: string, page: ParsedPage): SeoAnalysis {
  const queryWords = getQueryWords(query);

  const title = analyzeField(page.title, queryWords);
  const h1 = analyzeField(page.h1, queryWords);
  const description = analyzeField(page.metaDescription, queryWords);

  const score = calculateScore(title, h1, description, queryWords);
  const recommendations = generateRecommendations(title, h1, description);

  return {
    query,
    queryWords,
    title,
    h1,
    description,
    score,
    recommendations,
  };
}
