'use client';

import { useState } from 'react';
import { useTenderStore } from '@/lib/store/tenderStore';

export function TenderKeywords() {
  const [newKeyword, setNewKeyword] = useState('');
  const {
    keywords,
    selectedKeywords,
    isLoading,
    toggleKeyword,
    addKeyword,
    searchTenders,
  } = useTenderStore();

  function handleAddKeyword() {
    const trimmed = newKeyword.trim();
    if (!trimmed) return;
    addKeyword(trimmed);
    setNewKeyword('');
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAddKeyword();
    }
  }

  return (
    <div className="space-y-4">
      {/* Chips */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm font-medium text-gray-400">
            Ключевые слова
          </span>
          {selectedKeywords.length > 0 && (
            <span className="text-xs text-amber-400">
              Выбрано: {selectedKeywords.length}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {keywords.map((keyword) => {
            const isSelected = selectedKeywords.includes(keyword);
            return (
              <button
                key={keyword}
                onClick={() => toggleKeyword(keyword)}
                disabled={isLoading}
                className={`px-3 py-1.5 rounded-lg text-sm font-medium transition-all ${
                  isSelected
                    ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40 shadow-sm shadow-amber-500/10'
                    : 'bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-600 hover:text-gray-300'
                } disabled:opacity-50 disabled:cursor-not-allowed`}
              >
                {keyword}
              </button>
            );
          })}
        </div>
      </div>

      {/* Add custom keyword */}
      <div className="flex gap-2">
        <input
          type="text"
          value={newKeyword}
          onChange={(e) => setNewKeyword(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isLoading}
          placeholder="Добавить ключевое слово..."
          className="flex-1 px-4 py-2 border rounded-lg bg-gray-800 border-gray-700 text-gray-200 placeholder-gray-500 focus:ring-2 focus:ring-amber-500/50 focus:border-amber-500/50 text-sm disabled:opacity-50"
        />
        <button
          onClick={handleAddKeyword}
          disabled={isLoading || !newKeyword.trim()}
          className="px-4 py-2 bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
        >
          Добавить
        </button>
      </div>

      {/* Search button */}
      <button
        onClick={searchTenders}
        disabled={isLoading || selectedKeywords.length === 0}
        className="w-full sm:w-auto px-6 py-2.5 bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm font-medium flex items-center justify-center gap-2"
      >
        {isLoading ? (
          <>
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Поиск тендеров...
          </>
        ) : (
          <>
            Найти тендеры
            {selectedKeywords.length > 0 && (
              <span className="bg-amber-500/30 px-1.5 py-0.5 rounded text-xs">
                {selectedKeywords.length}
              </span>
            )}
          </>
        )}
      </button>
    </div>
  );
}
