import { NextRequest, NextResponse } from 'next/server';

/**
 * Proxy for cooperation.uz API — bypasses geo-block from Russian VPS.
 * Vercel runs on AWS (non-RU IP), so cooperation.uz allows requests.
 *
 * Usage: GET /api/proxy/cooperation?Skip=0&Take=50&endpoint=GetAllPlanSchedule
 * Auth: X-Proxy-Key header must match PROXY_SECRET env var.
 */

const ALLOWED_ENDPOINTS: Record<string, string> = {
  GetAllPlanSchedule:
    'https://new.cooperation.uz/ocelot/api-client/Client/GetAllPlanSchedule',
  GetAllOffer:
    'https://new.cooperation.uz/ocelot/api-client/Client/GetAllOffer',
  GetLotsInTrade:
    'https://new.cooperation.uz/ocelot/api-shop/LotRequest/GetLotsInTrade',
};

export async function GET(request: NextRequest) {
  // Auth check
  const proxySecret = process.env.PROXY_SECRET;
  if (!proxySecret) {
    return NextResponse.json({ error: 'Proxy not configured' }, { status: 503 });
  }

  const authKey = request.headers.get('X-Proxy-Key');
  if (authKey !== proxySecret) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  // Parse params
  const { searchParams } = new URL(request.url);
  const endpoint = searchParams.get('endpoint') || 'GetAllPlanSchedule';
  const skip = searchParams.get('Skip') || '0';
  const take = searchParams.get('Take') || '50';

  const baseUrl = ALLOWED_ENDPOINTS[endpoint];
  if (!baseUrl) {
    return NextResponse.json(
      { error: 'Unknown endpoint', allowed: Object.keys(ALLOWED_ENDPOINTS) },
      { status: 400 },
    );
  }

  // Proxy request to cooperation.uz
  const targetUrl = `${baseUrl}?Skip=${skip}&Take=${take}`;

  try {
    const resp = await fetch(targetUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (compatible; TenderMonitor/1.0)',
        Accept: 'application/json',
      },
      signal: AbortSignal.timeout(15000),
    });

    if (!resp.ok) {
      return NextResponse.json(
        { error: `Upstream error: ${resp.status}` },
        { status: resp.status },
      );
    }

    const data = await resp.json();
    return NextResponse.json(data);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return NextResponse.json({ error: message }, { status: 502 });
  }
}
