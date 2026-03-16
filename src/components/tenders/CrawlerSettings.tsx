'use client';

import { useState, useEffect, useCallback } from 'react';

interface CrawlerSetting {
  key: string;
  value: string;
  updatedAt: string;
}

interface SourceStat {
  source: string;
  count: number;
  lastCrawled: string | null;
}

// --- Helper ---
function formatTimeAgo(dateStr: string | null): string {
  if (!dateStr) return '—';
  const diff = Date.now() - new Date(dateStr).getTime();
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return 'только что';
  if (hours < 24) return hours + ' ч назад';
  const days = Math.floor(hours / 24);
  return days + ' дн назад';
}

// --- Chip Input component ---
function ChipInput({
  label,
  chips,
  onChange,
  saving,
}: {
  label: string;
  chips: string[];
  onChange: (chips: string[]) => void;
  saving: boolean;
}) {
  const [input, setInput] = useState('');

  function handleAdd() {
    const trimmed = input.trim();
    if (!trimmed || chips.includes(trimmed)) return;
    onChange([...chips, trimmed]);
    setInput('');
  }

  function handleRemove(chip: string) {
    onChange(chips.filter((c) => c !== chip));
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAdd();
    }
  }

  return (
    <div className="space-y-3">
      <span className="text-sm font-medium text-gray-400">{label}</span>
      <div className="flex flex-wrap gap-2">
        {chips.map((chip) => (
          <span
            key={chip}
            className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-sm bg-gray-800 text-gray-300 border border-gray-700"
          >
            {chip}
            <button
              onClick={() => handleRemove(chip)}
              disabled={saving}
              className="ml-1 text-gray-500 hover:text-red-400 transition-colors disabled:opacity-50"
              aria-label={'Удалить ' + chip}
            >
              &times;
            </button>
          </span>
        ))}
        {chips.length === 0 && (
          <span className="text-xs text-gray-600">Нет элементов</span>
        )}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={saving}
          placeholder="Добавить..."
          className="flex-1 px-4 py-2 border rounded-lg bg-gray-800 border-gray-700 text-gray-200 placeholder-gray-500 focus:ring-2 focus:ring-amber-500/50 focus:border-amber-500/50 text-sm disabled:opacity-50"
        />
        <button
          onClick={handleAdd}
          disabled={saving || !input.trim()}
          className="px-4 py-2 bg-gray-700 text-gray-300 rounded-lg hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors text-sm"
        >
          +
        </button>
      </div>
    </div>
  );
}

// --- Toggle Switch ---
function ToggleSwitch({
  label,
  enabled,
  onChange,
  saving,
}: {
  label: string;
  enabled: boolean;
  onChange: (val: boolean) => void;
  saving: boolean;
}) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-sm text-gray-300">{label}</span>
      <button
        onClick={() => onChange(!enabled)}
        disabled={saving}
        className={`relative w-11 h-6 rounded-full transition-colors disabled:opacity-50 ${
          enabled ? 'bg-amber-500' : 'bg-gray-700'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
            enabled ? 'translate-x-5' : 'translate-x-0'
          }`}
        />
      </button>
    </div>
  );
}

// --- Main Component ---
export function CrawlerSettings() {
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [sourceStats, setSourceStats] = useState<SourceStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  // Fetch settings on mount
  useEffect(() => {
    async function load() {
      try {
        const resp = await fetch('/api/tenders/settings');
        const data = await resp.json();
        if (data.error) {
          setError(data.error);
          return;
        }
        const map: Record<string, string> = {};
        for (const s of data.settings as CrawlerSetting[]) {
          map[s.key] = s.value;
        }
        setSettings(map);
        setSourceStats(data.sourceStats || []);
      } catch {
        setError('Ошибка загрузки настроек');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // Save a single setting
  const saveSetting = useCallback(async (key: string, value: string) => {
    setSaving(true);
    setSaveStatus(null);
    try {
      const resp = await fetch('/api/tenders/settings', {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'x-admin-token': process.env.NEXT_PUBLIC_ADMIN_SECRET_TOKEN || '',
        },
        body: JSON.stringify({ key, value }),
      });
      const data = await resp.json();
      if (data.error) {
        setSaveStatus('Ошибка: ' + data.error);
      } else {
        setSettings((prev) => ({ ...prev, [key]: value }));
        setSaveStatus('Сохранено');
        setTimeout(() => setSaveStatus(null), 2000);
      }
    } catch {
      setSaveStatus('Ошибка сети');
    } finally {
      setSaving(false);
    }
  }, []);

  // Parse JSON arrays from settings
  function getChips(key: string): string[] {
    try {
      return JSON.parse(settings[key] || '[]');
    } catch {
      return [];
    }
  }

  function getBool(key: string): boolean {
    return settings[key] === 'true';
  }

  function getNumber(key: string): number {
    return Number(settings[key]) || 0;
  }

  // Loading state
  if (loading) {
    return (
      <div className="space-y-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="h-20 bg-gray-800/50 rounded-lg animate-pulse" />
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

  return (
    <div className="space-y-6">
      {/* Save status */}
      {saveStatus && (
        <div
          className={`px-4 py-2 rounded-lg text-sm ${
            saveStatus.startsWith('Ошибка')
              ? 'bg-red-900/20 border border-red-800 text-red-400'
              : 'bg-emerald-900/20 border border-emerald-800 text-emerald-400'
          }`}
        >
          {saveStatus}
        </div>
      )}

      {/* Alert Keywords */}
      <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <ChipInput
          label="Ключевые слова алертов"
          chips={getChips('alert_keywords')}
          onChange={(chips) => saveSetting('alert_keywords', JSON.stringify(chips))}
          saving={saving}
        />
      </section>

      {/* Competitor Keywords */}
      <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <ChipInput
          label="Конкуренты"
          chips={getChips('competitor_keywords')}
          onChange={(chips) => saveSetting('competitor_keywords', JSON.stringify(chips))}
          saving={saving}
        />
      </section>

      {/* Min Price */}
      <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <div className="space-y-3">
          <span className="text-sm font-medium text-gray-400">
            Минимальная цена тендера (UZS)
          </span>
          <div className="flex gap-2 items-center">
            <input
              type="number"
              value={getNumber('min_price')}
              onChange={(e) => {
                const val = e.target.value;
                setSettings((prev) => ({ ...prev, min_price: val }));
              }}
              onBlur={() => saveSetting('min_price', settings['min_price'] || '0')}
              disabled={saving}
              className="w-48 px-4 py-2 border rounded-lg bg-gray-800 border-gray-700 text-gray-200 focus:ring-2 focus:ring-amber-500/50 focus:border-amber-500/50 text-sm disabled:opacity-50"
            />
            <span className="text-xs text-gray-500">
              {getNumber('min_price') > 0
                ? new Intl.NumberFormat('ru-RU').format(getNumber('min_price')) + ' UZS'
                : 'Не установлен'}
            </span>
          </div>
        </div>
      </section>

      {/* Feature Toggles */}
      <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <span className="text-sm font-medium text-gray-400 block mb-3">
          Модули
        </span>
        <div className="divide-y divide-gray-800">
          <ToggleSwitch
            label="AI фильтр качества"
            enabled={getBool('ai_filter_enabled')}
            onChange={(val) => saveSetting('ai_filter_enabled', String(val))}
            saving={saving}
          />
          <ToggleSwitch
            label="Мониторинг конкурентов"
            enabled={getBool('lead_gen_enabled')}
            onChange={(val) => saveSetting('lead_gen_enabled', String(val))}
            saving={saving}
          />
          <ToggleSwitch
            label="Напоминания о дедлайнах"
            enabled={getBool('deadline_reminders_enabled')}
            onChange={(val) => saveSetting('deadline_reminders_enabled', String(val))}
            saving={saving}
          />
        </div>
      </section>

      {/* Source Stats */}
      <section className="bg-gray-900 rounded-xl p-6 border border-gray-800">
        <span className="text-sm font-medium text-gray-400 block mb-3">
          Источники ({sourceStats.length})
        </span>
        {sourceStats.length === 0 ? (
          <p className="text-xs text-gray-600">Нет данных</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-800">
            <table className="w-full">
              <thead>
                <tr className="bg-gray-800/70 text-left text-xs text-gray-500 uppercase tracking-wider">
                  <th className="px-4 py-2">Источник</th>
                  <th className="px-4 py-2 text-right">Тендеров</th>
                  <th className="px-4 py-2 text-right">Обновлено</th>
                </tr>
              </thead>
              <tbody>
                {sourceStats.map((s) => (
                  <tr
                    key={s.source}
                    className="border-t border-gray-800 hover:bg-gray-800/50 transition-colors"
                  >
                    <td className="px-4 py-2 text-sm text-gray-300">{s.source}</td>
                    <td className="px-4 py-2 text-sm text-gray-400 text-right">
                      {new Intl.NumberFormat('ru-RU').format(s.count)}
                    </td>
                    <td className="px-4 py-2 text-xs text-gray-500 text-right">
                      {formatTimeAgo(s.lastCrawled)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
