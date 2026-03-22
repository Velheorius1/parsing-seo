'use client';

import { useEffect, useState } from 'react';

interface CompetitorStat {
  name: string;
  wins: number;
  totalValue: number;
  maxDeal: number;
  lastDate: string;
  categories: string[];
  isNiche: boolean;
}

interface RecentDeal {
  competitor: string;
  title: string;
  price: number;
  customer: string;
  date: string;
  isNiche: boolean;
}

interface Summary {
  totalCompetitors: number;
  totalDeals: number;
  nicheDeals: number;
  totalValue: number;
}

interface CompetitorData {
  stats: CompetitorStat[];
  recentDeals: RecentDeal[];
  summary: Summary;
}

const fmt = new Intl.NumberFormat('ru-RU');

function formatPrice(value: number): string {
  return fmt.format(value) + ' UZS';
}

function formatDate(dateStr: string): string {
  if (!dateStr) return '—';
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString('ru-RU', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    });
  } catch {
    return dateStr;
  }
}

export default function CompetitorsPage() {
  const [data, setData] = useState<CompetitorData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchData() {
      try {
        setLoading(true);
        setError(null);
        const res = await fetch('/api/competitors');
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `Ошибка ${res.status}`);
        }
        const json: CompetitorData = await res.json();
        setData(json);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Неизвестная ошибка');
      } finally {
        setLoading(false);
      }
    }

    fetchData();
  }, []);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-6xl mx-auto p-6 sm:p-10">
        {/* Header */}
        <header className="mb-8">
          <div className="flex items-center justify-between">
            <div>
              <div className="flex items-center gap-3">
                <a
                  href="/"
                  className="text-gray-500 hover:text-gray-300 transition-colors text-sm"
                >
                  &larr; Parsing SEO
                </a>
                <span className="text-gray-700 text-sm">/</span>
                <a
                  href="/tenders"
                  className="text-gray-500 hover:text-gray-300 transition-colors text-sm"
                >
                  Тендеры
                </a>
              </div>
              <h1 className="text-2xl font-bold mt-2">
                <span className="text-amber-400">&#9670;</span> Мониторинг конкурентов
              </h1>
              <p className="text-gray-500 text-sm mt-1">
                Анализ активности конкурентов на UZEX &middot; Полиграфия, упаковка
              </p>
            </div>
          </div>
        </header>

        <main className="space-y-6">
          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center py-20">
              <div className="text-center">
                <div className="inline-block w-8 h-8 border-2 border-gray-700 border-t-amber-400 rounded-full animate-spin mb-4" />
                <p className="text-gray-500 text-sm">Загрузка данных с UZEX...</p>
              </div>
            </div>
          )}

          {/* Error */}
          {error && !loading && (
            <div className="bg-red-900/20 border border-red-800 rounded-xl p-6 text-center">
              <p className="text-red-400 font-medium">Ошибка загрузки</p>
              <p className="text-red-500 text-sm mt-1">{error}</p>
              <button
                onClick={() => window.location.reload()}
                className="mt-4 px-4 py-2 bg-red-800/40 hover:bg-red-800/60 rounded-lg text-sm text-red-300 transition-colors"
              >
                Повторить
              </button>
            </div>
          )}

          {/* Data loaded */}
          {data && !loading && (
            <>
              {/* Summary cards */}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
                  <div className="text-xs text-gray-500 uppercase tracking-wider">Конкурентов</div>
                  <div className="text-2xl font-bold mt-1 text-gray-100">
                    {data.summary.totalCompetitors}
                  </div>
                </div>
                <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
                  <div className="text-xs text-gray-500 uppercase tracking-wider">Сделок</div>
                  <div className="text-2xl font-bold mt-1 text-gray-100">
                    {fmt.format(data.summary.totalDeals)}
                  </div>
                </div>
                <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
                  <div className="text-xs text-gray-500 uppercase tracking-wider">В нашей нише</div>
                  <div className="text-2xl font-bold mt-1 text-amber-400">
                    {fmt.format(data.summary.nicheDeals)}
                  </div>
                </div>
                <div className="bg-gray-900 rounded-xl p-4 border border-gray-800">
                  <div className="text-xs text-gray-500 uppercase tracking-wider">Общая сумма</div>
                  <div className="text-lg font-bold mt-1 text-gray-100">
                    {formatPrice(data.summary.totalValue)}
                  </div>
                </div>
              </div>

              {/* Competitors table */}
              <section className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
                <div className="p-4 border-b border-gray-800">
                  <h2 className="text-lg font-semibold">Рейтинг конкурентов</h2>
                  <p className="text-gray-500 text-xs mt-1">
                    Отсортировано по общей сумме сделок
                  </p>
                </div>

                {data.stats.length === 0 ? (
                  <div className="p-8 text-center text-gray-600">
                    Конкуренты не найдены. Добавьте ключевые слова в crawler_settings.
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                          <th className="text-left px-4 py-3">#</th>
                          <th className="text-left px-4 py-3">Конкурент</th>
                          <th className="text-right px-4 py-3">Побед</th>
                          <th className="text-right px-4 py-3">Общая сумма</th>
                          <th className="text-right px-4 py-3">Макс. сделка</th>
                          <th className="text-right px-4 py-3">Последняя дата</th>
                        </tr>
                      </thead>
                      <tbody>
                        {data.stats.map((stat, idx) => (
                          <tr
                            key={stat.name}
                            className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors"
                          >
                            <td className="px-4 py-3 text-gray-600">{idx + 1}</td>
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-2">
                                {stat.isNiche && (
                                  <span className="inline-block w-2 h-2 rounded-full bg-amber-400 flex-shrink-0" />
                                )}
                                <span className={stat.isNiche ? 'text-amber-300' : 'text-gray-200'}>
                                  {stat.name}
                                </span>
                              </div>
                              {stat.categories.length > 0 && (
                                <div className="mt-1 text-xs text-gray-600 truncate max-w-xs">
                                  {stat.categories.slice(0, 2).join(', ')}
                                </div>
                              )}
                            </td>
                            <td className="px-4 py-3 text-right text-gray-300 font-medium">
                              {stat.wins}
                            </td>
                            <td className="px-4 py-3 text-right text-gray-200 font-medium">
                              {formatPrice(stat.totalValue)}
                            </td>
                            <td className="px-4 py-3 text-right text-gray-400">
                              {formatPrice(stat.maxDeal)}
                            </td>
                            <td className="px-4 py-3 text-right text-gray-500">
                              {formatDate(stat.lastDate)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>

              {/* Recent niche deals */}
              {data.recentDeals.length > 0 && (
                <section className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
                  <div className="p-4 border-b border-gray-800">
                    <h2 className="text-lg font-semibold">
                      Последние сделки в нише
                      <span className="ml-2 text-sm font-normal text-gray-500">
                        ({data.recentDeals.length})
                      </span>
                    </h2>
                  </div>

                  <div className="divide-y divide-gray-800/50">
                    {data.recentDeals.map((deal, idx) => (
                      <div
                        key={idx}
                        className="px-4 py-3 hover:bg-gray-800/30 transition-colors"
                      >
                        <div className="flex items-start justify-between gap-4">
                          <div className="min-w-0 flex-1">
                            <div className="text-sm text-gray-200 truncate">{deal.title}</div>
                            <div className="flex items-center gap-2 mt-1">
                              <span className="text-xs text-amber-400 font-medium">{deal.competitor}</span>
                              <span className="text-gray-700">&middot;</span>
                              <span className="text-xs text-gray-500 truncate">{deal.customer}</span>
                            </div>
                          </div>
                          <div className="text-right flex-shrink-0">
                            <div className="text-sm font-medium text-gray-200">{formatPrice(deal.price)}</div>
                            <div className="text-xs text-gray-600">{formatDate(deal.date)}</div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </main>

        {/* Footer */}
        <footer className="mt-12 pt-6 border-t border-gray-800 text-center text-xs text-gray-600">
          UZEX &middot; Завершённые контракты &middot; Обновляется при каждом запросе
        </footer>
      </div>
    </div>
  );
}
