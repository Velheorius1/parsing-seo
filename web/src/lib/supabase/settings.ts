import { getSupabaseServer } from './client';

// Интерфейс строки в таблице crawler_settings
interface SettingRow {
  key: string;
  value: string;
  updated_at: string;
}

export interface CrawlerSetting {
  key: string;
  value: string;
  updatedAt: string;
}

function rowToSetting(row: SettingRow): CrawlerSetting {
  return {
    key: row.key,
    value: row.value,
    updatedAt: row.updated_at,
  };
}

// Ключи, которые сайт показывает и даёт менять. Всё остальное в crawler_settings —
// токены площадок (auth_token:*), курсоры и состояние сторожей краулера: наружу не
// отдаётся и через сайт не пишется. До 05.10.2026 GET отдавал всю таблицу без
// авторизации, включая токены Cooperation и ebirja (ревью 02.10, R01).
export const WEB_SETTING_KEYS = [
  'alert_keywords',
  'min_price',
  'ai_filter_enabled',
  'lead_gen_enabled',
  'deadline_reminders_enabled',
] as const;

export function isWebSettingKey(key: string): boolean {
  return (WEB_SETTING_KEYS as readonly string[]).includes(key);
}

// Получить настройки, которые показывает сайт (только WEB_SETTING_KEYS)
export async function getWebSettings(): Promise<{ settings: CrawlerSetting[]; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { settings: [], error: 'Supabase не настроен' };
  }

  const { data, error } = await supabase
    .from('crawler_settings')
    .select('key, value, updated_at')
    .in('key', [...WEB_SETTING_KEYS])
    .order('key');

  if (error) {
    console.error('Ошибка загрузки настроек:', error.message);
    return { settings: [], error: error.message };
  }

  return { settings: (data as SettingRow[]).map(rowToSetting) };
}

// Обновить настройку по ключу (upsert); только WEB_SETTING_KEYS
export async function upsertSetting(key: string, value: string): Promise<{ error?: string }> {
  if (!isWebSettingKey(key)) {
    return { error: 'Ключ не редактируется через сайт' };
  }
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { error: 'Supabase не настроен' };
  }

  const { error } = await supabase
    .from('crawler_settings')
    .upsert(
      { key, value, updated_at: new Date().toISOString() },
      { onConflict: 'key' },
    );

  if (error) {
    console.error('Ошибка сохранения настройки:', error.message);
    return { error: error.message };
  }

  return {};
}

// Получить статистику по источникам (из tenders)
export async function getSourceStats(): Promise<{
  stats: Array<{ source: string; count: number; lastCrawled: string | null }>;
  error?: string;
}> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { stats: [], error: 'Supabase не настроен' };
  }

  // Get tender count and last collected_at per source
  const { data, error } = await supabase
    .from('tenders')
    .select('source, collected_at');

  if (error) {
    console.error('Ошибка загрузки статистики:', error.message);
    return { stats: [], error: error.message };
  }

  const sourceMap: Record<string, { count: number; lastCrawled: string | null }> = {};
  for (const row of data || []) {
    const src = row.source || 'unknown';
    if (!sourceMap[src]) {
      sourceMap[src] = { count: 0, lastCrawled: null };
    }
    sourceMap[src].count += 1;
    const collected = row.collected_at as string | null;
    if (collected && (!sourceMap[src].lastCrawled || collected > sourceMap[src].lastCrawled!)) {
      sourceMap[src].lastCrawled = collected;
    }
  }

  const stats = Object.entries(sourceMap)
    .map(([source, s]) => ({ source, count: s.count, lastCrawled: s.lastCrawled }))
    .sort((a, b) => b.count - a.count);

  return { stats };
}
