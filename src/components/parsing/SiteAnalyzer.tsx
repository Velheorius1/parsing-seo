'use client';

import { useState } from 'react';
import type { Keyword } from '@/types/parsing';

interface ParsedPageResult {
  url: string;
  domain: string;
  title: string | null;
  h1: string | null;
  metaDescription: string | null;
  metaKeywords: string[];
}

export function SiteAnalyzer() {
  const [url, setUrl] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState<ParsedPageResult | null>(null);
  const [keywords, setKeywords] = useState<Keyword[]>([]);

  async function handleAnalyze() {
    let targetUrl = url.trim();
    if (!targetUrl) {
      setError('Введите URL');
      return;
    }

    // Добавляем https:// если нет протокола
    if (!targetUrl.startsWith('http://') && !targetUrl.startsWith('https://')) {
      targetUrl = 'https://' + targetUrl;
    }

    setError(null);
    setPage(null);
    setKeywords([]);
    setIsLoading(true);

    try {
      const response = await fetch('/api/parse', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: targetUrl }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Ошибка: ${response.status}`);
      }

      setPage(data.page);
      setKeywords(data.keywords || []);
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
          className="flex-1 px-4 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 focus:ring-2 focus:ring-blue-500 focus:border-transparent text-sm"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && !isLoading && handleAnalyze()}
          disabled={isLoading}
          placeholder="https://example.com"
        />
        <button
          onClick={handleAnalyze}
          disabled={isLoading}
          className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors whitespace-nowrap text-sm"
        >
          {isLoading ? 'Анализ...' : 'Анализировать'}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
          {error}
        </div>
      )}

      {page && (
        <div className="space-y-4">
          {/* Мета-данные страницы */}
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

          {/* Извлечённые ключевые слова */}
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
    </div>
  );
}
