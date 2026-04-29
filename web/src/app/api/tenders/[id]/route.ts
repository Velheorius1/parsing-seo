import { NextRequest, NextResponse } from 'next/server';
import { getTenderById } from '@/lib/supabase/tenders';

// Always read fresh from Supabase — extra_info is updated by the crawler
// notifier (screenshot URL) and we want the tender page to reflect it
// without waiting for ISR revalidation.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  const { id } = params;

  if (!id) {
    return NextResponse.json({ error: 'ID required' }, { status: 400 });
  }

  const tender = await getTenderById(id);

  if (!tender) {
    return NextResponse.json({ error: 'Tender not found' }, { status: 404 });
  }

  return NextResponse.json({ tender });
}
