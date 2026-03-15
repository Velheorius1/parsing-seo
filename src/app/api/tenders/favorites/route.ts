import { NextResponse } from 'next/server';
import { getFavorites, toggleFavorite, updateFavorite } from '@/lib/supabase/favorites';

// GET — список всех избранных
export async function GET() {
  try {
    const { favorites, error } = await getFavorites();
    if (error) {
      return NextResponse.json({ error }, { status: 500 });
    }
    return NextResponse.json({ favorites });
  } catch (error) {
    console.error('Favorites GET error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// POST — переключить избранное (toggle)
export async function POST(request: Request) {
  try {
    const body: unknown = await request.json();
    const { tenderId } = body as { tenderId?: string };

    if (!tenderId) {
      return NextResponse.json(
        { error: 'tenderId обязателен' },
        { status: 400 },
      );
    }

    const { favorite, removed, error } = await toggleFavorite(tenderId);
    if (error) {
      return NextResponse.json({ error }, { status: 500 });
    }

    return NextResponse.json({ favorite, removed });
  } catch (error) {
    console.error('Favorites POST error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// PUT — обновить цвет/заметку
export async function PUT(request: Request) {
  try {
    const body: unknown = await request.json();
    const { tenderId, color, note } = body as {
      tenderId?: string;
      color?: string;
      note?: string;
    };

    if (!tenderId) {
      return NextResponse.json(
        { error: 'tenderId обязателен' },
        { status: 400 },
      );
    }

    const { favorite, error } = await updateFavorite(tenderId, { color, note });
    if (error) {
      return NextResponse.json({ error }, { status: 500 });
    }

    return NextResponse.json({ favorite });
  } catch (error) {
    console.error('Favorites PUT error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
