import { NextRequest, NextResponse } from 'next/server';
import { isSupabaseConfigured } from '@/lib/supabase/client';
import { getAllSettings, upsertSetting, getSourceStats } from '@/lib/supabase/settings';

// GET — read all settings + source stats
export async function GET() {
  try {
    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { error: 'Supabase не настроен' },
        { status: 503 },
      );
    }

    const [settingsResult, statsResult] = await Promise.all([
      getAllSettings(),
      getSourceStats(),
    ]);

    if (settingsResult.error) {
      return NextResponse.json(
        { error: settingsResult.error },
        { status: 500 },
      );
    }

    return NextResponse.json({
      settings: settingsResult.settings,
      sourceStats: statsResult.stats,
    });
  } catch (error) {
    console.error('GET /api/tenders/settings error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// PUT — update a single setting by key (admin-only)
export async function PUT(request: NextRequest) {
  try {
    // Auth check: require admin token (if configured)
    const expectedToken = process.env.ADMIN_SECRET_TOKEN;
    if (expectedToken) {
      const adminToken = request.headers.get('x-admin-token');
      if (adminToken !== expectedToken) {
        return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
      }
    }

    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { error: 'Supabase не настроен' },
        { status: 503 },
      );
    }

    const body: unknown = await request.json();
    if (
      !body ||
      typeof body !== 'object' ||
      !('key' in body) ||
      !('value' in body)
    ) {
      return NextResponse.json(
        { error: 'Невалидные данные: нужны key и value' },
        { status: 400 },
      );
    }

    const { key, value } = body as { key: string; value: string };

    if (typeof key !== 'string' || key.length === 0 || key.length > 100) {
      return NextResponse.json(
        { error: 'Невалидный key' },
        { status: 400 },
      );
    }

    if (typeof value !== 'string' || value.length > 10000) {
      return NextResponse.json(
        { error: 'Невалидный value' },
        { status: 400 },
      );
    }

    const result = await upsertSetting(key, value);

    if (result.error) {
      return NextResponse.json(
        { error: result.error },
        { status: 500 },
      );
    }

    return NextResponse.json({ ok: true });
  } catch (error) {
    console.error('PUT /api/tenders/settings error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
