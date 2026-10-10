"""Обращения в поддержку из приложения: хранилище по клиентам (DATA_DIR/support/<chat_id>.json).

Сообщение админу уходит так же, как у sup:new; ответ владельца (/reply CHAT_ID текст) приходит клиенту в чат бота
и отмечается здесь, чтобы приложение могло показать статус. Модуль не знает про Flask.
"""
import os
import re
import secrets
import time

B = None
KEEP = 50
TOPICS = {"payment": "Оплата", "letter": "Письмо или открытка", "app": "Приложение", "other": "Другое"}


def init(bot_module):
    global B
    B = bot_module


def _path(chat_id):
    d = os.path.join(B.DATA_DIR, "support")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{int(chat_id)}.json")


def tickets(chat_id):
    return B.read_json(_path(chat_id), []) or []


def _save(chat_id, items):
    B.write_json(_path(chat_id), items[-KEEP:])


def add(chat_id, topic, text):
    items = tickets(chat_id)
    ticket = {"ticketId": f"T-{int(time.time())}{secrets.randbelow(900) + 100}",
              "topic": topic if topic in TOPICS else "other", "text": text, "status": "sent",
              "createdAt": B.now_msk().isoformat(), "reply": None, "repliedAt": None}
    items.append(ticket)
    _save(chat_id, items)
    return ticket


def count_since(chat_id, seconds):
    """Сколько обращений человек отправил за последние seconds секунд."""
    now = B.now_msk()
    n = 0
    for t in tickets(chat_id):
        try:
            if (now - B.datetime.fromisoformat(t["createdAt"])).total_seconds() <= seconds:
                n += 1
        except (KeyError, ValueError):
            continue
    return n


def mark_answered(chat_id, reply):
    """Ответ владельца: все открытые обращения человека получают этот ответ. Возвращает, сколько отмечено."""
    items = tickets(chat_id)
    n = 0
    for t in items:
        if t.get("status") != "answered":
            t.update(status="answered", reply=str(reply)[:2000], repliedAt=B.now_msk().isoformat())
            n += 1
    if n:
        _save(chat_id, items)
    return n


def public(items, limit=20):
    return [{"ticketId": t["ticketId"], "topic": t["topic"], "topicTitle": TOPICS.get(t["topic"], "Другое"),
             "text": t["text"], "status": t["status"], "createdAt": t["createdAt"], "reply": t.get("reply"),
             "repliedAt": t.get("repliedAt")} for t in reversed(items[-limit:])]


SAFE_TOPIC = re.compile(r"^[a-z]{3,10}$")
