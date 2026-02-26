'use client';

import { useMemo } from 'react';
import { useTenderStore } from '@/lib/store/tenderStore';
import type { Tender } from '@/types/parsing';

// Форматирование цены
function formatPrice(price: number | null, currency: string): string {
  if (price === null) return '—';
  return new Intl.NumberFormat('ru-RU').format(price) + ' ' + currency;
}

// Бейдж статуса
function StatusBadge({ status }: { status: Tender['status'] }) {
  const styles = {
    active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    closed: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
    cancelled: 'bg-red-500/15 text-red-400 border-red-500/30',
  };
  const labels = { active: 'Активный', closed: 'Закрыт', cancelled: 'Отменён' };

  return (
    <span className={`px-2 py-0.5 rounded text-xs border ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}

// Строка таблицы
function TenderRow({ tender, index }: { tender: Tender; index: number }) {
  return (
    <tr
      className="border-b border-gray-800 hover:bg-gray-800/50 transition-colors"
      style={{ animationDelay: `${index * 30}ms` }}
    >
      {/* Название + ключевые слова */}
      <td className="px-4 py-3 max-w-xs">
        <a
          href={tender.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="text-sm text-amber-400 hover:text-amber-300 hover:underline block truncate"
          title={tender.title}
        >
          {tender.title || tender.externalId}
        </a>
        {tender.matchedKeywords.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {tender.matchedKeywords.map((kw) => (
              <span key={kw} className="text-[10px] px-1.5 py-0.5 bg-amber-500/10 text-amber-500/70 rounded">
                {kw}
              </span>
            ))}
          </div>
        )}
      </td>

      {/* Заказчик */}
      <td className="px-4 py-3 text-sm text-gray-300 max-w-[200px] truncate" title={tender.organization}>
        {tender.organization || '—'}
      </td>

      {/* Сумма */}
      <td className="px-4 py-3 text-sm text-right whitespace-nowrap">
        <span className={tender.price ? 'text-green-400 font-medium' : 'text-gray-500'}>
          {formatPrice(tender.price, tender.currency)}
        </span>
      </td>

      {/* Дедлайн */}
      <td className="px-4 py-3 text-sm text-gray-400 whitespace-nowrap">
        {tender.deadline || '—'}
      </td>

      {/* Статус */}
      <td className="px-4 py-3">
        <StatusBadge status={tender.status} />
      </td>

      {/* Площадка */}
      <td className="px-4 py-3 text-xs text-gray-500">
        {tender.source}
      </td>
    </tr>
  );
}

export function TenderTable() {
  const { tenders, isLoading, error, totalFound, sortBy, setSortBy } = useTenderStore();

  // Сортировка
  const sorted = useMemo(() => {
    if (!sortBy) return tenders;
    return [...tenders].sort((a, b) => {
      if (sortBy === 'price-asc') return (a.price ?? 0) - (b.price ?? 0);
      if (sortBy === 'price-desc') return (b.price ?? 0) - (a.price ?? 0);
      if (sortBy === 'deadline') {
        if (!a.deadline) return 1;
        if (!b.deadline) return -1;
        return a.deadline.localeCompare(b.deadline);
      }
      return 0;
    });
  }, [tenders, sortBy]);

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-16 bg-gray-800/50 rounded-lg animate-pulse" />
        ))}
        <p className="text-center text-sm text-gray-500 mt-4">
          Поиск тендеров... Это может занять до минуты
        </p>
      </div>
    );
  }

  // Error
  if (error) {
    return (
      <div className="p-4 bg-red-900/20 border border-red-800 rounded-lg text-red-400 text-sm">
        {error}
      </div>
    );
  }

  // Empty state
  if (tenders.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <div className="text-4xl mb-3">&#128269;</div>
        <p className="text-sm">Выберите ключевые слова и нажмите «Найти тендеры»</p>
      </div>
    );
  }

  return (
    <div>
      {/* Header с количеством и сортировкой */}
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm text-gray-400">
          Найдено: <span className="text-amber-400 font-medium">{totalFound}</span> тендеров
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-500">Сортировка:</span>
          {[
            { key: 'price-desc' as const, label: 'Цена ↓' },
            { key: 'price-asc' as const, label: 'Цена ↑' },
            { key: 'deadline' as const, label: 'Дедлайн' },
          ].map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setSortBy(sortBy === key ? null : key)}
              className={`px-2 py-1 rounded text-xs transition-colors ${
                sortBy === key
                  ? 'bg-amber-500/20 text-amber-400'
                  : 'bg-gray-800 text-gray-500 hover:text-gray-300'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Таблица */}
      <div className="overflow-x-auto rounded-lg border border-gray-800">
        <table className="w-full">
          <thead>
            <tr className="bg-gray-800/70 text-left text-xs text-gray-500 uppercase tracking-wider">
              <th className="px-4 py-3">Тендер</th>
              <th className="px-4 py-3">Заказчик</th>
              <th className="px-4 py-3 text-right">Сумма</th>
              <th className="px-4 py-3">Дедлайн</th>
              <th className="px-4 py-3">Статус</th>
              <th className="px-4 py-3">Площадка</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((tender, i) => (
              <TenderRow key={tender.id} tender={tender} index={i} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
