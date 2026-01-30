import { NextResponse } from 'next/server';
import { z } from 'zod';
import { searchGoogleSerp } from '@/lib/parsers/googleSerp';
import { saveSerpResults } from '@/lib/supabase/serpResults';
import { isSupabaseConfigured } from '@/lib/supabase/client';

const requestSchema = z.object({
  query: z.string().min(1).max(200),
  region: z.string().max(10).optional(),
});

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

    const results = await searchGoogleSerp(parsed.data.query, parsed.data.region);

    // Сохраняем в Supabase
    let savedToDb = 0;
    if (isSupabaseConfigured() && results.length > 0) {
      const saveResult = await saveSerpResults(results);
      savedToDb = saveResult.saved;
      if (saveResult.error) {
        console.warn('Не удалось сохранить SERP в Supabase:', saveResult.error);
      }
    }

    return NextResponse.json({
      results,
      total: results.length,
      query: parsed.data.query,
      savedToDb,
    });
  } catch (error) {
    console.error('Ошибка SERP анализа:', error);
    const message = error instanceof Error ? error.message : 'Неизвестная ошибка';
    return NextResponse.json(
      { error: message },
      { status: 500 },
    );
  }
}
