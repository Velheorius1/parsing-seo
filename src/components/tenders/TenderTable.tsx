'use client';

import { useMemo } from 'react';
import { useTenderStore } from '@/lib/store/tenderStore';
import type { Tender } from '@/types/parsing';

// Форматирование "X назад"
function formatTimeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return 'только что';
  if (minutes < 60) return `${minutes} мин назад`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}ч назад`;
  const days = Math.floor(hours / 24);
  return `${days}д назад`;
}

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
    completed: 'bg-blue-500/15 text-blue-400 border-blue-500/30',
  };
  const labels = { active: 'Активный', closed: 'Закрыт', cancelled: 'Отменён', completed: 'Завершён' };

  return (
    <span className={`px-2 py-0.5 rounded text-xs border ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}

// Подсветка совпавших ключевых слов в названии
function HighlightedTitle({ title, keywords }: { title: string; keywords: string[] }) {
  if (keywords.length === 0) {
    return <>{title}</>;
  }

  const escaped = keywords.map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const pattern = new RegExp(`(${escaped.join('|')})`, 'gi');
  const parts = title.split(pattern);

  return (
    <>
      {parts.map((part, i) => {
        const isMatch = escaped.some(
          (esc) => new RegExp(`^${esc}$`, 'i').test(part),
        );
        return isMatch ? (
          <mark key={i} className="bg-amber-500/30 text-amber-300 rounded px-0.5">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        );
      })}
    </>
  );
}

// Расчёт дней до дедлайна
function calcDaysLeft(deadline: string | null): number | null {
  if (!deadline) return null;
  let date: Date;
  if (/^\d{4}-\d{2}-\d{2}/.test(deadline)) {
    date = new Date(deadline);
  } else if (/^\d{2}\.\d{2}\.\d{4}/.test(deadline)) {
    const [dd, mm, yyyy] = deadline.split('.');
    date = new Date(`${yyyy}-${mm}-${dd}`);
  } else {
    return null;
  }
  if (isNaN(date.getTime())) return null;
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  date.setHours(0, 0, 0, 0);
  return Math.ceil((date.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
}

// Бейдж дедлайна с цветовой индикацией
function DeadlineBadge({ deadline }: { deadline: string | null }) {
  const daysLeft = calcDaysLeft(deadline);

  if (!deadline) return <span className="text-gray-500">—</span>;
  if (daysLeft === null) return <span className="text-gray-400">{deadline}</span>;

  if (daysLeft < 0) {
    return (
      <span className="px-2 py-0.5 rounded text-xs border bg-gray-500/15 text-gray-400 border-gray-500/30">
        Истёк
      </span>
    );
  }

  let colorClass: string;
  if (daysLeft <= 1) {
    colorClass = 'bg-red-500/15 text-red-400 border-red-500/30';
  } else if (daysLeft <= 3) {
    colorClass = 'bg-orange-500/15 text-orange-400 border-orange-500/30';
  } else if (daysLeft <= 7) {
    colorClass = 'bg-yellow-500/15 text-yellow-400 border-yellow-500/30';
  } else {
    colorClass = 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
  }

  const label = daysLeft === 0 ? 'Сегодня' : daysLeft === 1 ? '1 день' : `${daysLeft}д`;

  return (
    <span className={`px-2 py-0.5 rounded text-xs border ${colorClass}`}>
      {label}
    </span>
  );
}

// Строка таблицы
function TenderRow({ tender, index }: { tender: Tender; index: number }) {
  const selectedKeywords = useTenderStore((s) => s.selectedKeywords);

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
          {tender.title
            ? <HighlightedTitle title={tender.title} keywords={selectedKeywords} />
            : tender.externalId}
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
        {tender.status === 'completed' && tender.winner && (
          <div className="mt-1.5 flex items-center gap-2 text-[11px] text-blue-400/80">
            <span>Победитель: {tender.winner}</span>
            {tender.winningPrice != null && (
              <span className="text-blue-300 font-medium">
                {new Intl.NumberFormat('ru-RU').format(tender.winningPrice)} {tender.currency}
              </span>
            )}
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
      <td className="px-4 py-3 text-sm whitespace-nowrap">
        <DeadlineBadge deadline={tender.deadline} />
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

// Панель статистики по источникам
function SourceStatsBar() {
  const { sourceStats } = useTenderStore();
  const entries = Object.entries(sourceStats).filter(([, count]) => count > 0);

  if (entries.length === 0) return null;

  const total = entries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className="mb-4 p-3 bg-gray-800/50 rounded-lg border border-gray-800">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500 uppercase tracking-wider">Источники</span>
        <span className="text-xs text-gray-400">
          Просканировано: <span className="text-amber-400 font-medium">{total.toLocaleString('ru-RU')}</span>
        </span>
      </div>
      <div className="flex flex-wrap gap-2">
        {entries.map(([name, count]) => (
          <div
            key={name}
            className="flex items-center gap-1.5 px-2 py-1 bg-gray-900/80 rounded text-xs border border-gray-700/50"
          >
            <div className="w-1.5 h-1.5 rounded-full bg-amber-500/70" />
            <span className="text-gray-400">{name}</span>
            <span className="text-gray-500">{count.toLocaleString('ru-RU')}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// Фильтр по источнику
function SourceFilter() {
  const { tenders, filterSource, setFilterSource } = useTenderStore();

  const sources = useMemo(() => {
    const set = new Set<string>();
    tenders.forEach(t => set.add(t.source));
    return Array.from(set).sort();
  }, [tenders]);

  if (sources.length <= 1) return null;

  return (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500">Площадка:</span>
      <button
        onClick={() => setFilterSource(null)}
        className={`px-2 py-1 rounded text-xs transition-colors ${
          filterSource === null
            ? 'bg-amber-500/20 text-amber-400'
            : 'bg-gray-800 text-gray-500 hover:text-gray-300'
        }`}
      >
        Все
      </button>
      {sources.map((src) => (
        <button
          key={src}
          onClick={() => setFilterSource(filterSource === src ? null : src)}
          className={`px-2 py-1 rounded text-xs transition-colors ${
            filterSource === src
              ? 'bg-amber-500/20 text-amber-400'
              : 'bg-gray-800 text-gray-500 hover:text-gray-300'
          }`}
        >
          {src}
        </button>
      ))}
    </div>
  );
}

// Бейдж "Обновлено X назад" + кнопка обновить
function LastUpdatedBadge() {
  const { lastCrawledAt, isRefreshing, refreshTenders, selectedKeywords } = useTenderStore();

  return (
    <div className="flex items-center gap-3">
      {lastCrawledAt && (
        <span className="text-xs text-gray-500">
          Обновлено: {formatTimeAgo(lastCrawledAt)}
        </span>
      )}
      {selectedKeywords.length > 0 && (
        <button
          onClick={refreshTenders}
          disabled={isRefreshing}
          className="px-3 py-1 rounded text-xs bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25 transition-colors disabled:opacity-50"
        >
          {isRefreshing ? 'Обновление...' : 'Обновить'}
        </button>
      )}
    </div>
  );
}

export function TenderTable() {
  const { tenders, isLoading, isRefreshing, error, totalFound, sortBy, setSortBy, filterSource } = useTenderStore();

  // Фильтрация + сортировка
  const filtered = useMemo(() => {
    let result = tenders;
    if (filterSource) {
      result = result.filter(t => t.source === filterSource);
    }
    if (!sortBy) return result;
    return [...result].sort((a, b) => {
      if (sortBy === 'price-asc') return (a.price ?? 0) - (b.price ?? 0);
      if (sortBy === 'price-desc') return (b.price ?? 0) - (a.price ?? 0);
      if (sortBy === 'deadline') {
        if (!a.deadline) return 1;
        if (!b.deadline) return -1;
        return a.deadline.localeCompare(b.deadline);
      }
      return 0;
    });
  }, [tenders, sortBy, filterSource]);

  // Loading state
  if (isLoading && !isRefreshing) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-16 bg-gray-800/50 rounded-lg animate-pulse" />
        ))}
        <p className="text-center text-sm text-gray-500 mt-4">
          Загрузка тендеров из базы данных...
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
      {/* Source stats bar */}
      <SourceStatsBar />

      {/* Header с количеством, фильтром и сортировкой */}
      <div className="flex flex-col gap-2 mb-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <div className="text-sm text-gray-400">
              Найдено: <span className="text-amber-400 font-medium">{totalFound}</span> тендеров
              {filterSource && (
                <span className="text-gray-500 ml-2">
                  (показано: {filtered.length})
                </span>
              )}
            </div>
            <LastUpdatedBadge />
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

        {/* Source filter */}
        <SourceFilter />
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
            {filtered.map((tender, i) => (
              <TenderRow key={tender.id} tender={tender} index={i} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
