import { getSupabaseServer } from './client';
import type { Tender } from '@/types/parsing';

// Интерфейс строки в таблице tenders
interface TenderRow {
  id: string;
  external_id: string;
  title: string;
  organization: string;
  price: number | null;
  price_formatted: string;
  currency: string;
  deadline: string | null;
  date_start: string | null;
  date_end: string | null;
  region: string;
  categories: string[];
  source: string;
  source_url: string;
  status: string;
  matched_keywords: string[];
  created_at: string;
}

// Сохранить тендеры в Supabase (upsert по external_id + source)
export async function saveTenders(tenders: Tender[]): Promise<{ saved: number; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { saved: 0, error: 'Supabase не настроен' };
  }

  const rows = tenders.map((t) => ({
    external_id: t.externalId,
    title: t.title,
    organization: t.organization,
    price: t.price,
    price_formatted: t.priceFormatted,
    currency: t.currency,
    deadline: t.deadline,
    date_start: t.dateStart,
    date_end: t.dateEnd,
    region: t.region,
    categories: t.categories,
    source: t.source,
    source_url: t.sourceUrl,
    status: t.status,
    matched_keywords: t.matchedKeywords,
  }));

  // Upsert пачками по 500 (лимит Supabase)
  const BATCH_SIZE = 500;
  let saved = 0;

  for (let i = 0; i < rows.length; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE);
    const { error } = await supabase
      .from('tenders')
      .upsert(batch, { onConflict: 'external_id,source' });

    if (error) {
      console.error('Ошибка сохранения тендеров в Supabase:', error.message);
      return { saved, error: error.message };
    }

    saved += batch.length;
  }

  return { saved };
}

// Получить сохранённые тендеры из Supabase
export async function getTenders(limit?: number): Promise<{ tenders: Tender[]; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { tenders: [], error: 'Supabase не настроен' };
  }

  let query = supabase
    .from('tenders')
    .select('*')
    .order('created_at', { ascending: false });

  if (limit) {
    query = query.limit(limit);
  }

  const { data, error } = await query;

  if (error) {
    console.error('Ошибка загрузки тендеров из Supabase:', error.message);
    return { tenders: [], error: error.message };
  }

  const tenders: Tender[] = (data as TenderRow[]).map((row) => ({
    id: row.id,
    externalId: row.external_id,
    title: row.title,
    organization: row.organization,
    price: row.price,
    priceFormatted: row.price_formatted,
    currency: row.currency,
    deadline: row.deadline,
    dateStart: row.date_start,
    dateEnd: row.date_end,
    region: row.region,
    categories: row.categories,
    source: row.source,
    sourceUrl: row.source_url,
    status: row.status as Tender['status'],
    matchedKeywords: row.matched_keywords,
    collectedAt: new Date(row.created_at),
  }));

  return { tenders };
}
