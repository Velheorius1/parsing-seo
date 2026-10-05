import { timingSafeEqual } from 'crypto';
import { NextRequest, NextResponse } from 'next/server';
import { isSupabaseConfigured } from '@/lib/supabase/client';
import {
  getWebSettings,
  upsertSetting,
  getSourceStats,
  isWebSettingKey,
} from '@/lib/supabase/settings';

// Сравнение токена без утечки по времени
function tokenMatches(given: string, expected: string): boolean {
  const a = Buffer.from(given);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

// GET — настройки сайта (только WEB_SETTING_KEYS, без токенов) + статистика источников
export async function GET() {
  try {
    if (!isSupabaseConfigured()) {
      return NextResponse.json(
        { error: 'Supabase не настроен' },
        { status: 503 },
      );
    }

    const [settingsResult, statsResult] = await Promise.all([
      getWebSettings(),
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

// POST — update a single setting by key (admin-only)
// Using POST instead of PUT — Vercel returns 405 for PUT on some route configs
export async function POST(request: NextRequest) {
  try {
    // Fail-closed: без ADMIN_SECRET_TOKEN запись выключена совсем. Раньше пустой
    // env пропускал любого — так было на проде до 05.10.2026 (ревью 02.10, R01).
    const expectedToken = process.env.ADMIN_SECRET_TOKEN;
    if (!expectedToken) {
      return NextResponse.json(
        { error: 'Запись настроек через сайт выключена: не задан ADMIN_SECRET_TOKEN' },
        { status: 503 },
      );
    }
    const adminToken = request.headers.get('x-admin-token') || '';
    if (!tokenMatches(adminToken, expectedToken)) {
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
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

    if (!isWebSettingKey(key)) {
      return NextResponse.json(
        { error: 'Ключ не редактируется через сайт' },
        { status: 403 },
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
