// Gold standard: Zustand store with typed state, actions, API fetch
// Pattern: create<State>() with get/set, async actions, error handling

import { create } from 'zustand';

interface ExampleState {
  items: { id: string; name: string }[];
  isLoading: boolean;
  error: string | null;
  filterBy: string | null;
  // Actions
  fetchItems: () => Promise<void>;
  setFilterBy: (filter: string | null) => void;
  reset: () => void;
}

export const useExampleStore = create<ExampleState>((set, get) => ({
  items: [],
  isLoading: false,
  error: null,
  filterBy: null,

  fetchItems: async () => {
    const { filterBy } = get();
    set({ isLoading: true, error: null });
    try {
      const params = new URLSearchParams();
      if (filterBy) params.set('filter', filterBy);
      const resp = await fetch(`/api/example?${params}`);
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || 'Server error');
      set({ items: data.items ?? [] });
    } catch (err) {
      set({ error: err instanceof Error ? err.message : 'Unknown error' });
    } finally {
      set({ isLoading: false });
    }
  },

  setFilterBy: (filterBy) => set({ filterBy }),
  reset: () => set({ items: [], error: null, filterBy: null }),
}));
