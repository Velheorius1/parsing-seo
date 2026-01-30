import { NextResponse } from 'next/server';
import { z } from 'zod';
import { collectYandexSuggestions } from '@/lib/parsers/yandexSuggest';

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

    const keywords = await collectYandexSuggestions(parsed.data.queries);

    return NextResponse.json({
      keywords,
      total: keywords.length,
      queriesProcessed: parsed.data.queries.length,
    });
  } catch (error) {
    console.error('Ошибка при сборе подсказок Yandex:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
