import type { TenderSourceAdapter, RawTenderItem } from './_types';

// TenderZone.uz requires authentication (SabyTrade platform at trade.tzone.uz)
// Landing page tenderzone.uz has no tender data — only marketing
// TODO: Add auth support when credentials are available

export const tenderzoneAdapter: TenderSourceAdapter = {
  name: 'tenderzone',
  displayName: 'TenderZone.uz',
  async fetch(): Promise<RawTenderItem[]> {
    // Requires auth — return empty until credentials configured
    return [];
  },
};
