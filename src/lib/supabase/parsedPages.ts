import { getSupabaseServer } from './client';
import type { ParsedPage } from '@/lib/parsers/siteParser';

// Интерфейс строки в таблице parsed_pages
interface ParsedPageRow {
  id: string;
  url: string;
  domain: string;
  title: string | null;
  h1: string | null;
  meta_description: string | null;
  meta_keywords: string[];
  parsed_at: string;
}

// Сохранить спарсенную страницу (upsert по url)
export async function saveParsedPage(page: ParsedPage): Promise<{ saved: boolean; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { saved: false, error: 'Supabase не настроен' };
  }

  const { error } = await supabase
    .from('parsed_pages')
    .upsert({
      url: page.url,
      domain: page.domain,
      title: page.title,
      h1: page.h1,
      meta_description: page.metaDescription,
      meta_keywords: page.metaKeywords,
    }, { onConflict: 'url' });

  if (error) {
    console.error('Ошибка сохранения parsed_page:', error.message);
    return { saved: false, error: error.message };
  }

  return { saved: true };
}

// Получить все спарсенные страницы
export async function getParsedPages(): Promise<{ pages: ParsedPage[]; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { pages: [], error: 'Supabase не настроен' };
  }

  const { data, error } = await supabase
    .from('parsed_pages')
    .select('*')
    .order('parsed_at', { ascending: false });

  if (error) {
    console.error('Ошибка загрузки parsed_pages:', error.message);
    return { pages: [], error: error.message };
  }

  const pages: ParsedPage[] = (data as ParsedPageRow[]).map((row) => ({
    url: row.url,
    domain: row.domain,
    title: row.title,
    h1: row.h1,
    metaDescription: row.meta_description,
    metaKeywords: row.meta_keywords || [],
  }));

  return { pages };
}
