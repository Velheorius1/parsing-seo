#!/usr/bin/env python3
"""Full quality audit — re-evaluate recent tenders through Qwen 3.6 Plus.

Checks:
1. Missed tenders: were there relevant tenders that got NO alert?
2. False positives: alerts that shouldn't have been sent
3. Source coverage: which sources contribute, which are dead
4. Keyword effectiveness: which keywords catch real tenders
5. Overall quality score

Usage:
    python3 -m crawler.scripts.audit_quality [--days 7] [--limit 500] [--send]
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

from crawler.config.settings import settings


def get_supabase():
    from supabase import create_client
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


async def ai_evaluate(title, organization, client):
    """Ask Qwen 3.6 Plus: is this relevant? Returns (bool, raw_answer)."""
    import re
    prompt = """Наша компания — ТИПОГРАФИЯ и УПАКОВОЧНОЕ производство в Узбекистане.

МЫ ДЕЛАЕМ (YES):
- Коробки (гофро, картон, подарочные)
- Этикетки, стикеры, наклейки
- Полиграфия (каталоги, брошюры, блокноты, визитки, буклеты)
- Пакеты (полиэтилен, крафт)
- Постеры, плакаты, интерьерная печать
- Сувенирная продукция (ручки, флешки, ежедневники)
- Печать на футболках, флагах, лентах (DTF, сублимация)
- UV печать, ламинирование, переплёт
- Пластиковые карты

НЕ НАШЕ (NO):
- Наружная реклама, вывески, световые короба
- Оклейка авто, тонировка
- Вакансии, SMM, IT, дизайн без печати
- Стройматериалы, мебель, станки
- Закупка готовых книг, учебников (не печать на заказ)
- Подписка на газеты/журналы
- Мелкий заказ (<50 штук)
- Реклама чужих услуг

Название: %s
Заказчик: %s

Ответь YES или NO и КРАТКО почему (1 предложение).
/no_think""" % (title[:300], organization or "не указан")

    try:
        import httpx
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": "Bearer %s" % settings.openrouter_api_key},
            json={
                "model": settings.ai_relevance_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 80,
                "temperature": 0,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            return None, "HTTP %d" % resp.status_code

        data = resp.json()
        raw = data["choices"][0]["message"]["content"] or ""
        answer = raw.strip()
        if "<think>" in answer:
            answer = re.sub(r"<think>.*?</think>", "", answer, flags=re.DOTALL).strip()

        is_yes = answer.upper().startswith("YES")
        return is_yes, answer
    except Exception as exc:
        return None, str(exc)[:100]


async def run_audit(days=7, limit=500, send_telegram=False):
    """Main audit logic."""
    import httpx

    sb = get_supabase()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # ── 1. Get recent alerted tenders (check false positives) ──
    logger.info("=== PHASE 1: Checking alerted tenders (false positives) ===")
    alerted = sb.table("tenders").select(
        "id,external_id,title,organization,source,source_url,alert_seq,price,deadline"
    ).gte("created_at", since).not_.is_("alert_seq", "null").order(
        "alert_seq", desc=True
    ).limit(100).execute()

    logger.info("Found %d alerted tenders in last %d days", len(alerted.data), days)

    false_positives = []
    true_positives = []

    async with httpx.AsyncClient(timeout=20) as client:
        for i, t in enumerate(alerted.data):
            is_rel, reason = await ai_evaluate(t["title"], t.get("organization"), client)
            status = "YES" if is_rel else "NO" if is_rel is False else "ERR"
            logger.info("  [%s] #%s %s: %s — %s",
                        status, t.get("alert_seq"), t["source"][:20],
                        t["title"][:50], reason[:60])
            if is_rel is False:
                false_positives.append({
                    "seq": t.get("alert_seq"),
                    "title": t["title"],
                    "source": t["source"],
                    "reason": reason,
                })
            elif is_rel is True:
                true_positives.append(t)
            await asyncio.sleep(0.3)  # rate limit

    # ── 2. Get recent NON-alerted tenders (check missed) ──
    logger.info("\n=== PHASE 2: Checking non-alerted tenders (missed opportunities) ===")
    non_alerted = sb.table("tenders").select(
        "id,external_id,title,organization,source,price,deadline"
    ).gte("created_at", since).is_("alert_seq", "null").order(
        "created_at", desc=True
    ).limit(limit).execute()

    logger.info("Sampling %d non-alerted tenders", len(non_alerted.data))

    missed = []
    async with httpx.AsyncClient(timeout=20) as client:
        for i, t in enumerate(non_alerted.data):
            is_rel, reason = await ai_evaluate(t["title"], t.get("organization"), client)
            if is_rel:
                missed.append({
                    "title": t["title"],
                    "source": t["source"],
                    "organization": t.get("organization"),
                    "price": t.get("price"),
                    "reason": reason,
                })
                logger.info("  [MISSED!] %s: %s — %s",
                            t["source"][:20], t["title"][:50], reason[:60])
            if (i + 1) % 50 == 0:
                logger.info("  ... checked %d / %d", i + 1, len(non_alerted.data))
            await asyncio.sleep(0.2)

    # ── 3. Source coverage ──
    logger.info("\n=== PHASE 3: Source coverage ===")
    all_tenders = sb.table("tenders").select("source").gte(
        "created_at", since
    ).limit(5000).execute()

    source_counts = Counter(t["source"] for t in all_tenders.data)
    total = len(all_tenders.data)

    # ── 4. Crawl runs stats ──
    logger.info("\n=== PHASE 4: Crawl runs ===")
    runs = sb.table("crawl_runs").select(
        "started_at,total_fetched,total_new,alerts_sent,errors_count,duration_seconds"
    ).gte("started_at", since).order("started_at", desc=True).limit(50).execute()

    total_fetched = sum(r["total_fetched"] or 0 for r in runs.data)
    total_new = sum(r["total_new"] or 0 for r in runs.data)
    total_alerts = sum(r["alerts_sent"] or 0 for r in runs.data)
    total_errors = sum(r["errors_count"] or 0 for r in runs.data)
    avg_duration = sum(r["duration_seconds"] or 0 for r in runs.data) / max(len(runs.data), 1)

    # ── 5. Compile report ──
    logger.info("\n=== COMPILING REPORT ===")

    precision = len(true_positives) / max(len(alerted.data), 1) * 100
    missed_rate = len(missed) / max(len(non_alerted.data), 1) * 100

    report = []
    report.append("📊 АУДИТ ПАРСЕРА — Qwen 3.6 Plus")
    report.append("Период: %d дней | Модель: %s" % (days, settings.ai_relevance_model))
    report.append("")

    report.append("═══ КАЧЕСТВО АЛЕРТОВ ═══")
    report.append("Проверено алертов: %d" % len(alerted.data))
    report.append("✅ Верных (true positive): %d" % len(true_positives))
    report.append("❌ Ложных (false positive): %d" % len(false_positives))
    report.append("Precision: %.0f%%" % precision)
    report.append("")

    if false_positives:
        report.append("Ложные алерты:")
        for fp in false_positives[:10]:
            report.append("  #%s %s — %s" % (fp["seq"], fp["title"][:40], fp["reason"][:50]))
        report.append("")

    report.append("═══ УПУЩЕННЫЕ ТЕНДЕРЫ ═══")
    report.append("Проверено: %d (без алерта)" % len(non_alerted.data))
    report.append("🔍 Найдено упущенных: %d (%.1f%%)" % (len(missed), missed_rate))
    report.append("")

    if missed:
        report.append("Упущенные:")
        for m in missed[:15]:
            price_str = "{:,.0f}".format(m["price"]) if m.get("price") else "?"
            report.append("  • %s | %s | %s сум" % (m["title"][:45], m["source"][:20], price_str))
        if len(missed) > 15:
            report.append("  ... и ещё %d" % (len(missed) - 15))
        report.append("")

    report.append("═══ ИСТОЧНИКИ (%d) ═══" % len(source_counts))
    report.append("Топ-10:")
    for src, cnt in source_counts.most_common(10):
        report.append("  %5d | %s" % (cnt, src))
    dead_sources = [s for s, c in source_counts.items() if c < 3]
    report.append("Мёртвые (<3 записей): %d источников" % len(dead_sources))
    report.append("")

    report.append("═══ ИНФРАСТРУКТУРА ═══")
    report.append("Прогонов: %d | Ошибок: %d" % (len(runs.data), total_errors))
    report.append("Fetch: %d | New: %d | Alerts: %d" % (total_fetched, total_new, total_alerts))
    report.append("Avg duration: %.0fs" % avg_duration)
    report.append("")

    # Overall score
    score = 10.0
    if precision < 80:
        score -= (80 - precision) / 10
    if missed_rate > 5:
        score -= missed_rate / 5
    if total_errors > 0:
        score -= 1
    if len(dead_sources) > 20:
        score -= 1
    score = max(1, min(10, score))

    report.append("═══ ИТОГОВАЯ ОЦЕНКА: %.1f / 10 ═══" % score)

    full_report = "\n".join(report)
    print("\n" + full_report)

    # Send to Telegram if requested
    if send_telegram and settings.telegram_bot_token and settings.telegram_alert_chat_id:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10) as tg:
            # Split long message
            chunks = [full_report[i:i+4000] for i in range(0, len(full_report), 4000)]
            for chunk in chunks:
                await tg.post(
                    "https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                    json={
                        "chat_id": settings.telegram_alert_chat_id,
                        "text": chunk,
                        "disable_web_page_preview": True,
                    },
                )
        logger.info("Report sent to Telegram")

    return full_report


def main():
    parser = argparse.ArgumentParser(description="Parsing-SEO quality audit")
    parser.add_argument("--days", type=int, default=7, help="Days to audit")
    parser.add_argument("--limit", type=int, default=300, help="Non-alerted sample size")
    parser.add_argument("--send", action="store_true", help="Send report to Telegram")
    args = parser.parse_args()

    asyncio.run(run_audit(days=args.days, limit=args.limit, send_telegram=args.send))


if __name__ == "__main__":
    main()
