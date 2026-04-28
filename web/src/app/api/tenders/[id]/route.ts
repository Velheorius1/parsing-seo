import { NextRequest, NextResponse } from 'next/server';
import { getTenderById } from '@/lib/supabase/tenders';

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
