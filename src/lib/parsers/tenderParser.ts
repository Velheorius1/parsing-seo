import axios from 'axios';
import { tenderLimiter, xaridLimiter } from '@/lib/utils/rateLimiter';
import type { Tender, TenderSearchResult } from '@/types/parsing';

// === API endpoints ===
const ETENDER_API = 'https://apietender.uzex.uz/api';
const XARID_API = 'https://xarid-api-purchase.uzex.uz';
const ETENDER_BASE = 'https://etender.uzex.uz';
const XARID_BASE = 'https://xarid.uzex.uz';

// Сколько страниц прямых закупок загружать (20 записей на страницу)
const DIRECT_PAGES = 30; // ~600 свежих записей (баланс скорость/охват)

// === Типы ===

interface UzexTrade {
  rn: number;
  id: number;
  display_no: string;
  total_count: number;
  name: string;
  start_date: string;
  end_date: string;
  clarific_date: string;
  cost: number;
  seller_name: string;
  seller_tin: string;
  region_name: string;
  district_name: string;
  seller_id: number;
  category_name: string | null;
  currency_id: number;
  currency_name: string;
  currency_code123: string;
  currency_codeabc: string;
}

interface XaridCompetition {
  id: number;
  end_date_submitting_offers: string;
  customer_region_name: string;
  customer_district_name: string;
  category_name: string;
  cost: number;
  currency_name: string;
  rn: number;
  total_count: number;
}

interface XaridDirectPurchase {
  id: number;
  display_id: string;
  category_name: string;
  provider_name: string;
  contract_sum: number;
  currency_name: string;
  contract_num: string;
  contract_date: string;
  typ_direct_purchase_name: string;
  date_ini: string;
  status_name: string;
  customer_name: string;
  customer_inn: string;
  customer_type: string;
  provider_inn: string;
  rn: number;
  total_count: number;
}

// === Утилиты ===

function formatPrice(cost: number, code: string): string {
  if (!cost) return '';
  return new Intl.NumberFormat('ru-RU').format(cost) + ' ' + code;
}

function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`;
  } catch {
    return null;
  }
}

function matchText(text: string, keywords: string[]): string[] {
  const lower = text.toLowerCase();
  return keywords.filter(kw => lower.includes(kw.toLowerCase()));
}

// === Источник 1: ETender ===

async function fetchETender(): Promise<UzexTrade[]> {
  try {
    const first = await tenderLimiter.schedule(() =>
      axios.post<UzexTrade[]>(`${ETENDER_API}/Common/TradeList`, { from: 0, to: 100 }, {
        headers: { 'Content-Type': 'application/json' }, timeout: 15000,
      })
    );
    const trades = first.data || [];
    if (trades.length === 0) return [];

    const total = trades[0]?.total_count || trades.length;
    if (total > 100) {
      const rest = await tenderLimiter.schedule(() =>
        axios.post<UzexTrade[]>(`${ETENDER_API}/Common/TradeList`, { from: 100, to: total }, {
          headers: { 'Content-Type': 'application/json' }, timeout: 30000,
        })
      );
      if (rest.data) trades.push(...rest.data);
    }
    return trades;
  } catch (err) {
    console.error('[ETender] Error:', (err as Error).message);
    return [];
  }
}

// Также загружаем тендеры на обсуждении
async function fetchETenderDiscussion(): Promise<UzexTrade[]> {
  try {
    const res = await tenderLimiter.schedule(() =>
      axios.post<UzexTrade[]>(`${ETENDER_API}/Common/DiscussionTradeList`, { from: 0, to: 500 }, {
        headers: { 'Content-Type': 'application/json' }, timeout: 15000,
      })
    );
    return res.data || [];
  } catch (err) {
    console.error('[ETender Discussion] Error:', (err as Error).message);
    return [];
  }
}

function convertETender(t: UzexTrade, matched: string[], isDiscussion = false): Tender {
  const cur = t.currency_codeabc || 'UZS';
  return {
    id: `etender-${t.id}`,
    externalId: t.display_no,
    title: t.name,
    organization: t.seller_name,
    price: t.cost || null,
    priceFormatted: formatPrice(t.cost, cur),
    currency: cur,
    deadline: formatDate(t.end_date),
    dateStart: formatDate(t.start_date),
    dateEnd: formatDate(t.end_date),
    region: [t.region_name, t.district_name].filter(Boolean).join(', '),
    categories: t.category_name ? [t.category_name] : [],
    source: isDiscussion ? 'etender (обсуждение)' : 'etender.uzex.uz',
    sourceUrl: `${ETENDER_BASE}/lots/2/${t.id}`,
    status: new Date(t.end_date) > new Date() ? 'active' : 'closed',
    matchedKeywords: matched,
    collectedAt: new Date(),
  };
}

// === Источник 2: Xarid Competitions ===

async function fetchCompetitions(): Promise<XaridCompetition[]> {
  try {
    const first = await xaridLimiter.schedule(() =>
      axios.post<XaridCompetition[]>(`${XARID_API}/Common/GetCompetitions`, { from: 0, to: 200 }, {
        headers: { 'Content-Type': 'application/json' }, timeout: 15000,
      })
    );
    const items = first.data || [];
    if (items.length === 0) return [];

    const total = items[0]?.total_count || items.length;
    if (total > 200) {
      const batches = [];
      for (let from = 200; from < total; from += 500) {
        batches.push(
          xaridLimiter.schedule(() =>
            axios.post<XaridCompetition[]>(`${XARID_API}/Common/GetCompetitions`, {
              from, to: Math.min(from + 500, total),
            }, { headers: { 'Content-Type': 'application/json' }, timeout: 30000 })
          )
        );
      }
      const results = await Promise.all(batches);
      for (const r of results) if (r.data) items.push(...r.data);
    }
    return items;
  } catch (err) {
    console.error('[Competitions] Error:', (err as Error).message);
    return [];
  }
}

function convertCompetition(c: XaridCompetition, matched: string[]): Tender {
  const cur = c.currency_name || 'UZS';
  return {
    id: `competition-${c.id}`,
    externalId: String(c.id),
    title: c.category_name || `Конкурс #${c.id}`,
    organization: '',
    price: c.cost || null,
    priceFormatted: formatPrice(c.cost, cur),
    currency: cur,
    deadline: formatDate(c.end_date_submitting_offers),
    dateStart: null,
    dateEnd: formatDate(c.end_date_submitting_offers),
    region: [c.customer_region_name, c.customer_district_name].filter(Boolean).join(', '),
    categories: c.category_name ? [c.category_name] : [],
    source: 'xarid (конкурс)',
    sourceUrl: `${XARID_BASE}/competitions/view/${c.id}`,
    status: new Date(c.end_date_submitting_offers) > new Date() ? 'active' : 'closed',
    matchedKeywords: matched,
    collectedAt: new Date(),
  };
}

// === Источник 3: Xarid Direct Purchases ===

async function fetchDirectPurchases(): Promise<XaridDirectPurchase[]> {
  try {
    // Загружаем DIRECT_PAGES страниц параллельно (по 20 записей на страницу)
    const pageSize = 20;
    const batches = [];
    for (let page = 0; page < DIRECT_PAGES; page++) {
      const from = page * pageSize;
      const to = from + pageSize;
      batches.push(
        xaridLimiter.schedule(() =>
          axios.post<XaridDirectPurchase[]>(`${XARID_API}/Common/GetDirectPurchases`, {
            from, to,
          }, { headers: { 'Content-Type': 'application/json' }, timeout: 15000 })
            .then(r => r.data || [])
            .catch(() => [] as XaridDirectPurchase[])
        )
      );
    }

    const results = await Promise.all(batches);
    const all: XaridDirectPurchase[] = [];
    for (const batch of results) all.push(...batch);
    return all;
  } catch (err) {
    console.error('[Direct Purchases] Error:', (err as Error).message);
    return [];
  }
}

function convertDirect(d: XaridDirectPurchase, matched: string[]): Tender {
  const cur = d.currency_name || 'UZS';
  return {
    id: `direct-${d.id}`,
    externalId: d.display_id || String(d.id),
    title: d.category_name || `Прямая закупка #${d.id}`,
    organization: d.customer_name,
    price: d.contract_sum || null,
    priceFormatted: formatPrice(d.contract_sum, cur),
    currency: cur,
    deadline: formatDate(d.contract_date),
    dateStart: null,
    dateEnd: formatDate(d.contract_date),
    region: '',
    categories: [d.category_name, d.typ_direct_purchase_name].filter(Boolean),
    source: 'xarid (прямая)',
    sourceUrl: `${XARID_BASE}/direct-purchases/view/${d.id}`,
    status: d.status_name?.includes('Опубликован') ? 'active' : 'closed',
    matchedKeywords: matched,
    collectedAt: new Date(),
  };
}

// === Комбайн: все источники параллельно ===

interface SourceStats {
  etender: number;
  etenderDiscussion: number;
  competitions: number;
  directPurchases: number;
  totalMatches: number;
}

export async function searchTendersMultiKeyword(keywords: string[]): Promise<Tender[]> {
  // Запускаем ВСЕ источники параллельно
  const [etender, discussion, competitions, directs] = await Promise.all([
    fetchETender(),
    fetchETenderDiscussion(),
    fetchCompetitions(),
    fetchDirectPurchases(),
  ]);

  const tenderMap = new Map<string, Tender>();

  function addMatches<T>(
    items: T[],
    getText: (item: T) => string,
    convert: (item: T, matched: string[]) => Tender,
  ) {
    for (const item of items) {
      const matched = matchText(getText(item), keywords);
      if (matched.length > 0) {
        const tender = convert(item, matched);
        const existing = tenderMap.get(tender.id);
        if (existing) {
          for (const kw of matched) {
            if (!existing.matchedKeywords.includes(kw)) existing.matchedKeywords.push(kw);
          }
        } else {
          tenderMap.set(tender.id, tender);
        }
      }
    }
  }

  // ETender (тендеры + обсуждения)
  addMatches(etender, t => `${t.name} ${t.seller_name} ${t.category_name || ''}`, (t, m) => convertETender(t, m));
  addMatches(discussion, t => `${t.name} ${t.seller_name} ${t.category_name || ''}`, (t, m) => convertETender(t, m, true));

  // Конкурсы
  addMatches(competitions, c => c.category_name || '', convertCompetition);

  // Прямые закупки
  addMatches(directs, d => `${d.category_name || ''} ${d.customer_name || ''} ${d.provider_name || ''}`, convertDirect);

  const stats: SourceStats = {
    etender: etender.length,
    etenderDiscussion: discussion.length,
    competitions: competitions.length,
    directPurchases: directs.length,
    totalMatches: tenderMap.size,
  };

  console.log(
    `[Комбайн] ETender: ${stats.etender} + Обсуждения: ${stats.etenderDiscussion} | ` +
    `Конкурсы: ${stats.competitions} | Прямые: ${stats.directPurchases} → ` +
    `Совпадений: ${stats.totalMatches}`
  );

  return Array.from(tenderMap.values());
}

export async function searchTenders(keyword: string): Promise<TenderSearchResult> {
  const tenders = await searchTendersMultiKeyword([keyword]);
  return { tenders, total: tenders.length, source: 'uzex-combine', keyword, page: 1 };
}
