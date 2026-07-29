"""fix_feedback_labels — точечная правка ошибочных меток в alert_feedback.

Зачем. Клики Данияра по кнопкам алертов — самая сильная правда, которая у нас
есть: из них строятся few-shot примеры для классификатора и еженедельная
дистилляция принципов. Поэтому ошибочный клик не просто «неверная строка в
таблице» — он учит систему неправильному, тихо и каждую неделю.

Три такие метки найдены 28.07 при разборе precision гейта лидов: помечены как
шум запросы на печать наклеек и изготовление коробки, то есть ровно наш профиль
(наклейки и коробки прямо в keep-списке промпта). Данияр подтвердил, что
ошибочными были клики, а не правила.

Что делает и чего НЕ делает:
  ✓ правит ТОЛЬКО `corrected_label` у трёх строк, найденных по UUID
  ✓ бэкап полных строк в JSON ДО любой записи
  ✓ по умолчанию dry-run; запись только с --execute
  ✓ сверяет текст и текущую метку перед записью — если данные разъехались,
    отказывается работать, а не правит наугад
  ✓ перечитывает строки после записи и подтверждает результат
  ✗ НЕ трогает `original_label` — это исторический факт «что сказала система»,
    и его подмена сфабриковала бы сигнал коррекции, которого не было
  ✗ НЕ трогает mute-счётчики (см. ниже — они на это не влияют)
  ✗ НЕ удаляет и не добавляет строк

Что поменяется после правки:
  • few-shot примеры (`get_few_shot_examples`): три примера перестанут учить
    классификатор считать такие запросы рекламой. Кэш в памяти живёт 2 часа —
    мгновенный эффект даёт `systemctl restart parsing-feedback-bot`
  • понедельничный `playbook_refine`: три строки уйдут из счёта отклонений
  • mute-счётчики `crawler_settings[mute_patterns_v1]` считаются инкрементами
    при клике и задним числом НЕ пересчитываются. Для этого источника там
    neg=192 / pos=70, а правило мьюта требует pos==0 — то есть три записи ничего
    не решают ни до, ни после. Трогать их не нужно

Запуск (на VPS):
  .venv/bin/python3 -m crawler.scripts.fix_feedback_labels            # показать
  .venv/bin/python3 -m crawler.scripts.fix_feedback_labels --execute  # применить
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/opt/parsing-seo")

# UUID — стабильный ключ. Текст и текущая метка проверяются перед записью:
# если строка окажется не той, скрипт откажется работать.
CORRECTIONS = [
    {
        "id": "1de03d5c-2fcd-473f-bae9-5c1039fbf34c",
        "alert_seq": 6463,
        "text_prefix": "Нужен исполнитель для печати наклеек на листе",
        "from": "irrelevant", "to": "client",
        "why": "печать наклеек — прямо в keep-списке промпта лидов",
    },
    {
        "id": "a4e9f2a6-4678-44c6-bfdf-de04a4ff3eef",
        "alert_seq": 6411,
        "text_prefix": "Изготовление именной подарочной коробки срочно",
        "from": "irrelevant", "to": "client",
        "why": "коробка — наш профиль; единичность не делает заказ чужим",
    },
    {
        "id": "f63c78fa-de31-4a88-b2b8-c703c30fb270",
        "alert_seq": 6041,
        "text_prefix": "sergelida UV pechat kimda bor",
        "from": "ad", "to": "client",
        "why": "запрос УФ-печати на коробке, помечен как реклама по ошибке",
    },
]

_DEFAULT_BACKUP_DIR = os.environ.get("METRICS_DIR") or "/opt/parsing-seo/logs"


def _client():
    from crawler.core.db import _get_client
    return _get_client()


def _fetch(client, row_id):
    from crawler.core.db import query_with_retry

    def _q():
        return client.table("alert_feedback").select("*").eq("id", row_id).limit(1).execute()

    rows = query_with_retry(_q, label="fb-fetch").data or []
    return rows[0] if rows else None


def _plan(client):
    """Returns (todo, already, problems) without touching anything."""
    todo, already, problems = [], [], []
    for c in CORRECTIONS:
        row = _fetch(client, c["id"])
        if row is None:
            problems.append((c, "строка с таким id не найдена"))
            continue
        text = (row.get("message_text") or "")
        if not text.startswith(c["text_prefix"]):
            problems.append((c, "текст не совпал: %r" % text[:60]))
            continue
        cur = row.get("corrected_label")
        if cur == c["to"]:
            already.append((c, row))
        elif cur != c["from"]:
            problems.append((c, "метка сейчас %r, ожидалась %r" % (cur, c["from"])))
        else:
            todo.append((c, row))
    return todo, already, problems


def _show(todo, already, problems):
    print("=" * 74)
    print("ПРАВКА МЕТОК В alert_feedback")
    print("=" * 74)
    for c, row in todo:
        print("\n  #%s  %s" % (c["alert_seq"], c["id"]))
        print("     текст:    %s" % (row.get("message_text") or "")[:64])
        print("     источник: %s" % row.get("source"))
        print("     клик:     %s  →  %s" % (c["from"], c["to"]))
        print("     причина:  %s" % c["why"])
        print("     original_label остаётся %r (историю не переписываем)"
              % row.get("original_label"))
    for c, _row in already:
        print("\n  #%s — уже %s, пропуск" % (c["alert_seq"], c["to"]))
    for c, why in problems:
        print("\n  ⚠️  #%s — ПРОБЛЕМА: %s" % (c["alert_seq"], why))
    print("\n" + "-" * 74)
    print("к правке: %d | уже применено: %d | проблем: %d"
          % (len(todo), len(already), len(problems)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true",
                    help="применить правку (без флага — только показать)")
    ap.add_argument("--backup", default=None,
                    help="куда сложить полные строки до правки")
    a = ap.parse_args()

    client = _client()
    todo, already, problems = _plan(client)
    _show(todo, already, problems)

    if problems:
        print("\n❌ Данные разъехались с ожиданием — НИЧЕГО не меняю.")
        print("   Разберись со строками выше и запусти заново.")
        return 2
    if not todo:
        print("\n✅ Всё уже применено, работы нет.")
        return 0
    if not a.execute:
        print("\nЭто dry-run. Чтобы применить:  --execute")
        return 0

    # Бэкап ДО записи — полные строки, чтобы откат был механическим.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = a.backup or os.path.join(_DEFAULT_BACKUP_DIR,
                                      "alert_feedback_backup_%s.json" % stamp)
    if not os.path.isdir(os.path.dirname(backup)):
        os.makedirs(os.path.dirname(backup))
    with open(backup, "w", encoding="utf-8") as f:
        json.dump([row for _c, row in todo], f, ensure_ascii=False, indent=1, default=str)
    print("\nбэкап: %s (%d строк)" % (backup, len(todo)))

    from crawler.core.db import query_with_retry
    changed = 0
    for c, _row in todo:
        def _upd(cc=c):
            return (client.table("alert_feedback")
                    .update({"corrected_label": cc["to"]})
                    .eq("id", cc["id"]).execute())
        try:
            query_with_retry(_upd, label="fb-update #%s" % c["alert_seq"])
            changed += 1
        except Exception as exc:
            print("  ❌ #%s не обновилась: %s" % (c["alert_seq"], str(exc)[:90]))

    # Проверка ПОСЛЕ записи — «обновил» без перечтения не считается.
    print("\nпроверка:")
    ok = 0
    for c, _row in todo:
        row = _fetch(client, c["id"])
        cur = (row or {}).get("corrected_label")
        good = cur == c["to"]
        ok += 1 if good else 0
        print("  %s #%s: corrected_label = %r" % ("✅" if good else "❌", c["alert_seq"], cur))

    print("\nобновлено %d из %d, подтверждено %d" % (changed, len(todo), ok))
    if ok == len(todo):
        print("\nДальше:")
        print("  1. systemctl restart parsing-feedback-bot   # сбросить 2-часовой кэш few-shot")
        print("  2. понедельничный playbook_refine пересчитает принципы сам")
        print("  3. откат при необходимости: метки из %s" % backup)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
