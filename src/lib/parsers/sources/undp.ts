import axios from 'axios';
import * as cheerio from 'cheerio';
import { scrapeLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';

const UNDP_URL = 'https://procurement-notices.undp.org/';

export const undpAdapter: TenderSourceAdapter = {
  name: 'undp',
  displayName: 'UNDP Procurement',
  async fetch(): Promise<RawTenderItem[]> {
    try {
      const res = await scrapeLimiter.schedule(() =>
        axios.get(UNDP_URL, {
          timeout: 20000,
          headers: {
            'User-Agent': 'Mozilla/5.0 (compatible; TenderMonitor/1.0)',
            'Accept': 'text/html,application/xhtml+xml',
          },
        })
      );

      const html = res.data;
      if (typeof html !== 'string') return [];

      const $ = cheerio.load(html);
      const items: RawTenderItem[] = [];

      // Each row is an anchor tag with class vacanciesTableLink
      $('a.vacanciesTableLink.vacanciesTable__row').each((_i, el) => {
        const $row = $(el);
        const href = $row.attr('href') || '';
        const cells = $row.find('.vacanciesTable__cell span');

        const title = cells.eq(0).text().trim();
        const refNo = cells.eq(1).text().trim();
        const office = cells.eq(2).text().trim();
        const deadline = cells.eq(4).text().trim();

        // Filter for Uzbekistan only (office contains UNDP-UZB)
        if (!office.includes('UZB')) return;
        if (!title || title.length < 3) return;

        const negoId = href.match(/nego_id=(\d+)/)?.[1] || refNo;

        items.push({
          id: `undp-${negoId}`,
          externalId: refNo || negoId,
          title,
          organization: 'UNDP Uzbekistan',
          price: null,
          currency: 'USD',
          deadline: deadline || null,
          dateStart: null,
          dateEnd: deadline || null,
          region: 'Uzbekistan',
          categories: [],
          source: 'UNDP',
          sourceUrl: href.startsWith('http') ? href : `https://procurement-notices.undp.org/${href}`,
          status: 'active',
          searchText: `${title} ${refNo} UNDP Uzbekistan`,
        });
      });

      return items;
    } catch (err) {
      console.error('[UNDP] Error:', (err as Error).message);
      return [];
    }
  },
};
