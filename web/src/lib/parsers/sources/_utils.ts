/** Format a numeric price with currency code */
export function formatPrice(cost: number, code: string): string {
  if (!cost) return '';
  return new Intl.NumberFormat('ru-RU').format(cost) + ' ' + code;
}

/** Format ISO date string to DD.MM.YYYY */
export function formatDate(iso: string | null): string | null {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    return `${String(d.getDate()).padStart(2, '0')}.${String(d.getMonth() + 1).padStart(2, '0')}.${d.getFullYear()}`;
  } catch {
    return null;
  }
}

/** Return keywords that appear in the text (case-insensitive) */
export function matchText(text: string, keywords: string[]): string[] {
  const lower = text.toLowerCase();
  return keywords.filter(kw => lower.includes(kw.toLowerCase()));
}
