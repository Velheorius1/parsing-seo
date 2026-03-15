import { getSupabaseServer } from './client';
import type { TenderFavorite } from '@/types/parsing';

interface FavoriteRow {
  id: string;
  tender_id: string;
  color: string;
  note: string;
  created_at: string;
}

function rowToFavorite(row: FavoriteRow): TenderFavorite {
  return {
    id: row.id,
    tenderId: row.tender_id,
    color: row.color as TenderFavorite['color'],
    note: row.note,
    createdAt: new Date(row.created_at),
  };
}

// Получить все избранные
export async function getFavorites(): Promise<{ favorites: TenderFavorite[]; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { favorites: [], error: 'Supabase не настроен' };
  }

  const { data, error } = await supabase
    .from('tender_favorites')
    .select('*')
    .order('created_at', { ascending: false });

  if (error) {
    return { favorites: [], error: error.message };
  }

  return { favorites: (data as FavoriteRow[]).map(rowToFavorite) };
}

// Переключить избранное (добавить/удалить)
export async function toggleFavorite(
  tenderId: string,
): Promise<{ favorite: TenderFavorite | null; removed: boolean; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { favorite: null, removed: false, error: 'Supabase не настроен' };
  }

  // Проверяем есть ли уже
  const { data: existing } = await supabase
    .from('tender_favorites')
    .select('*')
    .eq('tender_id', tenderId)
    .single();

  if (existing) {
    // Удаляем (через service_role — RLS разрешает)
    const { error } = await supabase
      .from('tender_favorites')
      .delete()
      .eq('id', existing.id);

    if (error) {
      return { favorite: null, removed: false, error: error.message };
    }
    return { favorite: null, removed: true };
  }

  // Создаём
  const { data, error } = await supabase
    .from('tender_favorites')
    .insert({ tender_id: tenderId })
    .select()
    .single();

  if (error) {
    return { favorite: null, removed: false, error: error.message };
  }

  return { favorite: rowToFavorite(data as FavoriteRow), removed: false };
}

// Обновить цвет/заметку избранного
export async function updateFavorite(
  tenderId: string,
  updates: { color?: string; note?: string },
): Promise<{ favorite: TenderFavorite | null; error?: string }> {
  const supabase = getSupabaseServer();
  if (!supabase) {
    return { favorite: null, error: 'Supabase не настроен' };
  }

  const updateData: Record<string, string> = {};
  if (updates.color !== undefined) updateData.color = updates.color;
  if (updates.note !== undefined) updateData.note = updates.note;

  const { data, error } = await supabase
    .from('tender_favorites')
    .update(updateData)
    .eq('tender_id', tenderId)
    .select()
    .single();

  if (error) {
    return { favorite: null, error: error.message };
  }

  return { favorite: rowToFavorite(data as FavoriteRow) };
}
