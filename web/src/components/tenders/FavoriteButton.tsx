'use client';

import { useState, useRef, useEffect } from 'react';
import type { TenderFavorite } from '@/types/parsing';

const COLORS: { key: TenderFavorite['color']; hex: string; label: string }[] = [
  { key: 'red', hex: '#ef4444', label: 'Красный' },
  { key: 'orange', hex: '#f97316', label: 'Оранжевый' },
  { key: 'yellow', hex: '#eab308', label: 'Жёлтый' },
  { key: 'green', hex: '#22c55e', label: 'Зелёный' },
  { key: 'blue', hex: '#3b82f6', label: 'Голубой' },
  { key: 'purple', hex: '#a855f7', label: 'Фиолетовый' },
];

function getColorHex(color: string): string {
  return COLORS.find((c) => c.key === color)?.hex || '#eab308';
}

interface FavoriteButtonProps {
  tenderId: string;
  favorite: TenderFavorite | null;
  onToggle: (tenderId: string) => Promise<void>;
  onUpdateColor: (tenderId: string, color: TenderFavorite['color']) => Promise<void>;
}

export function FavoriteButton({ tenderId, favorite, onToggle, onUpdateColor }: FavoriteButtonProps) {
  const [isLoading, setIsLoading] = useState(false);
  const [showPicker, setShowPicker] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  // Close picker on outside click
  useEffect(() => {
    if (!showPicker) return;
    function handleClick(e: MouseEvent) {
      if (pickerRef.current && !pickerRef.current.contains(e.target as Node)) {
        setShowPicker(false);
      }
    }
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showPicker]);

  async function handleClick(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (isLoading) return;
    setIsLoading(true);
    try {
      await onToggle(tenderId);
    } finally {
      setIsLoading(false);
    }
  }

  function handleContextMenu(e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (favorite) {
      setShowPicker(!showPicker);
    }
  }

  async function handleColorSelect(color: TenderFavorite['color']) {
    setShowPicker(false);
    if (isLoading) return;
    setIsLoading(true);
    try {
      await onUpdateColor(tenderId, color);
    } finally {
      setIsLoading(false);
    }
  }

  const isFav = !!favorite;
  const colorHex = isFav ? getColorHex(favorite.color) : undefined;

  return (
    <div className="relative inline-flex items-center">
      <button
        onClick={handleClick}
        onContextMenu={handleContextMenu}
        disabled={isLoading}
        className="p-1 rounded hover:bg-gray-800/50 transition-colors disabled:opacity-50"
        title={isFav ? 'Убрать из избранного (ПКМ — сменить цвет)' : 'Добавить в избранное'}
      >
        {isFav ? (
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill={colorHex}
            stroke={colorHex}
            strokeWidth="2"
          >
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        ) : (
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="#6b7280"
            strokeWidth="2"
          >
            <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
          </svg>
        )}
      </button>

      {/* Color picker popup */}
      {showPicker && (
        <div
          ref={pickerRef}
          className="absolute left-6 top-0 z-50 flex gap-1 p-1.5 bg-gray-900 border border-gray-700 rounded-lg shadow-xl"
        >
          {COLORS.map((c) => (
            <button
              key={c.key}
              onClick={() => handleColorSelect(c.key)}
              className="w-5 h-5 rounded-full border-2 transition-transform hover:scale-125"
              style={{
                backgroundColor: c.hex,
                borderColor: favorite?.color === c.key ? '#fff' : 'transparent',
              }}
              title={c.label}
            />
          ))}
        </div>
      )}
    </div>
  );
}
