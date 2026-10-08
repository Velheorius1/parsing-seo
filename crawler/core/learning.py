"""Предложения правок фильтра: кнопки «да / нет» и решение Данияра (фаза 5).

Система только предлагает — слово в словарь алертов, площадку в работу; включает
человек кнопкой под предложением (решение Данияра 07.10.2026). Предложения пишет
crawler/scripts/propose_fixes.py, клик ловит feedback_bot.

Кто нажал — проверяется. Кнопки релевантности feedback_bot принимает от любого,
кто видит сообщение; здесь клик меняет фильтр, поэтому решать может только
владелец личного чата алертов (id личного чата в Telegram совпадает с id
пользователя) или явный список LEARNING_APPROVER_IDS. Групповой чат (id < 0)
никого не даёт: без явного списка решать не может никто.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Set, Tuple

TABLE = 'learning_proposals'
PREFIX = 'lp'
DECISIONS = {'ok': 'approved', 'no': 'rejected'}
MARKS = {
    'approved': {'emoji': '✅', 'text': 'Одобрено'},
    'rejected': {'emoji': '❌', 'text': 'Отклонено'},
    'applied': {'emoji': '✅', 'text': 'Включено'},
    'retired': {'emoji': '⏹', 'text': 'Снято'},
}


# Короткие имена площадок для кнопок (полный текст — в propose_fixes.CHANNEL_TEXT).
CHANNEL_NAMES = {
    'ebirja_shop': 'Э-магазин ebirja',
    'direct': 'Прямые договоры UZEX',
    'ebirja_selection': 'Отборы ebirja',
    'ebirja_auction': 'Аукционы ebirja',
    'ebirja_tender': 'Тендеры ebirja',
}


def button_label(kind, key):
    # type: (str, str) -> str
    return (key if kind == 'keyword' else CHANNEL_NAMES.get(key, key))[:24]


def callback_data(pid, label):
    # type: (int, str) -> str
    return '%s:%d:%s' % (PREFIX, pid, label)


def keyboard(pid):
    # type: (int) -> list
    return [[{'text': '✅ Да', 'callback_data': callback_data(pid, 'ok')},
             {'text': '❌ Нет', 'callback_data': callback_data(pid, 'no')}]]


def parse(data):
    # type: (Any) -> Optional[Tuple[int, str]]
    """'lp:12:ok' -> (12, 'ok'). Мусор и чужие кнопки -> None, не исключение."""
    parts = str(data or '').split(':')
    if len(parts) != 3 or parts[0] != PREFIX or parts[2] not in DECISIONS:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    return (pid, parts[2]) if pid > 0 else None


def approvers(chat_id, extra=''):
    # type: (Any, Any) -> Set[int]
    """id тех, кто вправе решать: владелец личного чата алертов + явный список."""
    ids = set()  # type: Set[int]
    raw_ids = [chat_id]  # type: list
    raw_ids.extend(str(extra or '').split(','))
    for raw in raw_ids:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.add(value)
    return ids


def who(user):
    # type: (Dict[str, Any]) -> str
    name = user.get('username') or user.get('first_name') or ''
    return ('%s %s' % (user.get('id'), name)).strip()


def keyboard_for(rows):
    # type: (Iterable[Dict[str, Any]]) -> list
    """Клавиатура сообщения по СОСТОЯНИЮ В БАЗЕ: ждущее — две кнопки, решённое — подпись.

    Раньше бот правил разметку, пришедшую вместе с кликом. При быстрых кликах
    она уже устаревшая, и правки затирали друг друга: 08.10 «Э-магазин» был
    одобрен в базе, а в чате у него остались кнопки. Строить из базы — значит
    последняя правка всегда несёт полную правду."""
    out = []
    for row in sorted(rows, key=lambda r: r['id']):
        label = button_label(row.get('kind'), row.get('key'))
        if row.get('status') == 'proposed':
            out.append([{'text': '✅ %s' % label, 'callback_data': callback_data(row['id'], 'ok')},
                        {'text': '❌ %s' % label, 'callback_data': callback_data(row['id'], 'no')}])
        else:
            mark = MARKS.get(row.get('status'), {'emoji': '·', 'text': str(row.get('status'))})
            out.append([{'text': '%s %s · %s' % (mark['emoji'], mark['text'], label), 'callback_data': 'done'}])
    return out


def message_rows(client, message_id):
    # type: (Any, Any) -> list
    if not message_id:
        return []
    return client.table(TABLE).select('id,kind,key,status').eq('telegram_message_id', message_id) \
        .execute().data or []


def decide(client, pid, label, by, now=None):
    # type: (Any, int, str, str, Optional[datetime]) -> Tuple[Optional[str], Optional[Dict[str, Any]]]
    """Записать решение, если предложение ещё ждёт.

    -> (новый статус, строка) или (None, None): уже решено раньше или такого id
    нет. Условие status='proposed' стоит в самом UPDATE, поэтому второй клик
    (или клик по старому сообщению) не перезаписывает первое решение.
    """
    status = DECISIONS[label]
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    rows = client.table(TABLE).update(
        {'status': status, 'decided_by': by, 'decided_at': stamp, 'updated_at': stamp}
    ).eq('id', pid).eq('status', 'proposed').execute().data or []
    return (status, rows[0]) if rows else (None, None)


def ack_text(status, row):
    # type: (str, Dict[str, Any]) -> str
    text = MARKS[status]['text']
    if status == 'approved' and row.get('kind') == 'keyword':
        # Правило «слова только коммитом» (shadow_search.promote): живой словарь
        # один — alert_keywords в settings.py, и слово туда идёт с тестом.
        text += ': слово включу коммитом с тестом'
    return text


def pending_keywords(rows, live):
    # type: (Iterable[Dict[str, Any]], Iterable[str]) -> Tuple[list, list]
    """Одобренные слова: (уже в живом словаре -> applied, ещё ждут коммита)."""
    live_set = set(k.strip().lower() for k in live)
    applied, waiting = [], []
    for row in rows:
        if row.get('kind') != 'keyword' or row.get('status') != 'approved':
            continue
        (applied if row.get('key') in live_set else waiting).append(row)
    return applied, waiting
