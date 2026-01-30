import * as cheerio from 'cheerio';
import Bottleneck from 'bottleneck';

// Rate limiting: 1 запрос каждые 2 секунды
const siteLimiter = new Bottleneck({
  minTime: 2000,
  maxConcurrent: 1,
});

export interface ParsedPage {
  url: string;
  domain: string;
  title: string | null;
  h1: string | null;
  metaDescription: string | null;
  metaKeywords: string[];
}

// Парсинг одной страницы по URL
async function fetchAndParse(url: string): Promise<ParsedPage> {
  const response = await fetch(url, {
    headers: {
      'User-Agent': 'Mozilla/5.0 (compatible; ParsingSEO/1.0)',
      'Accept': 'text/html,application/xhtml+xml',
      'Accept-Language': 'ru,en;q=0.9',
    },
    signal: AbortSignal.timeout(15000),
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`);
  }

  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('text/html')) {
    throw new Error(`Не HTML страница: ${contentType}`);
  }

  const html = await response.text();
  const $ = cheerio.load(html);

  // Извлекаем домен из URL
  const domain = new URL(url).hostname;

  // Title
  const title = $('title').first().text().trim() || null;

  // H1 — первый на странице
  const h1 = $('h1').first().text().trim() || null;

  // Meta description
  const metaDescription =
    $('meta[name="description"]').attr('content')?.trim() ||
    $('meta[property="og:description"]').attr('content')?.trim() ||
    null;

  // Meta keywords — разбиваем по запятой
  const metaKeywordsRaw = $('meta[name="keywords"]').attr('content')?.trim() || '';
  const metaKeywords = metaKeywordsRaw
    ? metaKeywordsRaw.split(',').map((k) => k.trim()).filter((k) => k.length > 0)
    : [];

  return {
    url,
    domain,
    title,
    h1,
    metaDescription,
    metaKeywords,
  };
}

// Публичная функция с rate limiting
export async function parseSitePage(url: string): Promise<ParsedPage> {
  return siteLimiter.schedule(() => fetchAndParse(url));
}
