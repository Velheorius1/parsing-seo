'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import type { Tender } from '@/types/parsing';

// SPA-площадки без deep links — кнопка "Открыть на площадке" бесполезна
const BROKEN_SPA_HOSTS = [
  'hayotbirja.uz',
  'xt-xarid.uz',
  'cooperation.uz',
  'xarid.uzex.uz/prequalification',
  'xarid.uzex.uz/competitions',
  'xarid.uzex.uz/direct-purchases',
];

function isOurUrl(url: string): boolean {
  return url.includes('parsing-seo.vercel.app');
}

function isBrokenSpa(url: string): boolean {
  // hayotbirja.uz & xt-xarid.uz /procedure/{id}/core deep-links open publicly
  // for active tenders (verified 2026-06-08) — show the button for those.
  if (/(?:hayotbirja\.uz|xt-xarid\.uz)\/procedure\//.test(url)) return false;
  // cooperation.uz: площадка глушится целиком, но два маршрута проверены
  // рендером и открываются анонимно — /plan-schedule/{guid} (04.08) и
  // /auction/{числовой id} (05.08). Держать в синхроне с
  // crawler/core/snap.py WORKING_SPA_SOURCES.
  if (/cooperation\.uz\/(?:plan-schedule|auction)\/[^/]+$/.test(url)) return false;
  return BROKEN_SPA_HOSTS.some((host) => url.includes(host));
}

// Русские лейблы для «сырых» ключей extra_info кооп-лотов (в порядке показа).
const EXTRA_LABELS: Record<string, string> = {
  quantity: 'Кол-во',
  measure: 'Ед. изм.',
  min_part: 'Партия от',
  max_part: 'Партия до',
  unit_price: 'Цена за ед.',
  certificate: 'Сертификат',
  ref_supplier: 'Оферта-эталон (перебиваем его цену)',
  ref_supplier_tin: 'ИНН эталона',
  tnved: 'ТНВЭД',
  offer: 'Оферта №',
  photo: 'photo',
};
// Служебные ключи, не для показа.
const HIDDEN_EXTRA_KEYS = new Set(['preview_screenshot_url', 'Проверено', 'Найден']);

function formatPrice(price: number | null, currency: string): string {
  if (price === null || price === undefined) return 'Не указана';
  return price.toLocaleString('ru-RU') + ' ' + currency;
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return 'Не указана';
  try {
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr;
    return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' });
  } catch {
    return dateStr;
  }
}

function statusBadge(status: string) {
  const colors: Record<string, string> = {
    active: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
    closed: 'bg-red-500/20 text-red-400 border-red-500/30',
    cancelled: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
    completed: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  };
  const c = colors[status] || colors.active;
  return (
    <span className={`px-3 py-1 rounded-full text-sm border ${c}`}>
      {status === 'active' ? 'Активен' : status === 'closed' ? 'Закрыт' : status === 'cancelled' ? 'Отменён' : 'Завершён'}
    </span>
  );
}

function daysLeft(deadline: string | null): string | null {
  if (!deadline) return null;
  try {
    const d = new Date(deadline);
    if (isNaN(d.getTime())) return null;
    const diff = Math.ceil((d.getTime() - Date.now()) / (1000 * 60 * 60 * 24));
    if (diff < 0) return 'Просрочен';
    if (diff === 0) return 'Сегодня';
    if (diff === 1) return 'Завтра';
    return `${diff} дн.`;
  } catch {
    return null;
  }
}

export default function TenderDetailPage() {
  const params = useParams();
  const router = useRouter();
  const [tender, setTender] = useState<Tender | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const id = params?.id as string;
    if (!id) return;

    fetch(`/api/tenders/${id}`)
      .then((res) => {
        if (!res.ok) throw new Error('Тендер не найден');
        return res.json();
      })
      .then((data) => setTender(data.tender))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [params?.id]);

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="text-gray-400 text-lg">Загрузка...</div>
      </div>
    );
  }

  if (error || !tender) {
    return (
      <div className="min-h-screen bg-gray-950 flex flex-col items-center justify-center gap-4">
        <div className="text-red-400 text-lg">{error || 'Тендер не найден'}</div>
        <button
          onClick={() => router.push('/tenders')}
          className="text-amber-400 hover:text-amber-300 underline"
        >
          Назад к списку
        </button>
      </div>
    );
  }

  const dl = daysLeft(tender.deadline);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="max-w-4xl mx-auto p-6">
        {/* Шапка */}
        <div className="flex items-center gap-4 mb-6">
          <button
            onClick={() => router.push('/tenders')}
            className="text-gray-400 hover:text-gray-200 transition-colors"
          >
            &larr; Назад
          </button>
          <span className="text-gray-600">|</span>
          <span className="text-gray-500 text-sm font-mono">#{tender.externalId}</span>
        </div>

        {/* Основная карточка */}
        <div className="bg-gray-900 rounded-xl border border-gray-800 p-8">
          {/* Заголовок + статус */}
          <div className="flex items-start justify-between gap-4 mb-6">
            <h1 className="text-2xl font-bold text-gray-100 leading-tight">
              {tender.title}
            </h1>
            {statusBadge(tender.status)}
          </div>

          {/* Сетка данных */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            {/* Заказчик */}
            {tender.organization && (
              <div>
                <div className="text-gray-500 text-sm mb-1">Заказчик</div>
                <div className="text-gray-200">{tender.organization}</div>
              </div>
            )}

            {/* Сумма */}
            <div>
              <div className="text-gray-500 text-sm mb-1">Сумма</div>
              <div className="text-2xl font-bold text-amber-400">
                {formatPrice(tender.price, tender.currency)}
              </div>
            </div>

            {/* Дедлайн */}
            <div>
              <div className="text-gray-500 text-sm mb-1">Дедлайн</div>
              <div className="flex items-center gap-3">
                <span className="text-gray-200">{formatDate(tender.deadline)}</span>
                {dl && (
                  <span className={`text-sm px-2 py-0.5 rounded ${
                    dl === 'Просрочен' ? 'bg-red-500/20 text-red-400' :
                    dl === 'Сегодня' || dl === 'Завтра' ? 'bg-orange-500/20 text-orange-400' :
                    'bg-gray-700 text-gray-300'
                  }`}>
                    {dl}
                  </span>
                )}
              </div>
            </div>

            {/* Регион */}
            {tender.region && (
              <div>
                <div className="text-gray-500 text-sm mb-1">Регион</div>
                <div className="text-gray-200">{tender.region}</div>
              </div>
            )}

            {/* Период */}
            {(tender.dateStart || tender.dateEnd) && (
              <div>
                <div className="text-gray-500 text-sm mb-1">Период</div>
                <div className="text-gray-200">
                  {formatDate(tender.dateStart)} — {formatDate(tender.dateEnd)}
                </div>
              </div>
            )}

            {/* Площадка */}
            <div>
              <div className="text-gray-500 text-sm mb-1">Площадка</div>
              <div className="text-gray-200">{tender.source}</div>
            </div>
          </div>

          {/* Детали лота (enriched extra_info: обогащение кооп-лотов и др.) */}
          {tender.extraInfo && Object.keys(tender.extraInfo).length > 0 && (
            <div className="mb-6">
              <div className="text-gray-500 text-sm mb-2">Детали лота</div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-2 bg-gray-800/40 border border-gray-800 rounded-lg p-4">
                {Object.entries(EXTRA_LABELS).map(([key, label]) => {
                  const raw = tender.extraInfo?.[key];
                  if (raw === undefined || raw === null || raw === '') return null;
                  if (key === 'photo') {
                    return (
                      <a key={key} href={String(raw)} target="_blank" rel="noopener noreferrer"
                         className="text-amber-400 hover:text-amber-300 underline text-sm">
                        📷 Фото товара
                      </a>
                    );
                  }
                  const value = key === 'certificate'
                    // false раньше тоже печатался как «требуется» — guard выше
                    // пропускает только undefined/null/'', а false доходит сюда
                    ? (String(raw).toLowerCase() === 'true' ? 'требуется' : 'не требуется')
                    : key === 'unit_price'
                      ? Number(raw).toLocaleString('ru-RU') + ' сум/' + (tender.extraInfo?.['measure'] || 'ед')
                      : String(raw);
                  return (
                    <div key={key} className="text-sm">
                      <span className="text-gray-500">{label}: </span>
                      <span className="text-gray-200">{value}</span>
                    </div>
                  );
                })}
                {/* Остальные (уже русскоязычные) ключи enrichment'а — как есть */}
                {Object.entries(tender.extraInfo)
                  .filter(([k]) => !(k in EXTRA_LABELS) && !HIDDEN_EXTRA_KEYS.has(k))
                  .map(([k, v]) => (
                    <div key={k} className="text-sm">
                      <span className="text-gray-500">{k}: </span>
                      <span className="text-gray-200">{String(v)}</span>
                    </div>
                  ))}
              </div>
            </div>
          )}

          {/* Категории */}
          {tender.categories && tender.categories.length > 0 && (
            <div className="mb-6">
              <div className="text-gray-500 text-sm mb-2">Категории</div>
              <div className="flex flex-wrap gap-2">
                {tender.categories.map((cat, i) => (
                  <span key={i} className="px-3 py-1 bg-gray-800 text-gray-300 rounded-lg text-sm border border-gray-700">
                    {cat}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Ключевые слова */}
          {tender.matchedKeywords && tender.matchedKeywords.length > 0 && (
            <div className="mb-6">
              <div className="text-gray-500 text-sm mb-2">Ключевые слова</div>
              <div className="flex flex-wrap gap-2">
                {tender.matchedKeywords.map((kw, i) => (
                  <span key={i} className="px-3 py-1 bg-amber-500/10 text-amber-400 rounded-lg text-sm border border-amber-500/20">
                    {kw}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Результат (если завершён) */}
          {tender.winner && (
            <div className="mb-6 p-4 bg-emerald-500/5 border border-emerald-500/20 rounded-lg">
              <div className="text-gray-500 text-sm mb-2">Результат</div>
              <div className="text-gray-200">
                Победитель: <span className="font-semibold text-emerald-400">{tender.winner}</span>
              </div>
              {tender.winningPrice && (
                <div className="text-gray-200 mt-1">
                  Цена: <span className="font-semibold">{formatPrice(tender.winningPrice, tender.currency)}</span>
                </div>
              )}
              {tender.resultDate && (
                <div className="text-gray-400 text-sm mt-1">
                  Дата: {formatDate(tender.resultDate)}
                </div>
              )}
            </div>
          )}

          {/* Ссылка на площадку (только если deep link рабочий) */}
          {tender.sourceUrl && !isOurUrl(tender.sourceUrl) && !isBrokenSpa(tender.sourceUrl) && (
            <a
              href={tender.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 px-4 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-lg transition-colors border border-gray-700"
            >
              Открыть на площадке &rarr;
            </a>
          )}

          {/* Snapshot нашей карточки (для broken SPA) */}
          {tender.previewScreenshotUrl && (
            <div className="mt-6 pt-4 border-t border-gray-800">
              <div className="text-gray-500 text-sm mb-2">
                Снимок карточки <span className="text-gray-400">(площадка не открывается напрямую)</span>
              </div>
              <a href={tender.previewScreenshotUrl} target="_blank" rel="noopener noreferrer">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={tender.previewScreenshotUrl}
                  alt="Скриншот карточки тендера"
                  className="rounded-lg border border-gray-800 max-w-full"
                  loading="lazy"
                />
              </a>
            </div>
          )}

          {/* Мета */}
          <div className="mt-8 pt-4 border-t border-gray-800 text-gray-500 text-xs flex items-center gap-4">
            <span>Собран: {formatDate(tender.collectedAt?.toString())}</span>
            <span>ID: {tender.id}</span>
          </div>
        </div>
      </div>
    </div>
  );
}
