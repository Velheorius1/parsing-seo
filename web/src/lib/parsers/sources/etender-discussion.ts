import axios from 'axios';
import { tenderLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';
import { formatDate } from './_utils';

const ETENDER_API = 'https://apietender.uzex.uz/api';
const ETENDER_BASE = 'https://etender.uzex.uz';

interface UzexTrade {
  id: number;
  display_no: string;
  total_count: number;
  name: string;
  start_date: string;
  end_date: string;
  cost: number;
  seller_name: string;
  region_name: string;
  district_name: string;
  category_name: string | null;
  currency_codeabc: string;
}

function convert(t: UzexTrade): RawTenderItem {
  const cur = t.currency_codeabc || 'UZS';
  return {
    id: `etender-disc-${t.id}`,
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
    source: 'etender (обсуждение)',
    sourceUrl: `${ETENDER_BASE}/lots/2/${t.id}`,
    status: new Date(t.end_date) > new Date() ? 'active' : 'closed',
    searchText: `${t.name} ${t.seller_name} ${t.category_name || ''}`,
  };
}

export const etenderDiscussionAdapter: TenderSourceAdapter = {
  name: 'etender-discussion',
  displayName: 'ETender Обсуждения',
  async fetch(): Promise<RawTenderItem[]> {
    try {
      const res = await tenderLimiter.schedule(() =>
        axios.post<UzexTrade[]>(`${ETENDER_API}/Common/DiscussionTradeList`, { from: 0, to: 500 }, {
          headers: { 'Content-Type': 'application/json' }, timeout: 15000,
        })
      );
      return (res.data || []).map(convert);
    } catch (err) {
      console.error('[ETender Discussion] Error:', (err as Error).message);
      return [];
    }
  },
};
