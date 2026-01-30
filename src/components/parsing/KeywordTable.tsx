'use client';

import { useKeywordStore } from '@/lib/store/keywordStore';

// Человекочитаемые названия источников
const SOURCE_LABELS: Record<string, string> = {
  yandex_suggest: 'Яндекс Подсказки',
  google_suggest: 'Google Подсказки',
  wordstat: 'Wordstat',
  competitor: 'Конкурент',
};

export function KeywordTable() {
  const { keywords } = useKeywordStore();

  if (keywords.length === 0) {
    return (
      <p className="text-gray-500 dark:text-gray-400 text-sm">
        Нет данных. Нажмите &laquo;Собрать подсказки&raquo; для начала.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-200 dark:border-gray-700">
            <th className="text-left p-3 font-medium">#</th>
            <th className="text-left p-3 font-medium">Ключевое слово</th>
            <th className="text-left p-3 font-medium">Источник</th>
            <th className="text-left p-3 font-medium">Базовый запрос</th>
            <th className="text-left p-3 font-medium">Дата</th>
          </tr>
        </thead>
        <tbody>
          {keywords.map((kw, index) => (
            <tr
              key={`${kw.keyword}-${kw.source}`}
              className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-900"
            >
              <td className="p-3 text-gray-400">{index + 1}</td>
              <td className="p-3 font-medium">{kw.keyword}</td>
              <td className="p-3 text-gray-600 dark:text-gray-400">
                {SOURCE_LABELS[kw.source] || kw.source}
              </td>
              <td className="p-3 text-gray-500 dark:text-gray-500">
                {kw.baseQuery || '—'}
              </td>
              <td className="p-3 text-gray-500 dark:text-gray-500">
                {kw.collectedAt.toLocaleDateString('ru-RU')}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-gray-400">
        Всего: {keywords.length} ключевых слов
      </p>
    </div>
  );
}
