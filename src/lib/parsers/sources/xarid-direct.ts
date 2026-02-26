import axios from 'axios';
import { xaridLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';
import { formatDate } from './_utils';

const XARID_API = 'https://xarid-api-purchase.uzex.uz';
const XARID_BASE = 'https://xarid.uzex.uz';
const DIRECT_PAGES = 30; // ~600 records (20 per page)

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

function convert(d: XaridDirectPurchase): RawTenderItem {
  const cur = d.currency_name || 'UZS';
  return {
    id: `direct-${d.id}`,
    externalId: d.display_id || String(d.id),
    title: d.category_name || `Прямая закупка #${d.id}`,
    organization: d.customer_name,
    price: d.contract_sum || null,
    currency: cur,
    deadline: formatDate(d.contract_date),
    dateStart: null,
    dateEnd: formatDate(d.contract_date),
    region: '',
    categories: [d.category_name, d.typ_direct_purchase_name].filter(Boolean),
    source: 'xarid (прямая)',
    sourceUrl: `${XARID_BASE}/direct-purchases/view/${d.id}`,
    status: d.status_name?.includes('Опубликован') ? 'active' : 'closed',
    searchText: `${d.category_name || ''} ${d.customer_name || ''} ${d.provider_name || ''}`,
  };
}

export const xaridDirectAdapter: TenderSourceAdapter = {
  name: 'xarid-direct',
  displayName: 'Xarid Прямые закупки',
  async fetch(): Promise<RawTenderItem[]> {
    try {
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
      return all.map(convert);
    } catch (err) {
      console.error('[Direct Purchases] Error:', (err as Error).message);
      return [];
    }
  },
};
