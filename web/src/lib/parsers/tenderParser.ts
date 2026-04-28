// Thin orchestrator — re-exports from adapter registry
// All source logic lives in ./sources/ directory

export { searchTendersMultiKeyword, searchTendersWithStats } from './sources/_registry';

import { searchTendersMultiKeyword } from './sources/_registry';
import type { TenderSearchResult } from '@/types/parsing';

export async function searchTenders(keyword: string): Promise<TenderSearchResult> {
  const tenders = await searchTendersMultiKeyword([keyword]);
  return { tenders, total: tenders.length, source: 'uzex-combine', keyword, page: 1 };
}
