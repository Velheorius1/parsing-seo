import { NextResponse } from 'next/server';
import { getSupabaseServer } from '@/lib/supabase/client';

interface BuyerRow {
  organization: string;
  count: number;
  total: number;
}

interface RegionRow {
  region: string;
  count: number;
  total: number;
}

interface CategoryRow {
  category: string;
  count: number;
  total: number;
}

interface DiscountRow {
  avg_discount: number | null;
  total_with_winner: number;
}

export async function GET() {
  try {
    const supabase = getSupabaseServer();
    if (!supabase) {
      return NextResponse.json(
        { error: 'Supabase не настроен' },
        { status: 503 },
      );
    }

    // Top 10 buyers by tender count
    const { data: topBuyers, error: buyersError } = await supabase
      .rpc('analytics_top_buyers') as { data: BuyerRow[] | null; error: unknown };

    // Region stats
    const { data: regionStats, error: regionsError } = await supabase
      .rpc('analytics_region_stats') as { data: RegionRow[] | null; error: unknown };

    // Category stats (unnest categories array)
    const { data: categoryStats, error: categoriesError } = await supabase
      .rpc('analytics_category_stats') as { data: CategoryRow[] | null; error: unknown };

    // Average discount
    const { data: discountData, error: discountError } = await supabase
      .rpc('analytics_avg_discount') as { data: DiscountRow[] | null; error: unknown };

    const firstError = buyersError || regionsError || categoriesError || discountError;
    if (firstError) {
      // Fallback: query directly without RPC functions
      return await getAnalyticsFallback(supabase);
    }

    const avgDiscount = discountData && discountData.length > 0
      ? discountData[0]
      : { avg_discount: null, total_with_winner: 0 };

    return NextResponse.json({
      topBuyers: topBuyers || [],
      regionStats: regionStats || [],
      categoryStats: categoryStats || [],
      avgDiscount: avgDiscount.avg_discount,
      totalWithWinner: avgDiscount.total_with_winner,
    });
  } catch (error) {
    console.error('Analytics error:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}

// Fallback: raw SQL queries via supabase if RPC functions don't exist
async function getAnalyticsFallback(supabase: ReturnType<typeof getSupabaseServer>) {
  if (!supabase) {
    return NextResponse.json({ error: 'Supabase не настроен' }, { status: 503 });
  }

  // Fetch tenders for client-side aggregation (capped to prevent OOM)
  const { data: tenders, error } = await supabase
    .from('tenders')
    .select('organization, price, region, categories, winning_price')
    .limit(10000);

  if (error) {
    console.error('Analytics fallback error:', error.message);
    return NextResponse.json(
      { error: `Ошибка загрузки аналитики: ${error.message}` },
      { status: 500 },
    );
  }

  const rows = tenders as Array<{
    organization: string;
    price: number | null;
    region: string;
    categories: string[];
    winning_price: number | null;
  }>;

  // Top buyers
  const buyerMap = new Map<string, { count: number; total: number }>();
  for (const row of rows) {
    const org = row.organization || 'Не указан';
    const entry = buyerMap.get(org) || { count: 0, total: 0 };
    entry.count += 1;
    entry.total += row.price || 0;
    buyerMap.set(org, entry);
  }
  const topBuyers = Array.from(buyerMap.entries())
    .map(([organization, stats]) => ({ organization, ...stats }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  // Region stats
  const regionMap = new Map<string, { count: number; total: number }>();
  for (const row of rows) {
    const region = row.region || 'Не указан';
    const entry = regionMap.get(region) || { count: 0, total: 0 };
    entry.count += 1;
    entry.total += row.price || 0;
    regionMap.set(region, entry);
  }
  const regionStats = Array.from(regionMap.entries())
    .map(([region, stats]) => ({ region, ...stats }))
    .sort((a, b) => b.count - a.count);

  // Category stats (unnest)
  const catMap = new Map<string, { count: number; total: number }>();
  for (const row of rows) {
    const cats = row.categories || [];
    for (const cat of cats) {
      const entry = catMap.get(cat) || { count: 0, total: 0 };
      entry.count += 1;
      entry.total += row.price || 0;
      catMap.set(cat, entry);
    }
  }
  const categoryStats = Array.from(catMap.entries())
    .map(([category, stats]) => ({ category, ...stats }))
    .sort((a, b) => b.count - a.count);

  // Avg discount
  let discountSum = 0;
  let discountCount = 0;
  for (const row of rows) {
    if (row.winning_price !== null && row.price !== null && row.price > 0) {
      discountSum += ((row.price - row.winning_price) / row.price) * 100;
      discountCount += 1;
    }
  }

  return NextResponse.json({
    topBuyers,
    regionStats,
    categoryStats,
    avgDiscount: discountCount > 0 ? discountSum / discountCount : null,
    totalWithWinner: discountCount,
  });
}
