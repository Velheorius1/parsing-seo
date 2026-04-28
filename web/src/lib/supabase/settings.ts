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

// Получить все настройки
export async function getAllSettings(): Promise<{ settings: CrawlerSetting[]; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { settings: [], error: 'Supabase не настроен' };
  }

  const { data, error } = await supabase
    .from('crawler_settings')
    .select('*')
    .order('key');

  if (error) {
    console.error('Ошибка загрузки настроек:', error.message);
    return { settings: [], error: error.message };
  }

  return { settings: (data as SettingRow[]).map(rowToSetting) };
}

// Обновить настройку по ключу (upsert)
export async function upsertSetting(key: string, value: string): Promise<{ error?: string }> {
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
