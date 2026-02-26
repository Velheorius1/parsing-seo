import axios from 'axios';
import { xaridLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';
import { formatDate } from './_utils';

const XARID_API = 'https://xarid-api-purchase.uzex.uz';
const XARID_BASE = 'https://xarid.uzex.uz';

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

function convert(c: XaridCompetition): RawTenderItem {
  const cur = c.currency_name || 'UZS';
  return {
    id: `competition-${c.id}`,
    externalId: String(c.id),
    title: c.category_name || `Конкурс #${c.id}`,
    organization: '',
    price: c.cost || null,
    currency: cur,
    deadline: formatDate(c.end_date_submitting_offers),
    dateStart: null,
    dateEnd: formatDate(c.end_date_submitting_offers),
    region: [c.customer_region_name, c.customer_district_name].filter(Boolean).join(', '),
    categories: c.category_name ? [c.category_name] : [],
    source: 'xarid (конкурс)',
    sourceUrl: `${XARID_BASE}/competitions/view/${c.id}`,
    status: new Date(c.end_date_submitting_offers) > new Date() ? 'active' : 'closed',
    searchText: c.category_name || '',
  };
}

export const xaridCompetitionsAdapter: TenderSourceAdapter = {
  name: 'xarid-competitions',
  displayName: 'Xarid Конкурсы',
  async fetch(): Promise<RawTenderItem[]> {
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
      return items.map(convert);
    } catch (err) {
      console.error('[Competitions] Error:', (err as Error).message);
      return [];
    }
  },
};
