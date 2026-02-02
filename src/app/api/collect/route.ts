import { NextResponse } from 'next/server';
import { z } from 'zod';
import { getGoogleSuggestions } from '@/lib/parsers/googleSuggest';
import { searchGoogleSerp } from '@/lib/parsers/googleSerp';

const requestSchema = z.object({
  queries: z.array(z.string().min(1).max(200)).min(1).max(20),
  trackDomain: z.string().max(100).optional(),
});

export interface CollectResultItem {
  keyword: string;
  baseQuery: string;
  trackPosition: number | null;
  trackUrl: string | null;
  top3: { position: number; domain: string; url: string; title: string }[];
}

export async function POST(request: Request) {
  try {
    const body: unknown = await request.json();
    const parsed = requestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: 'Невалидные данные', details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const { queries, trackDomain } = parsed.data;
    const trackHost = trackDomain?.replace(/^https?:\/\//, '').replace(/\/.*$/, '').toLowerCase();

    const results: CollectResultItem[] = [];
    const seen = new Set<string>();
    let processedQueries = 0;

    for (const baseQuery of queries) {
      // 1. Get suggestions
      let suggestions: string[];
      try {
        suggestions = await getGoogleSuggestions(baseQuery);
      } catch {
        suggestions = [];
      }

      // Include the base query itself
      const allKeywords = [baseQuery, ...suggestions];

      for (const keyword of allKeywords) {
        const normalized = keyword.toLowerCase().trim();
        if (seen.has(normalized)) continue;
        seen.add(normalized);

        // 2. Check SERP for each keyword
        try {
          // Rate limit
          if (results.length > 0) {
            await new Promise((r) => setTimeout(r, 1100));
          }

          const serpResults = await searchGoogleSerp(keyword);

          let trackPosition: number | null = null;
          let trackUrl: string | null = null;

          if (trackHost) {
            const found = serpResults.find((r) =>
              r.domain.toLowerCase().includes(trackHost),
            );
            if (found) {
              trackPosition = found.position;
              trackUrl = found.url;
            }
          }

          const top3 = serpResults.slice(0, 3).map((r) => ({
            position: r.position,
            domain: r.domain,
            url: r.url,
            title: r.title,
          }));

          results.push({
            keyword,
            baseQuery,
            trackPosition,
            trackUrl,
            top3,
          });
        } catch {
          results.push({
            keyword,
            baseQuery,
            trackPosition: null,
            trackUrl: null,
            top3: [],
          });
        }
      }

      processedQueries++;
    }

    return NextResponse.json({
      results,
      total: results.length,
      processedQueries,
    });
  } catch (error) {
    console.error('Ошибка сбора семантики:', error);
    const message = error instanceof Error ? error.message : 'Неизвестная ошибка';
    return NextResponse.json(
      { error: `Ошибка: ${message}` },
      { status: 500 },
    );
  }
}
