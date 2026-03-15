// Gold standard: Next.js API route with Supabase
// Pattern extracted from /api/tenders and /api/tenders/favorites

import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { isSupabaseConfigured } from '@/lib/supabase/client';

// 1. Zod schema for POST body validation
const requestSchema = z.object({
  keywords: z.array(z.string().min(1).max(200)).min(1).max(50),
  source: z.string().optional(),
});

// 2. GET — read with query params, check Supabase first
export async function GET(request: NextRequest) {
  try {
    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { error: 'Supabase не настроен' },
        { status: 503 },
      );
    }

    const { searchParams } = request.nextUrl;
    const param = searchParams.get('param') || undefined;
    const numParam = searchParams.get('numParam');
    const numValue = numParam ? Number(numParam) : undefined;

    // Validate numeric params
    const safeNum = numValue && !isNaN(numValue) ? numValue : undefined;

    // Call Supabase query function
    const { data, error } = await queryFunction({ param, numValue: safeNum });

    if (error) {
      return NextResponse.json(
        { error: `Ошибка: ${error}` },
        { status: 500 },
      );
    }

    return NextResponse.json({ data });
  } catch (error) {
    console.error('GET error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// 3. POST — validate body with Zod, process, return result
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

    const { keywords } = parsed.data;
    // ... process ...

    return NextResponse.json({ result: keywords });
  } catch (error) {
    console.error('POST error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// --- PATTERNS ---
// - Always check isSupabaseConfigured() before DB calls
// - Validate numeric query params: Number() + isNaN check
// - Zod for POST body validation
// - Consistent error format: { error: string }
// - Console.error for server-side logging
// - 503 for unavailable services, 400 for bad input, 500 for internal errors
// - CSV query params: split(',').map(k => k.trim()).filter(Boolean)

// Placeholder to satisfy TypeScript
declare function queryFunction(params: Record<string, unknown>): Promise<{ data: unknown; error?: string }>;
