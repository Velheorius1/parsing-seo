import * as cheerio from 'cheerio';
import Bottleneck from 'bottleneck';
import type { SerpResult } from '@/types/parsing';

// Rate limiting: 1 запрос каждые 3 секунды (Google строже к скрейпингу)
const serpLimiter = new Bottleneck({
  minTime: 3000,
  maxConcurrent: 1,
});

/**
 * Парсинг Google SERP напрямую через fetch + cheerio.
 * ВАЖНО: Google может заблокировать при частых запросах.
 * Для продакшена рекомендуется SerpAPI или Apify.
 */
async function fetchGoogleSerp(query: string, region?: string): Promise<SerpResult[]> {
  const params = new URLSearchParams({
    q: query,
    hl: 'ru',
    gl: region || 'uz',
    num: '10',
  });

  const response = await fetch(`https://www.google.com/search?${params}`, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
      'Accept-Encoding': 'identity',
    },
    signal: AbortSignal.timeout(15000),
  });

  if (!response.ok) {
    if (response.status === 429) {
      throw new Error('Google заблокировал запросы (429). Подождите и попробуйте позже.');
    }
    throw new Error(`Google вернул ошибку: ${response.status}`);
  }

  const html = await response.text();

  // Проверяем на CAPTCHA
  if (html.includes('captcha') || html.includes('unusual traffic')) {
    throw new Error('Google показал CAPTCHA. Слишком много запросов — подождите несколько минут.');
  }

  return parseGoogleHtml(html, query);
}

/**
 * Извлечение результатов из HTML Google.
 */
function parseGoogleHtml(html: string, query: string): SerpResult[] {
  const $ = cheerio.load(html);
  const results: SerpResult[] = [];

  // Google оборачивает результаты в div.g
  $('div.g').each((i, el) => {
    if (results.length >= 10) return;

    const $el = $(el);

    // Ссылка — первый <a> с href
    const linkEl = $el.find('a[href^="http"]').first();
    const url = linkEl.attr('href') || '';

    if (!url || url.includes('google.com') || url.includes('youtube.com/redirect')) {
      return;
    }

    // Title — текст внутри h3
    const title = $el.find('h3').first().text().trim();

    // Description — текст в блоке после заголовка
    // Google использует разные селекторы, пробуем несколько
    const description =
      $el.find('[data-sncf]').first().text().trim() ||
      $el.find('.VwiC3b').first().text().trim() ||
      $el.find('span.st').first().text().trim() ||
      $el.find('div[style="-webkit-line-clamp:2"]').first().text().trim() ||
      '';

    // Домен
    let domain = '';
    try {
      domain = new URL(url).hostname;
    } catch {
      return;
    }

    if (title) {
      results.push({
        query,
        position: results.length + 1,
        url,
        title,
        description,
        domain,
      });
    }
  });

  return results;
}

/**
 * Публичная функция с rate limiting.
 */
export async function searchGoogleSerp(
  query: string,
  region?: string,
): Promise<SerpResult[]> {
  return serpLimiter.schedule(() => fetchGoogleSerp(query, region));
}
