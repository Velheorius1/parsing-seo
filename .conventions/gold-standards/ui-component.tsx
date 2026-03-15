// Gold standard: React component with Zustand store integration
// Pattern extracted from TenderTable, DeadlineBadge, StatusBadge, TenderFilters

'use client';

import { useMemo, useState, useEffect, useCallback } from 'react';
import { useExampleStore } from '@/lib/store/exampleStore';
import type { ExampleType } from '@/types/example';

// --- 1. Pure helper functions (no JSX, no hooks) ---
function formatValue(value: number | null): string {
  if (value === null) return '—';
  return new Intl.NumberFormat('ru-RU').format(value);
}

// --- 2. Small presentational components (badge/chip pattern) ---
// Static Tailwind classes only — never construct dynamically
function StatusBadge({ status }: { status: 'active' | 'closed' }) {
  const styles = {
    active: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
    closed: 'bg-gray-500/15 text-gray-400 border-gray-500/30',
  };
  const labels = { active: 'Активный', closed: 'Закрыт' };

  return (
    <span className={`px-2 py-0.5 rounded text-xs border ${styles[status]}`}>
      {labels[status]}
    </span>
  );
}

// --- 3. Row component with props (not store-dependent for data) ---
function ExampleRow({ item, index }: { item: ExampleType; index: number }) {
  // Store hooks only for shared UI state (selectedKeywords, filters)
  const selectedKeywords = useExampleStore((s) => s.selectedKeywords);

  return (
    <tr
      className="border-b border-gray-800 hover:bg-gray-800/50 transition-colors"
      style={{ animationDelay: `${index * 30}ms` }}
    >
      <td className="px-4 py-3 text-sm">{item.title}</td>
      <td className="px-4 py-3"><StatusBadge status={item.status} /></td>
    </tr>
  );
}

// --- 4. Main exported component ---
// - Zustand for global state
// - useMemo for filtering/sorting (list deps explicitly)
// - Loading/error/empty states before main content
export function ExampleTable() {
  const { items, isLoading, error, sortBy } = useExampleStore();

  // Client-side filtering with useMemo
  const filtered = useMemo(() => {
    let result = items;
    if (!sortBy) return result;
    return [...result].sort((a, b) => 0);
  }, [items, sortBy]);

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-16 bg-gray-800/50 rounded-lg animate-pulse" />
        ))}
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div className="p-4 bg-red-900/20 border border-red-800 rounded-lg text-red-400 text-sm">
        {error}
      </div>
    );
  }

  // Empty state
  if (items.length === 0) {
    return (
      <div className="text-center py-12 text-gray-500">
        <p className="text-sm">Нет данных</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-gray-800">
      <table className="w-full">
        <thead>
          <tr className="bg-gray-800/70 text-left text-xs text-gray-500 uppercase tracking-wider">
            <th className="px-4 py-3">Название</th>
            <th className="px-4 py-3">Статус</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((item, i) => (
            <ExampleRow key={item.id} item={item} index={i} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- PATTERNS ---
// - 'use client' directive at top for interactive components
// - Helper functions before components (pure, no hooks)
// - Small badge/chip components: static styles map + labels map
// - Row components accept data via props, use store only for shared UI state
// - Main component: store hook -> useMemo filter/sort -> loading/error/empty guards -> JSX
// - Dark theme: bg-gray-950 page, bg-gray-900 cards, bg-gray-800 borders
// - Tailwind classes: ONLY static, never string interpolation
// - Animations: animationDelay on rows, animate-pulse on skeletons
// - Russian labels for user-facing text

// Placeholders
declare const useExampleStore: any;
type ExampleType = { id: string; title: string; status: 'active' | 'closed'; selectedKeywords?: string[] };
