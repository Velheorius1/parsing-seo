'use client';

import { useState } from 'react';
import { KeywordCollector } from '@/components/parsing/KeywordCollector';
import { KeywordTable } from '@/components/parsing/KeywordTable';
import { ExportButton } from '@/components/export/ExportButton';
import { SiteAnalyzer } from '@/components/parsing/SiteAnalyzer';
import { SerpAnalyzer } from '@/components/parsing/SerpAnalyzer';

export default function Home() {
  // URL для анализа — устанавливается при клике "Парсить" из SERP
  const [analyzeUrl, setAnalyzeUrl] = useState('');

  function handleParseSite(url: string) {
    setAnalyzeUrl(url);
    // Скроллим к блоку анализа
    document.getElementById('site-analyzer')?.scrollIntoView({ behavior: 'smooth' });
  }

  return (
    <div className="min-h-screen p-6 sm:p-10 max-w-5xl mx-auto font-[family-name:var(--font-geist-sans)]">
      <header className="mb-8 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Parsing SEO</h1>
          <p className="text-gray-600 dark:text-gray-400 text-sm mt-1">
            Сбор семантики и анализ конкурентов
          </p>
        </div>
        <a
          href="/bulk"
          className="px-4 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 transition-colors text-sm"
        >
          Массовый анализ
        </a>
      </header>

      <main className="space-y-8">
        {/* SERP анализ */}
        <section>
          <h2 className="text-lg font-semibold mb-4">SERP анализ Google</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
            Введите запрос — увидите топ-10 сайтов из Google. Нажмите «Парсить» чтобы извлечь ключевые слова сайта.
          </p>
          <SerpAnalyzer onParseSite={handleParseSite} />
        </section>

        {/* Анализ сайта по URL */}
        <section id="site-analyzer">
          <h2 className="text-lg font-semibold mb-4">Анализ сайта по URL</h2>
          <p className="text-sm text-gray-500 dark:text-gray-400 mb-3">
            Введите URL любого сайта — получите title, H1, meta description и ключевые слова
          </p>
          <SiteAnalyzer initialUrl={analyzeUrl} />
        </section>

        {/* Сбор подсказок */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Сбор подсказок</h2>
            <ExportButton />
          </div>
          <KeywordCollector />
        </section>

        {/* Таблица результатов */}
        <section>
          <h2 className="text-lg font-semibold mb-4">Результаты</h2>
          <KeywordTable />
        </section>
      </main>
    </div>
  );
}
