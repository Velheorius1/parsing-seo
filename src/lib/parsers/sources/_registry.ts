import type { Tender } from '@/types/parsing';
import type { TenderSourceAdapter, SourceStats } from './_types';
import { rawToTender } from './_types';
import { matchText } from './_utils';

// --- Import all adapters ---
import { etenderAdapter } from './etender';
import { etenderDiscussionAdapter } from './etender-discussion';
import { xaridCompetitionsAdapter } from './xarid-competitions';
import { xaridDirectAdapter } from './xarid-direct';
import { ungmAdapter } from './ungm';
import { undpAdapter } from './undp';
import { worldBankAdapter } from './world-bank';
import { tenderzoneAdapter } from './tenderzone';
import { uzAirwaysAdapter } from './uz-airways';

/** All registered source adapters */
const adapters: TenderSourceAdapter[] = [
  etenderAdapter,
  etenderDiscussionAdapter,
  xaridCompetitionsAdapter,
  xaridDirectAdapter,
  ungmAdapter,
  undpAdapter,
  worldBankAdapter,
  tenderzoneAdapter,
  uzAirwaysAdapter,
];

export interface SearchResult {
  tenders: Tender[];
  sourceStats: SourceStats;
}

/** Run all sources in parallel, match keywords, deduplicate */
export async function searchTendersMultiKeyword(keywords: string[]): Promise<Tender[]> {
  const results = await Promise.allSettled(
    adapters.map(adapter => adapter.fetch())
  );

  const stats: SourceStats = {};
  const tenderMap = new Map<string, Tender>();

  for (let i = 0; i < results.length; i++) {
    const result = results[i];
    const adapter = adapters[i];

    if (result.status === 'rejected') {
      console.error(`[${adapter.displayName}] Failed:`, result.reason);
      stats[adapter.name] = 0;
      continue;
    }

    const items = result.value;
    stats[adapter.name] = items.length;

    for (const item of items) {
      const matched = matchText(item.searchText, keywords);
      if (matched.length > 0) {
        const existing = tenderMap.get(item.id);
        if (existing) {
          for (const kw of matched) {
            if (!existing.matchedKeywords.includes(kw)) {
              existing.matchedKeywords.push(kw);
            }
          }
        } else {
          tenderMap.set(item.id, rawToTender(item, matched));
        }
      }
    }
  }

  const totalScanned = Object.values(stats).reduce((a, b) => a + b, 0);
  const sourceLog = Object.entries(stats)
    .map(([name, count]) => `${name}: ${count}`)
    .join(' | ');
  console.log(`[Комбайн] ${sourceLog} → Всего: ${totalScanned} → Совпадений: ${tenderMap.size}`);

  return Array.from(tenderMap.values());
}

/** Extended search returning stats */
export async function searchTendersWithStats(keywords: string[]): Promise<SearchResult> {
  const results = await Promise.allSettled(
    adapters.map(adapter => adapter.fetch())
  );

  const sourceStats: SourceStats = {};
  const tenderMap = new Map<string, Tender>();

  for (let i = 0; i < results.length; i++) {
    const result = results[i];
    const adapter = adapters[i];

    if (result.status === 'rejected') {
      console.error(`[${adapter.displayName}] Failed:`, result.reason);
      sourceStats[adapter.name] = 0;
      continue;
    }

    const items = result.value;
    sourceStats[adapter.name] = items.length;

    for (const item of items) {
      const matched = matchText(item.searchText, keywords);
      if (matched.length > 0) {
        const existing = tenderMap.get(item.id);
        if (existing) {
          for (const kw of matched) {
            if (!existing.matchedKeywords.includes(kw)) {
              existing.matchedKeywords.push(kw);
            }
          }
        } else {
          tenderMap.set(item.id, rawToTender(item, matched));
        }
      }
    }
  }

  return {
    tenders: Array.from(tenderMap.values()),
    sourceStats,
  };
}
