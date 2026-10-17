"""Цифровые PDF Алисы: «20 вопросов маме и папе» (199 ₽) и «10 писем, которые стоит написать близким» (249 ₽).

Тот же путь, что у PDF «Разговор с папой» (occasions.DOC): заказ -> окно оплаты (ЮKassa / Telegram Payments) ->
fulfill_order -> отправка файла из assets/. Своей оплаты и своих ключей модуль не знает.

Включается переменной PDF_PRODUCTS=1. По умолчанию выключено: товары не видны в каталоге, ссылки
?start=pdf_questions и ?start=pdf_letters ничего не делают.
  PDF_PRODUCTS            1 = включить
  PDF_QUESTIONS_PRICE_RUB цена «20 вопросов», ₽ (по умолчанию 199)
  PDF_LETTERS_PRICE_RUB   цена «10 писем», ₽ (по умолчанию 249)
Подключается в bot_best.py: pdf_products.register(sys.modules[__name__]).
"""
import os

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
        "next_label": "🎀 Письмо маме или папе с открыткой", "next_cb": "occ:p:family",
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
    },
}


def enabled():
    return os.getenv("PDF_PRODUCTS", "").strip() == "1"


def get(key):
    return PRODUCTS.get(key)


def price(key):
    p = PRODUCTS[key]
    try:
        return max(1, int(os.getenv(p["price_env"], str(p["price"]))))
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
    return p["short"] if p else None


def invoice_description(order):
    p = of_order(order)
    return f"PDF «{p['short']}», сразу после оплаты. Заказ {order['order_id']}."


def catalog_buttons(kb):
    """Две кнопки в каталоге повода, только когда PDF_PRODUCTS=1."""
    if not enabled():
        return
    for key, p in PRODUCTS.items():
        kb.add(types.InlineKeyboardButton(f"{p['icon']} {p['title']} · {price(key)}₽", callback_data=f"pdfp:{key}"))


def open_order(chat_id, user, key):
    """Создаёт заказ и показывает окно оплаты (как occ_doc_order). Выключено: ничего не делает."""
    if not enabled() or key not in PRODUCTS:
        return False
    p = PRODUCTS[key]
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


def deliver(chat_id, order):
    """Отправляет файл после подтверждённой оплаты. Вызывается из fulfill_order."""
    p = of_order(order)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(p["next_label"], callback_data=p["next_cb"]))
    sent = False
    try:
        with open(file_path(p["key"]), "rb") as f:
            B.bot.send_document(chat_id, f, caption=p["caption"], reply_markup=kb)
        sent = True
    except OSError as exc:
        B.log.error("pdf not sent %s: %s", order["order_id"], exc)
        B.bot.send_message(chat_id, "Оплата прошла, но файл не отправился. Напиши в «❓ Помощь», пришлю вручную. "
                                    f"Номер: <code>{order['order_id']}</code>", parse_mode="HTML")
    try:
        if B.ADMIN_ID:
            note = (f"{'🧪 ТЕСТ, 0 ₽. ' if order.get('is_test') else '💰 '}PDF «{p['short']}»: "
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
        p = of_order(o)
        if p and o.get("status") == "done":
            out.append(types.InlineKeyboardButton(f"📥 Скачать ещё раз: {p['short']}"[:60],
                                                  callback_data=f"pdfp:again:{o['order_id']}"))
    return out


def register(bot_module):
    global B
    B = bot_module

    @B.bot.callback_query_handler(func=lambda c: (c.data or "").startswith("pdfp:"))
    def pdf_callback(call):
        chat_id = call.message.chat.id
        B.bot.answer_callback_query(call.id)
        parts = call.data.split(":", 2)
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
