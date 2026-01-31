import type { SerpResult } from '@/types/parsing';

const SERPER_API_URL = 'https://google.serper.dev/search';

/**
 * Проверяет настроен ли Serper API
 */
export function isSerperConfigured(): boolean {
  return !!process.env.SERPER_API_KEY;
}

/**
 * Поиск Google SERP через Serper.dev API.
 * Надёжный, не блокируется Google.
 */
export async function searchGoogleSerp(
  query: string,
  region?: string,
): Promise<SerpResult[]> {
  const apiKey = process.env.SERPER_API_KEY;

  if (!apiKey) {
    throw new Error('SERPER_API_KEY не настроен. Добавьте ключ в .env.local (serper.dev)');
  }

  const response = await fetch(SERPER_API_URL, {
    method: 'POST',
    headers: {
      'X-API-KEY': apiKey,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      q: query,
      gl: region || 'uz',
      hl: 'ru',
      num: 10,
    }),
  });

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Serper API ошибка ${response.status}: ${text}`);
  }

  const data = await response.json();

  // Serper возвращает { organic: [...], ... }
  const organic = data.organic as Array<{
    title: string;
    link: string;
    snippet: string;
    position?: number;
  }> | undefined;

  if (!organic || organic.length === 0) {
    return [];
  }

  return organic.map((r, i) => {
    let domain = '';
    try {
      domain = new URL(r.link).hostname;
    } catch {
      domain = r.link;
    }

    return {
      query,
      position: i + 1,
      url: r.link,
      title: r.title || '',
      description: r.snippet || '',
      domain,
    };
  });
}
