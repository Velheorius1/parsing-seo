import { NextResponse } from 'next/server';
import { getSupabaseServer } from '@/lib/supabase/client';

// Ключевые слова для фильтрации тендеров полиграфической ниши
const NICHE_KEYWORDS = [
  'полиграф', 'печат', 'типограф', 'упаков', 'коробк', 'этикет',
  'гофр', 'картон', 'книг', 'каталог', 'брошюр', 'блокнот',
  'календар', 'баннер', 'стенд', 'бланк',
  'kitob', 'jurnal', 'daftar', 'nashr', 'bosma', 'qadoq', 'etiket',
];

interface UzexContract {
  provider_name?: string;
  product_name?: string;
  price?: number;
  customer_name?: string;
  contract_date?: string;
  lot_name?: string;
}

interface CompetitorStat {
  name: string;
  wins: number;
  totalValue: number;
  maxDeal: number;
  lastDate: string;
  categories: string[];
  isNiche: boolean;
}

interface RecentDeal {
  competitor: string;
  title: string;
  price: number;
  customer: string;
  date: string;
  isNiche: boolean;
}

function isNicheTender(text: string): boolean {
  const lower = text.toLowerCase();
  return NICHE_KEYWORDS.some((kw) => lower.includes(kw));
}

export async function GET() {
  try {
    // Load competitor list from Supabase crawler_settings
    let competitorNames: string[] = [];
    const supabase = getSupabaseServer();

    if (supabase) {
      const { data } = await supabase
        .from('crawler_settings')
        .select('value')
        .eq('key', 'competitor_keywords')
        .single();

      if (data && data.value) {
        try {
          competitorNames = typeof data.value === 'string'
            ? JSON.parse(data.value)
            : data.value;
        } catch {
          competitorNames = [];
        }
      }
    }

    // Fetch resulted contracts from UZEX API
    const response = await fetch(
      'https://apietender.uzex.uz/api/CivilContracts/GetResulted',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ from: 0, to: 5200 }),
      },
    );

    if (!response.ok) {
      return NextResponse.json(
        { error: `UZEX API вернул ${response.status}` },
        { status: 502 },
      );
    }

    const contracts: UzexContract[] = await response.json();

    if (!Array.isArray(contracts)) {
      return NextResponse.json(
        { error: 'Некорректный формат данных от UZEX API' },
        { status: 502 },
      );
    }

    // Build competitor stats
    const statsMap = new Map<string, CompetitorStat>();
    const recentDeals: RecentDeal[] = [];
    const competitorNamesLower = competitorNames.map((n) => n.toLowerCase());

    for (const contract of contracts) {
      const providerName = (contract.provider_name || '').trim();
      if (!providerName) continue;

      const providerLower = providerName.toLowerCase();

      // Check if this provider is in our competitor list
      const isCompetitor = competitorNamesLower.some(
        (cn) => providerLower.includes(cn) || cn.includes(providerLower),
      );

      if (!isCompetitor) continue;

      const productText = [
        contract.product_name || '',
        contract.lot_name || '',
      ].join(' ');

      const niche = isNicheTender(productText);
      const price = Number(contract.price) || 0;
      const date = contract.contract_date || '';
      const customer = (contract.customer_name || '').trim();

      // Update stats
      let stat = statsMap.get(providerName);
      if (!stat) {
        stat = {
          name: providerName,
          wins: 0,
          totalValue: 0,
          maxDeal: 0,
          lastDate: '',
          categories: [],
          isNiche: false,
        };
        statsMap.set(providerName, stat);
      }

      stat.wins += 1;
      stat.totalValue += price;
      if (price > stat.maxDeal) stat.maxDeal = price;
      if (!stat.lastDate || date > stat.lastDate) stat.lastDate = date;
      if (niche) stat.isNiche = true;

      // Extract category from product name
      if (contract.product_name) {
        const category = contract.product_name.substring(0, 50);
        if (!stat.categories.includes(category) && stat.categories.length < 5) {
          stat.categories.push(category);
        }
      }

      // Collect niche deals for recent deals section
      if (niche) {
        recentDeals.push({
          competitor: providerName,
          title: (contract.product_name || contract.lot_name || 'Без названия').substring(0, 120),
          price,
          customer,
          date,
          isNiche: true,
        });
      }
    }

    // Sort stats by totalValue desc
    const stats = Array.from(statsMap.values()).sort(
      (a, b) => b.totalValue - a.totalValue,
    );

    // Sort recent deals by date desc, take top 20
    recentDeals.sort((a, b) => (b.date > a.date ? 1 : -1));
    const topRecentDeals = recentDeals.slice(0, 20);

    // Summary
    const summary = {
      totalCompetitors: stats.length,
      totalDeals: stats.reduce((sum, s) => sum + s.wins, 0),
      nicheDeals: recentDeals.length,
      totalValue: stats.reduce((sum, s) => sum + s.totalValue, 0),
    };

    return NextResponse.json({
      stats,
      recentDeals: topRecentDeals,
      summary,
    });
  } catch (error) {
    console.error('Ошибка получения данных конкурентов:', error);
    return NextResponse.json(
      { error: 'Внутренняя ошибка сервера' },
      { status: 500 },
    );
  }
}
