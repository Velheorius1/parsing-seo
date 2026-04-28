import { NextRequest, NextResponse } from 'next/server';

/**
 * Proxy for cooperation.uz API — bypasses geo-block from Russian VPS.
 * Uses Edge Runtime (Cloudflare network) since cooperation.uz blocks
 * both Russian IPs and AWS (Vercel serverless).
 *
 * Usage: GET /api/proxy/cooperation?Skip=0&Take=50&endpoint=GetAllPlanSchedule
 * Auth: X-Proxy-Key header must match PROXY_SECRET env var.
 */

export const runtime = 'edge';

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

  // Proxy request to cooperation.uz with manual timeout
  const targetUrl = `${baseUrl}?Skip=${skip}&Take=${take}`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 25000);

  try {
    const resp = await fetch(targetUrl, {
      headers: {
        'User-Agent':
          'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        Accept: 'application/json',
        'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8',
      },
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!resp.ok) {
      return NextResponse.json(
        { error: 'Upstream ' + resp.status, url: targetUrl },
        { status: resp.status },
      );
    }

    const data = await resp.json();
    return NextResponse.json(data);
  } catch (err) {
    clearTimeout(timeoutId);
    const message = err instanceof Error ? err.message : String(err);
    return NextResponse.json(
      { error: message, url: targetUrl, runtime: 'edge' },
      { status: 502 },
    );
  }
}
