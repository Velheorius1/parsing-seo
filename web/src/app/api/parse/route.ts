import { NextResponse } from 'next/server';
import { z } from 'zod';
import { parseSitePage } from '@/lib/parsers/siteParser';
import { crawlSite } from '@/lib/parsers/siteCrawler';
import { saveParsedPage, saveParsedPages } from '@/lib/supabase/parsedPages';
import { saveKeywords } from '@/lib/supabase/keywords';
import { isSupabaseConfigured } from '@/lib/supabase/client';
import type { Keyword } from '@/types/parsing';

const requestSchema = z.object({
  url: z.string().url('Введите корректный URL'),
  maxPages: z.number().min(1).max(50).optional().default(1),
});

export async function POST(request: Request) {
  try {
    const body: unknown = await request.json();
    const parsed = requestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: 'Невалидный URL', details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const { url, maxPages } = parsed.data;

    // Режим краулера: несколько страниц
    if (maxPages > 1) {
      const crawlResult = await crawlSite(url, maxPages);

      // Сохраняем в Supabase
      let savedPages = 0;
      let savedKeywords = 0;

      if (isSupabaseConfigured()) {
        const pagesResult = await saveParsedPages(crawlResult.pages);
        savedPages = pagesResult.saved;

        // Сохраняем агрегированные ключи как keywords
        const keywords: Keyword[] = crawlResult.aggregatedKeywords.map((ak) => ({
          keyword: ak.keyword,
          source: 'competitor' as const,
          baseQuery: url,
          collectedAt: new Date(),
        }));

        if (keywords.length > 0) {
          const kwResult = await saveKeywords(keywords);
          savedKeywords = kwResult.saved;
        }
      }

      return NextResponse.json({
        mode: 'crawl',
        domain: crawlResult.domain,
        pagesCount: crawlResult.pagesCount,
        pages: crawlResult.pages,
        aggregatedKeywords: crawlResult.aggregatedKeywords,
        savedPages,
        savedKeywords,
      });
    }

    // Режим одной страницы (оригинальное поведение)
    const page = await parseSitePage(url);

    // Извлекаем ключевые слова из meta + title + h1
    const extractedKeywords: Keyword[] = [];

    // Из meta keywords
    for (const kw of page.metaKeywords) {
      extractedKeywords.push({
        keyword: kw.toLowerCase(),
        source: 'competitor',
        baseQuery: page.url,
        collectedAt: new Date(),
      });
    }

    // Из title — разбиваем по разделителям
    if (page.title) {
      const titleWords = page.title
        .split(/[|\-–—,•·]/)
        .map((w) => w.trim().toLowerCase())
        .filter((w) => w.length > 2);
      for (const kw of titleWords) {
        extractedKeywords.push({
          keyword: kw,
          source: 'competitor',
          baseQuery: page.url,
          collectedAt: new Date(),
        });
      }
    }

    // Из h1
    if (page.h1) {
      extractedKeywords.push({
        keyword: page.h1.toLowerCase(),
        source: 'competitor',
        baseQuery: page.url,
        collectedAt: new Date(),
      });
    }

    // Из meta description — извлекаем фразы через разделители
    if (page.metaDescription) {
      const descWords = page.metaDescription
        .split(/[.,;!?|•·\-–—]/)
        .map((w) => w.trim().toLowerCase())
        .filter((w) => w.length > 3 && w.length < 100);
      for (const kw of descWords) {
        extractedKeywords.push({
          keyword: kw,
          source: 'competitor',
          baseQuery: page.url,
          collectedAt: new Date(),
        });
      }
    }

    // Дедупликация
    const uniqueKeywords = Array.from(
      new Map(extractedKeywords.map((kw) => [kw.keyword, kw])).values()
    );

    // Сохраняем в Supabase
    let savedPage = false;
    let savedKeywords = 0;

    if (isSupabaseConfigured()) {
      const pageResult = await saveParsedPage(page);
      savedPage = pageResult.saved;

      if (uniqueKeywords.length > 0) {
        const kwResult = await saveKeywords(uniqueKeywords);
        savedKeywords = kwResult.saved;
      }
    }

    return NextResponse.json({
      mode: 'single',
      page,
      keywords: uniqueKeywords,
      total: uniqueKeywords.length,
      savedPage,
      savedKeywords,
    });
  } catch (error) {
    console.error('Ошибка парсинга сайта:', error);
    const message = error instanceof Error ? error.message : 'Неизвестная ошибка';
    return NextResponse.json(
      { error: `Не удалось спарсить страницу: ${message}` },
      { status: 500 },
    );
  }
}
