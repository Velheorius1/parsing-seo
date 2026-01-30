'use client';

import { useState } from 'react';
import type { SerpResult } from '@/types/parsing';

interface SerpAnalyzerProps {
  onParseSite?: (url: string) => void;
}

export function SerpAnalyzer({ onParseSite }: SerpAnalyzerProps) {
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SerpResult[]>([]);

  async function handleSearch() {
    const q = query.trim();
    if (!q) {
      setError('Введите поисковый запрос');
      return;
    }

    setError(null);
    setResults([]);
    setIsLoading(true);

    try {
      const response = await fetch('/api/serp/google', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Ошибка: ${response.status}`);
      }

      setResults(data.results || []);

      if (data.results.length === 0) {
        setError('Результатов не найдено. Google мог заблокировать запрос — попробуйте позже.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <input
          type="text"
          className="flex-1 px-4 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 focus:ring-2 focus:ring-purple-500 focus:border-transparent text-sm"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !isLoading && handleSearch()}
          disabled={isLoading}
          placeholder="типография Ташкент"
        />
        <button
          onClick={handleSearch}
          disabled={isLoading}
          className="px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap text-sm"
        >
          {isLoading ? 'Поиск...' : 'Топ-10 Google'}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
          {error}
        </div>
      )}

      {results.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-gray-200 dark:border-gray-700">
                <th className="text-left py-2 px-2 w-10">#</th>
                <th className="text-left py-2 px-2">Сайт</th>
                <th className="text-left py-2 px-2 hidden sm:table-cell">Title</th>
                <th className="text-right py-2 px-2 w-24"></th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => (
                <tr
                  key={result.position}
                  className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50"
                >
                  <td className="py-2 px-2 text-gray-500 font-mono">
                    {result.position}
                  </td>
                  <td className="py-2 px-2">
                    <a
                      href={result.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-blue-600 hover:underline text-xs break-all"
                    >
                      {result.domain}
                    </a>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-1 sm:hidden">
                      {result.title}
                    </p>
                  </td>
                  <td className="py-2 px-2 hidden sm:table-cell">
                    <p className="line-clamp-1">{result.title}</p>
                    {result.description && (
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5 line-clamp-1">
                        {result.description}
                      </p>
                    )}
                  </td>
                  <td className="py-2 px-2 text-right">
                    <button
                      onClick={() => onParseSite?.(result.url)}
                      className="text-xs px-2 py-1 bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 rounded hover:bg-green-200 dark:hover:bg-green-900/50 transition-colors"
                    >
                      Парсить
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
