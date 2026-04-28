import type { Tender } from '@/types/parsing';

/** Raw item returned by each source adapter before keyword matching */
export interface RawTenderItem {
  id: string;             // prefixed: 'etender-123', 'undp-456'
  externalId: string;
  title: string;
  organization: string;
  price: number | null;
  currency: string;
  deadline: string | null;
  dateStart: string | null;
  dateEnd: string | null;
  region: string;
  categories: string[];
  source: string;         // display name for UI: 'etender.uzex.uz'
  sourceUrl: string;
  status: 'active' | 'closed' | 'cancelled';
  searchText: string;     // combined text for keyword matching
}

/** Every tender source implements this interface */
export interface TenderSourceAdapter {
  /** Unique source key, e.g. 'etender' */
  name: string;
  /** Human-readable name, e.g. 'ETender UZEX' */
  displayName: string;
  /** Fetch all available items from source. Must never throw — return [] on error. */
  fetch(): Promise<RawTenderItem[]>;
}

/** Stats per source after a search run */
export interface SourceStats {
  [sourceName: string]: number;
}

/** Convert a RawTenderItem to the Tender type used across the app */
export function rawToTender(item: RawTenderItem, matchedKeywords: string[]): Tender {
  return {
    id: item.id,
    externalId: item.externalId,
    title: item.title,
    organization: item.organization,
    price: item.price,
    priceFormatted: item.price
      ? new Intl.NumberFormat('ru-RU').format(item.price) + ' ' + item.currency
      : '',
    currency: item.currency,
    deadline: item.deadline,
    dateStart: item.dateStart,
    dateEnd: item.dateEnd,
    region: item.region,
    categories: item.categories,
    source: item.source,
    sourceUrl: item.sourceUrl,
    status: item.status,
    matchedKeywords,
    collectedAt: new Date(),
  };
}
