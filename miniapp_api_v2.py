"""API v2 для Mini App: поддержка, подписки, набор «3 письма», PDF, голосовое письмо, письмо группой.

Контракт: docs/MINIAPP_API.md, раздел 7. Те же авторизация, лимиты и формат ошибок, что у v1 (miniapp_api.py): ручки
регистрируются теми же декораторами. Бизнес-логика — функции бота (fulfill_order, partners, voice_flow, group_letters,
calendar_reminders, support_tickets). Включается флагом MINIAPP_API_V2 (по умолчанию 1 при MINIAPP_API=1); 0 — ручек нет.
"""
import os
import threading

import support_tickets
from flask import request

A = None   # miniapp_api
B = None   # bot_best


def enabled():
    return os.getenv("MINIAPP_API_V2", "1").strip().lower() not in ("0", "false", "no", "off")


def register(api_module, bot_module):
    """Вызывается из miniapp_api.register после v1-ручек."""
    global A, B
    A, B = api_module, bot_module
    if not enabled():
        return
    support_tickets.init(B)
    routes_support()
    routes_subscriptions()
    for name in ("routes_purchases", "routes_voice", "routes_groups"):   # блоки подключаются по мере готовности
        fn = globals().get(name)
        if fn:
            fn()
    B.log.info("miniapp api v2: включён")


# ── поддержка ───────────────────────────────────────────────────
SUPPORT_PER_HOUR = 3
SUPPORT_PER_DAY = 10


def notify_admin_support(user, ticket, text):
    """Сообщение админу в том же формате, что sup:new, плюс черновик ответа ИИ (как в боте), если он включён."""
    if not B.ADMIN_ID:
        return
    chat_id = user["id"]
    profile = B.get_client(chat_id) or {}
    try:
        B.bot.send_message(
            B.ADMIN_ID,
            "❓ <b>Обращение в поддержку</b> (приложение)" + chr(10) + chr(10)
            + f"Тема: {B.esc(support_tickets.TOPICS.get(ticket['topic'], 'Другое'))}" + chr(10)
            + f"От: {B.esc(profile.get('name', chat_id))} (@{profile.get('username') or '—'})" + chr(10)
            + f"chat_id: <code>{chat_id}</code>" + chr(10) + chr(10)
            + f"{B.esc(text)}" + chr(10) + chr(10)
            + f"Ответить: <code>/reply {chat_id} текст</code>",
            parse_mode="HTML")
    except Exception as exc:
        B.log.error("support notify failed: %s", exc)

    def draft():
        if not (B.AI and B.AI.available()):
            return
        try:
            orders = B.client_orders(chat_id)
            ctx = (f"клиент, заказов: {len(orders)}, "
                   f"писем получено: {len([o for o in orders if o.get('status') == 'done'])}")
            reply = B.AI.draft_support(text, ctx)
            B.bot.send_message(
                B.ADMIN_ID,
                f"🤖 Черновик ответа (не отправлен):{chr(10)}{'─' * 22}{chr(10)}{reply}{chr(10)}{'─' * 22}{chr(10)}"
                f"Отправить: <code>/reply {chat_id} текст</code>", parse_mode="HTML")
        except Exception as exc:
            B.log.error("support draft failed: %s", exc)
    threading.Thread(target=draft, daemon=True).start()


def routes_support():
    @A.endpoint("/support", methods=("POST",))
    def support_send(user):
        data = A.body()
        topic = str(data.get("topic") or "other").strip().lower()
        topic = topic if topic in support_tickets.TOPICS else "other"
        text = str(data.get("text") or "").strip()
        if not 5 <= len(text) <= 1000:
            raise A.err("bad_text", "Опиши вопрос — от 5 до 1000 знаков.", 400)
        B.upsert_client(A.tg_user(user))
        for window, limit in ((3600, SUPPORT_PER_HOUR), (86400, SUPPORT_PER_DAY)):
            if support_tickets.count_since(user["id"], window) >= limit:
                raise A.err("rate_limited", "Ты уже написала несколько обращений — подожди, я отвечу на них.", 429,
                            retryAfter=window // 4)
        ticket = support_tickets.add(user["id"], topic, text)
        notify_admin_support(user, ticket, text)
        return {"ticketId": ticket["ticketId"], "status": "sent"}

    @A.endpoint("/me/support")
    def support_list(user):
        return {"items": support_tickets.public(support_tickets.tickets(user["id"]))}


# ── подписки и рассылка ─────────────────────────────────────────
def subscriptions_state(user):
    CR = B.calendar_reminders
    profile, cal = CR.get_cal(user["id"])
    if profile is None:
        B.upsert_client(A.tg_user(user))
        profile, cal = CR.get_cal(user["id"])
    today = B.now_msk().date()
    holidays = []
    for key, h in CR.HOLIDAYS.items():
        when = None
        for year in (today.year, today.year + 1):
            when = h["date"](year)
            if when >= today:
                break
        holidays.append({"key": key, "icon": h["icon"], "title": h["title"], "next": when.isoformat() if when else None,
                         "subscribed": key in cal["subs"]})
    return {"holidays": holidays, "mailing": not profile.get("mailing_off"), "reminderDates": len(cal["dates"])}


def routes_subscriptions():
    @A.endpoint("/me/subscriptions")
    def subscriptions_get(user):
        return subscriptions_state(user)

    @A.endpoint("/me/subscriptions", methods=("PUT",))
    def subscriptions_put(user):
        CR = B.calendar_reminders
        data = A.body()
        profile, cal = CR.get_cal(user["id"])
        if profile is None:
            B.upsert_client(A.tg_user(user))
            profile, cal = CR.get_cal(user["id"])
        if "holidays" in data:
            if not isinstance(data["holidays"], list):
                raise A.err("bad_request", "holidays — список ключей праздников.", 400)
            wanted = [k for k in dict.fromkeys(data["holidays"]) if isinstance(k, str) and k in CR.HOLIDAYS]
            for k in wanted:
                if k not in cal["subs"]:
                    CR.log_event(user["id"], "sub", what=k)
            for k in cal["subs"]:
                if k not in wanted:
                    CR.log_event(user["id"], "unsub", what=k)
            cal["subs"] = wanted
        if "mailing" in data:
            if not isinstance(data["mailing"], bool):
                raise A.err("bad_request", "mailing — true или false.", 400)
            if data["mailing"]:
                profile.pop("mailing_off", None)
            else:
                profile["mailing_off"] = True
        CR._save(user["id"], profile)
        return subscriptions_state(user)
