import { z } from 'zod';
import { yandexLimiter } from '@/lib/utils/rateLimiter';
import type { Keyword } from '@/types/parsing';

// Схема валидации ответа Яндекс Suggest
const yandexSuggestSchema = z.tuple([
  z.string(),
  z.array(z.string()),
]);

// Используем yandex.com вместо yandex.ru (yandex.ru может быть заблокирован)
const YANDEX_SUGGEST_URL = 'https://suggest.yandex.com/suggest-ff.cgi';

// lr=10335 — регион Ташкент
const DEFAULT_PARAMS = {
  uil: 'ru',
  v: '4',
  sn: '5',
  lr: '10335',
} as const;

/**
 * Получить подсказки Яндекса для одного запроса.
 * Запрос идёт через rate limiter (1 запрос/сек).
 */
export async function getYandexSuggestions(query: string): Promise<string[]> {
  const params = new URLSearchParams({
    part: query,
    ...DEFAULT_PARAMS,
  });

  const response = await fetch(`${YANDEX_SUGGEST_URL}?${params}`);

  if (!response.ok) {
    throw new Error(`Yandex Suggest вернул ${response.status} для запроса "${query}"`);
  }

  const data: unknown = await response.json();
  const parsed = yandexSuggestSchema.safeParse(data);

  if (!parsed.success) {
    console.warn(`Невалидный ответ Yandex Suggest для "${query}":`, parsed.error.message);
    return [];
  }

  return parsed.data[1];
}

/**
 * Получить подсказки для одного запроса с rate limiting.
 */
export async function getYandexSuggestionsLimited(query: string): Promise<string[]> {
  return yandexLimiter.schedule(() => getYandexSuggestions(query));
}

/**
 * Собрать подсказки для массива запросов.
 * Возвращает дедуплицированный список Keyword.
 * onProgress — колбэк для отслеживания прогресса.
 */
export async function collectYandexSuggestions(
  queries: string[],
  onProgress?: (completed: number, total: number) => void,
): Promise<Keyword[]> {
  const seen = new Set<string>();
  const results: Keyword[] = [];
  const now = new Date();

  for (let i = 0; i < queries.length; i++) {
    const query = queries[i];
    const suggestions = await getYandexSuggestionsLimited(query);

    for (const suggestion of suggestions) {
      const normalized = suggestion.toLowerCase().trim();

      // Дедупликация по нормализованному ключевому слову
      if (!seen.has(normalized)) {
        seen.add(normalized);
        results.push({
          keyword: suggestion,
          source: 'yandex_suggest',
          baseQuery: query,
          collectedAt: now,
        });
      }
    }

    onProgress?.(i + 1, queries.length);
  }

  return results;
}
