import axios from 'axios';
import { tenderLimiter } from '@/lib/utils/rateLimiter';
import type { Tender, TenderSearchResult } from '@/types/parsing';

// Прямой REST API UZEX (etender.uzex.uz) — без авторизации
const UZEX_API = 'https://apietender.uzex.uz/api';

// Ссылка на тендер на сайте etender
const ETENDER_BASE = 'https://etender.uzex.uz';

// Интерфейс ответа UZEX API
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

/**
 * Форматирование цены для отображения
 */
function formatPrice(cost: number, currencyCode: string): string {
  if (!cost) return '';
  return new Intl.NumberFormat('ru-RU').format(cost) + ' ' + currencyCode;
}

/**
 * Форматирование даты из ISO в DD.MM.YYYY
 */
function formatDate(isoDate: string | null): string | null {
  if (!isoDate) return null;
  try {
    const d = new Date(isoDate);
    const dd = String(d.getDate()).padStart(2, '0');
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const yyyy = d.getFullYear();
    return `${dd}.${mm}.${yyyy}`;
  } catch {
    return null;
  }
}

/**
 * Проверяет совпадение тендера с ключевыми словами.
 * Ищет в названии тендера и имени заказчика.
 */
function matchesKeywords(trade: UzexTrade, keywords: string[]): string[] {
  const searchText = `${trade.name} ${trade.seller_name} ${trade.category_name || ''}`.toLowerCase();
  return keywords.filter(kw => searchText.includes(kw.toLowerCase()));
}

/**
 * Конвертирует тендер из формата UZEX API в наш формат Tender
 */
function convertToTender(trade: UzexTrade, matchedKeywords: string[]): Tender {
  const currency = trade.currency_codeabc || 'UZS';
  return {
    id: `uzex-${trade.id}`,
    externalId: trade.display_no,
    title: trade.name,
    organization: trade.seller_name,
    price: trade.cost || null,
    priceFormatted: formatPrice(trade.cost, currency),
    currency,
    deadline: formatDate(trade.end_date),
    dateStart: formatDate(trade.start_date),
    dateEnd: formatDate(trade.end_date),
    region: [trade.region_name, trade.district_name].filter(Boolean).join(', '),
    categories: trade.category_name ? [trade.category_name] : [],
    source: 'etender.uzex.uz',
    sourceUrl: `${ETENDER_BASE}/lots/2/${trade.id}`,
    status: new Date(trade.end_date) > new Date() ? 'active' : 'closed',
    matchedKeywords,
    collectedAt: new Date(),
  };
}

/**
 * Загружает все активные тендеры из UZEX API.
 * API не поддерживает текстовый поиск — загружаем все и фильтруем локально.
 */
async function fetchAllTrades(): Promise<UzexTrade[]> {
  // Сначала получаем первую порцию чтобы узнать total_count
  const firstBatch = await tenderLimiter.schedule(() =>
    axios.post<UzexTrade[]>(`${UZEX_API}/common/TradeList`, { from: 0, to: 50 }, {
      headers: { 'Content-Type': 'application/json' },
      timeout: 15000,
    })
  );

  const trades = firstBatch.data;
  if (!trades || trades.length === 0) return [];

  const totalCount = trades[0]?.total_count || trades.length;

  // Если есть ещё — догружаем
  if (totalCount > 50) {
    const remaining = await tenderLimiter.schedule(() =>
      axios.post<UzexTrade[]>(`${UZEX_API}/common/TradeList`, { from: 50, to: totalCount }, {
        headers: { 'Content-Type': 'application/json' },
        timeout: 30000,
      })
    );
    if (remaining.data) {
      trades.push(...remaining.data);
    }
  }

  return trades;
}

/**
 * Поиск тендеров по ключевым словам через UZEX API.
 * Загружает все активные тендеры и фильтрует по ключевым словам локально.
 */
export async function searchTenders(keyword: string): Promise<TenderSearchResult> {
  const trades = await fetchAllTrades();
  const tenders: Tender[] = [];

  for (const trade of trades) {
    const matched = matchesKeywords(trade, [keyword]);
    if (matched.length > 0) {
      tenders.push(convertToTender(trade, matched));
    }
  }

  return {
    tenders,
    total: tenders.length,
    source: 'etender.uzex.uz',
    keyword,
    page: 1,
  };
}

/**
 * Поиск тендеров по нескольким ключевым словам.
 * Загружает все тендеры один раз, фильтрует по всем ключам, дедуплицирует.
 */
export async function searchTendersMultiKeyword(keywords: string[]): Promise<Tender[]> {
  const trades = await fetchAllTrades();
  const tenderMap = new Map<string, Tender>();

  for (const trade of trades) {
    const matched = matchesKeywords(trade, keywords);
    if (matched.length > 0) {
      const tender = convertToTender(trade, matched);
      const existing = tenderMap.get(tender.id);
      if (existing) {
        // Объединяем ключевые слова
        for (const kw of matched) {
          if (!existing.matchedKeywords.includes(kw)) {
            existing.matchedKeywords.push(kw);
          }
        }
      } else {
        tenderMap.set(tender.id, tender);
      }
    }
  }

  return Array.from(tenderMap.values());
}
