import { NextResponse } from 'next/server';
import { z } from 'zod';
import { parseSitePage } from '@/lib/parsers/siteParser';
import { analyzeSeoOptimization } from '@/lib/utils/seoAnalyzer';

const requestSchema = z.object({
  query: z.string().min(1).max(200),
  url: z.string().url('Введите корректный URL'),
});

export async function POST(request: Request) {
  try {
    const body: unknown = await request.json();
    const parsed = requestSchema.safeParse(body);

    if (!parsed.success) {
      return NextResponse.json(
        { error: 'Невалидные параметры', details: parsed.error.flatten() },
        { status: 400 },
      );
    }

    const { query, url } = parsed.data;

    const page = await parseSitePage(url);
    const analysis = analyzeSeoOptimization(query, page);

    return NextResponse.json({ analysis });
  } catch (error) {
    console.error('Ошибка SEO-анализа:', error);
    const message = error instanceof Error ? error.message : 'Неизвестная ошибка';
    return NextResponse.json(
      { error: `Не удалось выполнить SEO-анализ: ${message}` },
      { status: 500 },
    );
  }
}
