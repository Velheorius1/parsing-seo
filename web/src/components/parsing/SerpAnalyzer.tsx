'use client';

import { useState } from 'react';
import type { SerpResult } from '@/types/parsing';
import type { SeoAnalysis } from '@/lib/utils/seoAnalyzer';

interface SerpAnalyzerProps {
  onParseSite?: (url: string) => void;
}

function ScoreBadge({ score }: { score: number }) {
  let color = 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
  if (score >= 70) {
    color = 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400';
  } else if (score >= 40) {
    color = 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400';
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${color}`}>
      {score}/100
    </span>
  );
}

function WordTags({ words, type }: { words: string[]; type: 'match' | 'missing' }) {
  if (words.length === 0) return null;
  const color =
    type === 'match'
      ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
      : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
  return (
    <span className="inline-flex flex-wrap gap-1">
      {words.map((w) => (
        <span key={w} className={`px-1.5 py-0.5 rounded text-xs ${color}`}>
          {w}
        </span>
      ))}
    </span>
  );
}

function SeoAnalysisPanel({
  analysis,
  onClose,
}: {
  analysis: SeoAnalysis;
  onClose: () => void;
}) {
  const fields = [
    { label: 'Title', data: analysis.title },
    { label: 'H1', data: analysis.h1 },
    { label: 'Description', data: analysis.description },
  ] as const;

  return (
    <div className="mt-4 p-4 bg-gray-50 dark:bg-gray-800/50 border border-gray-200 dark:border-gray-700 rounded-lg space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h3 className="text-sm font-semibold">SEO-анализ</h3>
          <ScoreBadge score={analysis.score} />
        </div>
        <button
          onClick={onClose}
          className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-lg leading-none"
        >
          &times;
        </button>
      </div>

      <p className="text-xs text-gray-500 dark:text-gray-400">
        Запрос: <span className="font-medium text-gray-700 dark:text-gray-300">{analysis.query}</span>
        {' | '}Слова: {analysis.queryWords.join(', ')}
      </p>

      <div className="space-y-2">
        {fields.map(({ label, data }) => (
          <div key={label} className="text-xs">
            <div className="flex items-start gap-2">
              <span className="font-semibold w-20 shrink-0 pt-0.5">{label}:</span>
              <span className="text-gray-600 dark:text-gray-400 line-clamp-2">
                {data.text || <span className="italic text-red-400">отсутствует</span>}
              </span>
            </div>
            {(data.matches.length > 0 || data.missing.length > 0) && (
              <div className="ml-[5.5rem] mt-1 flex flex-wrap items-center gap-2">
                <WordTags words={data.matches} type="match" />
                <WordTags words={data.missing} type="missing" />
              </div>
            )}
          </div>
        ))}
      </div>

      {analysis.recommendations.length > 0 && (
        <div className="pt-2 border-t border-gray-200 dark:border-gray-700">
          <p className="text-xs font-semibold mb-1">Рекомендации:</p>
          <ul className="text-xs text-gray-600 dark:text-gray-400 space-y-0.5">
            {analysis.recommendations.map((rec, i) => (
              <li key={i}>• {rec}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function SerpAnalyzer({ onParseSite }: SerpAnalyzerProps) {
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<SerpResult[]>([]);

  const [seoAnalysis, setSeoAnalysis] = useState<SeoAnalysis | null>(null);
  const [seoLoading, setSeoLoading] = useState<string | null>(null); // url being analyzed
  const [seoError, setSeoError] = useState<string | null>(null);

  async function handleSearch() {
    const q = query.trim();
    if (!q) {
      setError('Введите поисковый запрос');
      return;
    }

    setError(null);
    setResults([]);
    setSeoAnalysis(null);
    setSeoError(null);
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

  async function handleSeoAnalyze(url: string) {
    const q = query.trim();
    if (!q) return;

    setSeoAnalysis(null);
    setSeoError(null);
    setSeoLoading(url);

    try {
      const response = await fetch('/api/seo-analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, url }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Ошибка: ${response.status}`);
      }

      setSeoAnalysis(data.analysis);
    } catch (err) {
      setSeoError(err instanceof Error ? err.message : 'Не удалось выполнить анализ');
    } finally {
      setSeoLoading(null);
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
                <th className="text-right py-2 px-2 w-36"></th>
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
                  <td className="py-2 px-2 text-right space-x-1">
                    <button
                      onClick={() => handleSeoAnalyze(result.url)}
                      disabled={seoLoading === result.url}
                      className="text-xs px-2 py-1 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 rounded hover:bg-blue-200 dark:hover:bg-blue-900/50 transition-colors disabled:opacity-50"
                    >
                      {seoLoading === result.url ? 'Анализ...' : 'SEO'}
                    </button>
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

      {seoError && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
          {seoError}
        </div>
      )}

      {seoAnalysis && (
        <SeoAnalysisPanel
          analysis={seoAnalysis}
          onClose={() => setSeoAnalysis(null)}
        />
      )}
    </div>
  );
}
