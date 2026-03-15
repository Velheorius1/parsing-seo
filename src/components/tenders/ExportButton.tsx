'use client';

import { useTenderStore } from '@/lib/store/tenderStore';

export function ExportButton() {
  const {
    selectedKeywords,
    filterSource,
    filterRegion,
    filterMinPrice,
    filterMaxPrice,
    filterStatus,
    filterCategory,
    excludeKeywords,
  } = useTenderStore();

  const handleExport = () => {
    const params = new URLSearchParams();

    if (selectedKeywords.length > 0) {
      params.set('keywords', selectedKeywords.join(','));
    }
    if (filterSource) {
      params.set('source', filterSource);
    }
    if (filterRegion) {
      params.set('region', filterRegion);
    }
    if (filterStatus) {
      params.set('status', filterStatus);
    }
    if (filterCategory) {
      params.set('category', filterCategory);
    }
    if (filterMinPrice !== null) {
      params.set('minPrice', String(filterMinPrice));
    }
    if (filterMaxPrice !== null) {
      params.set('maxPrice', String(filterMaxPrice));
    }
    if (excludeKeywords.length > 0) {
      params.set('exclude', excludeKeywords.join(','));
    }

    const url = `/api/tenders/export?${params.toString()}`;
    window.open(url, '_blank');
  };

  return (
    <button
      onClick={handleExport}
      className="inline-flex items-center gap-1.5 rounded-md bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
    >
      <svg
        className="h-4 w-4"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
        />
      </svg>
      Экспорт Excel
    </button>
  );
}
