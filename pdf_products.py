"""Цифровые PDF Алисы: «20 вопросов маме и папе» (199 ₽) и «10 писем, которые стоит написать близким» (249 ₽).

Тот же путь, что у PDF «Разговор с папой» (occasions.DOC): заказ -> окно оплаты (ЮKassa / Telegram Payments) ->
fulfill_order -> отправка файла из assets/. Своей оплаты и своих ключей модуль не знает.

Включается переменной PDF_PRODUCTS=1. По умолчанию выключено: товары не видны в каталоге, ссылки
?start=pdf_questions и ?start=pdf_letters ничего не делают.
  PDF_PRODUCTS            1 = включить
  PDF_QUESTIONS_PRICE_RUB цена «20 вопросов», ₽ (по умолчанию 199)
  PDF_LETTERS_PRICE_RUB   цена «10 писем», ₽ (по умолчанию 249)
  PDF_FOLLOWUP            0 = не слать мягкое сообщение «теперь напиши письмо» после выдачи файла (по умолчанию шлём)
Набор «Для мамы» (письмо + открытка + два PDF одним платежом) живёт в конце файла и включается отдельно:
  MOM_SET=1               включить набор (по умолчанию ВЫКЛЮЧЕНО: кнопок нет, API отвечает enabled=false)
  MOM_SET_PRICE_RUB       цена набора, ₽ (по умолчанию 590)
Подключается в bot_best.py: pdf_products.register(sys.modules[__name__]).
"""
import os
import re

from telebot import types

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
B = None

PRODUCTS = {
    "pdf_questions": {
        "key": "pdf_questions", "icon": "📘", "title": "20 вопросов маме и папе (PDF)",
        "short": "20 вопросов маме и папе", "price": 199, "price_env": "PDF_QUESTIONS_PRICE_RUB",
        "file": "20_voprosov_mame_i_pape.pdf",
        "pitch": ("20 вопросов в четырёх частях: детство, работа и дом, любовь и дружба, слова напоследок. "
                  "К каждому есть место для ответа, а ещё подсказки, как записать голос близкого и ничего не испортить. "
                  "12 страниц, формат А5, приходит сразу после оплаты."),
        "caption": ("✅ Оплата прошла. Твои «20 вопросов маме и папе» 🤍\n"
                    "Начни с одного вечера и двух-трёх вопросов. Остальные подождут."),
        "caption_free": ("🎁 Твои «20 вопросов маме и папе» — подарок от меня 🤍\n"
                         "Начни с одного вечера и двух-трёх вопросов. Остальные подождут."),
        "next_label": "🎀 Письмо маме или папе с открыткой", "next_cb": "occ:p:family",
        "followup": ("Теперь про письмо 🤍\n\n"
                     "Когда мама или папа ответят хотя бы на один вопрос, у тебя появятся живые детали: "
                     "привычка, словечко, случай из детства. Из них получается письмо про этого человека, а не «с праздником».\n\n"
                     "Расскажи мне про своего человека, я соберу письмо и открытку. Можно не сегодня, кнопка будет здесь."),
        "followup_btn": "✉️ Написать письмо про своего человека", "followup_occ": "family",
    },
    "pdf_letters": {
        "key": "pdf_letters", "icon": "💌", "title": "10 писем близким (PDF)",
        "short": "10 писем, которые стоит написать близким", "price": 249, "price_env": "PDF_LETTERS_PRICE_RUB",
        "file": "10_pisem_blizkim.pdf",
        "pitch": ("10 писем-примеров: папе, маме, сестре, другу, любимому, бабушке, коллеге, тому, перед кем виноват, "
                  "себе и ребёнку. С разбором, что в каждом работает, и бланком для твоего письма. "
                  "15 страниц, формат А5, приходит сразу после оплаты."),
        "caption": ("✅ Оплата прошла. Твои «10 писем, которые стоит написать близким» 🤍\n"
                    "Начни с одной фразы: «Я заметил, что ты всегда…»"),
        "next_label": "🎀 Письмо с открыткой — соберу его с тобой", "next_cb": "occ:catalog",
        "followup": ("Теперь твоя очередь 🤍\n\n"
                     "Выбери одного человека из десяти и расскажи мне про него: привычку, момент, когда он тебя поддержал, "
                     "и то, что вы редко говорите вслух. Остальное я возьму на себя: соберу письмо и открытку.\n\n"
                     "Можно не сегодня, кнопка будет здесь."),
        "followup_btn": "✉️ Написать письмо про своего человека", "followup_occ": None,
    },
}


_TAGGED = re.compile(r"^(ig|vk|yt|pin|tg|ad|mx)_pdf([ql])(?:_[a-z0-9-]{1,24})?$")


def start_key(param):
    """Ключ PDF-товара по параметру /start: `pdf_questions`, `pdf_letters` или с меткой канала `ig_pdfq_<ролик>` (q = 20 вопросов, l = 10 писем).
    Метка остаётся в профиле как источник первого захода (канал виден в /sources), а гид выдаётся как обычно."""
    pr = (param or "").strip().lower()
    if pr in PRODUCTS:
        return pr
    m = _TAGGED.match(pr)
    return {"q": "pdf_questions", "l": "pdf_letters"}[m.group(2)] if m else None


def enabled():
    return os.getenv("PDF_PRODUCTS", "").strip() == "1"


def get(key):
    return PRODUCTS.get(key)


def price(key):
    p = PRODUCTS[key]
    try:
        return max(0, int(os.getenv(p["price_env"], str(p["price"]))))  # 0 = бесплатно: файл сразу, без оплаты
    except ValueError:
        return p["price"]


def file_path(key):
    return os.path.join(ASSETS, PRODUCTS[key]["file"])


def of_order(order):
    """Товар заказа или None, если заказ не про PDF-продукты."""
    return PRODUCTS.get((order or {}).get("product"))


def item_title(order):
    """Название позиции для окна оплаты и чека (без «Письмо от Алисы»)."""
    p = of_order(order)
    if not p and is_mom(order):
        return "Набор «Для мамы»: письмо и два гида"
    return p["short"] if p else None


def invoice_description(order):
    p = of_order(order)
    return f"PDF «{p['short']}», сразу после оплаты. Заказ {order['order_id']}."


def catalog_buttons(kb):
    """Две кнопки в каталоге повода, только когда PDF_PRODUCTS=1."""
    if not enabled():
        return
    for key, p in PRODUCTS.items():
        kb.add(types.InlineKeyboardButton(f"{p['icon']} {p['title']} · {str(price(key)) + '₽' if price(key) else 'бесплатно'}", callback_data=f"pdfp:{key}"))


def open_order(chat_id, user, key):
    """Создаёт заказ и показывает окно оплаты (как occ_doc_order). Выключено: ничего не делает."""
    if not enabled() or key not in PRODUCTS:
        return False
    p = PRODUCTS[key]
    if price(key) == 0:  # подарок: без заказа и оплаты, файл сразу
        B.upsert_client(user)
        B.bot.send_message(chat_id, f"{p['icon']} <b>{p['title']}</b>\n\n{p['pitch']}", parse_mode="HTML")
        deliver(chat_id, {"order_id": B.new_order_id(), "product": key, "price_rub": 0, "free": True})
        return True
    pending = B.pending_orders(chat_id)
    if pending and pending[0].get("product") == key:  # повторный переход по ссылке: тот же заказ
        B.send_order_invoice(chat_id, pending[0])
        return True
    if pending:
        B.bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченный заказ.",
                           reply_markup=B.pending_markup(pending[0]["order_id"]))
        return True
    profile = B.upsert_client(user)
    order = {
        "order_id": B.new_order_id(), "chat_id": chat_id, "name": profile["name"],
        "username": profile.get("username", ""), "pain": key, "product": key,
        "answers": [], "letter_text": "", "price_rub": price(key), "status": "pending", "is_gift": False,
        "created_at": B.now_msk().isoformat(), "paid_at": None, "delivered_at": None,
        "email": None, "rating": None,
    }
    B.partners.apply_to_order(order, chat_id)
    B.save_order(order)
    B.bot.send_message(chat_id, f"{p['icon']} <b>{p['title']}</b>\n\n{p['pitch']}", parse_mode="HTML")
    B.send_order_invoice(chat_id, order)
    return True


def followup_enabled():
    return os.getenv("PDF_FOLLOWUP", "1").strip().lower() not in ("0", "false", "no", "off")


def followup_due(chat_id, key):
    """Мягкое сообщение «теперь напиши письмо» шлём один раз на человека и товар."""
    if not followup_enabled() or not PRODUCTS[key].get("followup"):
        return False
    profile = B.get_client(chat_id)
    return not (profile or {}).get("pdf_followup", {}).get(key)


def followup_markup(key):
    """В режиме «только приложение» кнопка открывает Mini App сразу с поводом, иначе ведёт в каталог поводов."""
    p = PRODUCTS[key]
    if B.app_only():
        return B.app_markup(p["followup_btn"], occ=p.get("followup_occ"))
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(p["followup_btn"], callback_data=p["next_cb"]))
    return kb


def send_followup(chat_id, key):
    """Одно тёплое сообщение после файла: без давления, без таймеров. Запоминаем в профиле, чтобы не повторять."""
    try:
        B.bot.send_message(chat_id, PRODUCTS[key]["followup"], reply_markup=followup_markup(key))
        profile = B.get_client(chat_id)
        if profile is not None:
            profile.setdefault("pdf_followup", {})[key] = B.now_msk().isoformat()
            B.write_json(B.client_path(chat_id), profile)
        return True
    except Exception as exc:  # сообщение необязательное: файл уже у человека
        B.log.error("pdf followup %s: %s", chat_id, exc)
        return False


def deliver(chat_id, order):
    """Отправляет файл после подтверждённой оплаты (или сразу, если подарок). Вызывается из fulfill_order."""
    p = of_order(order)
    follow = followup_due(chat_id, p["key"])
    kb = None
    if not follow:   # если следом придёт сообщение с кнопкой, у файла кнопку не дублируем
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton(p["next_label"], callback_data=p["next_cb"]))
    sent = False
    try:
        with open(file_path(p["key"]), "rb") as f:
            B.bot.send_document(chat_id, f, caption=(p.get("caption_free") if order.get("free") else None) or p["caption"],
                                reply_markup=kb)
        sent = True
        if follow:
            send_followup(chat_id, p["key"])
    except OSError as exc:
        B.log.error("pdf not sent %s: %s", order["order_id"], exc)
        B.bot.send_message(chat_id, "Оплата прошла, но файл не отправился. Напиши в «❓ Помощь», пришлю вручную. "
                                    f"Номер: <code>{order['order_id']}</code>", parse_mode="HTML")
    try:
        if B.ADMIN_ID:
            note = (f"{'🧪 ТЕСТ, 0 ₽. ' if order.get('is_test') else ('🎁 бесплатно. ' if order.get('free') else '💰 ')}PDF «{p['short']}»: "
                    f"{order.get('price_rub', 0)} ₽ · <code>{order['order_id']}</code>"
                    + ("" if sent else " · ⚠️ файл не отправился"))
            B.safe_send(B.ADMIN_ID, note)
    except Exception as exc:
        B.log.error("pdf notify %s: %s", order["order_id"], exc)
    return sent


def again_buttons(orders):
    """Кнопки «скачать ещё раз» для кабинета: по одной на оплаченный PDF-заказ."""
    out = []
    for o in orders:
        if is_mom(o) and o.get("status") == "done":
            out.append(types.InlineKeyboardButton("📥 Скачать два гида из набора «Для мамы»",
                                                  callback_data=f"pdfp:mom:{o['order_id']}"))
            continue
        p = of_order(o)
        if p and o.get("status") == "done":
            out.append(types.InlineKeyboardButton(f"📥 Скачать ещё раз: {p['short']}"[:60],
                                                  callback_data=f"pdfp:again:{o['order_id']}"))
    return out


# ── Набор «Для мамы» (MOM_SET=1) ─────────────────────────────────────────────────
# Письмо про маму идёт обычным потоком повода «family» (вопросы, превью, открытка), но цена одна на всё:
# state["price"] = цена набора, state["bundle"] = "mom_set". Заказ остаётся письмом (product="family"),
# в нём order["bundle"]="mom_set"; после оплаты fulfill_order выдаёт письмо с открыткой как обычно и следом два PDF.
MOM_KEY = "mom_set"
MOM_PRICE = 590
MOM_OCCASION = "family"
MOM_FILES = ("pdf_letters", "pdf_questions")   # порядок выдачи


def mom_enabled():
    return os.getenv("MOM_SET", "").strip() == "1"


def mom_price():
    try:
        return max(1, int(os.getenv("MOM_SET_PRICE_RUB", str(MOM_PRICE))))
    except ValueError:
        return MOM_PRICE


def is_mom(order):
    return (order or {}).get("bundle") == MOM_KEY


def mom_letter_price():
    return B.OCC.PRODUCTS[MOM_OCCASION]["price"]


def mom_info():
    """Состав и честная цена. regular: сумма действующих цен тех же вещей по отдельности (PDF с ценой 0 считается за 0);
    savings только если набор дешевле, иначе выгоду не обещаем."""
    items = [{"key": "letter", "title": "Письмо про маму с открыткой и ссылкой-конвертом", "price": mom_letter_price()}]
    for k in MOM_FILES:
        items.append({"key": k, "title": PRODUCTS[k]["short"] + " (PDF)", "price": price(k), "pdf": True})
    regular = sum(i["price"] for i in items)
    p = mom_price()
    return {"key": MOM_KEY, "title": "Набор «Для мамы»", "price": p, "regularPrice": regular,
            "savings": max(0, regular - p), "items": items, "enabled": mom_enabled()}


def mom_text():
    """Страница набора в боте: состав, цены частей и итог без накрутки."""
    info = mom_info()
    lines = [f"🎁 <b>{info['title']}</b> · {info['price']}₽", "",
             "Всё, чтобы сказать маме важное, в одном наборе:",
             "✍️ письмо про твою маму: по твоим деталям, не шаблон",
             "🖼 открытка с её именем и ссылка-конверт: мама вскроет письмо сама",
             f"📘 «{PRODUCTS['pdf_letters']['short']}» (PDF)",
             f"📗 «{PRODUCTS['pdf_questions']['short']}» (PDF)", ""]
    parts = " + ".join(f"«{i['title'].split(' (')[0]}» {i['price']}₽" if i.get("pdf") else f"письмо {i['price']}₽"
                       for i in info["items"])
    if info["savings"] > 0:
        lines.append(f"Отдельно: {parts} = {info['regularPrice']}₽. В наборе {info['price']}₽, выгода {info['savings']}₽.")
    else:
        lines.append(f"Отдельно: {parts} = {info['regularPrice']}₽.")
    lines += ["", "Начало письма и превью открытки видно до оплаты. Платишь один раз, оба PDF приходят файлами следом за письмом."]
    return "\n".join(lines)


def mom_markup():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"✍️ Собрать набор · {mom_price()}₽", callback_data="momset:go"))
    kb.add(types.InlineKeyboardButton(f"✉️ Только письмо · {mom_letter_price()}₽", callback_data=f"occ:p:{MOM_OCCASION}"))
    kb.add(types.InlineKeyboardButton("↩️ Все письма", callback_data="occ:catalog"))
    return kb


def mom_button(kb):
    """Кнопка набора в каталоге поводов и на странице «Маме, папе, бабушке»; MOM_SET выключен: ничего."""
    if mom_enabled():
        kb.add(types.InlineKeyboardButton(f"🎁 Набор «Для мамы» {mom_price()} ₽ (письмо, открытка, два гида)",
                                          callback_data="momset:info"))


def mom_state_extra():
    """Что добавить в состояние диалога письма, чтобы цена и выдача стали наборными."""
    return {"price": mom_price(), "bundle": MOM_KEY}


def mom_files(order):
    """Для API: файлы набора оплаченного заказа (ключ, название); ссылки подписывает miniapp_api."""
    if not is_mom(order) or order.get("status") != "done":
        return []
    return [{"key": k, "title": PRODUCTS[k]["short"]} for k in MOM_FILES]


def deliver_mom(chat_id, order):
    """Два PDF файлами в чат после письма и открытки. Сбой одного файла не мешает второму; админу итог."""
    sent = []
    for k in MOM_FILES:
        cap = ("🎁 Из набора «Для мамы»: " + PRODUCTS[k]["short"] + " 🤍\n"
               + ("Начни с одной фразы: «Я заметил, что ты всегда…»" if k == "pdf_letters"
                  else "Начни с одного вечера и двух-трёх вопросов. Остальные подождут."))
        try:
            with open(file_path(k), "rb") as f:
                B.bot.send_document(chat_id, f, caption=cap)
            sent.append(k)
        except Exception as exc:
            B.log.error("mom set file %s %s: %s", k, order.get("order_id"), exc)
    if len(sent) < len(MOM_FILES):
        B.bot.send_message(chat_id, "Оплата прошла, но часть файлов не отправилась. Напиши в «❓ Помощь», пришлю вручную. "
                                    f"Номер: <code>{order['order_id']}</code>", parse_mode="HTML")
    try:
        order["bundle_files_at"] = B.now_msk().isoformat()
        order["bundle_files_sent"] = sent
        B.save_order(order)
        if B.ADMIN_ID:
            B.safe_send(B.ADMIN_ID, f"🎁 Набор «Для мамы»: {len(sent)} из {len(MOM_FILES)} PDF выданы · "
                                    f"{order.get('price_rub', 0)} ₽ · <code>{order['order_id']}</code>"
                        + ("" if len(sent) == len(MOM_FILES) else " · ⚠️ часть файлов не отправилась"))
    except Exception as exc:
        B.log.error("mom set notify %s: %s", order.get("order_id"), exc)
    return sent


def register(bot_module):
    global B
    B = bot_module

    @B.bot.callback_query_handler(func=lambda c: (c.data or "").startswith("pdfp:"))
    def pdf_callback(call):
        chat_id = call.message.chat.id
        B.bot.answer_callback_query(call.id)
        parts = call.data.split(":", 2)
        if len(parts) == 3 and parts[1] == "mom":   # повторная выдача двух PDF владельцу оплаченного набора
            order = B.get_order(parts[2])
            if order and order.get("chat_id") == chat_id and order.get("status") == "done" and is_mom(order):
                for k in MOM_FILES:
                    try:
                        with open(file_path(k), "rb") as f:
                            B.bot.send_document(chat_id, f, caption=f"📥 «{PRODUCTS[k]['short']}» 🤍")
                    except OSError as exc:
                        B.log.error("mom resend %s: %s", order["order_id"], exc)
            return
        if len(parts) == 3 and parts[1] == "again":
            order = B.get_order(parts[2])
            if not order or order.get("chat_id") != chat_id or order.get("status") != "done" or not of_order(order):
                return
            p = of_order(order)
            try:
                with open(file_path(p["key"]), "rb") as f:
                    B.bot.send_document(chat_id, f, caption=f"📥 «{p['short']}» 🤍")
            except OSError as exc:
                B.log.error("pdf resend %s: %s", order["order_id"], exc)
            return
        if not enabled() or len(parts) != 2 or parts[1] not in PRODUCTS:
            return
        if B.pending_count(chat_id) >= B.MAX_PENDING:
            pending = B.pending_orders(chat_id)[0]
            B.bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченный заказ.",
                               reply_markup=B.pending_markup(pending["order_id"]))
            return
        open_order(chat_id, call.from_user, parts[1])
