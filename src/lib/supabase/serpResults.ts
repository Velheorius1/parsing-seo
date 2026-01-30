import { getSupabaseServer } from './client';
import type { SerpResult } from '@/types/parsing';

// Сохранить SERP результаты в Supabase
export async function saveSerpResults(
  results: SerpResult[],
): Promise<{ saved: number; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { saved: 0, error: 'Supabase не настроен' };
  }

  const rows = results.map((r) => ({
    query: r.query,
    position: r.position,
    url: r.url,
    title: r.title,
    description: r.description,
    domain: r.domain,
    search_engine: 'google',
  }));

  const { error } = await supabase.from('serp_results').insert(rows);

  if (error) {
    console.error('Ошибка сохранения SERP в Supabase:', error.message);
    return { saved: 0, error: error.message };
  }

  return { saved: rows.length };
}
