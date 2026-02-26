import axios from 'axios';
import * as cheerio from 'cheerio';
import { scrapeLimiter } from '@/lib/utils/rateLimiter';
import type { TenderSourceAdapter, RawTenderItem } from './_types';

// Correct domain: corp.uzairways.com (not www.uzairways.com)
const UZA_BASE = 'https://corp.uzairways.com';
const PAGES_TO_FETCH = 3; // 3 pages of tenders

export const uzAirwaysAdapter: TenderSourceAdapter = {
  name: 'uz-airways',
  displayName: 'Uzbekistan Airways',
  async fetch(): Promise<RawTenderItem[]> {
    try {
      const allItems: RawTenderItem[] = [];

      for (let page = 0; page < PAGES_TO_FETCH; page++) {
        try {
          const url = page === 0
            ? `${UZA_BASE}/ru/press-center/tenders`
            : `${UZA_BASE}/ru/press-center/tenders?page=${page}`;

          const res = await scrapeLimiter.schedule(() =>
            axios.get(url, {
              timeout: 15000,
              headers: {
                'User-Agent': 'Mozilla/5.0 (compatible; TenderMonitor/1.0)',
                'Accept': 'text/html,application/xhtml+xml',
                'Accept-Language': 'ru,uz;q=0.9',
              },
              maxRedirects: 3,
            })
          );

          const html = res.data;
          if (typeof html !== 'string') continue;

          const $ = cheerio.load(html);

          // Parse tender items using Drupal structure
          $('.tender-item').each((_i, el) => {
            const $item = $(el);
            const titleEl = $item.find('.articles__title, .item-title a, h5 a').first();
            const title = titleEl.text().trim();
            const href = titleEl.attr('href') || '';
            const dateStr = $item.find('time[datetime]').attr('datetime') ||
              $item.find('.publish-date').text().trim();

            if (!title || title.length < 5) return;

            // Extract slug for unique ID
            const slug = href.replace(/^\/.*\/tenders\//, '').replace(/\/$/, '') || String(allItems.length);

            allItems.push({
              id: `uzair-${slug}`,
              externalId: slug,
              title,
              organization: 'Uzbekistan Airways',
              price: null,
              currency: 'UZS',
              deadline: dateStr || null,
              dateStart: dateStr || null,
              dateEnd: null,
              region: 'Tashkent',
              categories: ['aviation'],
              source: 'Uzbekistan Airways',
              sourceUrl: href.startsWith('http') ? href : `${UZA_BASE}${href}`,
              status: 'active',
              searchText: `${title} Uzbekistan Airways авиакомпания`,
            });
          });

          // Also try international tenders page on first iteration
          if (page === 0) {
            try {
              const intlRes = await scrapeLimiter.schedule(() =>
                axios.get(`${UZA_BASE}/ru/mezhdunarodnye-tendery`, {
                  timeout: 15000,
                  headers: {
                    'User-Agent': 'Mozilla/5.0 (compatible; TenderMonitor/1.0)',
                    'Accept': 'text/html',
                  },
                })
              );
              const intlHtml = intlRes.data;
              if (typeof intlHtml === 'string') {
                const $intl = cheerio.load(intlHtml);
                $intl('.tender-item, article').each((_i, el) => {
                  const $item = $intl(el);
                  const titleEl = $item.find('.articles__title, h5 a, a').first();
                  const title = titleEl.text().trim();
                  const href = titleEl.attr('href') || '';

                  if (!title || title.length < 5) return;
                  const slug = 'intl-' + (href.match(/\/([^/]+)\/?$/)?.[1] || String(allItems.length));

                  allItems.push({
                    id: `uzair-${slug}`,
                    externalId: slug,
                    title: `[Международный] ${title}`,
                    organization: 'Uzbekistan Airways',
                    price: null,
                    currency: 'USD',
                    deadline: null,
                    dateStart: null,
                    dateEnd: null,
                    region: 'International',
                    categories: ['aviation', 'international'],
                    source: 'Uzbekistan Airways',
                    sourceUrl: href.startsWith('http') ? href : `${UZA_BASE}${href}`,
                    status: 'active',
                    searchText: `${title} Uzbekistan Airways international`,
                  });
                });
              }
            } catch {
              // International page may not exist
            }
          }
        } catch {
          // Page may not exist — stop pagination
          break;
        }
      }

      return allItems;
    } catch (err) {
      console.error('[Uz Airways] Error:', (err as Error).message);
      return [];
    }
  },
};
