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

    @A.endpoint("/momset", auth=False)
    def momset_info():
        """Набор «Для мамы»: состав, цена и честная выгода. MOM_SET выключен: enabled=false, сайт блок не показывает."""
        return B.PDFP.mom_info()

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


# ── голосовое письмо (+100 ₽, ElevenLabs) ───────────────────────────
def voice_genders():
    VL = A.VL()
    return [{"key": g, "label": "Женский голос" if g == "f" else "Мужской голос"} for g in VL.genders()]


def pick_gender(requested):
    VL = A.VL()
    genders = VL.genders()
    if requested in genders:
        return requested
    if not requested and len(genders) == 1:
        return genders[0]
    raise A.err("bad_gender", "Выбери голос: женский или мужской.", 400)


def voice_response(order):
    """Полное состояние озвучки письма для GET /orders/<id>/voice."""
    VL = A.VL()
    info = A.voice_info(order)
    oid = order["order_id"]
    out = {"available": VL.enabled() and A.voice_cap_left(order["chat_id"]) > 0, "status": info["status"],
           "gender": info["gender"], "price": VL.upsell_price(), "chars": info["chars"], "audioUrl": None,
           "oggUrl": None, "error": None, "updatedAt": info.get("updatedAt")}
    if info["status"] == "ready":
        if os.path.exists(A.voice_file(oid, "mp3")):
            out["audioUrl"] = A.signed(oid, f"/orders/{oid}/voice.mp3")
        if os.path.exists(A.voice_file(oid, "ogg")):
            out["oggUrl"] = A.signed(oid, f"/orders/{oid}/voice.ogg")
    if info["status"] == "failed":
        out["error"] = ("Голос пока не записался. Письмо и открытка на месте — пришлём голосовое вручную "
                        "в ближайшее время.")
    return out


def routes_voice():
    @A.endpoint("/voice/options", auth=False)
    def voice_options():
        VL = A.VL()
        user = A.current_user(required=False)
        ok = VL.enabled() and VL.upsell_price() > 0 and A.voice_cap_left(user["id"] if user else None) > 0
        return {"enabled": bool(ok), "price": VL.upsell_price(), "genders": voice_genders() if ok else [],
                "music": True, "maxLetterChars": VL.MAX_TOTAL}

    @A.endpoint("/orders/<order_id>/voice", methods=("PUT",))
    def voice_toggle(order_id, user):
        """До оплаты: включить или убрать озвучку (+100 ₽ к сумме)."""
        d = A.own_draft(order_id, user)
        if d.get("synthetic") or d.get("group") or d.get("bundle"):
            raise A.err("voice_not_allowed", "Для этого письма озвучку до оплаты добавить нельзя.", 409)
        order = A.real_order(d)
        if order and order.get("status") == "done":
            raise A.err("paid", "Письмо уже оплачено — озвучку можно добавить отдельной доплатой.", 409)
        if not d.get("letter"):
            raise A.err("no_letter", "Сначала получи письмо.", 409)
        if order and order.get("status") == "pending" and order.get("yk_payment_id"):
            raise A.err("locked", "Оплата уже начата. Заверши её или отмени заказ.", 409)
        data = A.body()
        if data.get("enabled") is False:
            d.pop("voice", None)
            d.pop("voice_gender", None)
        elif data.get("enabled") is True:
            A.check_voice_allowed(user["id"], d["letter"])
            d["voice"], d["voice_gender"] = True, pick_gender(data.get("gender"))
        else:
            raise A.err("bad_request", "enabled — true или false.", 400)
        A.save_draft(d)
        q = A.quote(d)
        return {"ok": True, "price": q["payable"], "voice": A.voice_summary(d, None)}

    @A.endpoint("/orders/<order_id>/voice/pay", methods=("POST",))
    def voice_pay(order_id, user):
        """После оплаты письма: доплата за озвучку (как «Озвучить голосом» в боте)."""
        VL = A.VL()
        d = A.own_draft(order_id, user)
        order = A.real_order(d)
        if not order or order.get("status") != "done":
            raise A.err("not_paid", "Озвучить можно оплаченное письмо.", 403)
        info = A.voice_info(order)
        if info["status"] in ("queued", "working", "ready"):
            raise A.err("already_voiced" if info["status"] == "ready" else "voice_in_progress",
                        "Это письмо уже озвучено." if info["status"] == "ready" else "Голос уже записывается.", 409)
        data = A.body()
        A.check_voice_allowed(user["id"], order.get("letter_text"))
        gender = pick_gender(data.get("gender"))
        addon = A.pending_voice_order(order)
        if addon is None:
            others = [o for o in B.pending_orders(user["id"])]
            if others:
                raise A.err("pending_exists", "У тебя есть неоплаченный заказ — оплати или отмени его.", 409,
                            orderId=others[0]["order_id"])
            profile = B.upsert_client(A.tg_user(user))
            addon = {"order_id": B.new_order_id(), "chat_id": user["id"], "name": profile["name"],
                     "username": profile.get("username", ""), "pain": order.get("product"), "product": VL.ADDON_KEY,
                     "answers": [], "letter_text": order.get("letter_text", ""), "gift_for": order.get("gift_for"),
                     "is_gift": order.get("is_gift", False), "voice": True, "voice_gender": gender,
                     "voice_for": order["order_id"], "price_rub": VL.upsell_price(), "status": "pending",
                     "created_at": B.now_msk().isoformat(), "paid_at": None, "delivered_at": None, "email": None,
                     "rating": None}
            B.save_order(addon)
        elif addon.get("voice_gender") != gender:
            addon["voice_gender"] = gender
            B.save_order(addon)
        res = A.checkout(user, addon, data)
        res["voiceOrderId"] = res.pop("orderId", addon["order_id"])
        if res.get("paid"):
            res["status"] = "queued"
        return res

    @A.endpoint("/orders/<order_id>/voice/check", methods=("POST",))
    def voice_check(order_id, user):
        d = A.own_draft(order_id, user)
        order = A.real_order(d)
        if not order or order.get("status") != "done":
            raise A.err("not_paid", "Озвучить можно оплаченное письмо.", 403)
        addon = A.pending_voice_order(order)
        if addon:
            try:
                B.yk_check_order(addon["order_id"])
            except Exception as exc:
                B.log.error("miniapp voice check %s: %s", order_id, exc)
            order = B.get_order(order_id)
        info = A.voice_info(order)
        return {"paid": info["status"] not in ("none", "awaiting_payment"), "status": info["status"]}

    @A.endpoint("/orders/<order_id>/voice")
    def voice_state(order_id, user):
        d = A.own_draft(order_id, user)
        order = A.real_order(d)
        if order and order.get("status") == "done":
            return voice_response(order)
        VL = A.VL()
        return {"available": VL.enabled(), "status": "none", "gender": d.get("voice_gender"), "price": VL.upsell_price(),
                "chars": None, "audioUrl": None, "oggUrl": None, "error": None,
                "selected": bool(d.get("voice"))}

    def audio(order_id, user, ext, mimetype):
        d = A.find_draft(order_id)
        order = A.real_order(d) if d else None
        path = A.voice_file(order_id, ext)
        if (not order or order.get("status") != "done" or (user and d.get("chat_id") != user["id"])
                or A.voice_info(order)["status"] != "ready" or not os.path.exists(path)):
            raise A.err("not_found", "Голос ещё не готов.", 404)
        with open(path, "rb") as f:
            data = f.read()
        return A.Response(data, 200, mimetype=mimetype, headers={"Cache-Control": "private, max-age=300",
                          "Content-Disposition": f'inline; filename="Pismo_ot_Alisy.{ext}"'})

    @A.endpoint("/orders/<order_id>/voice.mp3", auth="sig")
    def voice_mp3(order_id, user):
        return audio(order_id, user, "mp3", "audio/mpeg")

    @A.endpoint("/orders/<order_id>/voice.ogg", auth="sig")
    def voice_ogg(order_id, user):
        return audio(order_id, user, "ogg", "audio/ogg")


# ── письмо группой (399 ₽) ─────────────────────────────────────────
MAX_OPEN_GROUPS = 5
GROUP_FIELD_MAX = 60


def GL():
    return B.group_letters


def invite_url(code):
    short = os.getenv("MINIAPP_SHORTNAME", "app")
    return f"https://t.me/{B.BOT_USERNAME}/{short}?startapp=grp_{code}"


def share_text(g):
    name = g["name"]
    return (f"💌 Собираем общее письмо для {name} ({g['who']}) — каждому нужно написать пару тёплых слов. "
            f"Два коротких ответа по ссылке, бесплатно. Только пока не показывай это {name} — это сюрприз 🤍")


def share_url(g):
    import urllib.parse
    return ("https://t.me/share/url?url=" + urllib.parse.quote(invite_url(g["code"]), safe="")
            + "&text=" + urllib.parse.quote(share_text(g), safe=""))


def group_or_404(code):
    g = GL().get_group(code or "")
    if not g or g.get("deleted"):
        raise A.err("not_found", "Эта ссылка не работает — попроси прислать новую.", 404)
    return g


def organizer_group(code, user):
    g = group_or_404(code)
    if g["organizer"] != user["id"]:
        raise A.err("forbidden", "Это письмо собирает другой человек.", 403)
    return g


def group_status(g):
    if g.get("status") == "ordered":
        return "ordered"
    return "built" if g.get("built") and A.load_draft(g.get("api_order_id") or "") else "open"


def questions_for(g):
    return [{"key": k, "text": t} for k, t in GL().questions(g)]


def group_view(g, user):
    mine = next((p for p in g["participants"] if p["chat_id"] == user["id"]), None)
    is_org = g["organizer"] == user["id"]
    open_ = g["status"] == "open"
    out = {"code": g["code"], "name": g["name"], "who": g["who"], "occasion": g["occasion"],
           "organizerName": g["organizer_name"], "status": group_status(g), "isOrganizer": is_org,
           "alreadyAnswered": mine is not None, "full": len(g["participants"]) >= GL().MAX_PARTICIPANTS and mine is None,
           "questions": questions_for(g) if open_ else []}
    if mine:
        out["mine"] = {"sign": mine["sign"]}
    if is_org:
        count = len(g["participants"])
        out.update(participants=[{"sign": p["sign"], "isMe": p["chat_id"] == user["id"], "at": p.get("at")}
                                 for p in g["participants"]],
                   count=count, maxParticipants=GL().MAX_PARTICIPANTS, canBuild=open_ and count >= GL().MIN_TO_BUILD,
                   shareText=share_text(g), shareUrl=share_url(g), inviteUrl=invite_url(g["code"]),
                   price=GL().GROUP_PRICE, orderId=g.get("api_order_id"), opened=len(g.get("opened", [])))
    return out


def own_groups(chat_id):
    out = []
    d = GL()._dir()
    for f in os.listdir(d):
        if f.endswith(".json"):
            g = B.read_json(os.path.join(d, f), None)
            if g and g.get("organizer") == chat_id and not g.get("deleted"):
                out.append(g)
    return sorted(out, key=lambda g: g.get("created_at", ""), reverse=True)


def group_draft(g, user, res):
    """Черновик заказа из собранного группового письма (как состояние occ_paywall в bot: build_cb)."""
    p = B.OCC.PRODUCTS[g["occasion"]]
    ref = B.cardbase_pick(user["id"], g["occasion"], [])
    oid = B.new_order_id()
    return {"order_id": oid, "api_order_id": oid, "chat_id": user["id"],
            "user": {k: user.get(k, "") for k in ("first_name", "last_name", "username")},
            "product": g["occasion"], "qa": [], "data": {}, "tone": p.get("tone") or "tender", "sign": g["sign"],
            "name": g["name"], "default_title": p["card_title"].format(name=g["name"])[:40],
            "letter": res["letter"], "card_title": res["card_title"], "card_line": res["card_line"],
            "card_ref": ref, "card_seen": [ref] if ref else [], "retexts": 0, "wish_used": False, "promo": None,
            "source": None, "group": g["code"], "price": GL().GROUP_PRICE, "group_count": len(g["participants"]),
            "created_at": B.now_msk().isoformat()}


def routes_groups():
    @A.endpoint("/groups/options", auth=False)
    def groups_options():
        G = GL()
        occasions = [{"key": k, "icon": B.OCC.PRODUCTS[k]["icon"], "title": B.OCC.PRODUCTS[k]["title"]}
                     for k in G.GROUP_OCCASIONS if k in B.OCC.PRODUCTS]
        return {"price": G.GROUP_PRICE, "maxParticipants": G.MAX_PARTICIPANTS, "minToBuild": G.MIN_TO_BUILD,
                "occasions": occasions,
                "questions": [{"key": k, "text": t} for k, t in G.questions({"name": "{name}"})]}

    @A.endpoint("/groups", methods=("POST",))
    def groups_create(user):
        data = A.body()
        occasion = data.get("occasion")
        if occasion not in GL().GROUP_OCCASIONS or occasion not in B.OCC.PRODUCTS:
            raise A.err("bad_occasion", "Выбери повод из списка.", 400)
        fields = {}
        for key, label in (("name", "имя получателя"), ("who", "кто он(а) для вас"), ("sign", "подпись от всех")):
            val = str(data.get(key) or "").strip()
            if not 1 <= len(val) <= GROUP_FIELD_MAX:
                raise A.err("bad_fields", f"Укажи {label} — до {GROUP_FIELD_MAX} знаков.", 400, field=key)
            fields[key] = val
        if len([g for g in own_groups(user["id"]) if g["status"] == "open"]) >= MAX_OPEN_GROUPS:
            raise A.err("too_many_groups", "Открытых писем уже пять — закончи или удали одно из них.", 429)
        profile = B.upsert_client(A.tg_user(user))
        import secrets
        code = secrets.token_urlsafe(5).replace("-", "x").replace("_", "y")
        g = {"code": code, "kind": "family", "organizer": user["id"], "organizer_name": profile["name"],
             "occasion": occasion, "name": fields["name"], "who": fields["who"], "sign": fields["sign"],
             "created_at": B.now_msk().isoformat(), "status": "open", "participants": [], "opened": [],
             "built": False, "order_id": None, "is_test": B.is_test_user(user["id"])}
        GL().save_group(g)
        return {"code": code, "status": "open", "inviteUrl": invite_url(code),
                "botUrl": GL().invite_link(code), "shareText": share_text(g), "shareUrl": share_url(g)}

    @A.endpoint("/groups")
    def groups_list(user):
        return {"items": [{"code": g["code"], "name": g["name"], "who": g["who"], "occasion": g["occasion"],
                           "status": group_status(g), "participants": len(g["participants"]),
                           "createdAt": g.get("created_at"), "orderId": g.get("api_order_id")}
                          for g in own_groups(user["id"])]}

    @A.endpoint("/groups/<code>")
    def groups_get(code, user):
        g = group_or_404(code)
        if user["id"] not in g["opened"] and g["organizer"] != user["id"]:
            g["opened"].append(user["id"])
            GL().save_group(g)
        return group_view(g, user)

    @A.endpoint("/groups/<code>/answers", methods=("POST",))
    def groups_answer(code, user):
        g = group_or_404(code)
        if g["status"] != "open":
            raise A.err("group_closed", "Письмо уже собрано и отправлено — спасибо, что хотели присоединиться 🤍", 409)
        mine = any(p["chat_id"] == user["id"] for p in g["participants"])
        if len(g["participants"]) >= GL().MAX_PARTICIPANTS and not mine:
            raise A.err("group_full", "Все места в этом письме уже заняты 🤍", 409)
        data = A.body()
        out = {}
        for key in ("memory", "thanks"):
            val = str(data.get(key) or "").strip()
            if not 10 <= len(val) <= 800:
                raise A.err("bad_answers", "Ответ — от 10 до 800 знаков: напиши пару слов от себя 🙂", 400, field=key)
            out[key] = val
        sign = str(data.get("sign") or "").strip()
        if not 1 <= len(sign) <= 40:
            raise A.err("bad_answers", "Подпись — коротко, до 40 знаков.", 400, field="sign")
        B.upsert_client(A.tg_user(user))
        part = {"chat_id": user["id"], "sign": sign, "memory": out["memory"], "thanks": out["thanks"],
                "at": B.now_msk().isoformat()}
        g["participants"] = [p for p in g["participants"] if p["chat_id"] != user["id"]] + [part]
        if user["id"] not in g["opened"]:
            g["opened"].append(user["id"])
        GL().save_group(g)
        n = len(g["participants"])
        if g["organizer"] != user["id"]:
            try:
                B.bot.send_message(g["organizer"], f"✍️ {B.esc(sign)} написал(а) свою часть. Уже: {n}.", parse_mode="HTML")
            except Exception as exc:
                B.log.error("group notify organizer: %s", exc)
        return {"ok": True, "count": n}

    @A.endpoint("/groups/<code>/build", methods=("POST",))
    def groups_build(code, user):
        g = organizer_group(code, user)
        data = A.body()
        if g["status"] != "open":
            raise A.err("already_built", "Письмо уже оплачено.", 409, orderId=g.get("api_order_id"))
        if len(g["participants"]) < GL().MIN_TO_BUILD:
            raise A.err("not_enough", f"Нужно хотя бы {GL().MIN_TO_BUILD} части — добавь свою и позови близких.", 412)
        d = A.load_draft(g.get("api_order_id") or "")
        if d and d.get("letter") and not data.get("rebuild"):
            out = A.present(d)
            out["outdated"] = len(g["participants"]) != d.get("group_count")
            return out
        A.gen_guard(user["id"])
        res = A.generate_letter({"group": code, "product": g["occasion"]})
        if d and d.get("letter"):   # пересборка с новыми участниками в тот же заказ
            d.update(letter=res["letter"], card_title=res["card_title"], card_line=res["card_line"], retexts=0,
                     wish_used=False, group_count=len(g["participants"]))
        else:
            d = group_draft(g, user, res)
        A.save_draft(d)
        g["built"] = True
        g["api_order_id"] = d["order_id"]
        GL().save_group(g)
        B.cb_event(user["id"], "preview", d["product"], d.get("card_ref"))
        out = A.present(d)
        out["outdated"] = False
        return out

    @A.endpoint("/groups/<code>/remind", methods=("POST",))
    def groups_remind(code, user):
        g = organizer_group(code, user)
        if g["status"] != "open" or not g["participants"]:
            raise A.err("nothing_to_remind", "Напоминать пока некому.", 409)
        import urllib.parse
        text = (f"🤍 Напоминаю про общее письмо для {g['name']} — пока не хватает твоей части. "
                "Два коротких ответа, это бесплатно:")
        return {"text": text, "inviteUrl": invite_url(code),
                "shareUrl": "https://t.me/share/url?url=" + urllib.parse.quote(invite_url(code), safe="")
                + "&text=" + urllib.parse.quote(text, safe="")}

    @A.endpoint("/groups/<code>", methods=("DELETE",))
    def groups_delete(code, user):
        g = organizer_group(code, user)
        if g["status"] == "ordered":
            raise A.err("group_ordered", "Письмо уже оформлено — удалить нельзя.", 409)
        if g.get("api_order_id"):
            try:
                os.remove(A.draft_path(g["api_order_id"]))
            except OSError:
                pass
        g["deleted"] = True
        GL().save_group(g)
        return {"ok": True}
