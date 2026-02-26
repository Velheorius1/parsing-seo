import axios from 'axios';
import { tenderLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';
import { formatDate } from './_utils';

const ETENDER_API = 'https://apietender.uzex.uz/api';
const ETENDER_BASE = 'https://etender.uzex.uz';

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

function convert(t: UzexTrade): RawTenderItem {
  const cur = t.currency_codeabc || 'UZS';
  return {
    id: `etender-${t.id}`,
    externalId: t.display_no,
    title: t.name,
    organization: t.seller_name,
    price: t.cost || null,
    currency: cur,
    deadline: formatDate(t.end_date),
    dateStart: formatDate(t.start_date),
    dateEnd: formatDate(t.end_date),
    region: [t.region_name, t.district_name].filter(Boolean).join(', '),
    categories: t.category_name ? [t.category_name] : [],
    source: 'etender.uzex.uz',
    sourceUrl: `${ETENDER_BASE}/lots/2/${t.id}`,
    status: new Date(t.end_date) > new Date() ? 'active' : 'closed',
    searchText: `${t.name} ${t.seller_name} ${t.category_name || ''}`,
  };
}

export const etenderAdapter: TenderSourceAdapter = {
  name: 'etender',
  displayName: 'ETender UZEX',
  async fetch(): Promise<RawTenderItem[]> {
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
      return trades.map(convert);
    } catch (err) {
      console.error('[ETender] Error:', (err as Error).message);
      return [];
    }
  },
};
