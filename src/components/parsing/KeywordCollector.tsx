'use client';

import { useState, useEffect } from 'react';
import { useKeywordStore } from '@/lib/store/keywordStore';
import { SEED_KEYWORDS } from '@/lib/constants/seedKeywords';
import type { Keyword } from '@/types/parsing';

type SuggestSource = 'google' | 'yandex';

export function KeywordCollector() {
  const [input, setInput] = useState(SEED_KEYWORDS.join('\n'));
  const [source, setSource] = useState<SuggestSource>('google');
  const { isLoading, error, progress, setKeywords, setLoading, setError, setProgress, clearAll } =
    useKeywordStore();

  // При загрузке компонента — подтягиваем keywords из Supabase
  useEffect(() => {
    async function loadFromDb() {
      try {
        const res = await fetch('/api/keywords');
        if (!res.ok) return;
        const data = await res.json();
        if (data.keywords && data.keywords.length > 0) {
          const loaded: Keyword[] = data.keywords.map((kw: Keyword) => ({
            ...kw,
            collectedAt: new Date(kw.collectedAt),
          }));
          setKeywords(loaded);
        }
      } catch {
        // Supabase не подключён — работаем без него
      }
    }
    loadFromDb();
  }, [setKeywords]);

  async function handleCollect() {
    const queries = input
      .split('\n')
      .map((q) => q.trim())
      .filter((q) => q.length > 0);

    if (queries.length === 0) {
      setError('Введите хотя бы один запрос');
      return;
    }

    if (queries.length > 50) {
      setError('Максимум 50 запросов за раз');
      return;
    }

    clearAll();
    setLoading(true);
    setProgress({ completed: 0, total: queries.length });

    try {
      const endpoint = source === 'google'
        ? '/api/suggestions/google'
        : '/api/suggestions/yandex';

      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ queries }),
      });

      if (!response.ok) {
        const data = await response.json();
        throw new Error(data.error || `Ошибка сервера: ${response.status}`);
      }

      const data = await response.json();
      // Восстанавливаем даты из JSON
      const keywords: Keyword[] = data.keywords.map((kw: Keyword) => ({
        ...kw,
        collectedAt: new Date(kw.collectedAt),
      }));

      setKeywords(keywords);
      setProgress({ completed: queries.length, total: queries.length });
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Неизвестная ошибка');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {/* Переключатель источника */}
      <div className="flex items-center gap-4">
        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Источник:</span>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="radio"
            name="suggest-source"
            value="google"
            checked={source === 'google'}
            onChange={() => setSource('google')}
            disabled={isLoading}
            className="accent-blue-600"
          />
          <span className="text-sm">Google</span>
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer">
          <input
            type="radio"
            name="suggest-source"
            value="yandex"
            checked={source === 'yandex'}
            onChange={() => setSource('yandex')}
            disabled={isLoading}
            className="accent-blue-600"
          />
          <span className="text-sm">Yandex</span>
        </label>
      </div>

      <div>
        <label
          htmlFor="seed-keywords"
          className="block text-sm font-medium mb-1"
        >
          Базовые запросы (по одному на строку, макс. 50):
        </label>
        <textarea
          id="seed-keywords"
          className="w-full h-48 p-3 border rounded-lg font-mono text-sm bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 focus:ring-2 focus:ring-blue-500 focus:border-transparent"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={isLoading}
          placeholder="картонная упаковка&#10;типография Ташкент&#10;печать визиток"
        />
      </div>

      <div className="flex items-center gap-4">
        <button
          onClick={handleCollect}
          disabled={isLoading}
          className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {isLoading ? 'Сбор...' : 'Собрать подсказки'}
        </button>

        {progress && (
          <span className="text-sm text-gray-600 dark:text-gray-400">
            Обработано: {progress.completed} / {progress.total} запросов
          </span>
        )}
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
          {error}
        </div>
      )}
    </div>
  );
}
