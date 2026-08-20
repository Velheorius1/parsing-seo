#!/usr/bin/env python3
"""Feedback bot — handles inline button callbacks from alert messages.

Runs as a standalone long-polling process. Only processes callback_query updates
(button presses on alert messages). Does NOT conflict with the crawler's
sendMessage calls (those are stateless HTTP, not long-polling).

Usage:
    python3 -m crawler.scripts.feedback_bot

Deploy as systemd service:
    [Unit]
    Description=Parsing SEO Feedback Bot
    After=network.target

    [Service]
    Type=simple
    WorkingDirectory=/opt/parsing-seo
    ExecStart=/usr/bin/python3 -m crawler.scripts.feedback_bot
    Restart=always
    RestartSec=10
    EnvironmentFile=/opt/parsing-seo/.env

    [Install]
    WantedBy=multi-user.target
"""

import json
import logging
import os
import sys
import time

import httpx

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from crawler.config.settings import settings
from crawler.core.feedback import record_feedback
from crawler.core import outcome as outcome_mod

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("feedback_bot")

# Label mapping: callback_data label -> human-readable + corrected_label
LABEL_MAP = {
    "ok": {"corrected": "client", "emoji": "\u2705", "text": "\u041a\u043b\u0438\u0435\u043d\u0442"},
    "ad": {"corrected": "ad", "emoji": "\U0001f4e2", "text": "\u0420\u0435\u043a\u043b\u0430\u043c\u0430"},
    "skip": {"corrected": "irrelevant", "emoji": "\u274c", "text": "\u041c\u0438\u043c\u043e"},
}

# Кнопки исхода (20.08). Ось ДРУГАЯ, чем у LABEL_MAP: та отвечает «релевантен
# ли лот», эта — «что мы с ним сделали и чем он кончился». Смешивать нельзя:
# лот бывает идеально релевантным и при этом проигранным.
#   bid  — пуш-кнопка «Подал заявку» / «Взял в работу»
#   won / lost / dead — ответы на еженедельный вопрос по уже поданным
#
# «lost» ложится в lot_result='won_by_other' с ПУСТЫМ winner: человек знает,
# что не выиграл, но не обязан знать кто выиграл. Пустое имя победителя и
# отличает «мы проиграли» от разобранного автоматикой «выиграл вот этот».
OUTCOME_MAP = {
    "bid":  {"kind": "action", "value": "bid",
             "emoji": "\U0001f4c4", "text": "\u041f\u043e\u0434\u0430\u043b"},
    "won":  {"kind": "result", "value": "won_by_us",
             "emoji": "\U0001f3c6", "text": "\u0412\u044b\u0438\u0433\u0440\u0430\u043b\u0438"},
    "lost": {"kind": "result", "value": "won_by_other",
             "emoji": "\u2796", "text": "\u041d\u0435 \u0432\u0437\u044f\u043b\u0438"},
    "dead": {"kind": "result", "value": "no_deal",
             "emoji": "\U0001f6ab", "text": "\u041d\u0435 \u0440\u0430\u0437\u044b\u0433\u0440\u0430\u043d"},
}

BOT_URL = "https://api.telegram.org/bot%s" % settings.telegram_bot_token


def answer_callback(callback_query_id, text):
    # type: (str, str) -> None
    """Send answerCallbackQuery to acknowledge button press."""
    try:
        httpx.post(
            BOT_URL + "/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("Failed to answer callback: %s", str(exc))


def remaining_keyboard(old_rows, seq, label_info, prefix="fb"):
    # type: (list, int, dict, str) -> list
    """Клавиатура ПОСЛЕ клика. Чистая функция — вся суть правки 11.08 здесь.

    Раньше любой клик заменял всю клавиатуру одной кнопкой-подписью. Для
    отдельного алерта это верно: сообщение про один лот, выбор сделан. Но
    дайджест — ОДНО сообщение на десять лотов, и там такой обмен означал бы:
    отметил третью строку — потерял кнопки у остальных девяти. То есть оценить
    можно было бы ровно один лот из десяти.

    Поэтому: строку кликнутого лота подменяем подписью, остальные оставляем
    как есть. Для одиночного алерта поведение прежнее (строка всего одна).
    """
    mark = "%s %s #%03d" % (label_info["emoji"], label_info["text"], seq)
    # Префикс важен: у пуш-алерта теперь ДВЕ строки на один и тот же номер —
    # оценка релевантности (fb:) и исход (out:). Клик по одной не должен
    # гасить другую, иначе отметив «интересно» теряешь возможность сказать,
    # что подал заявку.
    prefix = "%s:%d:" % (prefix, seq)
    out = []
    for row in (old_rows or []):
        # строка «принадлежит» лоту, если её кнопки ведут на этот номер
        if any(str(b.get("callback_data", "")).startswith(prefix) for b in row):
            out.append([{"text": mark, "callback_data": "done"}])
        else:
            out.append(row)
    return out or [[{"text": mark, "callback_data": "done"}]]


def edit_message_markup(chat_id, message_id, label_info, seq, old_rows=None, prefix="fb"):
    # type: (int, int, dict, int, list, str) -> None
    """Отметить выбор, не стирая кнопки соседних строк дайджеста."""
    try:
        httpx.post(
            BOT_URL + "/editMessageReplyMarkup",
            json={
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {
                    "inline_keyboard": remaining_keyboard(old_rows, seq, label_info, prefix),
                },
            },
            timeout=10,
        )
    except Exception as exc:
        logger.debug("Failed to edit markup: %s", str(exc))


def parse_callback(data):
    # type: (str) -> tuple
    """'out:123:bid' -> ('out', 123, 'bid'). Чистая функция.

    Возвращает (None, None, None) на любом мусоре: обработчик не должен падать
    от кнопки из чужого сообщения или от обрезанного callback_data.
    """
    parts = str(data or "").split(":")
    if len(parts) != 3:
        return (None, None, None)
    kind, raw_seq, label = parts
    if kind not in ("fb", "out"):
        return (None, None, None)
    try:
        return (kind, int(raw_seq), label)
    except (ValueError, TypeError):
        return (None, None, None)


def process_outcome(cq, seq, label_key):
    # type: (dict, int, str) -> None
    """Клик по кнопке исхода: «подал заявку» или ответ про результат.

    Пишется в alert_outcome, НЕ в alert_feedback: релевантность и исход — две
    независимые оси. Лот бывает идеально релевантным и при этом проигранным,
    и складывать это в одну метку значит терять обе.
    """
    callback_id = cq.get("id", "")
    info = OUTCOME_MAP.get(label_key)
    if not info:
        answer_callback(callback_id, "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043a\u043d\u043e\u043f\u043a\u0430")
        return

    if info["kind"] == "action":
        ok = outcome_mod.record_action(seq, info["value"])
    else:
        ok = outcome_mod.record_result(seq, info["value"])

    if not ok:
        answer_callback(callback_id, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0438\u0441\u0438")
        return

    answer_callback(callback_id, "\u0417\u0430\u043f\u0438\u0441\u0430\u043d\u043e: #%03d \u2192 %s" % (seq, info["text"]))
    msg = cq.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    if chat_id and message_id:
        old_rows = (msg.get("reply_markup") or {}).get("inline_keyboard")
        edit_message_markup(chat_id, message_id, info, seq, old_rows, prefix="out")
    logger.info("Outcome: #%03d -> %s", seq, info["text"])


def process_callback(update):
    # type: (dict) -> None
    """Process a single callback_query update."""
    cq = update.get("callback_query")
    if not cq:
        return

    data = cq.get("data", "")
    callback_id = cq.get("id", "")

    if data.startswith("out:"):
        kind, seq, label_key = parse_callback(data)
        if seq is None:
            answer_callback(callback_id, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0444\u043e\u0440\u043c\u0430\u0442\u0430")
            return
        process_outcome(cq, seq, label_key)
        return

    # Parse callback_data: "fb:{seq}:{label}"
    if not data.startswith("fb:"):
        if data == "done":
            answer_callback(callback_id, "\u0423\u0436\u0435 \u043e\u0442\u043c\u0435\u0447\u0435\u043d\u043e")
        return

    parts = data.split(":")
    if len(parts) != 3:
        answer_callback(callback_id, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0444\u043e\u0440\u043c\u0430\u0442\u0430")
        return

    try:
        seq = int(parts[1])
    except (ValueError, TypeError):
        answer_callback(callback_id, "\u041e\u0448\u0438\u0431\u043a\u0430 \u043d\u043e\u043c\u0435\u0440\u0430")
        return

    label_key = parts[2]
    label_info = LABEL_MAP.get(label_key)
    if not label_info:
        answer_callback(callback_id, "\u041d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u043d\u0430\u044f \u043c\u0435\u0442\u043a\u0430")
        return

    # Record feedback
    result = record_feedback(
        alert_seq=seq,
        corrected_label=label_info["corrected"],
    )

    if result:
        # Immediate visible effect: acknowledge + show what the click just did to the
        # source mute counter (feedback without a visible effect dies \u2014 proven).
        ack = "\u0417\u0430\u043f\u0438\u0441\u0430\u043d\u043e: #%03d \u2192 %s" % (seq, label_info["text"])
        mute = result.get("mute") if isinstance(result, dict) else None
        if mute and label_info["corrected"] in ("ad", "irrelevant"):
            if mute.get("muted"):
                ack += " \u00b7 \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u043f\u0440\u0438\u0433\u043b\u0443\u0448\u0451\u043d \u2192 \u0434\u0430\u0439\u0434\u0436\u0435\u0441\u0442"
            elif mute.get("neg"):
                ack += " \u00b7 \u0436\u0430\u043b\u043e\u0431: %d/%d" % (mute["neg"], mute.get("threshold", 3))
        answer_callback(callback_id, ack)
        # Update message buttons to show label
        msg = cq.get("message", {})
        chat_id = msg.get("chat", {}).get("id")
        message_id = msg.get("message_id")
        if chat_id and message_id:
            old_rows = (msg.get("reply_markup") or {}).get("inline_keyboard")
            edit_message_markup(chat_id, message_id, label_info, seq, old_rows)
    else:
        answer_callback(callback_id, "\u041e\u0448\u0438\u0431\u043a\u0430 \u0437\u0430\u043f\u0438\u0441\u0438")

    logger.info("Feedback: #%03d -> %s", seq, label_info["text"])


def poll_updates():
    # type: () -> None
    """Long-poll for callback_query updates only."""
    if not settings.telegram_bot_token:
        logger.error("TELEGRAM_BOT_TOKEN not set")
        sys.exit(1)

    logger.info("Feedback bot started. Listening for callback queries...")
    offset = 0

    with httpx.Client(timeout=60) as client:
        while True:
            try:
                resp = client.post(
                    BOT_URL + "/getUpdates",
                    json={
                        "offset": offset,
                        "timeout": 30,
                        "allowed_updates": ["callback_query"],
                    },
                )
                if resp.status_code != 200:
                    logger.warning("getUpdates error: %d", resp.status_code)
                    time.sleep(5)
                    continue

                data = resp.json()
                if not data.get("ok"):
                    logger.warning("getUpdates not ok: %s", json.dumps(data)[:200])
                    time.sleep(5)
                    continue

                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    process_callback(update)

            except httpx.TimeoutException:
                continue  # Normal for long-polling
            except KeyboardInterrupt:
                logger.info("Shutting down...")
                break
            except Exception as exc:
                logger.error("Poll error: %s", str(exc))
                time.sleep(5)


if __name__ == "__main__":
    poll_updates()
