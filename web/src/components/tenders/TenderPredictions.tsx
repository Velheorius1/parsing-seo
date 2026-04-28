'use client';

import { useState, useEffect } from 'react';

interface Prediction {
  id: string;
  organization: string;
  predicted_month: number;
  predicted_year: number;
  confidence: number;
  basis: string;
  product_hint: string;
}

interface PredictionsData {
  predictions: Prediction[];
  meta: {
    currentMonth: number;
    currentYear: number;
    nextMonth: number;
    nextYear: number;
    monthAfter: number;
    yearAfter: number;
  };
}

const MONTH_NAMES: Record<number, string> = {
  1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
  5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
  9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь',
};

function ConfidenceBadge({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  let colorClass = 'text-gray-400 bg-gray-800';
  if (pct >= 70) {
    colorClass = 'text-green-400 bg-green-900/30';
  } else if (pct >= 50) {
    colorClass = 'text-amber-400 bg-amber-900/30';
  } else if (pct >= 30) {
    colorClass = 'text-orange-400 bg-orange-900/30';
  }

  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${colorClass}`}>
      {pct}%
    </span>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-2">
      {Array.from({ length: 3 }).map((_, i) => (
        <div key={i} className="h-10 bg-gray-800/50 rounded animate-pulse" />
      ))}
    </div>
  );
}

export function TenderPredictions() {
  const [data, setData] = useState<PredictionsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchPredictions() {
      try {
        setIsLoading(true);
        setError(null);
        const res = await fetch('/api/tenders/predictions');
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.error || `HTTP ${res.status}`);
        }
        const json: PredictionsData = await res.json();
        setData(json);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Ошибка загрузки прогнозов');
      } finally {
        setIsLoading(false);
      }
    }
    fetchPredictions();
  }, []);

  const predictions = data?.predictions || [];

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-4">
      <h2 className="text-lg font-semibold text-gray-200 mb-4">
        Прогноз тендеров
      </h2>
      <p className="text-xs text-gray-500 mb-4">
        Организации с сезонными паттернами (3+ тендеров в одном месяце)
      </p>

      {error && (
        <div className="p-3 bg-red-900/20 border border-red-800 rounded-lg text-red-400 text-sm">
          {error}
        </div>
      )}

      {isLoading && <LoadingSkeleton />}

      {!isLoading && !error && predictions.length === 0 && (
        <p className="text-sm text-gray-500 text-center py-6">
          Нет прогнозов на ближайшие 2 месяца
        </p>
      )}

      {!isLoading && !error && predictions.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-gray-800">
                <th className="px-3 py-2">Организация</th>
                <th className="px-3 py-2">Ожидаемый месяц</th>
                <th className="px-3 py-2 text-center">Уверенность</th>
                <th className="px-3 py-2">Основание</th>
              </tr>
            </thead>
            <tbody>
              {predictions.map((pred) => (
                <tr
                  key={pred.id}
                  className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors"
                >
                  <td className="px-3 py-2 text-gray-300 max-w-[280px] truncate" title={pred.organization}>
                    {pred.organization}
                  </td>
                  <td className="px-3 py-2 text-amber-400 font-medium">
                    {MONTH_NAMES[pred.predicted_month] || pred.predicted_month} {pred.predicted_year}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <ConfidenceBadge value={pred.confidence} />
                  </td>
                  <td className="px-3 py-2 text-gray-500 text-xs">
                    {pred.basis}
                    {pred.product_hint && (
                      <span className="ml-2 text-gray-600">({pred.product_hint})</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
