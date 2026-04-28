import { NextResponse } from 'next/server';
import { z } from 'zod';
import { collectGoogleSuggestions } from '@/lib/parsers/googleSuggest';
import { saveKeywords } from '@/lib/supabase/keywords';
import { isSupabaseConfigured } from '@/lib/supabase/client';

// Схема валидации входных данных
const requestSchema = z.object({
  queries: z
    .array(z.string().min(1).max(200))
    .min(1)
    .max(50, 'Максимум 50 запросов за раз'),
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

    const keywords = await collectGoogleSuggestions(parsed.data.queries);

    // Сохраняем в Supabase, если подключён
    let savedToDb = 0;
    if (isSupabaseConfigured()) {
      const result = await saveKeywords(keywords);
      savedToDb = result.saved;
      if (result.error) {
        console.warn('Не удалось сохранить в Supabase:', result.error);
      }
    }

    return NextResponse.json({
      keywords,
      total: keywords.length,
      queriesProcessed: parsed.data.queries.length,
      savedToDb,
    });
  } catch (error) {
    console.error('Ошибка при сборе подсказок Google:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
