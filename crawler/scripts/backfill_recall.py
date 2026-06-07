"""C1 backfill: surface ACTIVE never-alerted in-scope lots in biddable buyer-bearing
sources that the now-fixed filter (C2/C3) catches. Reuses notifier.send_alerts
(keyword->AI->send->save_alert_seq). Strong-keyword prefilter EXCLUDES filler-word
noise (зарур/kerak/lozim → 92 construction махалла) so AI isn't wasted on garbage.
Default DRY-RUN. Pass --execute to actually send."""
import argparse, asyncio, sys
sys.path.insert(0, "/opt/parsing-seo")
from supabase import create_client
from crawler.config.settings import settings
from crawler.core.notifier import send_alerts
from crawler.core.models import RawTender

c = create_client(settings.supabase_url, settings.supabase_service_role_key)
TODAY = "2026-06-06"

SOURCES = [
    "B2Biz.uz (Тендеры)", "B2Biz.uz (Планы закупок)",
    "Hayotbirja отбор", "Hayotbirja встречные аукционы", "Hayotbirja тендеры",
    "Ebirja Электронный магазин", "Ebirja Национальный магазин",
    "ETender UZEX", "ETender Обсуждения",
    "Xarid Конкурсы", "Xarid Прямые закупки",
    "Tender.mc.uz (Минстрой)", "UZEX Предквалификации", "UZEX Обратные аукционы",
    "XT-Xarid встречные аукционы", "XT-Xarid тендеры",
]

# STRONG printing-product regex — high precision. Deliberately EXCLUDES filler
# (зарур/kerak/lozim) and bare ambiguous (промо/термос/футболк) that only add AI noise.
STRONG = (
    "печат|полиграф|типограф|bosma|chop etish|блокнот|ежедневник|тетрад|daftar|"
    "конверт|konvert|бланк|каталог|katalog|брошюр|буклет|buklet|картон|karton|гофр|"
    "коробк|упаков|qadoq|этикет|yorliq|наклейк|стикер|бейдж|табличк|выставочн|"
    "информацион.{0,12}стенд|стенд.{0,12}лдсп|календар|kalendar|открытк|визитк|vizitka|"
    "плакат|постер|издат|изделия из бумаг|чек.{0,4}лент|kitob"
)

FIELDS = ("id,external_id,title,organization,price,currency,deadline,date_start,date_end,"
          "source,source_url,status,search_text,message_type,extra_info,relevance_score")

def pull():
    rows = []
    for src in SOURCES:
        off = 0
        while True:
            q = (c.table("tenders").select(FIELDS).eq("source", src)
                 .is_("alert_seq", "null").gte("deadline", TODAY)
                 .range(off, off + 999).execute())
            d = q.data or []
            rows.extend(d)
            if len(d) < 1000:
                break
            off += 1000
    return rows

def to_tender(r):
    return RawTender(
        id=r.get("id") or r.get("external_id"), external_id=r.get("external_id") or "",
        title=r.get("title") or "", organization=r.get("organization") or "",
        price=r.get("price"), currency=r.get("currency") or "UZS",
        deadline=r.get("deadline"), date_start=r.get("date_start"), date_end=r.get("date_end"),
        source=r.get("source") or "", source_url=r.get("source_url") or "",
        status=r.get("status") or "active", search_text=r.get("search_text") or "",
        message_type=r.get("message_type") or "tender", extra_info=r.get("extra_info") or {},
    )

async def main(execute):
    import re
    rx = re.compile(STRONG, re.I)
    rows = pull()
    cands = [to_tender(r) for r in rows
             if rx.search((r.get("title") or "") + " " + (r.get("search_text") or ""))]
    print("Active never-alerted in biddable sources: %d | strong-keyword candidates: %d" % (len(rows), len(cands)))
    from collections import Counter
    bysrc = Counter(t.source for t in cands)
    for s, n in bysrc.most_common():
        print("   %-34s %d" % (s[:34], n))
    if not cands:
        print("nothing to backfill"); return
    print("\n=== send_alerts(dry_run=%s) — keyword+AI filter applied inside ===" % (not execute))
    sent = await send_alerts(cands, dry_run=(not execute))
    print("\n>>> %s: %s tenders would-alert/sent <<<" % ("EXECUTED" if execute else "DRY-RUN", sent))

if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()
    asyncio.run(main(a.execute))
