import { NextResponse } from 'next/server';
import { getSupabaseServer } from '@/lib/supabase/client';

interface PredictionRow {
  id: string;
  organization: string;
  predicted_month: number;
  predicted_year: number;
  confidence: number;
  basis: string;
  product_hint: string;
  created_at: string;
  notified: boolean;
}

export async function GET() {
  try {
    const supabase = getSupabaseServer();
    if (!supabase) {
      return NextResponse.json(
        { error: 'Supabase не настроен' },
        { status: 503 },
      );
    }

    // Return predictions for the next 2 months
    const now = new Date();
    const currentMonth = now.getMonth() + 1;
    const currentYear = now.getFullYear();

    const nextMonth = currentMonth < 12 ? currentMonth + 1 : 1;
    const nextYear = currentMonth < 12 ? currentYear : currentYear + 1;

    const monthAfter = nextMonth < 12 ? nextMonth + 1 : 1;
    const yearAfter = nextMonth < 12 ? nextYear : nextYear + 1;

    const { data, error } = await supabase
      .from('tender_predictions')
      .select('*')
      .or(
        `and(predicted_month.eq.${nextMonth},predicted_year.eq.${nextYear}),and(predicted_month.eq.${monthAfter},predicted_year.eq.${yearAfter})`
      )
      .order('confidence', { ascending: false }) as { data: PredictionRow[] | null; error: unknown };

    if (error) {
      const errMsg = typeof error === 'object' && error !== null && 'message' in error
        ? (error as { message: string }).message
        : 'Unknown error';
      console.error('Predictions error:', errMsg);
      return NextResponse.json(
        { error: `Ошибка загрузки прогнозов: ${errMsg}` },
        { status: 500 },
      );
    }

    return NextResponse.json({
      predictions: data || [],
      meta: {
        currentMonth,
        currentYear,
        nextMonth,
        nextYear,
        monthAfter,
        yearAfter,
      },
    });
  } catch (error) {
    console.error('Predictions error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
