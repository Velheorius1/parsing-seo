'use client';

import { useState, useEffect, useRef } from 'react';
import type { Keyword } from '@/types/parsing';

interface ParsedPageResult {
  url: string;
  domain: string;
  title: string | null;
  h1: string | null;
  metaDescription: string | null;
  metaKeywords: string[];
}

interface AggregatedKeyword {
  keyword: string;
  count: number;
  pages: string[];
}

interface SiteAnalyzerProps {
  initialUrl?: string;
}

export function SiteAnalyzer({ initialUrl }: SiteAnalyzerProps) {
  const [url, setUrl] = useState('');
  const [maxPages, setMaxPages] = useState(1);
  const prevInitialUrl = useRef('');

  useEffect(() => {
    if (initialUrl && initialUrl !== prevInitialUrl.current) {
      prevInitialUrl.current = initialUrl;
      setUrl(initialUrl);
      analyzeUrl(initialUrl);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialUrl]);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Single page mode
  const [page, setPage] = useState<ParsedPageResult | null>(null);
  const [keywords, setKeywords] = useState<Keyword[]>([]);

  // Crawl mode
  const [crawlPages, setCrawlPages] = useState<ParsedPageResult[]>([]);
  const [aggregatedKeywords, setAggregatedKeywords] = useState<AggregatedKeyword[]>([]);
  const [crawlDomain, setCrawlDomain] = useState<string | null>(null);
  const [showPagesTable, setShowPagesTable] = useState(false);

  async function analyzeUrl(targetUrl: string) {
    if (!targetUrl.startsWith('http://') && !targetUrl.startsWith('https://')) {
      targetUrl = 'https://' + targetUrl;
    }

    setError(null);
    setPage(null);
    setKeywords([]);
    setCrawlPages([]);
    setAggregatedKeywords([]);
    setCrawlDomain(null);
    setIsLoading(true);

    try {
      const response = await fetch('/api/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: targetUrl, maxPages }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Ошибка: ${response.status}`);
      }

      if (data.mode === 'crawl') {
        setCrawlPages(data.pages || []);
        setAggregatedKeywords(data.aggregatedKeywords || []);
        setCrawlDomain(data.domain || null);
      } else {
        setPage(data.page);
        setKeywords(data.keywords || []);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка');
    } finally {
      setIsLoading(false);
    }
  }

  async function handleAnalyze() {
    const targetUrl = url.trim();
    if (!targetUrl) {
      setError('Введите URL');
      return;
    }
    analyzeUrl(targetUrl);
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-3">
        <input
          type="text"
          className="flex-1 px-4 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !isLoading && handleAnalyze()}
          disabled={isLoading}
          placeholder="https://example.com"
        />
        <select
          className="px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm"
          value={maxPages}
          onChange={(e) => setMaxPages(Number(e.target.value))}
          disabled={isLoading}
        >
          <option value={1}>1 страница</option>
          <option value={10}>10 страниц</option>
          <option value={20}>20 страниц</option>
          <option value={50}>50 страниц</option>
        </select>
        <button
          onClick={handleAnalyze}
          disabled={isLoading}
          className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap text-sm"
        >
          {isLoading ? 'Анализ...' : 'Анализировать'}
        </button>
      </div>

      {isLoading && maxPages > 1 && (
        <div className="p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg text-blue-700 dark:text-blue-300 text-sm flex items-center gap-2">
          <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Краулинг до {maxPages} страниц... (~{maxPages * 2} сек)
          {maxPages >= 50 && (
            <span className="text-yellow-600 dark:text-yellow-400 ml-2">
              (может превысить timeout сервера)
            </span>
          )}
        </div>
      )}

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Single page result */}
      {page && (
        <div className="space-y-4">
          <div className="p-4 bg-gray-50 dark:bg-gray-800 rounded-lg space-y-2 text-sm">
            <div>
              <span className="font-medium text-gray-500 dark:text-gray-400">URL: </span>
              <a href={page.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline break-all">
                {page.url}
              </a>
            </div>
            <div>
              <span className="font-medium text-gray-500 dark:text-gray-400">Title: </span>
              <span>{page.title || '—'}</span>
            </div>
            <div>
              <span className="font-medium text-gray-500 dark:text-gray-400">H1: </span>
              <span>{page.h1 || '—'}</span>
            </div>
            <div>
              <span className="font-medium text-gray-500 dark:text-gray-400">Description: </span>
              <span className="text-gray-700 dark:text-gray-300">{page.metaDescription || '—'}</span>
            </div>
            {page.metaKeywords.length > 0 && (
              <div>
                <span className="font-medium text-gray-500 dark:text-gray-400">Meta Keywords: </span>
                <span>{page.metaKeywords.join(', ')}</span>
              </div>
            )}
          </div>

          {keywords.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold mb-2">
                Извлечённые ключевые слова ({keywords.length}):
              </h3>
              <div className="flex flex-wrap gap-2">
                {keywords.map((kw, i) => (
                  <span
                    key={i}
                    className="px-2 py-1 bg-blue-100 dark:bg-blue-900/30 text-blue-800 dark:text-blue-300 rounded text-xs"
                  >
                    {kw.keyword}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* Crawl result */}
      {crawlPages.length > 0 && (
        <div className="space-y-4">
          {/* Summary */}
          <div className="p-4 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg text-sm">
            <div className="font-semibold text-green-800 dark:text-green-300 mb-1">
              Краулинг завершён: {crawlDomain}
            </div>
            <div className="text-green-700 dark:text-green-400">
              Найдено {crawlPages.length} страниц, {aggregatedKeywords.length} ключевых слов
            </div>
          </div>

          {/* Pages table (collapsible) */}
          <div>
            <button
              onClick={() => setShowPagesTable(!showPagesTable)}
              className="text-sm font-semibold mb-2 flex items-center gap-1 hover:text-blue-600 transition-colors"
            >
              <span className="text-xs">{showPagesTable ? '▼' : '▶'}</span>
              Страницы ({crawlPages.length})
            </button>
            {showPagesTable && (
              <div className="overflow-x-auto border rounded-lg dark:border-gray-700">
                <table className="w-full text-xs">
                  <thead className="bg-gray-100 dark:bg-gray-800">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 dark:text-gray-400">#</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 dark:text-gray-400">URL</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 dark:text-gray-400">Title</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 dark:text-gray-400">H1</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y dark:divide-gray-700">
                    {crawlPages.map((p, i) => (
                      <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                        <td className="px-3 py-2 text-gray-500">{i + 1}</td>
                        <td className="px-3 py-2 max-w-xs truncate">
                          <a href={p.url} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                            {p.url.replace(/^https?:\/\/[^/]+/, '')}
                          </a>
                        </td>
                        <td className="px-3 py-2 max-w-xs truncate">{p.title || '—'}</td>
                        <td className="px-3 py-2 max-w-xs truncate">{p.h1 || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {/* Aggregated keywords */}
          {aggregatedKeywords.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold mb-2">
                Ключевые слова ({aggregatedKeywords.length}):
              </h3>
              <div className="overflow-x-auto border rounded-lg dark:border-gray-700">
                <table className="w-full text-xs">
                  <thead className="bg-gray-100 dark:bg-gray-800">
                    <tr>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 dark:text-gray-400">Ключевое слово</th>
                      <th className="px-3 py-2 text-left font-medium text-gray-600 dark:text-gray-400">Страниц</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y dark:divide-gray-700">
                    {aggregatedKeywords.slice(0, 100).map((ak, i) => (
                      <tr key={i} className="hover:bg-gray-50 dark:hover:bg-gray-800/50">
                        <td className="px-3 py-2">{ak.keyword}</td>
                        <td className="px-3 py-2">
                          <span className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${
                            ak.count > 1
                              ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300'
                              : 'bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400'
                          }`}>
                            {ak.count}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {aggregatedKeywords.length > 100 && (
                  <div className="px-3 py-2 text-xs text-gray-500 bg-gray-50 dark:bg-gray-800">
                    Показано 100 из {aggregatedKeywords.length}
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
