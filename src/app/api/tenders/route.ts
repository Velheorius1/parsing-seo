import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { searchTendersWithStats } from '@/lib/parsers/tenderParser';
import { saveTenders, queryTenders } from '@/lib/supabase/tenders';
import { isSupabaseConfigured } from '@/lib/supabase/client';

// Схема валидации входных данных для поиска тендеров
const requestSchema = z.object({
  keywords: z
    .array(z.string().min(1).max(200))
    .min(1)
    .max(50, 'Максимум 50 ключевых слов за раз'),
  page: z.number().int().min(1).optional().default(1),
  source: z.string().optional().default('etender.uzex.uz'),
});

// POST — поиск тендеров по ключевым словам
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

    const { keywords, source } = parsed.data;

    // Поиск тендеров по всем ключевым словам с дедупликацией
    const { tenders, sourceStats } = await searchTendersWithStats(keywords);

    // Сохраняем в Supabase, если подключён
    let savedToDb = 0;
    if (isSupabaseConfigured() && tenders.length > 0) {
      const result = await saveTenders(tenders);
      savedToDb = result.saved;
      if (result.error) {
        console.warn('Не удалось сохранить тендеры в Supabase:', result.error);
      }
    }

    return NextResponse.json({
      tenders,
      total: tenders.length,
      keywords,
      source,
      savedToDb,
      sourceStats,
    });
  } catch (error) {
    console.error('Ошибка поиска тендеров:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// GET — получить тендеры из Supabase с фильтрами
// ?keywords=упаковка,печать&source=etender&status=active
export async function GET(request: NextRequest) {
  try {
    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { error: 'Supabase не настроен — сохранённые тендеры недоступны' },
        { status: 503 },
      );
    }

    const { searchParams } = request.nextUrl;
    const keywordsParam = searchParams.get('keywords');
    const source = searchParams.get('source') || undefined;
    const status = searchParams.get('status') || undefined;

    const keywords = keywordsParam
      ? keywordsParam.split(',').map((k) => k.trim()).filter(Boolean)
      : undefined;

    const { tenders, sourceStats, lastCrawledAt, error } = await queryTenders({
      keywords,
      source,
      status,
    });

    if (error) {
      return NextResponse.json(
        { error: `Ошибка загрузки тендеров: ${error}` },
        { status: 500 },
      );
    }

    return NextResponse.json({
      tenders,
      total: tenders.length,
      sourceStats,
      lastCrawledAt,
    });
  } catch (error) {
    console.error('Ошибка получения тендеров:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
