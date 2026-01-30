import { create } from 'zustand';
import type { Keyword } from '@/types/parsing';

interface KeywordState {
  keywords: Keyword[];
  isLoading: boolean;
  error: string | null;
  progress: { completed: number; total: number } | null;

  // Действия
  setKeywords: (keywords: Keyword[]) => void;
  addKeywords: (keywords: Keyword[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setProgress: (progress: { completed: number; total: number } | null) => void;
  clearAll: () => void;
}

export const useKeywordStore = create<KeywordState>((set) => ({
  keywords: [],
  isLoading: false,
  error: null,
  progress: null,

  setKeywords: (keywords) => set({ keywords }),
  addKeywords: (newKeywords) =>
    set((state) => ({
      keywords: [...state.keywords, ...newKeywords],
    })),
  setLoading: (isLoading) => set({ isLoading }),
  setError: (error) => set({ error }),
  setProgress: (progress) => set({ progress }),
  clearAll: () => set({ keywords: [], error: null, progress: null }),
}));
