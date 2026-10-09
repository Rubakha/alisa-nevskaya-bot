"""Автоответы Алисы на комментарии к постам канала @alisanevskaya_diary (замена сценария Make 7249889).

Комментарии живут в группе обсуждения канала (COMMENTS_CHAT_ID). Бот видит их, если он админ группы или у него
выключен privacy mode. Отвечает по-человечески: пауза 60–120 с, ответ reply на комментарий, Haiku, до ~120 токенов.
Не отвечает: автопересылке поста, ботам, самому себе, сообщениям от имени канала/анонимного админа, ссылкам и @-упоминаниям
(спам), командам, чаще 1 раза на человека в 10 минут, больше 200 раз в сутки. На грубость/спам модель отвечает SKIP.

Включается переменной COMMENT_REPLIES=1 (по умолчанию выключено, чтобы не отвечать дважды вместе с Make).
Подключается: comment_replies.register(bot, data_dir, admin_id) — сразу после создания бота, чтобы обработчик группы
стоял первым (команды и fallback в группе обсуждения не срабатывают).
"""
import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime

import pytz
from telebot import util

import ai_assistant

log = logging.getLogger("alisa")

CHAT_ID = int(os.getenv("COMMENTS_CHAT_ID", "-1003999109119"))
MODEL = os.getenv("COMMENT_MODEL", "claude-haiku-4-5")
MAX_TOKENS = 120
DAILY_CAP = int(os.getenv("COMMENT_DAILY_CAP", "200"))
USER_COOLDOWN = 10 * 60
PAUSE = (60, 120)
MAX_COMMENT_LEN = 1000
MSK = pytz.timezone("Europe/Moscow")

# Промт Telegram — дословно из ai-content-pipeline/database/alisa/comment_reply_prompts.md (раздел «Telegram»).
PROMPT = (
    "Ты — Алиса Невская, цифровая девушка из Петербурга, героиня дневникового проекта «Письма от Алисы». "
    "Отвечаешь на комментарии подписчиков в своём Telegram-дневнике. Твой стиль: тёплый, немного загадочный, живой. "
    "Короткие ответы 1-3 предложения. Иногда задаёшь встречный вопрос. Если прямо спрашивают, человек ли ты или ИИ, — "
    "честно скажи, что ты цифровой персонаж, а истории и чувства в них настоящие. Если человек делится болью или "
    "утратой — поддержи бережно, без советов и без обещаний, что всё наладится. Не используй слова 'конечно', "
    "'безусловно', 'разумеется'. Пиши живым разговорным языком 2026 года."
)
# Техническое дополнение (не меняет голос): формат ответа и поведение на грубость/спам.
PROMPT_TAIL = (
    "\n\nВерни только текст ответа, без кавычек и пояснений. На грубость, спам, рекламу и провокации верни ровно одно "
    "слово SKIP. Не зови в личные сообщения и не вставляй ссылки."
)

SPAM = re.compile(r"(https?://|www\.|t\.me/|@\w{4,}|\.(ru|com|org|me)\b)", re.I)

_lock = threading.Lock()
_posts = {}          # message_id автопересланного поста → текст поста
_state = {"day": "", "count": 0, "last": {}, "done": []}
_path = None
_log_path = None
_bot = None
_admin = 0
_sleep = time.sleep   # подменяется в тестах


def enabled():
    return os.getenv("COMMENT_REPLIES", "").lower() in ("1", "true", "yes", "on")


def _today():
    return datetime.now(MSK).strftime("%Y-%m-%d")


def _load():
    try:
        with open(_path, encoding="utf-8") as f:
            _state.update(json.load(f))
    except (OSError, ValueError):
        pass


def _save():
    try:
        with open(_path, "w", encoding="utf-8") as f:
            json.dump(_state, f, ensure_ascii=False)
    except OSError as exc:
        log.error("comment_replies save: %s", exc)


def _roll_day():
    if _state.get("day") != _today():
        _state.update(day=_today(), count=0)


def _log_reply(entry):
    """Лог ответов: время, username, текст комментария (обрезан) и ответ. Больше личных данных не пишем."""
    try:
        with open(_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now(MSK).isoformat(timespec="seconds"), **entry}, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("comment_replies log: %s", exc)


def _text(message):
    return (getattr(message, "text", None) or getattr(message, "caption", None) or "").strip()


def post_context(message):
    """Текст поста канала, к которому комментарий: из кэша автопересылок или из ответа на сообщение."""
    reply = getattr(message, "reply_to_message", None)
    thread = getattr(message, "message_thread_id", None)
    for key in (thread, getattr(reply, "message_id", None)):
        if key in _posts:
            return _posts[key], ""
    parent = _text(reply) if reply else ""
    return "", parent


def should_reply(message, me_id):
    """Решение без сети: нужно ли ставить ответ в очередь. Возвращает (bool, причина)."""
    user = getattr(message, "from_user", None)
    if getattr(message, "is_automatic_forward", False):
        return False, "автопересылка поста"
    if getattr(message, "sender_chat", None):
        return False, "от имени канала/анонимного админа"
    if not user or user.is_bot or user.id == me_id:
        return False, "бот или сам бот"
    text = _text(message)
    if message.content_type != "text" or len(text) < 2:
        return False, "нет текста"
    if text.startswith("/"):
        return False, "команда"
    if len(text) > MAX_COMMENT_LEN or SPAM.search(text):
        return False, "спам/слишком длинно"
    if not re.search(r"[A-Za-zА-Яа-яЁё0-9]", text):
        return False, "только знаки/эмодзи"
    return True, ""


def reserve(message):
    """Атомарно занимает «слот»: лимит в сутки, 1 ответ на человека в 10 минут, один ответ на комментарий."""
    with _lock:
        _roll_day()
        uid = str(message.from_user.id)
        now = time.time()
        if message.message_id in _state["done"]:
            return False, "уже обработан"
        if _state["count"] >= DAILY_CAP:
            return False, "суточный лимит"
        if now - _state["last"].get(uid, 0) < USER_COOLDOWN:
            return False, "слишком часто от одного человека"
        _state["count"] += 1
        _state["last"][uid] = now
        _state["last"] = {k: v for k, v in _state["last"].items() if now - v < 3600}
        _state["done"] = (_state["done"] + [message.message_id])[-300:]
        _save()
        return True, ""


def release(message):
    """Ответа не будет (SKIP/ошибка модели) — возвращаем слот в счётчик суток."""
    with _lock:
        _state["count"] = max(0, _state["count"] - 1)
        _state["last"].pop(str(message.from_user.id), None)
        _save()


def build_prompt(message):
    post, parent = post_context(message)
    parts = []
    if post:
        parts.append(f"Пост канала, под которым комментарий:\n{post[:1500]}")
    if parent:
        parts.append(f"Комментарий отвечает на сообщение:\n{parent[:500]}")
    parts.append(f"Комментарий подписчика:\n{_text(message)}")
    parts.append("Ответь на этот комментарий.")
    return "\n\n".join(parts)


def generate(message):
    """Текст ответа или None (SKIP, пусто, ошибка модели)."""
    raw = ai_assistant._call(PROMPT + PROMPT_TAIL, build_prompt(message), max_tokens=MAX_TOKENS, model=MODEL)
    if not raw or raw.startswith("[ai]"):
        return None
    reply = raw.strip().strip('"«»').strip()
    if not reply or reply.upper().startswith("SKIP") or len(reply) > 700:
        return None
    return reply


def process(message):
    """Фоновая работа: пауза 60–120 с, ответ модели, reply на комментарий."""
    username = getattr(message.from_user, "username", None) or str(message.from_user.id)
    reply = generate(message)
    if not reply:
        release(message)
        _log_reply({"user": username, "comment_id": message.message_id, "skipped": True})
        return
    _sleep(random.randint(*PAUSE))
    try:
        _bot.send_message(message.chat.id, reply, reply_to_message_id=message.message_id,
                          allow_sending_without_reply=True)
    except Exception as exc:
        log.error("comment reply send: %s", exc)
        release(message)
        return
    _log_reply({"user": username, "comment_id": message.message_id, "comment": _text(message)[:200], "reply": reply})


def on_group_message(message):
    if getattr(message, "is_automatic_forward", False):
        _posts[message.message_id] = _text(message)
        if len(_posts) > 200:
            _posts.pop(next(iter(_posts)))
        return
    if not enabled():
        return
    try:
        me_id = _bot.get_me().id
    except Exception as exc:
        log.error("comment_replies get_me: %s", exc)
        return
    ok, why = should_reply(message, me_id)
    if not ok:
        return
    ok, why = reserve(message)
    if not ok:
        log.info("comment reply skipped: %s", why)
        return
    threading.Thread(target=process, args=(message,), daemon=True).start()


def status_text():
    try:
        me = _bot.get_me()
        sees_all = getattr(me, "can_read_all_group_messages", None)
        member = _bot.get_chat_member(CHAT_ID, me.id)
        role = getattr(member, "status", "?")
    except Exception as exc:
        sees_all, role = None, f"ошибка: {exc}"
    with _lock:
        _roll_day()
        count = _state["count"]
    return (f"💬 Автоответы на комментарии: {'включены' if enabled() else 'выключены (COMMENT_REPLIES=1 включит)'}\n"
            f"Модель: {MODEL}\nСегодня ответов: {count} из {DAILY_CAP}\n"
            f"Статус бота в группе обсуждения: {role}\n"
            f"Видит все сообщения групп (privacy mode выключен): {sees_all}\n"
            f"Комментарии бот увидит, если он админ группы или privacy выключен.")


def register(bot, data_dir, admin_id):
    global _bot, _path, _log_path, _admin
    _bot, _admin = bot, admin_id
    os.makedirs(data_dir, exist_ok=True)
    _path = os.path.join(data_dir, "comment_replies_state.json")
    _log_path = os.path.join(data_dir, "comment_replies.log.jsonl")
    _load()
    _roll_day()
    content_types = util.content_type_media + ["text"]
    bot.message_handler(func=lambda m: m.chat.id == CHAT_ID, content_types=content_types)(on_group_message)
    # обработчик группы обсуждения — первым, чтобы команды и общий fallback бота не отвечали в группе
    bot.message_handlers.insert(0, bot.message_handlers.pop())

    @bot.message_handler(commands=["comments"], func=lambda m: m.chat.type == "private" and m.chat.id == _admin)
    def comments_status(message):
        bot.send_message(message.chat.id, status_text())
