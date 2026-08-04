"""Еженедельная сводка «что нового у банков» (04.08).

Зачем отдельно от алертов. Банковские сайты — узкий, но ценный канал: там
лежат «конверты для банковских карт», «наклейки A5», «корпоративный мерч», то
есть прямые заказы без посредника. Обычный конвейер присылает оттуда только
то, что прошло AI-гейт, а Данияру нужна ещё и картина покрытия: какие банки
вообще что-то опубликовали за неделю. Эта сводка — про покрытие, а не про
релевантность, поэтому она НЕ фильтрует по профилю и НЕ трогает alert_seq.

Что считаем актуальным. Срок подачи ещё не прошёл ЛИБО опубликовано за
последние FRESH_DAYS дней. Два условия нужны потому, что банки хранят разное:
у одних в поле лежит настоящая «Дата истечения» (aab, aloqabank, mkbank,
turonbank, poytaxtbank, trustbank), у других срока нет в природе, и мы кладём
дату публикации в date_start (anorbank, sqb). Лот без обеих дат в сводку не
берётся — иначе туда затечёт весь архив: замер 04.08 показал 90 архивных
лотов на 7 актуальных.

Почему с состоянием. Без него лот с открытым сроком приходил бы каждую неделю,
пока срок не истечёт. Отправленное запоминаем в `bank_digest_seen` и больше не
показываем.

Почему сводка приходит ДАЖЕ когда нового нет. Ровно тот урок, который этот
проект получил 04.08 трижды за день: отсутствие сигнала неотличимо от
отсутствия события. Молчащая рассылка выглядит так же, как «банки ничего не
публикуют» — и так же, как сломанный крон.

    python3 -m crawler.scripts.bank_digest            # сухой прогон
    python3 -m crawler.scripts.bank_digest --send     # отправка + запись состояния

Крон: 30 7 * * 1 (понедельник, 12:30 по Ташкенту).
"""
import argparse
import sys
from datetime import date, timedelta

import httpx

from crawler.config.settings import settings
from crawler.core.notifier import _parse_deadline

STATE_KEY = "bank_digest_seen"
FRESH_DAYS = 7
TG_LIMIT = 3200
KEEP_SEEN = 500          # сколько external_id помним, чтобы состояние не пухло

# Источники-банки. Список ведём руками: «банк» по имени определить нельзя —
# World Bank и IsDB это доноры-грантодатели, а не заказчики нашего профиля.
BANK_SOURCES = [
    "Anor Bank",
    "Asia Alliance Bank",
    "Алокабанк",
    "Узпромстройбанк (SQB)",
    "Трастбанк",
    "Туронбанк",
    "Микрокредитбанк",
    "НБУ (Нац. банк ВЭД)",
    "Пойтахт банк",
    "Хамкорбанк",
    "Ипотека-банк",
]


def _headers():
    return {"apikey": settings.supabase_service_role_key,
            "Authorization": "Bearer " + settings.supabase_service_role_key}


def _as_date(value):
    dt = _parse_deadline(value)
    return dt.date() if dt else None


def freshness(row, today=None, fresh_days=FRESH_DAYS):
    # type: (dict, date, int) -> tuple
    """(актуален?, чем подтверждён). Чистая функция — на ней держатся тесты."""
    today = today or date.today()
    dl = _as_date(row.get("deadline"))
    if dl is not None and dl >= today:
        return True, "до %s" % dl.strftime("%d.%m.%Y")
    pub = _as_date(row.get("date_start"))
    if pub is not None and pub >= today - timedelta(days=fresh_days):
        return True, "опубликован %s" % pub.strftime("%d.%m.%Y")
    return False, None


def fetch_bank(source, since_iso):
    p = [("select", "external_id,title,source_url,deadline,date_start,relevance_score,alert_seq"),
         ("source", "eq." + source), ("collected_at", "gte." + since_iso), ("limit", "200")]
    r = httpx.get(settings.supabase_url + "/rest/v1/tenders", headers=_headers(),
                  params=p, timeout=60)
    if r.status_code != 200:
        print("  %s: HTTP %d — источник пропущен, охват неполный" % (source, r.status_code))
        return []
    return r.json()


def build_message(groups, total, archived, silent_banks):
    head = "🏦 Банки за неделю — новых лотов: %d" % total
    if not total:
        body = [head, "",
                "Новых объявлений нет. Сводка приходит и пустой намеренно: молчание"
                " неотличимо от сломанного крона."]
        if silent_banks:
            body.append("")
            body.append("Не публиковали: %s" % ", ".join(silent_banks))
        return ["\n".join(body)]

    header = "%s\nОтсеяно как архив: %d\n" % (head, archived)
    blocks = []
    for bank, items in groups:
        lines = ["\n▪️ %s — %d" % (bank, len(items))]
        for row, why in items:
            mark = ""
            if row.get("alert_seq"):
                mark = " ✅уже присылал"
            elif (row.get("relevance_score") or 0) >= 70:
                mark = " ⭐наш профиль"
            lines.append("• %s · %s%s\n%s" % ((row.get("title") or "")[:88], why, mark,
                                              row.get("source_url") or "—"))
        blocks.append("\n".join(lines))
    if silent_banks:
        blocks.append("\n▫️ Не публиковали: %s" % ", ".join(silent_banks))

    msgs, cur = [], header
    for b in blocks:
        if len(cur) + len(b) + 2 > TG_LIMIT:
            msgs.append(cur)
            cur = b
        else:
            cur = cur + "\n" + b
    if cur.strip():
        msgs.append(cur)
    return msgs


def send(text):
    r = httpx.post("https://api.telegram.org/bot%s/sendMessage" % settings.telegram_bot_token,
                   json={"chat_id": settings.telegram_alert_chat_id, "text": text,
                         "disable_web_page_preview": True}, timeout=30)
    d = r.json()
    return r.status_code == 200, d.get("description")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--fresh-days", type=int, default=FRESH_DAYS)
    ap.add_argument("--ignore-state", action="store_true",
                    help="не фильтровать по уже показанному (для разовой сверки)")
    args = ap.parse_args()

    from crawler.auth.session_store import session_store
    state = session_store.get_setting(STATE_KEY) or {}
    seen = set() if args.ignore_state else set(state.get("ids") or [])

    since = (date.today() - timedelta(days=2)).isoformat()
    today = date.today()
    groups, total, archived, silent = [], 0, 0, []
    fresh_ids = []
    for bank in BANK_SOURCES:
        rows = fetch_bank(bank, since)
        items = []
        for row in rows:
            ok, why = freshness(row, today, args.fresh_days)
            if not ok:
                archived += 1
                continue
            if row.get("external_id") in seen:
                continue
            items.append((row, why))
            fresh_ids.append(row.get("external_id"))
        if items:
            groups.append((bank, items))
            total += len(items)
        elif not rows:
            silent.append(bank)

    msgs = build_message(groups, total, archived, silent)
    print("банков с новым: %d, лотов: %d, архива отсеяно: %d, молчат: %d, сообщений: %d"
          % (len(groups), total, archived, len(silent), len(msgs)))
    if not args.send:
        for m in msgs:
            print("\n===== %d символов =====\n%s" % (len(m), m[:1500]))
        return 0

    for i, m in enumerate(msgs, 1):
        suffix = "" if len(msgs) == 1 else "\n\n(%d из %d)" % (i, len(msgs))
        ok, err = send(m + suffix)
        print("  #%d: %s %s" % (i, "отправлено" if ok else "ОШИБКА", err or ""))
        if not ok:
            return 1

    # Состояние пишем ТОЛЬКО после успешной отправки: иначе неудачная неделя
    # молча съела бы лоты и они не пришли бы уже никогда.
    if not args.ignore_state:
        merged = (state.get("ids") or []) + [i for i in fresh_ids if i]
        state["ids"] = merged[-KEEP_SEEN:]
        state["last_run"] = today.isoformat()
        session_store.set_setting(STATE_KEY, state)
        print("состояние обновлено: помним %d id" % len(state["ids"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
