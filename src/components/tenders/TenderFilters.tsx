'use client';

import { useMemo, useState } from 'react';
import { useTenderStore } from '@/lib/store/tenderStore';

function ChipGroup({
  label,
  options,
  selected,
  onSelect,
}: {
  label: string;
  options: string[];
  selected: string | null;
  onSelect: (value: string | null) => void;
}) {
  if (options.length === 0) return null;

  return (
    <div>
      <span className="text-xs text-gray-500 uppercase tracking-wider">{label}</span>
      <div className="flex flex-wrap gap-1.5 mt-1.5">
        <button
          onClick={() => onSelect(null)}
          className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
            selected === null
              ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
              : 'bg-gray-800 text-gray-500 hover:text-gray-300 border border-gray-700/50'
          }`}
        >
          Все
        </button>
        {options.map((opt) => (
          <button
            key={opt}
            onClick={() => onSelect(selected === opt ? null : opt)}
            className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
              selected === opt
                ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                : 'bg-gray-800 text-gray-500 hover:text-gray-300 border border-gray-700/50'
            }`}
          >
            {opt}
          </button>
        ))}
      </div>
    </div>
  );
}

const STATUS_OPTIONS = [
  { value: 'active', label: 'Активный' },
  { value: 'closed', label: 'Закрыт' },
  { value: 'completed', label: 'Завершён' },
  { value: 'cancelled', label: 'Отменён' },
];

export function TenderFilters() {
  const {
    tenders,
    showAdvancedFilters,
    setShowAdvancedFilters,
    filterSource,
    setFilterSource,
    filterRegion,
    setFilterRegion,
    filterStatus,
    setFilterStatus,
    filterCategory,
    setFilterCategory,
    filterMinPrice,
    setFilterMinPrice,
    filterMaxPrice,
    setFilterMaxPrice,
    excludeKeywords,
    addExcludeKeyword,
    removeExcludeKeyword,
    resetFilters,
  } = useTenderStore();

  const [excludeInput, setExcludeInput] = useState('');

  // Extract unique regions, sources, categories from loaded tenders
  const regions = useMemo(() => {
    const set = new Set<string>();
    tenders.forEach((t) => { if (t.region) set.add(t.region); });
    return Array.from(set).sort();
  }, [tenders]);

  const sources = useMemo(() => {
    const set = new Set<string>();
    tenders.forEach((t) => set.add(t.source));
    return Array.from(set).sort();
  }, [tenders]);

  const categories = useMemo(() => {
    const set = new Set<string>();
    tenders.forEach((t) => t.categories.forEach((c) => set.add(c)));
    return Array.from(set).sort();
  }, [tenders]);

  const hasActiveFilters =
    filterSource !== null ||
    filterRegion !== null ||
    filterStatus !== null ||
    filterCategory !== null ||
    filterMinPrice !== null ||
    filterMaxPrice !== null ||
    excludeKeywords.length > 0;

  const handleExcludeKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && excludeInput.trim()) {
      addExcludeKeyword(excludeInput);
      setExcludeInput('');
    }
  };

  return (
    <div>
      <button
        onClick={() => setShowAdvancedFilters(!showAdvancedFilters)}
        className="flex items-center gap-2 text-sm text-gray-400 hover:text-gray-300 transition-colors"
      >
        <svg
          className={`h-4 w-4 transition-transform ${showAdvancedFilters ? 'rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
        </svg>
        Расширенный поиск
        {hasActiveFilters && (
          <span className="w-2 h-2 rounded-full bg-amber-500" />
        )}
      </button>

      {showAdvancedFilters && (
        <div className="mt-3 p-4 bg-gray-900 rounded-xl border border-gray-800 space-y-4">
          {/* Status */}
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wider">Статус</span>
            <div className="flex flex-wrap gap-1.5 mt-1.5">
              <button
                onClick={() => setFilterStatus(null)}
                className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
                  filterStatus === null
                    ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                    : 'bg-gray-800 text-gray-500 hover:text-gray-300 border border-gray-700/50'
                }`}
              >
                Все
              </button>
              {STATUS_OPTIONS.map(({ value, label }) => (
                <button
                  key={value}
                  onClick={() => setFilterStatus(filterStatus === value ? null : value)}
                  className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
                    filterStatus === value
                      ? 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
                      : 'bg-gray-800 text-gray-500 hover:text-gray-300 border border-gray-700/50'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Source */}
          <ChipGroup
            label="Площадка"
            options={sources}
            selected={filterSource}
            onSelect={setFilterSource}
          />

          {/* Region */}
          <ChipGroup
            label="Регион"
            options={regions}
            selected={filterRegion}
            onSelect={setFilterRegion}
          />

          {/* Category */}
          <ChipGroup
            label="Категория"
            options={categories}
            selected={filterCategory}
            onSelect={setFilterCategory}
          />

          {/* Price range */}
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wider">Цена (UZS)</span>
            <div className="flex items-center gap-2 mt-1.5">
              <input
                type="number"
                placeholder="от"
                value={filterMinPrice ?? ''}
                onChange={(e) => setFilterMinPrice(e.target.value ? Number(e.target.value) : null)}
                className="w-32 px-3 py-1.5 rounded-md text-sm bg-gray-800 border border-gray-700/50 text-gray-300 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
              />
              <span className="text-gray-600">—</span>
              <input
                type="number"
                placeholder="до"
                value={filterMaxPrice ?? ''}
                onChange={(e) => setFilterMaxPrice(e.target.value ? Number(e.target.value) : null)}
                className="w-32 px-3 py-1.5 rounded-md text-sm bg-gray-800 border border-gray-700/50 text-gray-300 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
              />
            </div>
          </div>

          {/* Exclude keywords */}
          <div>
            <span className="text-xs text-gray-500 uppercase tracking-wider">Исключить слова</span>
            <div className="mt-1.5">
              <input
                type="text"
                placeholder="Введите слово и нажмите Enter"
                value={excludeInput}
                onChange={(e) => setExcludeInput(e.target.value)}
                onKeyDown={handleExcludeKeyDown}
                className="w-full max-w-xs px-3 py-1.5 rounded-md text-sm bg-gray-800 border border-gray-700/50 text-gray-300 placeholder-gray-600 focus:border-amber-500/50 focus:outline-none"
              />
              {excludeKeywords.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {excludeKeywords.map((kw) => (
                    <button
                      key={kw}
                      onClick={() => removeExcludeKeyword(kw)}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs bg-red-500/15 text-red-400 border border-red-500/30 hover:bg-red-500/25 transition-colors"
                    >
                      {kw}
                      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                      </svg>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Reset */}
          {hasActiveFilters && (
            <button
              onClick={resetFilters}
              className="px-3 py-1.5 rounded-md text-xs text-gray-400 hover:text-gray-300 bg-gray-800 border border-gray-700/50 hover:border-gray-600 transition-colors"
            >
              Сбросить фильтры
            </button>
          )}
        </div>
      )}
    </div>
  );
}
