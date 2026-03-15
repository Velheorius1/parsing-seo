'use client';

import { useState, useEffect } from 'react';

interface BuyerStat {
  organization: string;
  count: number;
  total: number;
}

interface RegionStat {
  region: string;
  count: number;
  total: number;
}

interface CategoryStat {
  category: string;
  count: number;
  total: number;
}

interface AnalyticsData {
  topBuyers: BuyerStat[];
  regionStats: RegionStat[];
  categoryStats: CategoryStat[];
  avgDiscount: number | null;
  totalWithWinner: number;
}

type TabKey = 'buyers' | 'regions' | 'categories' | 'discount';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'buyers', label: 'Заказчики' },
  { key: 'regions', label: 'Регионы' },
  { key: 'categories', label: 'Категории' },
  { key: 'discount', label: 'Снижение цен' },
];

function formatSum(value: number): string {
  if (value >= 1_000_000_000) {
    return (value / 1_000_000_000).toFixed(1) + ' млрд';
  }
  if (value >= 1_000_000) {
    return (value / 1_000_000).toFixed(1) + ' млн';
  }
  if (value >= 1_000) {
    return (value / 1_000).toFixed(0) + ' тыс';
  }
  return value.toLocaleString('ru-RU');
}

function LoadingSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="h-10 bg-gray-800/50 rounded animate-pulse" />
      ))}
    </div>
  );
}

function RankTable({
  rows,
  nameLabel,
}: {
  rows: Array<{ name: string; count: number; total: number }>;
  nameLabel: string;
}) {
  if (rows.length === 0) {
    return <p className="text-sm text-gray-500 text-center py-6">Нет данных</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
            <th className="px-3 py-2 w-10">#</th>
            <th className="px-3 py-2">{nameLabel}</th>
            <th className="px-3 py-2 text-right">Кол-во</th>
            <th className="px-3 py-2 text-right">Сумма (UZS)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={row.name}
              className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors"
            >
              <td className="px-3 py-2 text-gray-500">{i + 1}</td>
              <td className="px-3 py-2 text-gray-300 max-w-[280px] truncate" title={row.name}>
                {row.name}
              </td>
              <td className="px-3 py-2 text-right text-amber-400 font-medium">
                {row.count.toLocaleString('ru-RU')}
              </td>
              <td className="px-3 py-2 text-right text-green-400">
                {formatSum(row.total)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function DiscountTab({ avgDiscount, totalWithWinner }: { avgDiscount: number | null; totalWithWinner: number }) {
  if (avgDiscount === null) {
    return (
      <div className="text-center py-8">
        <p className="text-gray-500 text-sm">Нет данных о победителях торгов</p>
        <p className="text-gray-600 text-xs mt-1">
          Снижение рассчитывается по тендерам с указанной ценой победителя
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-6 py-4">
      <div className="text-center">
        <div className="text-4xl font-bold text-amber-400">
          {avgDiscount.toFixed(1)}%
        </div>
        <p className="text-sm text-gray-400 mt-2">
          Среднее снижение цены на торгах
        </p>
        <p className="text-xs text-gray-600 mt-1">
          На основе {totalWithWinner} тендеров с результатами
        </p>
      </div>

      <div className="bg-gray-800/50 rounded-lg p-4 border border-gray-800">
        <div className="flex items-center justify-between text-xs text-gray-500 mb-2">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
        <div className="w-full h-3 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full bg-gradient-to-r from-amber-500 to-amber-400 rounded-full transition-all duration-700"
            style={{ width: `${Math.min(avgDiscount, 100)}%` }}
          />
        </div>
        <p className="text-xs text-gray-500 mt-2">
          Формула: (начальная цена - цена победителя) / начальная цена * 100%
        </p>
      </div>
    </div>
  );
}

export function TenderAnalytics() {
  const [data, setData] = useState<AnalyticsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<TabKey>('buyers');

  useEffect(() => {
    async function fetchAnalytics() {
      try {
        setIsLoading(true);
        setError(null);
        const res = await fetch('/api/tenders/analytics');
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `HTTP ${res.status}`);
        }
        const json: AnalyticsData = await res.json();
        setData(json);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Ошибка загрузки аналитики');
      } finally {
        setIsLoading(false);
      }
    }
    fetchAnalytics();
  }, []);

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      <h2 className="text-lg font-semibold text-gray-200 mb-4">Аналитика тендеров</h2>

      {/* Tabs */}
      <div className="flex gap-1 mb-4 bg-gray-800/50 rounded-lg p-1">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={`flex-1 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
              activeTab === key
                ? 'bg-amber-500/20 text-amber-400'
                : 'text-gray-500 hover:text-gray-300'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      {error && (
        <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {isLoading && <LoadingSkeleton />}

      {!isLoading && !error && data && (
        <>
          {activeTab === 'buyers' && (
            <RankTable
              rows={data.topBuyers.map((b) => ({ name: b.organization, count: b.count, total: b.total }))}
              nameLabel="Заказчик"
            />
          )}
          {activeTab === 'regions' && (
            <RankTable
              rows={data.regionStats.map((r) => ({ name: r.region, count: r.count, total: r.total }))}
              nameLabel="Регион"
            />
          )}
          {activeTab === 'categories' && (
            <RankTable
              rows={data.categoryStats.map((c) => ({ name: c.category, count: c.count, total: c.total }))}
              nameLabel="Категория"
            />
          )}
          {activeTab === 'discount' && (
            <DiscountTab
              avgDiscount={data.avgDiscount}
              totalWithWinner={data.totalWithWinner}
            />
          )}
        </>
      )}
    </div>
  );
}
