import { NextResponse } from 'next/server';
import { getKeywords } from '@/lib/supabase/keywords';
import { isSupabaseConfigured } from '@/lib/supabase/client';

export async function GET() {
  if (!isSupabaseConfigured()) {
    return NextResponse.json(
      { keywords: [], error: 'Supabase не настроен. Заполните .env.local' },
      { status: 200 },
    );
  }

  const { keywords, error } = await getKeywords();

  if (error) {
    return NextResponse.json(
      { keywords: [], error },
      { status: 500 },
    );
  }

  return NextResponse.json({ keywords, total: keywords.length });
}
