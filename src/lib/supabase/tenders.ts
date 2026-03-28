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
  collected_at: string;
  created_at: string;
}

// Параметры запроса тендеров из БД
export interface TenderQueryParams {
  keywords?: string[];
  source?: string;
  status?: string;
  region?: string;
  category?: string;
  minPrice?: number;
  maxPrice?: number;
  exclude?: string[];
  limit?: number;
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

// Маппинг строки БД → Tender
function rowToTender(row: TenderRow): Tender {
  return {
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
    collectedAt: new Date(row.collected_at || row.created_at),
  };
}

// Получить тендер по ID (Supabase UUID или external_id)
export async function getTenderById(id: string): Promise<Tender | null> {
  const supabase = getSupabaseServer();
  if (!supabase) return null;

  // Сначала ищем по Supabase UUID
  const { data, error } = await supabase
    .from('tenders')
    .select('*')
    .eq('id', id)
    .limit(1)
    .single();

  if (!error && data) {
    return rowToTender(data as TenderRow);
  }

  // Если не нашли — ищем по external_id (может быть несколько с разных площадок)
  const { data: extData, error: extError } = await supabase
    .from('tenders')
    .select('*')
    .eq('external_id', id)
    .limit(1)
    .single();

  if (!extError && extData) {
    return rowToTender(extData as TenderRow);
  }

  return null;
}

// Получить тендер по prefixed ID (e.g. "xtx-red-6899401")
export async function getTenderByPrefixedId(prefixedId: string): Promise<Tender | null> {
  const supabase = getSupabaseServer();
  if (!supabase) return null;

  // prefixed ID format: "{id_prefix}-{external_id}" stored in tenders as external_id
  // But we store just the numeric external_id, and source separately.
  // Try to extract external_id from the prefixed form
  const parts = prefixedId.split('-');
  if (parts.length >= 2) {
    const externalId = parts[parts.length - 1];
    const { data } = await supabase
      .from('tenders')
      .select('*')
      .eq('external_id', externalId)
      .limit(1)
      .single();

    if (data) return rowToTender(data as TenderRow);
  }

  // Fallback: try as-is
  return getTenderById(prefixedId);
}

// Получить тендеры с фильтрами
export async function queryTenders(
  params: TenderQueryParams = {},
): Promise<{ tenders: Tender[]; sourceStats: Record<string, number>; lastCrawledAt: string | null; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { tenders: [], sourceStats: {}, lastCrawledAt: null, error: 'Supabase не настроен' };
  }

  let query = supabase
    .from('tenders')
    .select('*')
    .order('collected_at', { ascending: false });

  if (params.source) {
    query = query.eq('source', params.source);
  }

  if (params.status) {
    query = query.eq('status', params.status);
  }

  if (params.keywords && params.keywords.length > 0) {
    query = query.overlaps('matched_keywords', params.keywords);
  }

  if (params.region) {
    query = query.eq('region', params.region);
  }

  if (params.category) {
    query = query.contains('categories', [params.category]);
  }

  if (params.minPrice !== undefined) {
    query = query.gte('price', params.minPrice);
  }

  if (params.maxPrice !== undefined) {
    query = query.lte('price', params.maxPrice);
  }

  // Исключить тендеры, содержащие указанные слова в title
  if (params.exclude && params.exclude.length > 0) {
    for (const word of params.exclude) {
      // Escape SQL wildcards to prevent pattern injection
      const escaped = word.replace(/%/g, '\\%').replace(/_/g, '\\_');
      query = query.not('title', 'ilike', `%${escaped}%`);
    }
  }

  if (params.limit) {
    query = query.limit(params.limit);
  }

  const { data, error } = await query;

  if (error) {
    console.error('Ошибка загрузки тендеров из Supabase:', error.message);
    return { tenders: [], sourceStats: {}, lastCrawledAt: null, error: error.message };
  }

  const rows = data as TenderRow[];
  const tenders = rows.map(rowToTender);

  // Подсчёт по источникам
  const sourceStats: Record<string, number> = {};
  for (const row of rows) {
    sourceStats[row.source] = (sourceStats[row.source] || 0) + 1;
  }

  // Последнее время сбора
  const lastCrawledAt = rows.length > 0 ? (rows[0].collected_at || rows[0].created_at) : null;

  return { tenders, sourceStats, lastCrawledAt };
}

// Получить сохранённые тендеры из Supabase (обратная совместимость)
export async function getTenders(limit?: number): Promise<{ tenders: Tender[]; error?: string }> {
  const result = await queryTenders({ limit });
  return { tenders: result.tenders, error: result.error };
}
