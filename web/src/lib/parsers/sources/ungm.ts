import axios from 'axios';
import * as cheerio from 'cheerio';
import { scrapeLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';

// UNGM uses a POST AJAX API that returns HTML fragments
const UNGM_API = 'https://www.ungm.org/Public/Notice/Search';

export const ungmAdapter: TenderSourceAdapter = {
  name: 'ungm',
  displayName: 'UN Global Marketplace',
  async fetch(): Promise<RawTenderItem[]> {
    try {
      const res = await scrapeLimiter.schedule(() =>
        axios.post(UNGM_API, {
          PageIndex: 0,
          PageSize: 50,
          Title: '',
          Description: '',
          Reference: '',
          SortField: 'DatePublished',
          SortAscending: false,
          isPagingReset: false,
          NoticeTypes: [],
          UNSPSCs: [],
          Countries: [],
          Agencies: [],
        }, {
          timeout: 15000,
          headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'User-Agent': 'Mozilla/5.0 (compatible; TenderMonitor/1.0)',
          },
        })
      );

      const html = res.data;
      if (typeof html !== 'string') return [];

      const $ = cheerio.load(html);
      const items: RawTenderItem[] = [];

      $('div.tableRow.dataRow[data-noticeid]').each((_i, el) => {
        const $row = $(el);
        const noticeId = $row.attr('data-noticeid') || '';
        const title = $row.find('.ungm-title').first().text().trim();
        const deadline = $row.find('.deadline span').first().text().trim();
        const agency = $row.find('.resultAgency span').first().text().trim();

        // Get country from last cell
        const cells = $row.find('.tableCell span');
        const country = cells.last().text().trim();

        if (!title || title.length < 3) return;

        items.push({
          id: `ungm-${noticeId}`,
          externalId: noticeId,
          title,
          organization: agency,
          price: null,
          currency: 'USD',
          deadline: deadline || null,
          dateStart: null,
          dateEnd: deadline || null,
          region: country || 'International',
          categories: [],
          source: 'UNGM',
          sourceUrl: `https://www.ungm.org/Public/Notice/${noticeId}`,
          status: 'active',
          searchText: `${title} ${agency} ${country}`,
        });
      });

      return items;
    } catch (err) {
      console.error('[UNGM] Error:', (err as Error).message);
      return [];
    }
  },
};
