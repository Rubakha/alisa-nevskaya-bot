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


# ── набор «3 письма», PDF «Разговор с папой», опрос покупок ─────────────
def shop_item(kind):
    return B.OCC.PACK if kind == "pack" else B.OCC.DOC


def regular_pack_price():
    return 3 * B.OCC.price_from()


def purchase_order(order_id, user):
    o = B.get_order(order_id) if A.SAFE_ID.match(order_id or "") else None
    if not o or o.get("chat_id") != user["id"] or o.get("product") not in (B.OCC.PACK["key"], B.OCC.DOC["key"]):
        raise A.err("not_found", "Покупка не найдена.", 404)
    return o


def kind_of(order):
    return "pack" if order.get("product") == B.OCC.PACK["key"] else "doc"


def doc_path():
    return os.path.join(os.path.dirname(B.FREE_PDF_PATH), B.OCC.DOC["file"])


def create_purchase(user, kind, data):
    """Заказ набора или PDF так же, как в боте (occ_pack / occ_doc_order): один неоплаченный на человека."""
    item = shop_item(kind)
    pending = B.pending_orders(user["id"])
    if pending and pending[0].get("product") != item["key"]:
        raise A.err("pending_exists", "У тебя есть неоплаченный заказ — оплати или отмени его.", 409,
                    orderId=pending[0]["order_id"])
    promo = str(data.get("promo") or "").strip()[:40] or None
    if promo:
        ok, res = B.partners.apply_promo_code(user["id"], promo)
        if not ok:
            raise A.err("bad_promo", res, 400)
    if pending:   # повторное «Оплатить»: тот же заказ (платёж ЮKassa не плодим)
        return pending[0]
    profile = B.upsert_client(A.tg_user(user))
    order = {"order_id": B.new_order_id(), "chat_id": user["id"], "name": profile["name"],
             "username": profile.get("username", ""), "pain": item["key"], "product": item["key"], "answers": [],
             "letter_text": "", "price_rub": item["price"], "status": "pending", "is_gift": False,
             "created_at": B.now_msk().isoformat(), "paid_at": None, "delivered_at": None, "email": None,
             "rating": None}
    B.partners.apply_to_order(order, user["id"], promo)
    B.save_order(order)
    return order


def purchase_state(order):
    kind = kind_of(order)
    done = order.get("status") == "done"
    out = {"orderId": order["order_id"], "kind": kind, "paid": done, "status": "delivered" if done else "awaiting_payment",
           "price": (order.get("test_price_rub") if order.get("is_test") else order.get("price_rub")),
           "test": bool(order.get("is_test"))}
    if done and kind == "doc":
        out["downloadUrl"] = A.signed(order["order_id"], f"/doc/{order['order_id']}/file.pdf")
    if done and kind == "pack":
        out["credits"] = (B.get_client(order["chat_id"]) or {}).get("credits", 0)
    return out


def routes_purchases():
    @A.endpoint("/pack", auth=False)
    def pack_info():
        user = A.current_user(required=False)
        P = B.OCC.PACK
        regular = regular_pack_price()
        out = {"key": P["key"], "title": P["title"], "price": P["price"], "credits": P["credits"],
               "regularPrice": regular, "savings": regular - P["price"]}
        if user:
            out["myCredits"] = (B.get_client(user["id"]) or {}).get("credits", 0)
        return out

    @A.endpoint("/pack/pay", methods=("POST",))
    def pack_pay(user):
        data = A.body()
        order = create_purchase(user, "pack", data)
        return A.checkout(user, order, data)

    @A.endpoint("/doc", auth=False)
    def doc_info():
        D = B.OCC.DOC
        return {"key": D["key"], "title": D["title"], "price": D["price"], "pages": 10,
                "description": "30 фраз для пяти ситуаций (давно не говорили, после ссоры, строгий отец, расстояние, "
                               "его нет рядом), три начала письма и правила разговора. Приходит сразу после оплаты."}

    @A.endpoint("/doc/pay", methods=("POST",))
    def doc_pay(user):
        data = A.body()
        order = create_purchase(user, "doc", data)
        return A.checkout(user, order, data)

    @A.endpoint("/doc/<order_id>")
    def doc_state(order_id, user):
        order = purchase_order(order_id, user)
        if kind_of(order) != "doc":
            raise A.err("not_found", "Покупка не найдена.", 404)
        return purchase_state(order)

    @A.endpoint("/doc/<order_id>/file.pdf", auth="sig")
    def doc_file(order_id, user):
        order = B.get_order(order_id) if A.SAFE_ID.match(order_id or "") else None
        if (not order or order.get("product") != B.OCC.DOC["key"] or order.get("status") != "done"
                or (user and order.get("chat_id") != user["id"]) or not os.path.exists(doc_path())):
            raise A.err("not_found", "Файл станет доступен после оплаты.", 404)
        with open(doc_path(), "rb") as f:
            data = f.read()
        return A.Response(data, 200, mimetype="application/pdf",
                          headers={"Content-Disposition": 'attachment; filename="razgovor_s_papoy.pdf"',
                                   "Cache-Control": "private, max-age=300"})

    @A.endpoint("/purchases/<order_id>/check", methods=("POST",))
    def purchase_check(order_id, user):
        order = purchase_order(order_id, user)
        if order.get("status") != "done":
            try:
                B.yk_check_order(order_id)
            except Exception as exc:
                B.log.error("miniapp purchase check %s: %s", order_id, exc)
            order = B.get_order(order_id)
        return purchase_state(order)

    @A.endpoint("/purchases/<order_id>/cancel", methods=("POST",))
    def purchase_cancel(order_id, user):
        order = purchase_order(order_id, user)
        if order.get("status") == "done":
            raise A.err("paid", "Оплаченную покупку отменить нельзя.", 409)
        order["status"] = "cancelled"
        B.save_order(order)
        return {"ok": True}

    @A.endpoint("/me/purchases")
    def my_purchases(user):
        items = []
        for o in sorted(B.client_orders(user["id"]), key=lambda x: x.get("created_at", ""), reverse=True):
            if o.get("product") in (B.OCC.PACK["key"], B.OCC.DOC["key"]) and o.get("status") in ("pending", "done") \
                    and not o.get("hidden_by_client"):
                st = purchase_state(o)
                items.append({"orderId": st["orderId"], "kind": st["kind"], "title": shop_item(st["kind"])["title"],
                              "price": st["price"], "status": st["status"], "createdAt": o.get("created_at"),
                              "test": st["test"]})
        return {"items": items}
