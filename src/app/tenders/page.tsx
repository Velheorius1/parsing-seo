'use client';

import { TenderKeywords } from '@/components/tenders/TenderKeywords';
import { TenderFilters } from '@/components/tenders/TenderFilters';
import { ExportButton } from '@/components/tenders/ExportButton';
import { TenderTable } from '@/components/tenders/TenderTable';
import { TenderAnalytics } from '@/components/tenders/TenderAnalytics';

export default function TendersPage() {
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
              </div>
              <h1 className="text-2xl font-bold mt-2">
                <span className="text-amber-400">&#9670;</span> Мониторинг тендеров
              </h1>
              <p className="text-gray-500 text-sm mt-1">
                93 площадки, 12000+ тендеров &middot; Полиграфия, упаковка, печать
              </p>
            </div>
            <div className="hidden sm:block text-right">
              <div className="text-xs text-gray-600">Источники</div>
              <div className="text-sm text-gray-400">ETender + Xarid (конкурсы + прямые)</div>
              <div className="text-xs text-gray-600 mt-1">3 API параллельно</div>
            </div>
          </div>
        </header>

        <main className="space-y-6">
          {/* Выбор ключевых слов */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <TenderKeywords />
          </section>

          {/* Фильтры */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <TenderFilters />
          </section>

          {/* Панель действий */}
          <div className="flex items-center justify-end">
            <ExportButton />
          </div>

          {/* Результаты */}
          <section>
            <TenderTable />
          </section>

          {/* Аналитика */}
          <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
            <TenderAnalytics />
          </section>
        </main>

        {/* Footer */}
        <footer className="mt-12 pt-6 border-t border-gray-800 text-center text-xs text-gray-600">
          93 площадки &middot; 12000+ тендеров &middot; ETender + Xarid (конкурсы + прямые) &middot; Обновляется при каждом запросе
        </footer>
      </div>
    </div>
  );
}
