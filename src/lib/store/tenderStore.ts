import { create } from 'zustand';
import type { Tender } from '@/types/parsing';

const DEFAULT_KEYWORDS = [
  // Русский
  'упаковка', 'полиграфия', 'гофра', 'коробка',
  'печать', 'этикетка', 'типография', 'книга',
  'каталог', 'брошюра', 'блокнот', 'календарь',
  // Узбекский
  'bosma', 'qadoqlash', 'qadoq', 'kitob', 'quti',
  'etiketka', 'matbaa', 'katalog', 'bloknot',
  'kalendar', 'gofra', 'broshyura', 'nashri',
  'chop etish', 'qog\'oz', 'karton', 'paddon',
  'paket', 'stiker', 'plakat', 'banner',
];

interface TenderState {
  tenders: Tender[];
  keywords: string[];
  selectedKeywords: string[];
  isLoading: boolean;
  error: string | null;
  totalFound: number;
  sourceStats: Record<string, number>;

  // Фильтры
  sortBy: 'price-asc' | 'price-desc' | 'deadline' | null;
  filterSource: string | null;
  filterRegion: string | null;
  filterMinPrice: number | null;
  filterMaxPrice: number | null;

  // Действия
  toggleKeyword: (keyword: string) => void;
  addKeyword: (keyword: string) => void;
  removeKeyword: (keyword: string) => void;
  searchTenders: () => Promise<void>;
  setTenders: (tenders: Tender[]) => void;
  setSortBy: (sort: TenderState['sortBy']) => void;
  setFilterSource: (source: string | null) => void;
  setFilterRegion: (region: string | null) => void;
  setFilterMinPrice: (price: number | null) => void;
  setFilterMaxPrice: (price: number | null) => void;
}

export const useTenderStore = create<TenderState>((set, get) => ({
  tenders: [],
  keywords: DEFAULT_KEYWORDS,
  selectedKeywords: [],
  isLoading: false,
  error: null,
  totalFound: 0,
  sourceStats: {},

  sortBy: null,
  filterSource: null,
  filterRegion: null,
  filterMinPrice: null,
  filterMaxPrice: null,

  toggleKeyword: (keyword) =>
    set((state) => {
      const exists = state.selectedKeywords.includes(keyword);
      return {
        selectedKeywords: exists
          ? state.selectedKeywords.filter((k) => k !== keyword)
          : [...state.selectedKeywords, keyword],
      };
    }),

  addKeyword: (keyword) =>
    set((state) => {
      const trimmed = keyword.trim().toLowerCase();
      if (!trimmed || state.keywords.includes(trimmed)) return state;
      return {
        keywords: [...state.keywords, trimmed],
        selectedKeywords: [...state.selectedKeywords, trimmed],
      };
    }),

  removeKeyword: (keyword) =>
    set((state) => ({
      keywords: state.keywords.filter((k) => k !== keyword),
      selectedKeywords: state.selectedKeywords.filter((k) => k !== keyword),
    })),

  searchTenders: async () => {
    const { selectedKeywords } = get();

    if (selectedKeywords.length === 0) {
      set({ error: 'Выберите хотя бы одно ключевое слово' });
      return;
    }

    set({ isLoading: true, error: null, tenders: [], totalFound: 0, sourceStats: {} });

    try {
      const response = await fetch('/api/tenders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keywords: selectedKeywords }),
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Ошибка сервера: ${response.status}`);
      }

      set({
        tenders: data.tenders || [],
        totalFound: data.total || 0,
        sourceStats: data.sourceStats || {},
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Неизвестная ошибка' });
    } finally {
      set({ isLoading: false });
    }
  },

  setTenders: (tenders) => set({ tenders, totalFound: tenders.length }),
  setSortBy: (sortBy) => set({ sortBy }),
  setFilterSource: (filterSource) => set({ filterSource }),
  setFilterRegion: (filterRegion) => set({ filterRegion }),
  setFilterMinPrice: (filterMinPrice) => set({ filterMinPrice }),
  setFilterMaxPrice: (filterMaxPrice) => set({ filterMaxPrice }),
}));
