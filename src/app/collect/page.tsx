'use client';

import { useState } from 'react';
import * as XLSX from 'xlsx';

interface CollectResultItem {
  keyword: string;
  baseQuery: string;
  trackPosition: number | null;
  trackUrl: string | null;
  top3: { position: number; domain: string; url: string; title: string }[];
}

export default function CollectPage() {
  const [queriesText, setQueriesText] = useState('');
  const [trackDomain, setTrackDomain] = useState('winch.uz');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<CollectResultItem[]>([]);
  const [progress, setProgress] = useState('');

  async function handleCollect() {
    const lines = queriesText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.length > 0);

    if (lines.length === 0) {
      setError('Введите хотя бы один запрос');
      return;
    }

    if (lines.length > 20) {
      setError('Максимум 20 базовых запросов за раз');
      return;
    }

    setError(null);
    setResults([]);
    setIsLoading(true);
    setProgress(`Собираем подсказки и проверяем SERP для ${lines.length} запросов...`);

    try {
      const response = await fetch('/api/collect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          queries: lines,
          trackDomain: trackDomain.trim() || undefined,
        }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || 'Ошибка сервера');
      }

      setResults(data.results || []);
      setProgress(`Найдено ${data.total} запросов`);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка');
      setProgress('');
    } finally {
      setIsLoading(false);
    }
  }

  function handleExport() {
    if (results.length === 0) return;

    const domain = trackDomain.trim() || 'tracked';

    const rows = results.map((r) => ({
      'Найденный запрос': r.keyword,
      'Базовый запрос': r.baseQuery,
      [`Позиция ${domain}`]: r.trackPosition ?? 'Не найден',
      [`URL ${domain}`]: r.trackUrl ?? '',
      'Топ-1': r.top3[0]?.domain ?? '',
      'Топ-2': r.top3[1]?.domain ?? '',
      'Топ-3': r.top3[2]?.domain ?? '',
    }));

    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Семантика');

    const colWidths = Object.keys(rows[0] || {}).map((key) => ({
      wch: Math.max(key.length, 20),
    }));
    ws['!cols'] = colWidths;

    XLSX.writeFile(wb, `semantics-${domain}.xlsx`);
  }

  const domain = trackDomain.trim().toUpperCase() || 'ДОМЕН';

  // Summary stats
  const inTop3 = results.filter((r) => r.trackPosition !== null && r.trackPosition <= 3).length;
  const inTop10 = results.filter((r) => r.trackPosition !== null && r.trackPosition > 3 && r.trackPosition <= 10).length;
  const notFound = results.filter((r) => r.trackPosition === null).length;

  return (
    <div className="min-h-screen p-6 sm:p-10 max-w-6xl mx-auto font-[family-name:var(--font-geist-sans)]">
      <header className="mb-8">
        <div className="flex items-center gap-4">
          <a
            href="/"
            className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            &larr; Главная
          </a>
          <h1 className="text-2xl font-bold">Собрать семантику</h1>
        </div>
        <p className="text-gray-600 dark:text-gray-400 text-sm mt-1">
          Введите темы — получите подсказки Google + позиции конкурентов для каждого запроса
        </p>
      </header>

      <main className="space-y-6">
        {/* Input */}
        <section>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="sm:col-span-2">
              <label className="block text-sm font-medium mb-1">
                Базовые запросы (по одному на строку, макс. 20)
              </label>
              <textarea
                value={queriesText}
                onChange={(e) => setQueriesText(e.target.value)}
                rows={5}
                disabled={isLoading}
                placeholder={'упаковка для БАДов\nкартонная коробка\nкрафт пакеты'}
                className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm resize-y"
              />
            </div>
            <div className="space-y-3">
              <div>
                <label className="block text-sm font-medium mb-1">Домен для отслеживания</label>
                <input
                  type="text"
                  value={trackDomain}
                  onChange={(e) => setTrackDomain(e.target.value)}
                  placeholder="winch.uz"
                  disabled={isLoading}
                  className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm"
                />
              </div>
              <button
                onClick={handleCollect}
                disabled={isLoading}
                className="w-full px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
              >
                {isLoading ? 'Сбор...' : 'Собрать семантику'}
              </button>
            </div>
          </div>

          <p className="mt-2 text-xs text-gray-400">
            Для каждого запроса: Google Suggest (~10 подсказок) + проверка SERP топ-3 для каждой.
            1 запрос ~ 10-15 credits Serper.
          </p>
        </section>

        {/* Progress */}
        {(isLoading || progress) && (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            {isLoading && (
              <div className="w-4 h-4 border-2 border-purple-500 border-t-transparent rounded-full animate-spin" />
            )}
            <span>{progress}</span>
          </div>
        )}

        {/* Error */}
        {error && (
          <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Results */}
        {results.length > 0 && (
          <section>
            {/* Summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              <SummaryCard label="Всего запросов" value={results.length} color="gray" />
              <SummaryCard label="В топ-3" value={inTop3} color="green" />
              <SummaryCard label="В топ-10" value={inTop10} color="yellow" />
              <SummaryCard label="Не в топе" value={notFound} color="red" />
            </div>

            {/* Export */}
            <button
              onClick={handleExport}
              className="mb-4 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm"
            >
              Скачать результаты (Excel)
            </button>

            {/* Table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-700">
                    <th className="text-left py-2 px-2">Найденный запрос</th>
                    <th className="text-left py-2 px-2 hidden sm:table-cell text-xs text-gray-400">Базовый</th>
                    <th className="text-left py-2 px-2 hidden md:table-cell">#1</th>
                    <th className="text-left py-2 px-2 hidden md:table-cell">#2</th>
                    <th className="text-left py-2 px-2 hidden md:table-cell">#3</th>
                    <th className="text-center py-2 px-2 w-20">{domain}</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50"
                    >
                      <td className="py-2 px-2 text-xs">{r.keyword}</td>
                      <td className="py-2 px-2 hidden sm:table-cell text-xs text-gray-400">
                        {r.baseQuery}
                      </td>
                      <td className="py-2 px-2 hidden md:table-cell text-xs text-gray-500 max-w-[180px] truncate">
                        {r.top3[0]?.domain ?? ''}
                      </td>
                      <td className="py-2 px-2 hidden md:table-cell text-xs text-gray-500 max-w-[180px] truncate">
                        {r.top3[1]?.domain ?? ''}
                      </td>
                      <td className="py-2 px-2 hidden md:table-cell text-xs text-gray-500 max-w-[180px] truncate">
                        {r.top3[2]?.domain ?? ''}
                      </td>
                      <td className="py-2 px-2 text-center">
                        {r.trackPosition !== null ? (
                          <PositionBadge position={r.trackPosition} />
                        ) : (
                          <span className="text-xs text-gray-400">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: 'green' | 'yellow' | 'red' | 'gray';
}) {
  const colors = {
    green: 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-400',
    yellow: 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400',
    red: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-700 dark:text-red-400',
    gray: 'bg-gray-50 dark:bg-gray-800/50 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300',
  };

  return (
    <div className={`p-3 rounded-lg border ${colors[color]}`}>
      <p className="text-2xl font-bold">{value}</p>
      <p className="text-xs">{label}</p>
    </div>
  );
}

function PositionBadge({ position }: { position: number }) {
  let color = 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
  if (position <= 3) {
    color = 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400';
  } else if (position <= 10) {
    color = 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400';
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${color}`}>
      #{position}
    </span>
  );
}
