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
  isRefreshing: boolean;
  error: string | null;
  totalFound: number;
  sourceStats: Record<string, number>;
  lastCrawledAt: string | null;

  // Фильтры
  sortBy: 'price-asc' | 'price-desc' | 'deadline' | null;
  filterSource: string | null;
  filterRegion: string | null;
  filterMinPrice: number | null;
  filterMaxPrice: number | null;
  filterStatus: string | null;
  filterCategory: string | null;
  excludeKeywords: string[];
  showAdvancedFilters: boolean;

  // Действия
  toggleKeyword: (keyword: string) => void;
  addKeyword: (keyword: string) => void;
  removeKeyword: (keyword: string) => void;
  searchTenders: () => Promise<void>;
  refreshTenders: () => Promise<void>;
  setTenders: (tenders: Tender[]) => void;
  setSortBy: (sort: TenderState['sortBy']) => void;
  setFilterSource: (source: string | null) => void;
  setFilterRegion: (region: string | null) => void;
  setFilterMinPrice: (price: number | null) => void;
  setFilterMaxPrice: (price: number | null) => void;
  setFilterStatus: (status: string | null) => void;
  setFilterCategory: (category: string | null) => void;
  addExcludeKeyword: (keyword: string) => void;
  removeExcludeKeyword: (keyword: string) => void;
  setShowAdvancedFilters: (show: boolean) => void;
  resetFilters: () => void;
}

export const useTenderStore = create<TenderState>((set, get) => ({
  tenders: [],
  keywords: DEFAULT_KEYWORDS,
  selectedKeywords: [],
  isLoading: false,
  isRefreshing: false,
  error: null,
  totalFound: 0,
  sourceStats: {},
  lastCrawledAt: null,

  sortBy: null,
  filterSource: null,
  filterRegion: null,
  filterMinPrice: null,
  filterMaxPrice: null,
  filterStatus: null,
  filterCategory: null,
  excludeKeywords: [],
  showAdvancedFilters: false,

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
    const {
      selectedKeywords, filterSource, filterRegion,
      filterMinPrice, filterMaxPrice, filterStatus, filterCategory,
      excludeKeywords,
    } = get();

    set({ isLoading: true, error: null, tenders: [], totalFound: 0, sourceStats: {} });

    try {
      const params = new URLSearchParams();
      if (selectedKeywords.length > 0) {
        params.set('keywords', selectedKeywords.join(','));
      }
      if (filterSource) params.set('source', filterSource);
      if (filterRegion) params.set('region', filterRegion);
      if (filterStatus) params.set('status', filterStatus);
      if (filterCategory) params.set('category', filterCategory);
      if (filterMinPrice !== null) params.set('minPrice', String(filterMinPrice));
      if (filterMaxPrice !== null) params.set('maxPrice', String(filterMaxPrice));
      if (excludeKeywords.length > 0) params.set('exclude', excludeKeywords.join(','));

      const response = await fetch(`/api/tenders?${params.toString()}`);
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.error || `Ошибка сервера: ${response.status}`);
      }

      set({
        tenders: data.tenders || [],
        totalFound: data.total || 0,
        sourceStats: data.sourceStats || {},
        lastCrawledAt: data.lastCrawledAt || null,
      });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Неизвестная ошибка' });
    } finally {
      set({ isLoading: false });
    }
  },

  refreshTenders: async () => {
    const { selectedKeywords } = get();

    if (selectedKeywords.length === 0) {
      set({ error: 'Выберите хотя бы одно ключевое слово' });
      return;
    }

    set({ isRefreshing: true, error: null });

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
      set({ isRefreshing: false });
    }
  },

  setTenders: (tenders) => set({ tenders, totalFound: tenders.length }),
  setSortBy: (sortBy) => set({ sortBy }),
  setFilterSource: (filterSource) => set({ filterSource }),
  setFilterRegion: (filterRegion) => set({ filterRegion }),
  setFilterMinPrice: (filterMinPrice) => set({ filterMinPrice }),
  setFilterMaxPrice: (filterMaxPrice) => set({ filterMaxPrice }),
  setFilterStatus: (filterStatus) => set({ filterStatus }),
  setFilterCategory: (filterCategory) => set({ filterCategory }),
  addExcludeKeyword: (keyword) =>
    set((state) => {
      const trimmed = keyword.trim().toLowerCase();
      if (!trimmed || state.excludeKeywords.includes(trimmed)) return state;
      return { excludeKeywords: [...state.excludeKeywords, trimmed] };
    }),
  removeExcludeKeyword: (keyword) =>
    set((state) => ({
      excludeKeywords: state.excludeKeywords.filter((k) => k !== keyword),
    })),
  setShowAdvancedFilters: (showAdvancedFilters) => set({ showAdvancedFilters }),
  resetFilters: () =>
    set({
      filterSource: null,
      filterRegion: null,
      filterMinPrice: null,
      filterMaxPrice: null,
      filterStatus: null,
      filterCategory: null,
      excludeKeywords: [],
      sortBy: null,
    }),
}));
