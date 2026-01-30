import Papa from 'papaparse';
import type { Keyword } from '@/types/parsing';

interface ExportOptions {
  filename?: string;
}

/**
 * Экспортировать ключевые слова в CSV-файл.
 * BOM символ \ufeff обеспечивает корректное отображение кириллицы в Excel.
 */
export function exportKeywordsToCSV(
  keywords: Keyword[],
  options: ExportOptions = {},
): void {
  const { filename = 'keywords-export.csv' } = options;

  // Преобразуем данные в формат для CSV
  const rows = keywords.map((kw) => ({
    'Ключевое слово': kw.keyword,
    'Источник': kw.source,
    'Базовый запрос': kw.baseQuery || '',
    'Частотность': kw.searchVolume ?? '',
    'Дата сбора': kw.collectedAt.toLocaleDateString('ru-RU'),
  }));

  const csv = Papa.unparse(rows);

  // BOM для корректной кириллицы в Excel
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);

  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();

  URL.revokeObjectURL(url);
}
