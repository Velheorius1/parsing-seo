'use client';

import { useKeywordStore } from '@/lib/store/keywordStore';
import { exportKeywordsToCSV } from '@/lib/utils/csvExport';

export function ExportButton() {
  const { keywords } = useKeywordStore();
  const disabled = keywords.length === 0;

  function handleExport() {
    exportKeywordsToCSV(keywords);
  }

  return (
    <button
      onClick={handleExport}
      disabled={disabled}
      className="px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
    >
      Экспорт CSV ({keywords.length})
    </button>
  );
}
