"""«Голосовое письмо» в боте: пункт каталога, апселл после обычного письма, выдача голосом.

Подключается из bot_best: voice_flow.register(bot_best) — до fallback-обработчика.
Все точки входа молчат, пока voice_letter.enabled() == False (нет ключа ElevenLabs / id голоса).
"""
import io
import logging
import threading
import types as pytypes

from telebot import types

import voice_letter as VL

log = logging.getLogger("alisa.voice")

B = None
ASYNC = True  # озвучка идёт в потоке: вебхук Telegram не должен ждать ElevenLabs и ffmpeg (тесты ставят False)

GENDER_LABEL = {"f": "👩 Женский голос", "m": "👨 Мужской голос"}
INTRO = (
    "🎙 <b>Голосовое письмо</b> — {price}₽\n\n"
    "Письмо, которое я прочитаю живым голосом, с тихой музыкой на фоне. "
    "Это уже не текст, а аудио: его хочется включить и переслать тому, кому оно написано 🤍\n\n"
    "<b>Что внутри:</b>\n"
    "✍️ письмо по твоим деталям — не шаблон\n"
    "🖼 открытка с именем\n"
    "🎧 голосовое сообщение + mp3 в подарок\n\n"
    "Выбери, для кого пишем 🌿"
)


# ── каталог ──────────────────────────────────────────────────

def catalog_button(kb):
    """Пункт «Голосовое письмо» в каталоге — только если озвучка настроена."""
    if VL.enabled():
        kb.add(types.InlineKeyboardButton(f"🎙 Голосовое письмо · {VL.price()}₽", callback_data="vo:menu"))


def _occasion_markup():
    kb = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(f"{B.OCC.PRODUCTS[k]['icon']} {B.OCC.PRODUCTS[k]['title']}",
                                       callback_data=f"vo:go:{k}")
            for k in B.OCC.CATALOG_ORDER]
    for i in range(0, len(btns), 2):
        kb.row(*btns[i:i + 2])
    kb.add(types.InlineKeyboardButton("↩️ Все письма", callback_data="occ:catalog"))
    return kb


def _gender_markup(prefix):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[types.InlineKeyboardButton(GENDER_LABEL[g], callback_data=f"{prefix}:{g}") for g in VL.genders()])
    return kb


def _menu(call):
    B.bot.answer_callback_query(call.id)
    if not VL.enabled():
        return
    B.safe_edit(call, INTRO.format(price=VL.price()), _occasion_markup())


def _go(call):
    """vo:go:<повод>[:<голос>] — запускает обычную анкету письма с флагом озвучки и ценой 390."""
    chat_id = call.message.chat.id
    parts = call.data.split(":")
    key, gender = parts[2], (parts[3] if len(parts) > 3 else None)
    if not VL.enabled() or key not in B.OCC.PRODUCTS:
        B.bot.answer_callback_query(call.id)
        return
    if gender not in VL.genders():
        gs = VL.genders()
        if len(gs) == 1:
            gender = gs[0]
        else:
            B.bot.answer_callback_query(call.id)
            B.bot.send_message(chat_id, "Каким голосом прочитать письмо? 🎙",
                               reply_markup=_gender_markup(f"vo:go:{key}"))
            return
    fake = pytypes.SimpleNamespace(id=call.id, data=f"occ:go:{key}", from_user=call.from_user, message=call.message)
    B.occ_go(fake)  # проверка неоплаченного заказа, анкета, ответ на callback — как у обычного письма
    state = B.STATES.get(chat_id)
    if state and state.get("step") == "occ_q" and state.get("product") == key:
        state.update(voice=True, voice_gender=gender, price=VL.price())


# ── апселл после обычного письма ─────────────────────────────

def _upsell_price(order):
    """Доплата = цена «Голосового письма» минус прайсовая цена этого письма (скидки не учитываем)."""
    if order.get("voice") or order.get("voice_addon_order") or order.get("status") != "done":
        return 0
    prod = B.OCC.PRODUCTS.get(order.get("product"))
    return VL.surcharge(prod["price"]) if prod else 0


def add_upsell(kb, order):
    """Кнопка «Озвучить голосом — +N ₽» в сообщение «Как подарить»."""
    if not VL.enabled():
        return
    extra = _upsell_price(order)
    if extra > 0:
        kb.add(types.InlineKeyboardButton(f"🎙 Озвучить голосом — +{extra}₽", callback_data=f"vo:up:{order['order_id']}"))


def _upsell(call):
    chat_id = call.message.chat.id
    parts = call.data.split(":")
    order = B.get_order(parts[2])
    B.bot.answer_callback_query(call.id)
    if not VL.enabled() or not order or order.get("chat_id") != chat_id:
        return
    extra = _upsell_price(order)
    if extra <= 0:
        B.bot.send_message(chat_id, "Это письмо уже озвучено или озвучка недоступна 🤍")
        return
    gender = parts[3] if len(parts) > 3 else None
    if gender not in VL.genders():
        gs = VL.genders()
        if len(gs) == 1:
            gender = gs[0]
        else:
            B.bot.send_message(chat_id, "Каким голосом прочитать письмо? 🎙",
                               reply_markup=_gender_markup(f"vo:up:{order['order_id']}"))
            return
    if B.pending_count(chat_id) >= B.MAX_PENDING:
        pending = B.pending_orders(chat_id)[0]
        B.bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченный заказ.",
                           reply_markup=B.pending_markup(pending["order_id"]))
        return
    profile = B.upsert_client(call.from_user)
    addon = {
        "order_id": B.new_order_id(), "chat_id": chat_id, "name": profile["name"],
        "username": profile.get("username", ""), "pain": order.get("product"), "product": VL.ADDON_KEY,
        "answers": [], "letter_text": order.get("letter_text", ""), "gift_for": order.get("gift_for"),
        "is_gift": order.get("is_gift", False), "voice": True, "voice_gender": gender,
        "voice_for": order["order_id"], "price_rub": extra, "status": "pending",
        "created_at": B.now_msk().isoformat(), "paid_at": None, "delivered_at": None,
        "email": None, "rating": None,
    }
    B.save_order(addon)
    B.bot.send_message(
        chat_id,
        f"🎙 Прочитаю это письмо голосом — с тихой музыкой, как аудиоподарок. Доплата: <b>{extra}₽</b> "
        f"(вместе с письмом выходит {VL.price()}₽).", parse_mode="HTML")
    if B.send_order_invoice(chat_id, addon):
        B.notify_admin_new_order(addon)


# ── переключатель озвучки на экране превью ───────────────────

def add_preview_toggle(kb, state):
    """Кнопка на превью: включить озвучку (+N ₽) или вернуть обычное письмо."""
    if not VL.enabled() or state.get("group") or state.get("product") not in B.OCC.PRODUCTS:
        return
    extra = VL.surcharge(B.OCC.PRODUCTS[state["product"]]["price"])
    if state.get("voice"):
        kb.add(types.InlineKeyboardButton("🎙 Озвучка включена · убрать", callback_data="vo:pv:off"))
    elif extra > 0:
        kb.add(types.InlineKeyboardButton(f"🎙 Озвучить голосом — +{extra}₽", callback_data="vo:pv:on"))


def _preview_toggle(call):
    """vo:pv:on[:<голос>] / vo:pv:off — меняет цену и флаг озвучки в состоянии, перерисовывает превью."""
    chat_id = call.message.chat.id
    state = B.STATES.get(chat_id) or {}
    parts = call.data.split(":")
    B.bot.answer_callback_query(call.id)
    if (not VL.enabled() or state.get("step") != "occ_paywall" or state.get("group")
            or state.get("product") not in B.OCC.PRODUCTS):
        return
    if parts[2] == "off":
        if state.get("voice"):
            state.pop("voice", None)
            state.pop("voice_gender", None)
            if state.get("price_before_voice"):
                state["price"] = state.pop("price_before_voice")
            else:
                state.pop("price", None)
    elif not state.get("voice"):
        gender = parts[3] if len(parts) > 3 else None
        if gender not in VL.genders():
            gs = VL.genders()
            if len(gs) != 1:
                B.bot.send_message(chat_id, "Каким голосом прочитать письмо? 🎙",
                                   reply_markup=_gender_markup("vo:pv:on"))
                return
            gender = gs[0]
        if state.get("price"):
            state["price_before_voice"] = state["price"]
        state.update(voice=True, voice_gender=gender, price=VL.price())
    B.occ_refresh_text(chat_id, state)


def fulfill_addon(chat_id, addon):
    """Вызывается из fulfill_order, когда оплачена доплата за озвучку."""
    parent = B.get_order(addon.get("voice_for")) or {}
    if parent:
        parent["voice_addon_order"] = addon["order_id"]
        B.save_order(parent)
    B.bot.send_message(chat_id, "✅ Оплата прошла. Записываю голос — это займёт минуту 🎙", parse_mode="HTML")
    B.notify_admin_paid(addon)
    spawn(chat_id, addon["order_id"])


# ── выдача ───────────────────────────────────────────────────

def spawn(chat_id, order_id):
    if ASYNC:
        threading.Thread(target=deliver, args=(chat_id, order_id), name="voice", daemon=True).start()
    else:
        deliver(chat_id, order_id)


def deliver_if_voice(chat_id, order):
    """Хук occ_deliver: заказ «Голосовое письмо» после текста и открытки получает озвучку."""
    if order.get("voice") and order.get("product") in B.OCC.PRODUCTS and VL.enabled():
        B.bot.send_message(chat_id, "🎙 Теперь запишу письмо голосом — минутку…")
        spawn(chat_id, order["order_id"])


def _target(order):
    """Заказ, на котором живёт конверт и file_id голоса: для доплаты — исходное письмо."""
    return (B.get_order(order["voice_for"]) if order.get("voice_for") else None) or order


def _file(data, name):
    f = io.BytesIO(data)
    f.name = name
    return f


def deliver(chat_id, order_id, force=False):
    """Озвучивает письмо и отправляет голосовое + mp3. Возвращает True при успехе."""
    order = B.get_order(order_id)
    if not order or not order.get("letter_text"):
        return False
    if order.get("voice_status") in ("working", "done") and not force:
        return False
    order["voice_status"] = "working"
    B.save_order(order)
    gender = order.get("voice_gender") if order.get("voice_gender") in VL.genders() else (VL.genders() or ["f"])[0]
    try:
        res = VL.make(order["letter_text"], gender, seed=order_id)
    except Exception as exc:
        log.error("voice failed %s: %s", order_id, exc)
        order["voice_status"] = "failed"
        order["voice_error"] = str(exc)[:200]
        B.save_order(order)
        VL.log_usage(B.DATA_DIR, order_id, 0, gender, ok=False)
        B.bot.send_message(chat_id, "Голос пока не записался 😔 Я уже знаю об этом и пришлю голосовое "
                                    "вручную в ближайшее время. Письмо и открытка — у тебя, они на месте 🤍")
        if B.ADMIN_ID:
            B.safe_send(B.ADMIN_ID, f"⚠️ Не записалось голосовое письмо {order_id}: {B.esc(str(exc)[:200])}\n"
                                    f"Повторить: /voice_retry {order_id}")
        return False

    VL.log_usage(B.DATA_DIR, order_id, res["chars"], gender)
    order["voice_chars"] = res["chars"]
    order["voice_music"] = res["music"]
    who = (order.get("gift_for") or "").strip()
    target = _target(order)
    file_id = None
    if res["ogg"]:
        msg = B.bot.send_voice(chat_id, _file(res["ogg"], "letter.ogg"),
                               caption="🎙 Твоё голосовое письмо" + (f" для {who}" if who else ""))
        file_id = getattr(getattr(msg, "voice", None), "file_id", None)
    B.bot.send_audio(chat_id, _file(res["mp3"], "Pismo_ot_Alisy.mp3"), title="Письмо от Алисы",
                     performer="Алиса Невская",
                     caption="🎧 То же самое файлом mp3 — сохрани или перешли" if res["ogg"] else "🎧 Твоё голосовое письмо")
    order["voice_status"] = "done"
    order["voice_delivered_at"] = B.now_msk().isoformat()
    B.save_order(order)
    if file_id:
        target["voice_file_id"] = file_id
        B.save_order(target)
        code = B.occ_gift_code(target)
        kb = types.InlineKeyboardMarkup(row_width=1)
        label = f"🎙 Отправить {who}" if who and len(who) <= 20 else "🎙 Отправить адресату"
        kb.add(types.InlineKeyboardButton(
            label, switch_inline_query_chosen_chat=types.SwitchInlineQueryChosenChat(
                query=f"voice_{code}", allow_user_chats=True, allow_group_chats=True)))
        B.bot.send_message(chat_id, "Нажми «Отправить» и выбери человека из своих чатов — голосовое придёт ему "
                                    "от тебя ✨ Я сообщу, когда он откроет письмо.", reply_markup=kb)
    return True


def inline_send(query):
    """Автор отправляет голосовое в чат получателя в один тап (inline-запрос voice_<код>)."""
    code = query.query[len("voice_"):].strip()
    ref = B.read_json(B.gift_path(code), None) if code.isalnum() else None
    order = B.get_order(ref["order_id"]) if ref else None
    if (not order or order.get("status") != "done" or order.get("chat_id") != query.from_user.id
            or not order.get("voice_file_id")):
        B.bot.answer_inline_query(query.id, [], cache_time=0, is_personal=True)
        return
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Открыть письмо 💌", url=B.occ_gift_link(order)))
    who = order.get("sign") or "близкого человека"
    result = types.InlineQueryResultCachedVoice(
        id=code, voice_file_id=order["voice_file_id"], title="Голосовое письмо 🎙",
        caption=f"🎙 Тебе голосовое письмо от {who}", reply_markup=kb)
    B.bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)


def _retry(message):
    """/voice_retry <order_id> — админ повторяет озвучку после сбоя."""
    if not B.admin_only(message):
        return
    parts = (message.text or "").split()
    order = B.get_order(parts[1]) if len(parts) > 1 else None
    if not order:
        B.bot.send_message(message.chat.id, "Использование: /voice_retry ALI-… (номер заказа)")
        return
    ok = deliver(order["chat_id"], order["order_id"], force=True)
    B.bot.send_message(message.chat.id, "✅ Отправлено" if ok else "❌ Не получилось, см. логи")


def register(bot_module):
    global B
    B = bot_module
    bot = B.bot
    bot.callback_query_handler(func=lambda c: c.data == "vo:menu")(_menu)
    bot.callback_query_handler(func=lambda c: c.data.startswith("vo:go:"))(_go)
    bot.callback_query_handler(func=lambda c: c.data.startswith("vo:up:"))(_upsell)
    bot.callback_query_handler(func=lambda c: c.data.startswith("vo:pv:"))(_preview_toggle)
    bot.inline_handler(func=lambda q: (q.query or "").startswith("voice_"))(inline_send)
    bot.message_handler(commands=["voice_retry"])(_retry)
