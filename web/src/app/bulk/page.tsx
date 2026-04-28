'use client';

import { useState, useCallback, useRef } from 'react';
import * as XLSX from 'xlsx';
import { parseExcel, getColumnValues, type ExcelData } from '@/lib/utils/excelParser';

interface BulkResultItem {
  query: string;
  trackPosition: number | null;
  trackUrl: string | null;
  top3: { position: number; domain: string; url: string; title: string }[];
  error?: string;
}

interface BulkSummary {
  total: number;
  inTop3: number;
  inTop10: number;
  notFound: number;
  topCompetitors: { domain: string; count: number }[];
}

interface BulkResponse {
  results: BulkResultItem[];
  summary: BulkSummary;
}

const BATCH_SIZE = 50;
const RANGE_SIZE = 100;

export default function BulkPage() {
  const [excelData, setExcelData] = useState<ExcelData | null>(null);
  const [fileName, setFileName] = useState('');
  const [selectedSheet, setSelectedSheet] = useState('');
  const [selectedColumn, setSelectedColumn] = useState('');
  const [trackDomain, setTrackDomain] = useState('winch.uz');
  const [analyzeCount, setAnalyzeCount] = useState(50);
  const [isDragging, setIsDragging] = useState(false);

  // Pagination state
  const [startFrom, setStartFrom] = useState(1);
  const [selectedRange, setSelectedRange] = useState<string>('custom');
  const [lastProcessedIndex, setLastProcessedIndex] = useState<number | null>(null);
  const [wasInterrupted, setWasInterrupted] = useState(false);

  const [isAnalyzing, setIsAnalyzing] = useState(false);
  const [progress, setProgress] = useState({ completed: 0, total: 0 });
  const [results, setResults] = useState<BulkResultItem[]>([]);
  const [summary, setSummary] = useState<BulkSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);

  const handleFile = useCallback(async (file: File) => {
    setError(null);
    setResults([]);
    setSummary(null);
    setStartFrom(1);
    setSelectedRange('custom');
    setLastProcessedIndex(null);
    setWasInterrupted(false);

    if (!file.name.match(/\.xlsx?$/i)) {
      setError('Поддерживаются только файлы .xlsx и .xls');
      return;
    }

    try {
      const data = await parseExcel(file);
      setExcelData(data);
      setFileName(file.name);

      if (data.sheets.length > 0) {
        setSelectedSheet(data.sheets[0]);
        const cols = data.columns[data.sheets[0]] || [];
        setSelectedColumn(cols.length > 0 ? cols[0] : '');
      }
    } catch {
      setError('Не удалось прочитать файл Excel');
    }
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setIsDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) handleFile(file);
    },
    [handleFile],
  );

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const onDragLeave = useCallback(() => setIsDragging(false), []);

  function handleSheetChange(sheet: string) {
    setSelectedSheet(sheet);
    const cols = excelData?.columns[sheet] || [];
    setSelectedColumn(cols.length > 0 ? cols[0] : '');
  }

  const allQueries =
    excelData && selectedSheet && selectedColumn
      ? getColumnValues(excelData, selectedSheet, selectedColumn)
      : [];

  // Generate ranges for dropdown
  const ranges: { value: string; label: string; start: number; end: number }[] = [];
  for (let i = 0; i < allQueries.length; i += RANGE_SIZE) {
    const start = i + 1;
    const end = Math.min(i + RANGE_SIZE, allQueries.length);
    ranges.push({ value: `${start}-${end}`, label: `Запросы ${start}-${end}`, start, end });
  }

  // Calculate effective query range based on settings
  const getEffectiveRange = () => {
    if (selectedRange !== 'custom' && selectedRange !== 'all') {
      const range = ranges.find(r => r.value === selectedRange);
      if (range) return { start: range.start - 1, end: range.end };
    }
    const start = Math.max(0, startFrom - 1);
    const count = analyzeCount === 0 ? allQueries.length : analyzeCount;
    const end = Math.min(start + count, allQueries.length);
    return { start, end };
  };

  const effectiveRange = getEffectiveRange();
  const queries = allQueries.slice(effectiveRange.start, effectiveRange.end);
  const previewRows = queries.slice(0, 10);

  async function handleStartAnalysis(continueFrom?: number) {
    if (queries.length === 0) return;

    abortControllerRef.current = new AbortController();
    const startIdx = continueFrom ?? 0;
    const selected = queries.slice(startIdx);

    // Keep existing results if continuing
    const existingResults = continueFrom ? [...results] : [];
    if (!continueFrom) {
      setResults([]);
      setSummary(null);
    }
    setError(null);
    setIsAnalyzing(true);
    setWasInterrupted(false);
    setProgress({ completed: existingResults.length, total: queries.length });

    const allResults: BulkResultItem[] = [...existingResults];
    let lastSummary: BulkSummary | null = null;
    let processedCount = startIdx;

    // Process in batches of BATCH_SIZE
    for (let i = 0; i < selected.length; i += BATCH_SIZE) {
      // Check if aborted
      if (abortControllerRef.current?.signal.aborted) {
        break;
      }

      const batch = selected.slice(i, i + BATCH_SIZE);

      try {
        const response = await fetch('/api/serp/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            queries: batch,
            trackDomain: trackDomain.trim() || undefined,
          }),
          signal: abortControllerRef.current?.signal,
        });

        const data: BulkResponse = await response.json();

        if (!response.ok) {
          throw new Error((data as unknown as { error: string }).error || 'Ошибка сервера');
        }

        allResults.push(...data.results);
        lastSummary = data.summary;
        processedCount = startIdx + i + batch.length;

        setResults([...allResults]);
        setLastProcessedIndex(processedCount);
        setProgress({ completed: allResults.length, total: queries.length });

        // Update summary after each batch for live feedback
        const combinedSummary = recalcSummary(allResults, trackDomain.trim());
        setSummary(combinedSummary);
      } catch (err) {
        // Save progress on error
        setLastProcessedIndex(processedCount);
        setWasInterrupted(true);

        if (err instanceof Error && err.name === 'AbortError') {
          // User cancelled - just stop
        } else {
          setError(err instanceof Error ? err.message : 'Ошибка при анализе');
        }
        break;
      }
    }

    // Recalculate summary across all batches
    if (allResults.length > 0) {
      const combinedSummary = recalcSummary(allResults, trackDomain.trim());
      setSummary(combinedSummary);
    } else if (lastSummary) {
      setSummary(lastSummary);
    }

    setIsAnalyzing(false);
  }

  function handleStopAnalysis() {
    abortControllerRef.current?.abort();
    setWasInterrupted(true);
  }

  function handleContinueAnalysis() {
    if (lastProcessedIndex !== null && lastProcessedIndex < queries.length) {
      handleStartAnalysis(lastProcessedIndex);
    }
  }

  function recalcSummary(items: BulkResultItem[], domain: string): BulkSummary {
    let inTop3 = 0;
    let inTop10 = 0;
    let notFound = 0;
    const competitorCount: Record<string, number> = {};
    const host = domain.replace(/^https?:\/\//, '').replace(/\/.*$/, '').toLowerCase();

    for (const item of items) {
      if (item.trackPosition !== null) {
        if (item.trackPosition <= 3) inTop3++;
        else if (item.trackPosition <= 10) inTop10++;
        else notFound++;
      } else if (host) {
        notFound++;
      }

      for (const r of item.top3) {
        if (host && r.domain.toLowerCase().includes(host)) continue;
        competitorCount[r.domain] = (competitorCount[r.domain] || 0) + 1;
      }
    }

    const topCompetitors = Object.entries(competitorCount)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([d, count]) => ({ domain: d, count }));

    return { total: items.length, inTop3, inTop10, notFound, topCompetitors };
  }

  function handleExport() {
    if (results.length === 0) return;

    const domain = trackDomain.trim() || 'tracked';

    const rows = results.map((r) => ({
      'Запрос': r.query,
      [`Позиция ${domain}`]: r.trackPosition ?? 'Не найден',
      [`URL ${domain}`]: r.trackUrl ?? '',
      'Топ-1 домен': r.top3[0]?.domain ?? '',
      'Топ-1 title': r.top3[0]?.title ?? '',
      'Топ-2 домен': r.top3[1]?.domain ?? '',
      'Топ-2 title': r.top3[1]?.title ?? '',
      'Топ-3 домен': r.top3[2]?.domain ?? '',
      'Топ-3 title': r.top3[2]?.title ?? '',
    }));

    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'SERP Results');

    // Auto column widths
    const colWidths = Object.keys(rows[0] || {}).map((key) => ({
      wch: Math.max(key.length, 20),
    }));
    ws['!cols'] = colWidths;

    XLSX.writeFile(wb, `serp-results-${domain}.xlsx`);
  }

  return (
    <div className="min-h-screen p-6 sm:p-10 max-w-6xl mx-auto font-[family-name:var(--font-geist-sans)]">
      <header className="mb-8">
        <div className="flex items-center gap-4">
          <a
            href="/"
            className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
          >
            &larr; Главная
          </a>
          <h1 className="text-2xl font-bold">Массовый SERP анализ</h1>
        </div>
        <p className="text-gray-600 dark:text-gray-400 text-sm mt-1">
          Загрузите Excel с запросами — узнайте позиции вашего сайта в Google
        </p>
      </header>

      <main className="space-y-6">
        {/* Step 1: File Upload */}
        <section>
          <h2 className="text-lg font-semibold mb-3">1. Загрузите файл</h2>
          <div
            onDrop={onDrop}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onClick={() => fileInputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
              isDragging
                ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/20'
                : 'border-gray-300 dark:border-gray-700 hover:border-purple-400 hover:bg-gray-50 dark:hover:bg-gray-800/50'
            }`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.xls"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) handleFile(file);
              }}
            />
            {fileName ? (
              <p className="text-sm text-gray-700 dark:text-gray-300">
                Файл: <span className="font-semibold">{fileName}</span>
              </p>
            ) : (
              <div className="space-y-2">
                <p className="text-gray-500 dark:text-gray-400 text-sm">
                  Перетащите Excel файл сюда или нажмите для выбора
                </p>
                <p className="text-xs text-gray-400">.xlsx, .xls</p>
              </div>
            )}
          </div>
        </section>

        {/* Step 2: Sheet/Column selection */}
        {excelData && (
          <section>
            <h2 className="text-lg font-semibold mb-3">2. Выберите данные</h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Лист</label>
                <select
                  value={selectedSheet}
                  onChange={(e) => handleSheetChange(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm"
                >
                  {excelData.sheets.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1">Колонка с запросами</label>
                <select
                  value={selectedColumn}
                  onChange={(e) => setSelectedColumn(e.target.value)}
                  className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm"
                >
                  {(excelData.columns[selectedSheet] || []).map((c) => (
                    <option key={c} value={c}>
                      {c}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {/* Preview */}
            {allQueries.length > 0 && (
              <div className="mt-3">
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-2">
                  Всего запросов в файле: <span className="font-semibold">{allQueries.length}</span>
                  {previewRows.length > 0 && ` (превью первых ${Math.min(10, previewRows.length)} из выбранного диапазона)`}
                </p>
                <div className="bg-gray-50 dark:bg-gray-800/50 rounded-lg p-3 space-y-1">
                  {previewRows.map((q, i) => (
                    <p key={i} className="text-xs text-gray-600 dark:text-gray-400">
                      {effectiveRange.start + i + 1}. {q}
                    </p>
                  ))}
                </div>
              </div>
            )}
          </section>
        )}

        {/* Step 3: Settings & Run */}
        {excelData && allQueries.length > 0 && (
          <section>
            <h2 className="text-lg font-semibold mb-3">3. Настройки анализа</h2>

            {/* Range selector */}
            {ranges.length > 1 && (
              <div className="mb-4">
                <label className="block text-sm font-medium mb-2">Диапазон запросов</label>
                <div className="flex flex-wrap gap-2">
                  {ranges.map((range) => (
                    <button
                      key={range.value}
                      onClick={() => {
                        setSelectedRange(range.value);
                        setStartFrom(range.start);
                        setAnalyzeCount(range.end - range.start + 1);
                      }}
                      disabled={isAnalyzing}
                      className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                        selectedRange === range.value
                          ? 'bg-purple-600 text-white border-purple-600'
                          : 'bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 hover:border-purple-400'
                      } disabled:opacity-50`}
                    >
                      {range.label}
                    </button>
                  ))}
                  <button
                    onClick={() => setSelectedRange('custom')}
                    disabled={isAnalyzing}
                    className={`px-3 py-1.5 text-xs rounded-lg border transition-colors ${
                      selectedRange === 'custom'
                        ? 'bg-purple-600 text-white border-purple-600'
                        : 'bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 hover:border-purple-400'
                    } disabled:opacity-50`}
                  >
                    Свой диапазон
                  </button>
                </div>
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
              <div>
                <label className="block text-sm font-medium mb-1">Домен для отслеживания</label>
                <input
                  type="text"
                  value={trackDomain}
                  onChange={(e) => setTrackDomain(e.target.value)}
                  placeholder="winch.uz"
                  disabled={isAnalyzing}
                  className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm disabled:opacity-50"
                />
              </div>
              {selectedRange === 'custom' && (
                <>
                  <div>
                    <label className="block text-sm font-medium mb-1">Начать с запроса #</label>
                    <input
                      type="number"
                      min={1}
                      max={allQueries.length}
                      value={startFrom}
                      onChange={(e) => setStartFrom(Math.max(1, Math.min(allQueries.length, Number(e.target.value))))}
                      disabled={isAnalyzing}
                      className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm disabled:opacity-50"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium mb-1">Количество</label>
                    <select
                      value={analyzeCount}
                      onChange={(e) => setAnalyzeCount(Number(e.target.value))}
                      disabled={isAnalyzing}
                      className="w-full px-3 py-2 border rounded-lg bg-white dark:bg-gray-900 border-gray-300 dark:border-gray-700 text-sm disabled:opacity-50"
                    >
                      <option value={50}>50 запросов</option>
                      <option value={100}>100 запросов</option>
                      <option value={150}>150 запросов</option>
                      <option value={200}>200 запросов</option>
                      <option value={0}>До конца ({Math.max(0, allQueries.length - startFrom + 1)})</option>
                    </select>
                  </div>
                </>
              )}
              <div className="flex items-end gap-2">
                {!isAnalyzing ? (
                  <button
                    onClick={() => handleStartAnalysis()}
                    disabled={queries.length === 0}
                    className="flex-1 px-6 py-2 bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
                  >
                    Начать анализ
                  </button>
                ) : (
                  <button
                    onClick={handleStopAnalysis}
                    className="flex-1 px-6 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors text-sm"
                  >
                    Остановить
                  </button>
                )}
              </div>
            </div>

            {/* Current range info */}
            <div className="mt-2 text-xs text-gray-500">
              Будет обработано: запросы {effectiveRange.start + 1} - {effectiveRange.end} ({queries.length} шт.)
            </div>

            <CostWarning count={queries.length} />

            {/* Progress bar */}
            {(isAnalyzing || progress.completed > 0) && (
              <div className="mt-3">
                <div className="flex items-center gap-3">
                  <div className="flex-1 bg-gray-200 dark:bg-gray-700 rounded-full h-2">
                    <div
                      className="bg-purple-600 h-2 rounded-full transition-all"
                      style={{
                        width: `${progress.total > 0 ? (progress.completed / progress.total) * 100 : 0}%`,
                      }}
                    />
                  </div>
                  <span className="text-sm text-gray-500 whitespace-nowrap">
                    {progress.completed}/{progress.total}
                  </span>
                </div>
              </div>
            )}

            {/* Continue button after interruption */}
            {wasInterrupted && lastProcessedIndex !== null && lastProcessedIndex < queries.length && !isAnalyzing && (
              <div className="mt-3 p-3 bg-yellow-50 dark:bg-yellow-900/10 border border-yellow-200 dark:border-yellow-800 rounded-lg">
                <div className="flex items-center justify-between gap-4">
                  <div className="text-sm text-yellow-700 dark:text-yellow-400">
                    Обработано {lastProcessedIndex} из {queries.length}. Можно продолжить.
                  </div>
                  <button
                    onClick={handleContinueAnalysis}
                    className="px-4 py-1.5 bg-yellow-600 text-white rounded-lg hover:bg-yellow-700 transition-colors text-sm whitespace-nowrap"
                  >
                    Продолжить с #{lastProcessedIndex + 1}
                  </button>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Error */}
        {error && (
          <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-red-700 dark:text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Summary - show even with partial results */}
        {(summary || results.length > 0) && (
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-lg font-semibold">Результаты</h2>
              {results.length > 0 && (
                <span className="text-sm text-gray-500">
                  Обработано: {results.length} из {queries.length}
                </span>
              )}
            </div>
            {summary && (
              <>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
                  <SummaryCard
                    label="В топ-3"
                    value={summary.inTop3}
                    color="green"
                  />
                  <SummaryCard
                    label="В топ-10"
                    value={summary.inTop10}
                    color="yellow"
                  />
                  <SummaryCard
                    label="Не в топе"
                    value={summary.notFound}
                    color="red"
                  />
                  <SummaryCard
                    label="Обработано"
                    value={summary.total}
                    color="gray"
                  />
                </div>

                {summary.topCompetitors.length > 0 && (
                  <div className="mb-4 p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg">
                    <p className="text-sm font-semibold mb-2">Главные конкуренты:</p>
                    <div className="flex flex-wrap gap-2">
                      {summary.topCompetitors.map((c) => (
                        <span
                          key={c.domain}
                          className="text-xs px-2 py-1 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded"
                        >
                          {c.domain}{' '}
                          <span className="text-gray-400">({c.count})</span>
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}

            {/* Export - available even with partial results */}
            {results.length > 0 && (
              <button
                onClick={handleExport}
                className="mb-4 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 transition-colors text-sm"
              >
                Скачать {results.length} результатов (Excel)
              </button>
            )}

            {/* Results Table */}
            <div className="overflow-x-auto">
              <table className="w-full text-sm border-collapse">
                <thead>
                  <tr className="border-b border-gray-200 dark:border-gray-700">
                    <th className="text-left py-2 px-2">Запрос</th>
                    <th className="text-center py-2 px-2 w-20">
                      {trackDomain.trim().toUpperCase() || 'Домен'}
                    </th>
                    <th className="text-left py-2 px-2 hidden md:table-cell">#1</th>
                    <th className="text-left py-2 px-2 hidden md:table-cell">#2</th>
                    <th className="text-left py-2 px-2 hidden md:table-cell">#3</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r, i) => (
                    <tr
                      key={i}
                      className="border-b border-gray-100 dark:border-gray-800 hover:bg-gray-50 dark:hover:bg-gray-800/50"
                    >
                      <td className="py-2 px-2 text-xs">{r.query}</td>
                      <td className="py-2 px-2 text-center">
                        {r.error ? (
                          <span className="text-xs text-red-400">err</span>
                        ) : r.trackPosition !== null ? (
                          <PositionBadge position={r.trackPosition} />
                        ) : (
                          <span className="text-xs text-gray-400">-</span>
                        )}
                      </td>
                      <td className="py-2 px-2 hidden md:table-cell text-xs text-gray-500 max-w-[200px] truncate">
                        {r.top3[0]?.domain ?? ''}
                      </td>
                      <td className="py-2 px-2 hidden md:table-cell text-xs text-gray-500 max-w-[200px] truncate">
                        {r.top3[1]?.domain ?? ''}
                      </td>
                      <td className="py-2 px-2 hidden md:table-cell text-xs text-gray-500 max-w-[200px] truncate">
                        {r.top3[2]?.domain ?? ''}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        )}
      </main>
    </div>
  );
}

function SummaryCard({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: 'green' | 'yellow' | 'red' | 'gray';
}) {
  const colors = {
    green: 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800 text-green-700 dark:text-green-400',
    yellow: 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-800 text-yellow-700 dark:text-yellow-400',
    red: 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800 text-red-700 dark:text-red-400',
    gray: 'bg-gray-50 dark:bg-gray-800/50 border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300',
  };

  return (
    <div className={`p-3 rounded-lg border ${colors[color]}`}>
      <p className="text-2xl font-bold">{value}</p>
      <p className="text-xs">{label}</p>
    </div>
  );
}

function CostWarning({ count }: { count: number }) {
  const minutes = Math.max(1, Math.ceil(count / 50));
  return (
    <div className="mt-3 p-3 bg-yellow-50 dark:bg-yellow-900/10 border border-yellow-200 dark:border-yellow-800 rounded-lg text-xs text-yellow-700 dark:text-yellow-400 space-y-1">
      <p>
        ~{count} запросов = ~{minutes} мин, ~{count} Serper credits
      </p>
      <div className="text-yellow-600 dark:text-yellow-500 space-y-0.5">
        <p>50 запросов ~ 1 мин, ~50 credits</p>
        <p>100 запросов ~ 2 мин, ~100 credits</p>
        <p>200 запросов ~ 4 мин, ~200 credits</p>
      </div>
    </div>
  );
}

function PositionBadge({ position }: { position: number }) {
  let color = 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400';
  if (position <= 3) {
    color = 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400';
  } else if (position <= 10) {
    color = 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400';
  }
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${color}`}>
      {position}
    </span>
  );
}
