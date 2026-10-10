"""Автоответы на комментарии канала: фильтры, лимиты, контекст поста, SKIP, пауза и reply. Запуск: python tests/test_comment_replies.py"""
import json
import os
import sys
import tempfile
import time
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TG_BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ.pop("COMMENT_REPLIES", None)

import bot_best as B  # noqa: E402
import comment_replies as C  # noqa: E402

CHAT = C.CHAT_ID
assert CHAT == -1003999109119
SENT, MODEL_CALLS = [], []
WAITS = []
C._sleep = lambda s: WAITS.append(s)
C._bot = pytypes.SimpleNamespace(
    get_me=lambda: pytypes.SimpleNamespace(id=999, can_read_all_group_messages=False),
    send_message=lambda *a, **k: SENT.append((a, k)))
B.bot.get_me = C._bot.get_me


def fake_call(system, prompt, max_tokens=1500, model=None, temperature=None):
    MODEL_CALLS.append({"system": system, "prompt": prompt, "max_tokens": max_tokens, "model": model})
    return "Хочется верить, что да. А у тебя бывает так же?"


C.ai_assistant._call = fake_call


def msg(text, mid, uid=10, username="anna", bot=False, auto=False, sender_chat=None, reply=None, thread=None,
        ctype="text", chat=CHAT):
    return pytypes.SimpleNamespace(
        message_id=mid, chat=pytypes.SimpleNamespace(id=chat, type="supergroup"), text=text, caption=None,
        content_type=ctype, is_automatic_forward=auto, sender_chat=sender_chat, reply_to_message=reply,
        message_thread_id=thread,
        from_user=pytypes.SimpleNamespace(id=uid, username=username, is_bot=bot))


def wait_threads():
    import threading
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(timeout=3)


# ── по умолчанию выключено ──
assert not C.enabled()
C.on_group_message(msg("Очень отозвалось", 1))
wait_threads()
assert not SENT and not MODEL_CALLS, "без COMMENT_REPLIES=1 бот молчит"

# ── фильтры без сети ──
ok = lambda m: C.should_reply(m, 999)[0]  # noqa: E731
assert ok(msg("Очень отозвалось, спасибо", 2))
assert not ok(msg("пост", 3, auto=True))
assert not ok(msg("от канала", 4, sender_chat=pytypes.SimpleNamespace(id=-100)))
assert not ok(msg("я бот", 5, bot=True))
assert not ok(msg("сам", 6, uid=999))
assert not ok(msg("/start", 7))
assert not ok(msg("заходи https://spam.ru", 8))
assert not ok(msg("пиши @spammer_bot", 9))
assert not ok(msg("🙂🙂", 10))
assert not ok(msg("", 11, ctype="photo"))
assert not ok(msg("а" * 1001, 12))

# ── порядок обработчиков: группа обсуждения — первая ──
first = next(h for h in B.bot.message_handlers if h["function"] is C.on_group_message)
assert B.bot.message_handlers.index(first) < 3, "обработчик группы — среди первых"
assert first["function"] is C.on_group_message
assert first["filters"]["func"](msg("x", 1)) and not first["filters"]["func"](msg("x", 1, chat=500))

# ── включено: контекст поста, модель, пауза, reply ──
os.environ["COMMENT_REPLIES"] = "1"
POST = "Сегодня я писала письмо тому, кого уже нет рядом."
C.on_group_message(msg(POST, 100, uid=-1, auto=True, sender_chat=pytypes.SimpleNamespace(id=-100)))
assert C._posts[100] == POST
C.on_group_message(msg("Очень отозвалось, у меня так же", 101, uid=10, thread=100))
wait_threads()
assert len(SENT) == 1
(args, kw) = SENT[0]
assert args[0] == CHAT and kw["reply_to_message_id"] == 101 and "А у тебя" in args[1]
call = MODEL_CALLS[-1]
assert call["model"] == "claude-haiku-4-5" and call["max_tokens"] == 120
assert POST in call["prompt"] and "Очень отозвалось" in call["prompt"]
assert call["system"].startswith("Ты — Алиса Невская, цифровая девушка из Петербурга")
assert "Не используй слова 'конечно'" in call["system"] and "SKIP" in call["system"]
assert WAITS and 60 <= WAITS[-1] <= 120, "пауза 60–120 с"

# ── 1 ответ на человека в 10 минут, другой человек — можно ──
C.on_group_message(msg("И ещё одно", 102, uid=10, thread=100))
wait_threads()
assert len(SENT) == 1, "тот же человек сразу — не отвечаем"
C.on_group_message(msg("Мне тоже грустно сегодня", 103, uid=11, username="olga", thread=100))
wait_threads()
assert len(SENT) == 2
C.on_group_message(msg("Мне тоже грустно сегодня", 103, uid=12, thread=100))
wait_threads()
assert len(SENT) == 2, "один комментарий — один ответ"

# ── SKIP и ошибка модели: не отвечаем и слот возвращается ──
n = C._state["count"]
C.ai_assistant._call = lambda *a, **k: "SKIP"
C.on_group_message(msg("грубость", 104, uid=13))
wait_threads()
assert len(SENT) == 2 and C._state["count"] == n, "SKIP не тратит лимит"
C.ai_assistant._call = lambda *a, **k: "[ai] Не получилось: down"
C.on_group_message(msg("привет", 105, uid=14))
wait_threads()
assert len(SENT) == 2

# ── суточный лимит ──
C.ai_assistant._call = fake_call
C._state["count"] = C.DAILY_CAP
C.on_group_message(msg("Ещё один тёплый комментарий", 106, uid=15))
wait_threads()
assert len(SENT) == 2, "после 200 ответов в сутки — тишина"
C._state["count"] = 0

# ── лог: только username, без id и лишнего ──
rows = [json.loads(x) for x in open(C._log_path, encoding="utf-8")]
replied = [r for r in rows if r.get("reply")]
assert len(replied) == 2 and replied[0]["user"] == "anna" and "uid" not in replied[0] and "id" not in replied[0]
assert any(r.get("skipped") for r in rows)

# ── состояние переживает перезапуск ──
saved = json.load(open(C._path, encoding="utf-8"))
assert 101 in saved["done"] and 103 in saved["done"]

# ── статус для админа ──
assert "Модель: claude-haiku-4-5" in C.status_text() or "claude-haiku-4-5" in C.status_text()
os.environ.pop("COMMENT_REPLIES")
print("OK: comment replies")
