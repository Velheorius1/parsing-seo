import { NextResponse } from 'next/server';
import { z } from 'zod';
import { searchGoogleSerp } from '@/lib/parsers/googleSerp';

const requestSchema = z.object({
  queries: z.array(z.string().min(1).max(200)).min(1).max(50),
  trackDomain: z.string().max(100).optional(),
});

interface BulkResultItem {
  query: string;
  trackPosition: number | null;
  trackUrl: string | null;
  top3: { position: number; domain: string; url: string; title: string }[];
  error?: string;
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

    const results: BulkResultItem[] = [];
    const competitorCount: Record<string, number> = {};

    let inTop3 = 0;
    let inTop10 = 0;
    let notFound = 0;

    for (const query of queries) {
      try {
        // Rate limit: ~1 req/sec
        if (results.length > 0) {
          await new Promise((r) => setTimeout(r, 1100));
        }

        const serpResults = await searchGoogleSerp(query);

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

        if (trackPosition !== null) {
          if (trackPosition <= 3) inTop3++;
          else if (trackPosition <= 10) inTop10++;
          else notFound++;
        } else if (trackHost) {
          notFound++;
        }

        // Count competitors (exclude tracked domain)
        for (const r of serpResults.slice(0, 5)) {
          if (trackHost && r.domain.toLowerCase().includes(trackHost)) continue;
          competitorCount[r.domain] = (competitorCount[r.domain] || 0) + 1;
        }

        const top3 = serpResults.slice(0, 3).map((r) => ({
          position: r.position,
          domain: r.domain,
          url: r.url,
          title: r.title,
        }));

        results.push({ query, trackPosition, trackUrl, top3 });
      } catch (err) {
        results.push({
          query,
          trackPosition: null,
          trackUrl: null,
          top3: [],
          error: err instanceof Error ? err.message : 'Ошибка запроса',
        });
      }
    }

    // Top competitors sorted by frequency
    const topCompetitors = Object.entries(competitorCount)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([domain, count]) => ({ domain, count }));

    return NextResponse.json({
      results,
      summary: {
        total: queries.length,
        inTop3,
        inTop10,
        notFound,
        topCompetitors,
      },
    });
  } catch (error) {
    console.error('Ошибка массового SERP анализа:', error);
    const message = error instanceof Error ? error.message : 'Неизвестная ошибка';
    return NextResponse.json(
      { error: `Ошибка анализа: ${message}` },
      { status: 500 },
    );
  }
}
