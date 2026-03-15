import { NextRequest, NextResponse } from 'next/server';
import * as XLSX from 'xlsx';
import { queryTenders } from '@/lib/supabase/tenders';
import { isSupabaseConfigured } from '@/lib/supabase/client';
import type { Tender } from '@/types/parsing';

export async function GET(request: NextRequest) {
  try {
    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { error: 'Supabase не настроен — экспорт недоступен' },
        { status: 503 },
      );
    }

    const { searchParams } = request.nextUrl;
    const keywordsParam = searchParams.get('keywords');
    const source = searchParams.get('source') || undefined;
    const status = searchParams.get('status') || undefined;
    const region = searchParams.get('region') || undefined;
    const category = searchParams.get('category') || undefined;
    const minPrice = searchParams.get('minPrice') ? Number(searchParams.get('minPrice')) : undefined;
    const maxPrice = searchParams.get('maxPrice') ? Number(searchParams.get('maxPrice')) : undefined;
    const excludeParam = searchParams.get('exclude');

    const keywords = keywordsParam
      ? keywordsParam.split(',').map((k) => k.trim()).filter(Boolean)
      : undefined;

    const exclude = excludeParam
      ? excludeParam.split(',').map((k) => k.trim()).filter(Boolean)
      : undefined;

    const { tenders, error } = await queryTenders({
      keywords,
      source,
      status,
      region,
      category,
      minPrice,
      maxPrice,
      exclude,
    });

    if (error) {
      return NextResponse.json(
        { error: `Ошибка загрузки тендеров: ${error}` },
        { status: 500 },
      );
    }

    // Build Excel rows
    const rows = tenders.map((t: Tender) => ({
      'Название': t.title,
      'Заказчик': t.organization,
      'Сумма': t.price,
      'Валюта': t.currency,
      'Дедлайн': t.deadline,
      'Осталось дней': t.daysLeft ?? '',
      'Статус': t.status,
      'Регион': t.region,
      'Площадка': t.source,
      'Ссылка': t.sourceUrl,
      'Победитель': t.winner ?? '',
      'Цена победителя': t.winningPrice ?? '',
    }));

    const workbook = XLSX.utils.book_new();
    const worksheet = XLSX.utils.json_to_sheet(rows);

    // Auto-width columns
    const colWidths = Object.keys(rows[0] || {}).map((key) => {
      const maxLen = Math.max(
        key.length,
        ...rows.map((r) => String((r as Record<string, unknown>)[key] ?? '').length),
      );
      return { wch: Math.min(maxLen + 2, 60) };
    });
    worksheet['!cols'] = colWidths;

    XLSX.utils.book_append_sheet(workbook, worksheet, 'Тендеры');

    const buffer = XLSX.write(workbook, { type: 'buffer', bookType: 'xlsx' });

    return new NextResponse(buffer, {
      headers: {
        'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'Content-Disposition': `attachment; filename="tenders-${new Date().toISOString().slice(0, 10)}.xlsx"`,
      },
    });
  } catch (error) {
    console.error('Ошибка экспорта тендеров:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
