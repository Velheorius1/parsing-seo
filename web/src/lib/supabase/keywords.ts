import { getSupabaseServer } from './client';
import type { Keyword } from '@/types/parsing';

// Интерфейс строки в таблице keywords
interface KeywordRow {
  id: string;
  keyword: string;
  source: string;
  search_volume: number | null;
  competition: string | null;
  base_query: string | null;
  created_at: string;
}

// Сохранить keywords в Supabase (upsert — ON CONFLICT обновляет)
export async function saveKeywords(keywords: Keyword[]): Promise<{ saved: number; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { saved: 0, error: 'Supabase не настроен' };
  }

  const rows = keywords.map((kw) => ({
    keyword: kw.keyword,
    source: kw.source,
    search_volume: kw.searchVolume ?? null,
    competition: kw.competition ?? null,
    base_query: kw.baseQuery ?? null,
  }));

  // Upsert пачками по 500 (лимит Supabase)
  const BATCH_SIZE = 500;
  let saved = 0;

  for (let i = 0; i < rows.length; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);
    const { error } = await supabase
      .from('keywords')
      .upsert(batch, { onConflict: 'keyword,source' });

    if (error) {
      console.error('Ошибка сохранения keywords в Supabase:', error.message);
      return { saved, error: error.message };
    }

    saved += batch.length;
  }

  return { saved };
}

// Получить все keywords из Supabase
export async function getKeywords(): Promise<{ keywords: Keyword[]; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { keywords: [], error: 'Supabase не настроен' };
  }

  const { data, error } = await supabase
    .from('keywords')
    .select('*')
    .order('created_at', { ascending: false });

  if (error) {
    console.error('Ошибка загрузки keywords из Supabase:', error.message);
    return { keywords: [], error: error.message };
  }

  const keywords: Keyword[] = (data as KeywordRow[]).map((row) => ({
    keyword: row.keyword,
    source: row.source as Keyword['source'],
    searchVolume: row.search_volume ?? undefined,
    competition: row.competition as Keyword['competition'],
    baseQuery: row.base_query ?? undefined,
    collectedAt: new Date(row.created_at),
  }));

  return { keywords };
}
