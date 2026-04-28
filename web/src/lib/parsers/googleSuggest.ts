import { z } from 'zod';
import { yandexLimiter } from '@/lib/utils/rateLimiter';
import type { Keyword } from '@/types/parsing';

// Google Suggest возвращает формат Firefox: ["запрос", ["подсказка1", "подсказка2"]]
const googleSuggestSchema = z.tuple([
  z.string(),
  z.array(z.string()),
]);

const GOOGLE_SUGGEST_URL = 'https://suggestqueries.google.com/complete/search';

// Rate limiter — используем тот же (1 req/sec)
const googleLimiter = yandexLimiter;

/**
 * Получить подсказки Google для одного запроса.
 */
export async function getGoogleSuggestions(query: string): Promise<string[]> {
  const params = new URLSearchParams({
    client: 'firefox',
    q: query,
    hl: 'ru',
  });

  const response = await fetch(`${GOOGLE_SUGGEST_URL}?${params}`);

  if (!response.ok) {
    throw new Error(`Google Suggest вернул ${response.status} для запроса "${query}"`);
  }

  const data: unknown = await response.json();
  const parsed = googleSuggestSchema.safeParse(data);

  if (!parsed.success) {
    console.warn(`Невалидный ответ Google Suggest для "${query}":`, parsed.error.message);
    return [];
  }

  return parsed.data[1];
}

/**
 * Получить подсказки с rate limiting.
 */
export async function getGoogleSuggestionsLimited(query: string): Promise<string[]> {
  return googleLimiter.schedule(() => getGoogleSuggestions(query));
}

/**
 * Собрать подсказки Google для массива запросов.
 * Дедупликация, source = 'google_suggest'.
 */
export async function collectGoogleSuggestions(
  queries: string[],
  onProgress?: (completed: number, total: number) => void,
): Promise<Keyword[]> {
  const seen = new Set<string>();
  const results: Keyword[] = [];
  const now = new Date();

  for (let i = 0; i < queries.length; i++) {
    const query = queries[i];
    const suggestions = await getGoogleSuggestionsLimited(query);

    for (const suggestion of suggestions) {
      const normalized = suggestion.toLowerCase().trim();

      if (!seen.has(normalized)) {
        seen.add(normalized);
        results.push({
          keyword: suggestion,
          source: 'google_suggest',
          baseQuery: query,
          collectedAt: now,
        });
      }
    }

    onProgress?.(i + 1, queries.length);
  }

  return results;
}
