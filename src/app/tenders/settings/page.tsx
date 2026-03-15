'use client';

import { CrawlerSettings } from '@/components/tenders/CrawlerSettings';

export default function SettingsPage() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-4xl mx-auto p-6 sm:p-10">
        {/* Header */}
        <header className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <a
              href="/tenders"
              className="text-gray-500 hover:text-gray-300 transition-colors text-sm"
            >
              &larr; Мониторинг тендеров
            </a>
          </div>
          <h1 className="text-2xl font-bold">
            <span className="text-amber-400">&#9881;</span> Настройки краулера
          </h1>
          <p className="text-gray-500 text-sm mt-1">
            Ключевые слова, пороги, модули и статус источников
          </p>
        </header>

        <main>
          <CrawlerSettings />
        </main>

        {/* Footer */}
        <footer className="mt-12 pt-6 border-t border-gray-800 text-center text-xs text-gray-600">
          Настройки применяются при следующем цикле парсинга
        </footer>
      </div>
    </div>
  );
}
