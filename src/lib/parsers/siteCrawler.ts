import { parseSitePageWithLinks, type ParsedPage } from './siteParser';

export interface AggregatedKeyword {
  keyword: string;
  count: number;
  pages: string[];
}

export interface CrawlResult {
  domain: string;
  pagesCount: number;
  pages: ParsedPage[];
  aggregatedKeywords: AggregatedKeyword[];
}

// BFS краулер: обходит сайт начиная с startUrl, до maxPages страниц
export async function crawlSite(
  startUrl: string,
  maxPages: number = 20,
): Promise<CrawlResult> {
  const parsedStart = new URL(startUrl);
  const domain = parsedStart.hostname;

  const visited = new Set<string>();
  const queue: string[] = [startUrl];
  const pages: ParsedPage[] = [];

  while (queue.length > 0 && pages.length < maxPages) {
    const currentUrl = queue.shift()!;

    // Нормализуем URL для дедупликации (убираем trailing slash)
    const normalized = normalizeUrl(currentUrl);
    if (visited.has(normalized)) continue;
    visited.add(normalized);

    try {
      const { page, links } = await parseSitePageWithLinks(currentUrl);
      pages.push(page);

      // Добавляем новые ссылки в очередь
      for (const link of links) {
        const normLink = normalizeUrl(link);
        if (!visited.has(normLink)) {
          queue.push(link);
        }
      }
    } catch (err) {
      // Страница не загрузилась — пропускаем, продолжаем краулинг
      console.warn(`Ошибка при парсинге ${currentUrl}:`, err instanceof Error ? err.message : err);
    }
  }

  // Агрегация ключевых слов со всех страниц
  const aggregatedKeywords = aggregateKeywords(pages);

  return {
    domain,
    pagesCount: pages.length,
    pages,
    aggregatedKeywords,
  };
}

function normalizeUrl(url: string): string {
  try {
    const u = new URL(url);
    // Убираем trailing slash, hash, сортируем параметры
    const path = u.pathname.replace(/\/+$/, '') || '/';
    u.searchParams.sort();
    return `${u.protocol}//${u.hostname}${path}${u.search}`;
  } catch {
    return url;
  }
}

function aggregateKeywords(pages: ParsedPage[]): AggregatedKeyword[] {
  const keywordMap = new Map<string, { count: number; pages: string[] }>();

  for (const page of pages) {
    const keywords = new Set<string>();

    // Из meta keywords
    for (const kw of page.metaKeywords) {
      keywords.add(kw.toLowerCase().trim());
    }

    // Из title — разбиваем по разделителям
    if (page.title) {
      const titleWords = page.title
        .split(/[|\-–—,•·]/)
        .map((w) => w.trim().toLowerCase())
        .filter((w) => w.length > 2);
      for (const kw of titleWords) {
        keywords.add(kw);
      }
    }

    // Из h1
    if (page.h1) {
      keywords.add(page.h1.toLowerCase().trim());
    }

    // Из meta description
    if (page.metaDescription) {
      const descWords = page.metaDescription
        .split(/[.,;!?|•·\-–—]/)
        .map((w) => w.trim().toLowerCase())
        .filter((w) => w.length > 3 && w.length < 100);
      for (const kw of descWords) {
        keywords.add(kw);
      }
    }

    // Записываем в агрегацию
    for (const kw of Array.from(keywords)) {
      const existing = keywordMap.get(kw);
      if (existing) {
        existing.count++;
        existing.pages.push(page.url);
      } else {
        keywordMap.set(kw, { count: 1, pages: [page.url] });
      }
    }
  }

  // Сортировка: чаще встречающиеся — первые
  return Array.from(keywordMap.entries())
    .map(([keyword, data]) => ({ keyword, count: data.count, pages: data.pages }))
    .sort((a, b) => b.count - a.count);
}
