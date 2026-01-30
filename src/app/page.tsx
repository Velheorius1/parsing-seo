import { KeywordCollector } from '@/components/parsing/KeywordCollector';
import { KeywordTable } from '@/components/parsing/KeywordTable';
import { ExportButton } from '@/components/export/ExportButton';

export default function Home() {
  return (
    <div className="min-h-screen p-6 sm:p-10 max-w-5xl mx-auto font-[family-name:var(--font-geist-sans)]">
      <header className="mb-8">
        <h1 className="text-2xl font-bold">Parsing SEO</h1>
        <p className="text-gray-600 dark:text-gray-400 text-sm mt-1">
          Сбор семантики и анализ конкурентов
        </p>
      </header>

      <main className="space-y-8">
        {/* Блок сбора подсказок */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold">Сбор подсказок Яндекс</h2>
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
