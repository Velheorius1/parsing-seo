import axios from 'axios';
import { worldBankLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';

const WB_API = 'https://search.worldbank.org/api/v2/procnotices';

interface WBNotice {
  id: string;
  notice_type: string;
  noticedate: string;
  notice_status: string;
  submission_deadline_date: string;
  project_ctry_name: string;
  project_id: string;
  project_name: string;
  bid_reference_no: string;
  bid_description: string;
  procurement_group: string;
  procurement_method_name: string;
  contact_organization: string;
  notice_text: string;
  notice_lang_name: string;
}

interface WBResponse {
  total: number;
  rows: number;
  procnotices: Record<string, WBNotice>;
}

function convert(n: WBNotice): RawTenderItem {
  const deadline = n.submission_deadline_date
    ? new Date(n.submission_deadline_date).toLocaleDateString('ru-RU')
    : null;
  const published = n.noticedate || null;

  return {
    id: `wb-${n.id}`,
    externalId: n.bid_reference_no || n.id,
    title: n.project_name || n.bid_description?.slice(0, 200) || `WB Notice #${n.id}`,
    organization: n.contact_organization || '',
    price: null,
    currency: 'USD',
    deadline,
    dateStart: published,
    dateEnd: deadline,
    region: n.project_ctry_name || 'Uzbekistan',
    categories: [n.procurement_group, n.procurement_method_name, n.notice_type].filter(Boolean),
    source: 'World Bank',
    sourceUrl: `https://projects.worldbank.org/en/projects-operations/procurement-detail/${n.id}`,
    status: n.submission_deadline_date && new Date(n.submission_deadline_date) > new Date()
      ? 'active' : 'closed',
    searchText: `${n.project_name || ''} ${n.bid_description || ''} ${n.notice_text || ''} ${n.contact_organization || ''} ${n.procurement_group || ''}`.slice(0, 2000),
  };
}

export const worldBankAdapter: TenderSourceAdapter = {
  name: 'world-bank',
  displayName: 'World Bank',
  async fetch(): Promise<RawTenderItem[]> {
    try {
      const res = await worldBankLimiter.schedule(() =>
        axios.get<WBResponse>(WB_API, {
          params: {
            format: 'json',
            countrycode: 'UZ',
            rows: 200,
            os: 0,
          },
          timeout: 15000,
        })
      );

      const data = res.data;
      if (!data?.procnotices) return [];

      return Object.values(data.procnotices).map(convert);
    } catch (err) {
      console.error('[World Bank] Error:', (err as Error).message);
      return [];
    }
  },
};
