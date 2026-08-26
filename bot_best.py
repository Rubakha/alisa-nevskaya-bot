"""
АЛИСА НЕВСКАЯ — Telegram-бот писем.
Версия BEST: примеры до оплаты, резюме перед платежом, рабочий кабинет,
Telegram Stars, админ-панель с фильтрами, рассылка, экспорт в Excel.
"""

import os
import json
import logging
from datetime import datetime, timedelta

import pytz
from dotenv import load_dotenv
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import LabeledPrice, Update

try:
    import ai_assistant as AI
except ImportError:  # модуль опционален
    AI = None

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("alisa")

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or "").rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "AlisaNevskaya_bot")
PORT = int(os.getenv("PORT", "5000"))

if not TG_BOT_TOKEN:
    raise ValueError("TG_BOT_TOKEN не задан в окружении")

app = Flask(__name__)
bot = TeleBot(TG_BOT_TOKEN, threaded=False)

MSK = pytz.timezone("Europe/Moscow")
FULL_PRICE_START = MSK.localize(datetime(2026, 10, 1))

# DATA_DIR указывает на постоянный диск (Render Disk).
# Локально по умолчанию ./data, на Render — путь монтирования, напр. /var/data.
DATA_DIR = os.getenv("DATA_DIR", "data")
ORDERS_DIR = os.path.join(DATA_DIR, "orders")
CLIENTS_DIR = os.path.join(DATA_DIR, "clients")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")

# срок исполнения, часы
DELIVERY_HOURS = 48
# сколько неоплаченных заказов можно держать одновременно
MAX_PENDING = 1
# интервал автобэкапа в часах, 0 — выключить
BACKUP_EVERY_HOURS = int(os.getenv("BACKUP_EVERY_HOURS", "168"))

PRICES_RUB = {
    "intro": {"письмо": 390, "дневник": 590, "сценарий": 890},
    "full": {"письмо": 690, "дневник": 890, "сценарий": 1290},
}
PRICES_STARS = {
    "intro": {"письмо": 325, "дневник": 492, "сценарий": 742},
    "full": {"письмо": 575, "дневник": 742, "сценарий": 1075},
}

FORMATS = ["письмо", "дневник", "сценарий"]

FORMAT_META = {
    "письмо": {
        "icon": "💌",
        "title": "ПИСЬМО",
        "length": "400–600 слов",
        "read": "5–7 минут",
        "style": "личное, прямое",
        "best_for": "когда нужен ответ на один острый вопрос",
        "sample": (
            "Ты ищешь ответ, потому что боишься ошибиться.\n"
            "Но самые живые истории получаются у тех, кто ошибался часто.\n\n"
            "Я не дам совет. Я дам разрешение — идти дальше.\n"
            "Твой ответ уже внутри, я только помогу его назвать."
        ),
    },
    "дневник": {
        "icon": "📖",
        "title": "ДНЕВНИК",
        "length": "700–1000 слов",
        "read": "10–12 минут",
        "style": "истории из жизни",
        "best_for": "когда хочется не ответа, а узнавания себя",
        "sample": (
            "Помню летний день на Невском. Я сидела у окна и смотрела,\n"
            "как все куда-то спешат. И каждый что-то ищет.\n\n"
            "Тогда я поняла: готовых ответов нет. Есть только шаги.\n"
            "Расскажу, как я тоже не знала — и всё равно пошла."
        ),
    },
    "сценарий": {
        "icon": "🎬",
        "title": "СЦЕНАРИЙ",
        "length": "1000–1500 слов",
        "read": "15–18 минут",
        "style": "диалог, встреча в кафе",
        "best_for": "когда тема большая и в одно письмо не влезает",
        "sample": (
            "Мы встречаемся в маленьком кафе на Невском.\n"
            "Ты рассказываешь. Я слушаю и переспрашиваю.\n\n"
            "А потом говорю всё, что думаю. Честно, без обтекаемости.\n"
            "Как живой разговор, только его можно перечитать."
        ),
    },
}

REVIEWS = [
    ("Мария", 5, "Письмо изменило то, как я смотрю на ситуацию."),
    ("Иван", 5, "Очень точно. Как будто мне в голову заглянули."),
    ("Наташа", 5, "Перечитываю третий раз. Спасибо."),
]

STATUS_LABEL = {
    "pending": ("⏳", "ждёт оплаты"),
    "paid": ("✍️", "в работе"),
    "done": ("✅", "письмо получено"),
    "cancelled": ("❌", "отменён"),
}

# состояния диалога: chat_id -> dict
STATES = {}


# ─────────────────────────────────────────────────────────────
# ХРАНИЛИЩЕ
# ─────────────────────────────────────────────────────────────

def ensure_dirs():
    for path in (DATA_DIR, ORDERS_DIR, CLIENTS_DIR, EXPORTS_DIR):
        os.makedirs(path, exist_ok=True)


def storage_check():
    """Проверяет, что каталог данных смонтирован и доступен на запись."""
    probe = os.path.join(DATA_DIR, ".write_probe")
    try:
        ensure_dirs()
        with open(probe, "w", encoding="utf-8") as f:
            f.write(datetime.now(MSK).isoformat())
        os.remove(probe)
        return True, os.path.abspath(DATA_DIR)
    except OSError as exc:
        return False, f"{os.path.abspath(DATA_DIR)} — {exc}"


def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    ensure_dirs()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def now_msk():
    return datetime.now(MSK)


def fmt_dt(iso_str):
    """ISO -> '25.08 в 14:30'."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        return "—"
    if dt.tzinfo is None:
        dt = MSK.localize(dt)
    return dt.astimezone(MSK).strftime("%d.%m в %H:%M")


def price_tier():
    return "intro" if now_msk() < FULL_PRICE_START else "full"


def rub(fmt):
    return PRICES_RUB[price_tier()].get(fmt, PRICES_RUB[price_tier()]["письмо"])


def stars(fmt):
    return PRICES_STARS[price_tier()].get(fmt, PRICES_STARS[price_tier()]["письмо"])


def client_path(chat_id):
    return os.path.join(CLIENTS_DIR, f"{chat_id}.json")


def order_path(order_id):
    return os.path.join(ORDERS_DIR, f"{order_id}.json")


def get_client(chat_id):
    return read_json(client_path(chat_id), None)


def upsert_client(user, referred_by=None):
    """Создаёт или обновляет профиль клиента, возвращает профиль."""
    chat_id = user.id
    profile = get_client(chat_id)
    name = (user.first_name or "").strip()
    if user.last_name:
        name = f"{name} {user.last_name}".strip()
    name = name or f"Гость {chat_id}"

    if profile is None:
        profile = {
            "chat_id": chat_id,
            "name": name,
            "username": user.username or "",
            "created_at": now_msk().isoformat(),
            "orders": [],
            "referred_by": referred_by,
            "referrals": [],
            "bonus_rub": 0,
            "total_spent_rub": 0,
            "notes": "",          # заметки Алисы о человеке
            "tags": [],           # характер, темы, привычки
        }
        if referred_by and referred_by != chat_id:
            add_referral(referred_by, chat_id)
    else:
        profile["name"] = name
        profile["username"] = user.username or ""

    write_json(client_path(chat_id), profile)
    return profile


def add_referral(inviter_id, new_id):
    inviter = get_client(inviter_id)
    if not inviter:
        return
    if new_id in inviter.get("referrals", []):
        return
    inviter.setdefault("referrals", []).append(new_id)
    write_json(client_path(inviter_id), inviter)


def save_order(order):
    write_json(order_path(order["order_id"]), order)
    profile = get_client(order["chat_id"])
    if profile is not None:
        if order["order_id"] not in profile.get("orders", []):
            profile.setdefault("orders", []).append(order["order_id"])
            write_json(client_path(order["chat_id"]), profile)


def get_order(order_id):
    return read_json(order_path(order_id), None)


def all_orders():
    ensure_dirs()
    items = []
    for fname in os.listdir(ORDERS_DIR):
        if not fname.endswith(".json"):
            continue
        data = read_json(os.path.join(ORDERS_DIR, fname), None)
        if data:
            items.append(data)
    items.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return items


def client_orders(chat_id):
    return [o for o in all_orders() if o.get("chat_id") == chat_id]


def new_order_id():
    return f"ALI-{int(now_msk().timestamp())}"


def pending_count(chat_id):
    return len([o for o in client_orders(chat_id) if o.get("status") == "pending"])


def random_review():
    idx = int(now_msk().timestamp()) % len(REVIEWS)
    name, rate, text = REVIEWS[idx]
    return f"💬 «{text}» — {name} {'⭐' * rate}"


# ─────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ─────────────────────────────────────────────────────────────

def kb_client():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("💌 Заказать письмо", "📖 Примеры")
    kb.add("👤 Мой кабинет", "🎁 Подарочное письмо")
    kb.add("❓ Помощь", "👥 Пригласить друга")
    return kb


def kb_admin():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Заказы", "📈 Статистика")
    kb.add("📮 Рассылка", "📥 Экспорт")
    kb.add("💾 Бэкап", "🖥 Диск")
    kb.add("🤖 Помощник")
    return kb


def kb_formats(prefix="fmt"):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for f in FORMATS:
        meta = FORMAT_META[f]
        kb.add(
            types.InlineKeyboardButton(
                f"{meta['icon']} {meta['title']} · {rub(f)}₽",
                callback_data=f"{prefix}:{f}",
            )
        )
    return kb


def esc(text):
    """Экранирует пользовательский текст для parse_mode=HTML."""
    if text is None:
        return ""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def safe_edit(call, text, markup=None):
    """Редактирует сообщение, при ошибке отправляет новое."""
    try:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as exc:
        log.debug("edit failed, sending new: %s", exc)
        try:
            bot.send_message(
                call.message.chat.id, text, parse_mode="HTML", reply_markup=markup
            )
        except Exception:
            bot.send_message(call.message.chat.id, text, reply_markup=markup)


# ─────────────────────────────────────────────────────────────
# СТАРТ
# ─────────────────────────────────────────────────────────────

@bot.message_handler(commands=["start"])
def cmd_start(message):
    chat_id = message.chat.id
    STATES.pop(chat_id, None)

    referred_by = None
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_"):
        try:
            referred_by = int(parts[1][4:])
        except ValueError:
            referred_by = None

    is_new = get_client(chat_id) is None
    profile = upsert_client(message.from_user, referred_by)

    if chat_id == ADMIN_ID:
        bot.send_message(
            chat_id,
            "👨‍💼 Панель администратора\n\nВыбери раздел ниже.",
            reply_markup=kb_admin(),
        )
        return

    greet = "Привет!" if is_new else f"С возвращением, {esc(profile['name'].split()[0])}!"
    invited = ""
    if referred_by and is_new:
        inviter = get_client(referred_by)
        if inviter:
            invited = f"\nТебя пригласил(а) {esc(inviter['name'])}. 💌\n"

    text = (
        f"💌 <b>Алиса Невская</b>\n\n"
        f"{greet} Я пишу письма о том, что человека держит.\n"
        f"Не советы — разговор.\n"
        f"{invited}\n"
        f"⭐ 4.8 из 5 · письма получили 234 человека\n"
        f"Письмо от {rub('письмо')}₽, готово за 24–48 часов."
    )
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb_client())


# ─────────────────────────────────────────────────────────────
# ЗАКАЗ: ШАГ 1 — ФОРМАТ
# ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "💌 Заказать письмо")
def order_start(message):
    chat_id = message.chat.id

    if pending_count(chat_id) >= MAX_PENDING:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("👤 Открыть кабинет", callback_data="cab:home"))
        bot.send_message(
            chat_id,
            "⏳ У тебя уже есть неоплаченный заказ.\n"
            "Заверши или отмени его в кабинете — и вернёмся к новому.",
            reply_markup=kb,
        )
        return

    STATES[chat_id] = {"step": "format"}
    bot.send_message(
        chat_id,
        "Выбери формат. Дальше покажу пример, как это выглядит 👇",
        reply_markup=kb_formats(),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("fmt:"))
def order_format(call):
    chat_id = call.message.chat.id
    fmt = call.data.split(":", 1)[1]
    if fmt not in FORMAT_META:
        bot.answer_callback_query(call.id, "Неизвестный формат")
        return

    STATES[chat_id] = {"step": "confirm_format", "format": fmt}
    meta = FORMAT_META[fmt]

    text = (
        f"{meta['icon']} <b>{meta['title']}</b> — {rub(fmt)}₽\n\n"
        f"Объём: {meta['length']}\n"
        f"Чтение: {meta['read']}\n"
        f"Стиль: {meta['style']}\n"
        f"Подходит: {meta['best_for']}\n\n"
        f"─── как это звучит ───\n"
        f"<i>{meta['sample']}</i>\n"
        f"─────────────────────\n\n"
        f"{random_review()}"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✅ Да, беру этот формат", callback_data=f"ask:{fmt}"))
    kb.add(types.InlineKeyboardButton("↩️ Другой формат", callback_data="back:formats"))
    safe_edit(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "back:formats")
def order_back_formats(call):
    STATES[call.message.chat.id] = {"step": "format"}
    safe_edit(call, "Выбери формат 👇", kb_formats())
    bot.answer_callback_query(call.id)


# ─────────────────────────────────────────────────────────────
# ЗАКАЗ: ШАГ 2 — ВОПРОС
# ─────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data.startswith("ask:"))
def order_ask_question(call):
    chat_id = call.message.chat.id
    fmt = call.data.split(":", 1)[1]
    STATES[chat_id] = {"step": "question", "format": fmt}

    meta = FORMAT_META[fmt]
    text = (
        f"{meta['icon']} {meta['title']} · {rub(fmt)}₽\n\n"
        "<b>Напиши свой вопрос</b>\n\n"
        "Что тебя волнует? Что ты хочешь спросить у Алисы?\n\n"
        "Пиши как есть — одним сообщением, без формулировок «правильно»."
    )
    safe_edit(call, text)
    bot.answer_callback_query(call.id)


@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "question"
    and m.content_type == "text"
)
def order_receive_question(message):
    chat_id = message.chat.id
    question = (message.text or "").strip()

    if len(question) < 15:
        bot.send_message(
            chat_id,
            "Слишком коротко — я не пойму контекст.\n"
            "Опиши ситуацию хотя бы парой предложений.",
        )
        return
    if len(question) > 2000:
        bot.send_message(chat_id, "Слишком длинно. Сократи до 2000 знаков, пожалуйста.")
        return

    state = STATES[chat_id]
    state["question"] = question
    state["step"] = "summary"

    fmt = state["format"]
    meta = FORMAT_META[fmt]
    ready_by = now_msk() + timedelta(hours=DELIVERY_HOURS)

    preview = esc(question if len(question) <= 400 else question[:400] + "…")
    text = (
        "🧾 <b>Проверь заказ</b>\n\n"
        f"Формат: {meta['icon']} {meta['title']} ({meta['length']})\n"
        f"Цена: <b>{rub(fmt)}₽</b> = {stars(fmt)} ⭐\n"
        f"Готово не позднее: {ready_by.strftime('%d.%m в %H:%M')} МСК\n\n"
        "Твой вопрос:\n"
        f"<i>{preview}</i>\n\n"
        "🔒 Оплата внутри Telegram\n"
        "♻️ Не откликнулось — перепишу один раз бесплатно\n\n"
        "Всё верно?"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"💳 Оплатить {stars(fmt)} ⭐", callback_data="ord:create"))
    kb.add(types.InlineKeyboardButton("✏️ Изменить вопрос", callback_data=f"ask:{fmt}"))
    kb.add(types.InlineKeyboardButton("↩️ Другой формат", callback_data="back:formats"))
    kb.add(types.InlineKeyboardButton("❌ Отменить", callback_data="ord:cancel"))
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "ord:cancel")
def order_cancel(call):
    STATES.pop(call.message.chat.id, None)
    safe_edit(call, "Заказ отменён. Если захочешь вернуться — жми «💌 Заказать письмо».")
    bot.answer_callback_query(call.id)


# ─────────────────────────────────────────────────────────────
# ЗАКАЗ: ШАГ 3 — СОЗДАНИЕ И ОПЛАТА
# ─────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "ord:create")
def order_create(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    fmt = state.get("format")
    question = state.get("question")

    if not fmt or not question:
        safe_edit(call, "Заказ устарел. Начни заново: «💌 Заказать письмо».")
        bot.answer_callback_query(call.id)
        return

    profile = upsert_client(call.from_user)
    order_id = new_order_id()
    order = {
        "order_id": order_id,
        "chat_id": chat_id,
        "name": profile["name"],
        "username": profile.get("username", ""),
        "format": fmt,
        "question": question,
        "price_rub": rub(fmt),
        "price_stars": stars(fmt),
        "status": "pending",
        "is_gift": bool(state.get("gift_for")),
        "gift_for": state.get("gift_for"),
        "created_at": now_msk().isoformat(),
        "paid_at": None,
        "delivered_at": None,
        "due_at": (now_msk() + timedelta(hours=DELIVERY_HOURS)).isoformat(),
        "letter_text": None,
        "rating": None,
    }
    save_order(order)
    STATES.pop(chat_id, None)

    meta = FORMAT_META[fmt]
    try:
        bot.send_invoice(
            chat_id=chat_id,
            title=f"{meta['title']} от Алисы",
            description=f"{meta['length']}. Готово за 24–48 часов. Заказ {order_id}.",
            invoice_payload=order_id,
            provider_token="",  # для Telegram Stars токен не нужен
            currency="XTR",
            prices=[LabeledPrice(label=meta["title"], amount=stars(fmt))],
        )
        bot.answer_callback_query(call.id)
    except Exception as exc:
        log.error("invoice error %s: %s", order_id, exc)
        bot.answer_callback_query(call.id)
        bot.send_message(
            chat_id,
            "⚠️ Не смог открыть окно оплаты.\n"
            f"Номер заказа: <code>{order_id}</code>\n"
            "Напиши в «❓ Помощь» — разберёмся вручную.",
            parse_mode="HTML",
        )
        return

    notify_admin_new_order(order)


@bot.pre_checkout_query_handler(func=lambda q: True)
def pre_checkout(query):
    order = get_order(query.invoice_payload)
    if not order:
        bot.answer_pre_checkout_query(
            query.id, ok=False, error_message="Заказ не найден. Оформи его заново."
        )
        return
    if order.get("status") != "pending":
        bot.answer_pre_checkout_query(
            query.id, ok=False, error_message="Этот заказ уже оплачен."
        )
        return
    bot.answer_pre_checkout_query(query.id, ok=True)


@bot.message_handler(content_types=["successful_payment"])
def on_paid(message):
    chat_id = message.chat.id
    payment = message.successful_payment
    order_id = payment.invoice_payload
    order = get_order(order_id)

    if not order:
        bot.send_message(
            chat_id,
            "Платёж получен, но заказ не найден. Напиши в «❓ Помощь», "
            f"укажи номер: <code>{order_id}</code>",
            parse_mode="HTML",
        )
        return

    order["status"] = "paid"
    order["paid_at"] = now_msk().isoformat()
    order["due_at"] = (now_msk() + timedelta(hours=DELIVERY_HOURS)).isoformat()
    order["charge_id"] = payment.telegram_payment_charge_id
    save_order(order)

    profile = get_client(chat_id)
    if profile:
        profile["total_spent_rub"] = profile.get("total_spent_rub", 0) + order["price_rub"]
        write_json(client_path(chat_id), profile)
        inviter_id = profile.get("referred_by")
        if inviter_id:
            inviter = get_client(inviter_id)
            if inviter:
                inviter["bonus_rub"] = inviter.get("bonus_rub", 0) + 50
                write_json(client_path(inviter_id), inviter)
                try:
                    bot.send_message(
                        inviter_id,
                        f"🎁 {profile['name']} заказал(а) письмо по твоей ссылке.\n"
                        f"Тебе начислено 50₽. Всего бонусов: {inviter['bonus_rub']}₽.",
                    )
                except Exception:
                    pass

    meta = FORMAT_META[order["format"]]
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("👤 Открыть кабинет", callback_data="cab:home"))

    bot.send_message(
        chat_id,
        "✅ <b>Оплата прошла</b>\n\n"
        f"Номер заказа: <code>{order_id}</code>\n"
        f"Формат: {meta['icon']} {meta['title']}\n"
        f"Оплачено: {order['price_rub']}₽\n"
        f"Готово не позднее: {fmt_dt(order['due_at'])} МСК\n\n"
        "Письмо придёт сюда же, в этот чат, и появится в кабинете.\n"
        "Ждать в чате не нужно — я напишу сама.",
        parse_mode="HTML",
        reply_markup=kb,
    )
    notify_admin_paid(order)


# ─────────────────────────────────────────────────────────────
# КАБИНЕТ КЛИЕНТА
# ─────────────────────────────────────────────────────────────

def cabinet_view(chat_id):
    profile = get_client(chat_id) or {}
    orders = client_orders(chat_id)
    done = [o for o in orders if o.get("status") == "done"]

    lines = [
        "👤 <b>Мой кабинет</b>",
        "",
        f"Имя: {esc(profile.get('name', '—'))}",
        f"Писем получено: {len(done)}",
        f"Потрачено: {profile.get('total_spent_rub', 0)}₽",
    ]
    if profile.get("bonus_rub"):
        lines.append(f"Бонусов за друзей: {profile['bonus_rub']}₽")
    lines.append("")

    if not orders:
        lines.append("Заказов пока нет. Первое письмо начинается с вопроса.")
    else:
        lines.append("<b>Мои заказы</b>")
        for o in orders[:10]:
            icon, label = STATUS_LABEL.get(o.get("status"), ("•", o.get("status", "")))
            meta = FORMAT_META.get(o["format"], {"icon": "•", "title": o["format"]})
            q = o["question"]
            short = esc(q if len(q) <= 40 else q[:40] + "…")
            lines.append(
                f"\n{icon} {meta['icon']} {meta['title']} — {label}\n"
                f"   «{short}»\n"
                f"   {fmt_dt(o.get('created_at'))} · <code>{o['order_id']}</code>"
            )

    kb = types.InlineKeyboardMarkup(row_width=1)
    for o in done[:5]:
        meta = FORMAT_META.get(o["format"], {"icon": "•", "title": o["format"]})
        kb.add(
            types.InlineKeyboardButton(
                f"📖 Читать: {meta['title']} · {fmt_dt(o.get('delivered_at'))}",
                callback_data=f"cab:read:{o['order_id']}",
            )
        )
    kb.add(types.InlineKeyboardButton("💌 Новое письмо", callback_data="cab:new"))
    kb.add(types.InlineKeyboardButton("🎁 Подарочное письмо", callback_data="gift:menu"))
    return "\n".join(lines), kb


@bot.message_handler(func=lambda m: m.text == "👤 Мой кабинет")
def cabinet_open(message):
    text, kb = cabinet_view(message.chat.id)
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "cab:home")
def cabinet_home(call):
    text, kb = cabinet_view(call.message.chat.id)
    safe_edit(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "cab:new")
def cabinet_new_order(call):
    STATES[call.message.chat.id] = {"step": "format"}
    safe_edit(call, "Выбери формат 👇", kb_formats())
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("cab:read:"))
def cabinet_read(call):
    chat_id = call.message.chat.id
    order_id = call.data.split(":", 2)[2]
    order = get_order(order_id)

    if not order or order.get("chat_id") != chat_id:
        bot.answer_callback_query(call.id, "Письмо не найдено")
        return
    if not order.get("letter_text"):
        bot.answer_callback_query(call.id, "Письмо ещё пишется")
        return

    meta = FORMAT_META.get(order["format"], {"icon": "•", "title": order["format"]})
    header = f"{meta['icon']} <b>{meta['title']}</b> · {fmt_dt(order.get('delivered_at'))}\n\n"
    body = esc(order["letter_text"])

    kb = types.InlineKeyboardMarkup(row_width=5)
    if not order.get("rating"):
        kb.row(
            *[
                types.InlineKeyboardButton(
                    "⭐" * i, callback_data=f"rate:{order_id}:{i}"
                )
                for i in range(1, 6)
            ]
        )
    kb.add(types.InlineKeyboardButton("↩️ В кабинет", callback_data="cab:home"))

    chunk = header + body
    if len(chunk) <= 3900:
        safe_edit(call, chunk, kb)
    else:
        safe_edit(call, header + body[:3800] + "…")
        bot.send_message(chat_id, body[3800:], reply_markup=kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("rate:"))
def cabinet_rate(call):
    _, order_id, value = call.data.split(":")
    order = get_order(order_id)
    if not order or order.get("chat_id") != call.message.chat.id:
        bot.answer_callback_query(call.id, "Заказ не найден")
        return

    order["rating"] = int(value)
    save_order(order)
    bot.answer_callback_query(call.id, "Спасибо за оценку!")

    if ADMIN_ID:
        try:
            bot.send_message(
                ADMIN_ID,
                f"⭐ Оценка {value}/5 · {order['name']} · {order_id}",
            )
        except Exception:
            pass

    if int(value) <= 3:
        bot.send_message(
            call.message.chat.id,
            "Жаль, что не откликнулось. Напиши в «❓ Помощь», "
            "что было не так — перепишу бесплатно.",
        )


# ─────────────────────────────────────────────────────────────
# ПОДАРОЧНОЕ ПИСЬМО
# ─────────────────────────────────────────────────────────────

def gift_menu_markup():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🎁 Вопрос напишу я", callback_data="gift:self"))
    kb.add(types.InlineKeyboardButton("✍️ Пусть спросит сам", callback_data="gift:link"))
    kb.add(types.InlineKeyboardButton("↩️ Назад", callback_data="cab:home"))
    return kb


GIFT_TEXT = (
    "🎁 <b>Подарочное письмо</b>\n\n"
    "Два способа:\n\n"
    "🎁 <b>Вопрос напишу я</b> — ты формулируешь вопрос за друга "
    "и оплачиваешь. Письмо получите оба.\n\n"
    "✍️ <b>Пусть спросит сам</b> — отправляешь ссылку, друг заказывает "
    "сам. Тебе 50₽ бонуса."
)


@bot.message_handler(func=lambda m: m.text == "🎁 Подарочное письмо")
def gift_open(message):
    bot.send_message(message.chat.id, GIFT_TEXT, parse_mode="HTML", reply_markup=gift_menu_markup())


@bot.callback_query_handler(func=lambda c: c.data == "gift:menu")
def gift_menu(call):
    safe_edit(call, GIFT_TEXT, gift_menu_markup())
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "gift:link")
def gift_link(call):
    chat_id = call.message.chat.id
    profile = get_client(chat_id) or {}
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{chat_id}"
    text = (
        "✍️ <b>Ссылка для друга</b>\n\n"
        f"<code>{link}</code>\n\n"
        "Нажми на ссылку, чтобы скопировать, и отправь другу.\n"
        "Когда он оплатит первое письмо, тебе начислится 50₽.\n\n"
        f"Приглашено: {len(profile.get('referrals', []))} · "
        f"бонусов: {profile.get('bonus_rub', 0)}₽"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("↩️ Назад", callback_data="gift:menu"))
    safe_edit(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "gift:self")
def gift_self(call):
    chat_id = call.message.chat.id
    STATES[chat_id] = {"step": "gift_contact"}
    safe_edit(
        call,
        "🎁 <b>Шаг 1 из 3</b>\n\n"
        "Кому подарок? Напиши @username или имя друга —\n"
        "это попадёт в заказ, чтобы я знала, для кого пишу.",
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "gift_contact"
    and m.content_type == "text"
)
def gift_contact(message):
    chat_id = message.chat.id
    contact = (message.text or "").strip()
    if len(contact) < 2:
        bot.send_message(chat_id, "Слишком коротко. Напиши @username или имя.")
        return

    STATES[chat_id] = {"step": "gift_format", "gift_for": contact}
    bot.send_message(
        chat_id,
        f"🎁 <b>Шаг 2 из 3</b>\n\nПодарок для: {esc(contact)}\nТеперь выбери формат 👇",
        parse_mode="HTML",
        reply_markup=kb_formats(prefix="giftfmt"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("giftfmt:"))
def gift_format(call):
    chat_id = call.message.chat.id
    fmt = call.data.split(":", 1)[1]
    state = STATES.get(chat_id) or {}
    gift_for = state.get("gift_for")

    if fmt not in FORMAT_META or not gift_for:
        bot.answer_callback_query(call.id, "Начни заново")
        return

    STATES[chat_id] = {"step": "question", "format": fmt, "gift_for": gift_for}
    meta = FORMAT_META[fmt]
    safe_edit(
        call,
        f"🎁 <b>Шаг 3 из 3</b>\n\n"
        f"Подарок для: {esc(gift_for)}\n"
        f"Формат: {meta['icon']} {meta['title']} · {rub(fmt)}₽\n\n"
        "Какой вопрос задать от его имени?\n"
        "Напиши так, как спросил бы он сам.",
    )
    bot.answer_callback_query(call.id)


# ─────────────────────────────────────────────────────────────
# ПРИМЕРЫ, ПОМОЩЬ, ПРИГЛАШЕНИЕ
# ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📖 Примеры")
def show_examples(message):
    bot.send_message(
        message.chat.id,
        "Выбери формат — покажу отрывок и параметры 👇",
        reply_markup=kb_formats(),
    )


@bot.message_handler(func=lambda m: m.text == "👥 Пригласить друга")
def invite_friend(message):
    chat_id = message.chat.id
    profile = get_client(chat_id) or {}
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{chat_id}"
    bot.send_message(
        chat_id,
        "👥 <b>Пригласить друга</b>\n\n"
        f"Твоя ссылка:\n<code>{link}</code>\n\n"
        "За каждого друга, который оплатит письмо, — 50₽ бонуса.\n\n"
        f"Приглашено: {len(profile.get('referrals', []))}\n"
        f"Бонусов: {profile.get('bonus_rub', 0)}₽",
        parse_mode="HTML",
    )


@bot.message_handler(func=lambda m: m.text == "❓ Помощь")
def help_menu(message):
    chat_id = message.chat.id
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✉️ Написать в поддержку", callback_data="sup:new"))
    bot.send_message(
        chat_id,
        "❓ <b>Помощь</b>\n\n"
        f"<b>Сроки.</b> 24–48 часов, максимум 72.\n"
        f"<b>Цена.</b> Письмо {rub('письмо')}₽, дневник {rub('дневник')}₽, "
        f"сценарий {rub('сценарий')}₽.\n"
        "<b>Оплата.</b> Звёздами Telegram, внутри приложения.\n"
        "<b>Гарантия.</b> Не откликнулось — перепишу один раз бесплатно.\n"
        "<b>Приватность.</b> Вопросы не публикую и никому не пересылаю.\n\n"
        "Остались вопросы — жми кнопку ниже.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data == "sup:new")
def support_start(call):
    STATES[call.message.chat.id] = {"step": "support"}
    safe_edit(call, "Опиши, что случилось. Отвечу в течение нескольких часов.")
    bot.answer_callback_query(call.id)


@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "support"
    and m.content_type == "text"
)
def support_receive(message):
    chat_id = message.chat.id
    STATES.pop(chat_id, None)
    profile = get_client(chat_id) or {}

    if ADMIN_ID:
        try:
            bot.send_message(
                ADMIN_ID,
                "❓ <b>Обращение в поддержку</b>\n\n"
                f"От: {esc(profile.get('name', chat_id))} "
                f"(@{profile.get('username') or '—'})\n"
                f"chat_id: <code>{chat_id}</code>\n\n"
                f"{esc(message.text)}\n\n"
                f"Ответить: <code>/reply {chat_id} текст</code>",
                parse_mode="HTML",
            )
        except Exception as exc:
            log.error("support notify failed: %s", exc)

    bot.send_message(chat_id, "✅ Передала. Отвечу в этом чате.", reply_markup=kb_client())

    if ADMIN_ID and AI and AI.available():
        try:
            orders = client_orders(chat_id)
            ctx = (f"клиент, заказов: {len(orders)}, "
                   f"писем получено: {len([o for o in orders if o.get('status') == 'done'])}")
            reply = AI.draft_support(message.text, ctx)
            bot.send_message(
                ADMIN_ID,
                f"🤖 Черновик ответа (не отправлен):\n{'─' * 22}\n{reply}\n{'─' * 22}\n"
                f"Отправить: <code>/reply {chat_id} текст</code>",
                parse_mode="HTML",
            )
        except Exception as exc:
            log.error("support draft failed: %s", exc)


# ─────────────────────────────────────────────────────────────
# УВЕДОМЛЕНИЯ АДМИНУ
# ─────────────────────────────────────────────────────────────

def notify_admin_new_order(order):
    if not ADMIN_ID:
        return
    meta = FORMAT_META.get(order["format"], {"icon": "•", "title": order["format"]})
    gift = f"\n🎁 Подарок для: {esc(order['gift_for'])}" if order.get("is_gift") else ""
    try:
        bot.send_message(
            ADMIN_ID,
            "🆕 <b>Заказ создан</b> (ждёт оплаты)\n\n"
            f"{esc(order['name'])} (@{esc(order.get('username')) or '—'}){gift}\n"
            f"{meta['icon']} {meta['title']} · {order['price_rub']}₽\n"
            f"<code>{order['order_id']}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        log.error("admin notify failed: %s", exc)


def notify_admin_paid(order):
    if not ADMIN_ID:
        return
    meta = FORMAT_META.get(order["format"], {"icon": "•", "title": order["format"]})
    gift = f"\n🎁 Подарок для: {order['gift_for']}" if order.get("is_gift") else ""
    prev = [
        o for o in client_orders(order["chat_id"])
        if o.get("status") == "done" and o.get("order_id") != order["order_id"]
    ]
    who = f"\n🔁 Возвращается, писем до этого: {len(prev)}" if prev else "\n🆕 Пишет впервые"

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "✍️ Написать письмо", callback_data=f"adm:write:{order['order_id']}"
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "🗂 Карточка", callback_data=f"card:show:{order['chat_id']}"
        )
    )
    try:
        bot.send_message(
            ADMIN_ID,
            "💳 <b>ОПЛАЧЕНО</b>\n\n"
            f"{order['name']} (@{order.get('username') or '—'}){gift}\n"
            f"{meta['icon']} {meta['title']} · {order['price_rub']}₽\n"
            f"Срок: до {fmt_dt(order['due_at'])} МСК\n\n"
            f"Вопрос:\n{esc(order['question'])}\n\n"
            f"<code>{order['order_id']}</code>",
            parse_mode="HTML",
            reply_markup=kb,
        )
    except Exception as exc:
        log.error("admin notify failed: %s", exc)


# ─────────────────────────────────────────────────────────────
# АДМИН: ЗАКАЗЫ И СТАТИСТИКА
# ─────────────────────────────────────────────────────────────

def admin_only(message):
    return message.chat.id == ADMIN_ID


def orders_list_text(status_filter):
    orders = all_orders()
    if status_filter != "all":
        orders = [o for o in orders if o.get("status") == status_filter]

    if not orders:
        return "Пусто в этом разделе."

    lines = []
    for o in orders[:20]:
        icon, label = STATUS_LABEL.get(o.get("status"), ("•", ""))
        meta = FORMAT_META.get(o["format"], {"icon": "•", "title": o["format"]})
        q = o["question"]
        short = esc(q if len(q) <= 60 else q[:60] + "…")
        lines.append(
            f"{icon} {meta['icon']} {esc(o['name'])} · {o['price_rub']}₽ · {label}\n"
            f"   «{short}»\n"
            f"   {fmt_dt(o.get('created_at'))}\n"
            f"   /write_{o['order_id'].replace('-', '_')}"
        )
    return "\n\n".join(lines)


def kb_admin_orders(active="paid"):
    kb = types.InlineKeyboardMarkup(row_width=2)
    tabs = [("paid", "✍️ В работе"), ("pending", "⏳ Не оплачены"),
            ("done", "✅ Готовы"), ("all", "📋 Все")]
    row = []
    for key, label in tabs:
        mark = "• " if key == active else ""
        row.append(types.InlineKeyboardButton(f"{mark}{label}", callback_data=f"adm:list:{key}"))
    kb.add(*row)
    return kb


@bot.message_handler(func=lambda m: admin_only(m) and m.text == "📊 Заказы")
def admin_orders(message):
    bot.send_message(
        message.chat.id,
        "📊 <b>Заказы в работе</b>\n\n" + orders_list_text("paid"),
        parse_mode="HTML",
        reply_markup=kb_admin_orders("paid"),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:list:"))
def admin_orders_tab(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    key = call.data.split(":", 2)[2]
    titles = {"paid": "В работе", "pending": "Не оплачены", "done": "Готовы", "all": "Все заказы"}
    safe_edit(
        call,
        f"📊 <b>{titles.get(key, key)}</b>\n\n" + orders_list_text(key),
        kb_admin_orders(key),
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(func=lambda m: admin_only(m) and m.text == "📈 Статистика")
def admin_stats(message):
    orders = all_orders()
    paid = [o for o in orders if o.get("status") in ("paid", "done")]
    revenue = sum(o.get("price_rub", 0) for o in paid)
    ratings = [o["rating"] for o in orders if o.get("rating")]
    avg = round(sum(ratings) / len(ratings), 1) if ratings else "—"

    by_format = {}
    for o in paid:
        by_format[o["format"]] = by_format.get(o["format"], 0) + 1
    fmt_lines = "\n".join(
        f"  {FORMAT_META[f]['icon']} {f}: {n}" for f, n in by_format.items()
    ) or "  —"

    ensure_dirs()
    clients_total = len([f for f in os.listdir(CLIENTS_DIR) if f.endswith(".json")])

    bot.send_message(
        message.chat.id,
        "📈 <b>Статистика</b>\n\n"
        f"Клиентов: {clients_total}\n"
        f"Заказов всего: {len(orders)}\n"
        f"Оплачено: {len(paid)}\n"
        f"В работе: {len([o for o in orders if o.get('status') == 'paid'])}\n"
        f"Отправлено: {len([o for o in orders if o.get('status') == 'done'])}\n"
        f"Не оплачено: {len([o for o in orders if o.get('status') == 'pending'])}\n\n"
        f"Выручка: <b>{revenue}₽</b>\n"
        f"Средний чек: {round(revenue / len(paid)) if paid else 0}₽\n"
        f"Средняя оценка: {avg}\n\n"
        f"По форматам:\n{fmt_lines}",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────
# АДМИН: ОТПРАВКА ПИСЬМА
# ─────────────────────────────────────────────────────────────

TAG_PRESETS = [
    "тревожность", "выгорание", "отношения", "работа", "семья",
    "самооценка", "одиночество", "поиск себя", "утрата", "деньги",
    "рационален", "эмоционален", "ищет разрешения", "ищет план",
]


def client_card(chat_id, exclude_order=None):
    """Собирает карточку человека: история, темы, заметки."""
    profile = get_client(chat_id) or {}
    orders = [o for o in client_orders(chat_id) if o.get("order_id") != exclude_order]
    done = [o for o in orders if o.get("status") == "done"]

    if not orders and not profile.get("notes"):
        return "🆕 <b>Новый человек</b> — пишет впервые."

    lines = ["🗂 <b>Карточка</b>"]

    first = profile.get("created_at")
    lines.append(f"С нами с {fmt_dt(first)} · писем: {len(done)}")

    ratings = [o["rating"] for o in orders if o.get("rating")]
    if ratings:
        avg = round(sum(ratings) / len(ratings), 1)
        lines.append(f"Средняя оценка писем: {avg} ({len(ratings)} шт.)")

    if profile.get("tags"):
        lines.append("Метки: " + ", ".join(esc(t) for t in profile["tags"]))

    if profile.get("notes"):
        lines.append(f"\n📝 <b>Заметки:</b>\n<i>{esc(profile['notes'])}</i>")

    if done:
        lines.append("\n<b>О чём спрашивал раньше:</b>")
        for o in done[:4]:
            meta = FORMAT_META.get(o["format"], {"icon": "•"})
            q = o.get("question") or ""
            q = q if len(q) <= 110 else q[:110] + "…"
            rate = f" · {o['rating']}⭐" if o.get("rating") else ""
            lines.append(f"{meta['icon']} {fmt_dt(o.get('delivered_at'))}{rate}\n«{esc(q)}»")

    return "\n".join(lines)


def kb_card(chat_id, order_id=None):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📝 Заметка", callback_data=f"card:note:{chat_id}"),
        types.InlineKeyboardButton("🏷 Метки", callback_data=f"card:tags:{chat_id}"),
    )
    if AI and AI.available():
        kb.add(
            types.InlineKeyboardButton("🤖 Предложить метки",
                                       callback_data=f"ai:tags:{chat_id}")
        )
    if order_id:
        kb.add(
            types.InlineKeyboardButton(
                "📖 Прошлые письма", callback_data=f"card:letters:{chat_id}"
            )
        )
    return kb


@bot.message_handler(commands=["card"])
def admin_card_cmd(message):
    if not admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Формат: /card CHAT_ID")
        return
    try:
        target = int(parts[1].strip())
    except ValueError:
        bot.send_message(message.chat.id, "chat_id должен быть числом.")
        return
    profile = get_client(target)
    if not profile:
        bot.send_message(message.chat.id, "Такого клиента нет.")
        return
    bot.send_message(
        message.chat.id,
        f"👤 <b>{esc(profile['name'])}</b> (@{esc(profile.get('username')) or '—'})\n"
        f"chat_id: <code>{target}</code>\n\n" + client_card(target),
        parse_mode="HTML",
        reply_markup=kb_card(target, order_id=True),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:show:"))
def card_show(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    profile = get_client(target) or {}
    bot.send_message(
        ADMIN_ID,
        f"👤 <b>{esc(profile.get('name', target))}</b>\n"
        f"chat_id: <code>{target}</code>\n\n" + client_card(target),
        parse_mode="HTML",
        reply_markup=kb_card(target, order_id=True),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:note:"))
def card_note_start(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    profile = get_client(target) or {}
    STATES[ADMIN_ID] = {"step": "card_note", "target": target}
    current = profile.get("notes") or "пусто"
    bot.send_message(
        ADMIN_ID,
        f"📝 Заметка о {esc(profile.get('name', target))}\n\n"
        f"Сейчас: <i>{esc(current)}</i>\n\n"
        "Пришли новый текст — перезапишет. /cancel — отмена.",
        parse_mode="HTML",
    )
    bot.answer_callback_query(call.id)


@bot.message_handler(
    func=lambda m: admin_only(m)
    and STATES.get(m.chat.id, {}).get("step") == "card_note"
    and m.content_type == "text"
)
def card_note_save(message):
    target = STATES[message.chat.id]["target"]
    STATES.pop(message.chat.id, None)
    profile = get_client(target)
    if not profile:
        bot.send_message(message.chat.id, "Клиент не найден.")
        return
    profile["notes"] = message.text.strip()
    write_json(client_path(target), profile)
    bot.send_message(message.chat.id, "✅ Заметка сохранена.")


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:tags:"))
def card_tags(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    profile = get_client(target) or {}
    active = profile.get("tags", [])

    kb = types.InlineKeyboardMarkup(row_width=2)
    row = []
    for tag in TAG_PRESETS:
        mark = "✅ " if tag in active else ""
        row.append(
            types.InlineKeyboardButton(
                f"{mark}{tag}", callback_data=f"card:tag:{target}:{tag}"
            )
        )
    for i in range(0, len(row), 2):
        kb.row(*row[i:i + 2])
    kb.add(types.InlineKeyboardButton("↩️ Готово", callback_data=f"card:done:{target}"))

    text = (
        f"🏷 Метки для {esc(profile.get('name', target))}\n\n"
        f"Сейчас: {', '.join(esc(t) for t in active) if active else 'нет'}\n\n"
        "Нажимай, чтобы включить или убрать."
    )
    safe_edit(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:tag:"))
def card_tag_toggle(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    _, _, target, tag = call.data.split(":", 3)
    target = int(target)
    profile = get_client(target)
    if not profile:
        bot.answer_callback_query(call.id, "Клиент не найден")
        return

    tags = profile.setdefault("tags", [])
    if tag in tags:
        tags.remove(tag)
    else:
        tags.append(tag)
    write_json(client_path(target), profile)
    bot.answer_callback_query(call.id, f"{tag}: {'убрал' if tag not in tags else 'добавил'}")
    card_tags(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:done:"))
def card_done(call):
    target = int(call.data.split(":", 2)[2])
    profile = get_client(target) or {}
    safe_edit(
        call,
        f"👤 <b>{esc(profile.get('name', target))}</b>\n\n" + client_card(target),
        kb_card(target, order_id=True),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:letters:"))
def card_letters(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    done = [o for o in client_orders(target) if o.get("letter_text")]
    if not done:
        bot.answer_callback_query(call.id, "Писем ещё не было")
        return

    bot.answer_callback_query(call.id)
    for o in done[:3]:
        meta = FORMAT_META.get(o["format"], {"icon": "•", "title": o["format"]})
        body = o["letter_text"]
        body = body if len(body) <= 3000 else body[:3000] + "…"
        bot.send_message(
            ADMIN_ID,
            f"{meta['icon']} <b>{meta['title']}</b> · {fmt_dt(o.get('delivered_at'))}\n"
            f"Вопрос: <i>{esc((o.get('question') or '')[:150])}</i>\n\n"
            f"{esc(body)}",
            parse_mode="HTML",
        )


def start_writing(chat_id, order_id):
    order = get_order(order_id)
    if not order:
        bot.send_message(chat_id, f"Заказ {order_id} не найден.")
        return
    if order.get("status") == "pending":
        bot.send_message(chat_id, "⚠️ Заказ ещё не оплачен. Всё равно можно написать.")

    STATES[chat_id] = {"step": "admin_write", "order_id": order_id}
    meta = FORMAT_META.get(order["format"], {"icon": "•", "title": order["format"]})
    gift = f"\n🎁 Для: {esc(order['gift_for'])}" if order.get("is_gift") else ""

    # карточка человека приходит первой — контекст до текста
    bot.send_message(
        chat_id,
        client_card(order["chat_id"], exclude_order=order_id),
        parse_mode="HTML",
        reply_markup=kb_card(order["chat_id"], order_id=order_id),
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    if AI and AI.available():
        kb.row(
            types.InlineKeyboardButton("🔍 Разобрать вопрос",
                                       callback_data=f"ai:analyze:{order_id}"),
            types.InlineKeyboardButton("✏️ Черновик",
                                       callback_data=f"ai:draft:{order_id}"),
        )

    bot.send_message(
        chat_id,
        f"✍️ <b>Письмо для {esc(order['name'])}</b>{gift}\n"
        f"{meta['icon']} {meta['title']} · {meta['length']}\n\n"
        f"Вопрос:\n{esc(order['question'])}\n\n"
        "Пришли текст письма одним сообщением. /cancel — отмена.",
        parse_mode="HTML",
        reply_markup=kb if kb.keyboard else None,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:write:"))
def admin_write_cb(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    start_writing(call.message.chat.id, call.data.split(":", 2)[2])
    bot.answer_callback_query(call.id)


@bot.message_handler(commands=["letter"])
def admin_letter_cmd(message):
    if not admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Формат: /letter ALI-1234567890")
        return
    start_writing(message.chat.id, parts[1].strip())


@bot.message_handler(func=lambda m: admin_only(m) and (m.text or "").startswith("/write_"))
def admin_write_shortcut(message):
    order_id = message.text[len("/write_"):].replace("_", "-")
    start_writing(message.chat.id, order_id)


@bot.message_handler(commands=["cancel"])
def admin_cancel(message):
    STATES.pop(message.chat.id, None)
    bot.send_message(message.chat.id, "Отменено.")


@bot.message_handler(
    func=lambda m: admin_only(m)
    and STATES.get(m.chat.id, {}).get("step") == "admin_write"
    and m.content_type == "text"
)
def admin_write_receive(message):
    chat_id = message.chat.id
    order_id = STATES[chat_id]["order_id"]
    order = get_order(order_id)
    STATES.pop(chat_id, None)

    if not order:
        bot.send_message(chat_id, "Заказ пропал. Проверь список.")
        return

    letter = message.text
    order["letter_text"] = letter
    order["status"] = "done"
    order["delivered_at"] = now_msk().isoformat()
    save_order(order)

    meta = FORMAT_META.get(order["format"], {"icon": "•", "title": order["format"]})
    kb = types.InlineKeyboardMarkup(row_width=5)
    kb.row(
        *[
            types.InlineKeyboardButton("⭐" * i, callback_data=f"rate:{order_id}:{i}")
            for i in range(1, 6)
        ]
    )
    kb.add(types.InlineKeyboardButton("👤 В кабинет", callback_data="cab:home"))

    header = f"📬 <b>Твоё письмо готово</b>\n{meta['icon']} {meta['title']}\n\n"
    try:
        safe_letter = esc(letter)
        if len(header) + len(safe_letter) <= 3900:
            bot.send_message(order["chat_id"], header + safe_letter, parse_mode="HTML")
        else:
            bot.send_message(order["chat_id"], header, parse_mode="HTML")
            for i in range(0, len(letter), 3800):
                bot.send_message(order["chat_id"], letter[i:i + 3800])
        bot.send_message(
            order["chat_id"],
            "Если откликнулось — поставь оценку. Если нет — напиши, перепишу.",
            reply_markup=kb,
        )
        bot.send_message(chat_id, f"✅ Отправлено: {order['name']} · {order_id}")
    except Exception as exc:
        log.error("send letter failed %s: %s", order_id, exc)
        bot.send_message(chat_id, f"❌ Не доставлено: {exc}\nПисьмо сохранено в кабинете клиента.")


# ─────────────────────────────────────────────────────────────
# CLAUDE-ПОМОЩНИК (только для админа, клиенту ничего не уходит)
# ─────────────────────────────────────────────────────────────

def ai_guard(call_or_msg) -> bool:
    """Проверяет доступ и наличие ключа."""
    chat_id = (call_or_msg.message.chat.id
               if hasattr(call_or_msg, "message") else call_or_msg.chat.id)
    if chat_id != ADMIN_ID:
        return False
    if not (AI and AI.available()):
        bot.send_message(
            chat_id,
            "🤖 Помощник выключен.\n\n"
            "Нужно: <code>pip install anthropic</code> и переменная "
            "<code>ANTHROPIC_API_KEY</code> в окружении.",
            parse_mode="HTML",
        )
        return False
    return True


def send_long(chat_id, text, prefix="", markup=None):
    """Отправляет длинный текст частями."""
    full = prefix + text
    if len(full) <= 3900:
        bot.send_message(chat_id, full, reply_markup=markup)
        return
    bot.send_message(chat_id, prefix.rstrip() or "…")
    chunks = [text[i:i + 3800] for i in range(0, len(text), 3800)]
    for i, ch in enumerate(chunks):
        bot.send_message(chat_id, ch,
                         reply_markup=markup if i == len(chunks) - 1 else None)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ai:analyze:"))
def ai_analyze(call):
    if not ai_guard(call):
        bot.answer_callback_query(call.id, "Недоступно")
        return
    order_id = call.data.split(":", 2)[2]
    order = get_order(order_id)
    if not order:
        bot.answer_callback_query(call.id, "Заказ не найден")
        return

    bot.answer_callback_query(call.id, "Разбираю…")
    bot.send_chat_action(ADMIN_ID, "typing")
    card = client_card(order["chat_id"], exclude_order=order_id)
    result = AI.analyze_question(order["question"], order["format"], card)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ Теперь черновик",
                                      callback_data=f"ai:draft:{order_id}"))
    send_long(ADMIN_ID, result, f"🔍 РАЗБОР · {order['name']}\n\n", kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ai:draft:"))
def ai_draft(call):
    if not ai_guard(call):
        bot.answer_callback_query(call.id, "Недоступно")
        return
    order_id = call.data.split(":", 2)[2]
    order = get_order(order_id)
    if not order:
        bot.answer_callback_query(call.id, "Заказ не найден")
        return

    bot.answer_callback_query(call.id, "Пишу черновик, 20–40 сек…")
    bot.send_chat_action(ADMIN_ID, "typing")

    profile = get_client(order["chat_id"]) or {}
    card = client_card(order["chat_id"], exclude_order=order_id)
    draft = AI.draft_letter(
        order["question"], order["format"],
        card=card, notes=profile.get("notes", ""),
    )

    # черновик остаётся в состоянии, чтобы можно было взять как основу
    st = STATES.get(ADMIN_ID) or {}
    if st.get("order_id") == order_id:
        st["draft"] = draft
        STATES[ADMIN_ID] = st

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔁 Другой вариант",
                                      callback_data=f"ai:draft:{order_id}"))
    kb.add(types.InlineKeyboardButton("📋 Взять как основу",
                                      callback_data=f"ai:use:{order_id}"))
    send_long(
        ADMIN_ID, draft,
        f"✏️ ЧЕРНОВИК · {order['name']} · {order['format']}\n"
        f"{'─' * 25}\n\n", kb,
    )
    bot.send_message(
        ADMIN_ID,
        "⚠️ Это черновик для тебя. Клиенту ничего не ушло.\n"
        "Правь и присылай свой текст — он и уйдёт клиенту.",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("ai:use:"))
def ai_use_draft(call):
    """Отправляет черновик отдельным сообщением для копирования."""
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    order_id = call.data.split(":", 2)[2]
    st = STATES.get(ADMIN_ID) or {}
    draft = st.get("draft")
    if not draft or st.get("order_id") != order_id:
        bot.answer_callback_query(call.id, "Черновик потерялся, сделай новый")
        return
    bot.answer_callback_query(call.id, "Скопируй, поправь и пришли")
    bot.send_message(
        ADMIN_ID,
        "Ниже текст без разметки — скопируй, поправь и пришли обратно:",
    )
    for i in range(0, len(draft), 3800):
        bot.send_message(ADMIN_ID, draft[i:i + 3800])


@bot.message_handler(func=lambda m: admin_only(m) and m.text == "🤖 Помощник")
def ai_menu(message):
    if not ai_guard(message):
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📊 Сводка за 7 дней", callback_data="ai:digest:7"))
    kb.add(types.InlineKeyboardButton("📊 Сводка за 30 дней", callback_data="ai:digest:30"))
    bot.send_message(
        message.chat.id,
        "🤖 <b>Помощник</b>\n\n"
        "Где он работает:\n"
        "· кнопки «Разобрать вопрос» и «Черновик» при написании письма\n"
        "· метки для карточки клиента\n"
        "· черновик ответа в поддержку\n"
        "· сводка по заказам\n\n"
        "Письма клиентам он не отправляет — только готовит для тебя.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("ai:digest:"))
def ai_digest(call):
    if not ai_guard(call):
        bot.answer_callback_query(call.id, "Недоступно")
        return
    days = int(call.data.split(":", 2)[2])
    bot.answer_callback_query(call.id, "Считаю…")
    bot.send_chat_action(ADMIN_ID, "typing")

    since = now_msk() - timedelta(days=days)
    orders = []
    for o in all_orders():
        try:
            created = datetime.fromisoformat(o.get("created_at", ""))
            if created.tzinfo is None:
                created = MSK.localize(created)
            if created >= since:
                orders.append(o)
        except ValueError:
            continue

    paid = [o for o in orders if o.get("status") in ("paid", "done")]
    ratings = [o["rating"] for o in orders if o.get("rating")]
    by_client = {}
    for o in paid:
        by_client[o["chat_id"]] = by_client.get(o["chat_id"], 0) + 1

    stats = {
        "total": len(orders),
        "paid": len(paid),
        "done": len([o for o in orders if o.get("status") == "done"]),
        "pending": len([o for o in orders if o.get("status") == "pending"]),
        "revenue": sum(o.get("price_rub", 0) for o in paid),
        "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else "—",
        "repeat": len([c for c, n in by_client.items() if n > 1]),
    }
    questions = [o.get("question", "") for o in orders]

    result = AI.weekly_digest(stats, questions)
    send_long(ADMIN_ID, result, f"📊 СВОДКА ЗА {days} ДНЕЙ\n{'─' * 25}\n\n")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ai:tags:"))
def ai_tags(call):
    """Предлагает метки для карточки клиента."""
    if not ai_guard(call):
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    orders = [o for o in client_orders(target) if o.get("question")]
    if not orders:
        bot.answer_callback_query(call.id, "Нет заказов для анализа")
        return

    bot.answer_callback_query(call.id, "Смотрю историю…")
    bot.send_chat_action(ADMIN_ID, "typing")
    profile = get_client(target) or {}
    result = AI.suggest_tags([o["question"] for o in orders],
                             profile.get("notes", ""))
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🏷 Выставить метки",
                                      callback_data=f"card:tags:{target}"))
    send_long(ADMIN_ID, result,
              f"🏷 ПРЕДЛОЖЕНИЕ ДЛЯ {esc(profile.get('name', target))}\n\n", kb)


@bot.message_handler(commands=["reply"])
def admin_reply(message):
    if not admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        bot.send_message(message.chat.id, "Формат: /reply CHAT_ID текст")
        return
    try:
        target = int(parts[1])
    except ValueError:
        bot.send_message(message.chat.id, "chat_id должен быть числом.")
        return
    try:
        bot.send_message(target, f"💬 Ответ от Алисы:\n\n{parts[2]}")
        bot.send_message(message.chat.id, "✅ Отправлено.")
    except Exception as exc:
        bot.send_message(message.chat.id, f"❌ Ошибка: {exc}")


# ─────────────────────────────────────────────────────────────
# АДМИН: РАССЫЛКА
# ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: admin_only(m) and m.text == "📮 Рассылка")
def mailing_start(message):
    ensure_dirs()
    total = len([f for f in os.listdir(CLIENTS_DIR) if f.endswith(".json")])
    STATES[message.chat.id] = {"step": "mailing_text"}
    bot.send_message(
        message.chat.id,
        f"📮 Рассылка на {total} получателей.\n\n"
        "Пришли текст сообщения. /cancel — отмена.",
    )


@bot.message_handler(
    func=lambda m: admin_only(m)
    and STATES.get(m.chat.id, {}).get("step") == "mailing_text"
    and m.content_type == "text"
)
def mailing_preview(message):
    chat_id = message.chat.id
    STATES[chat_id] = {"step": "mailing_confirm", "text": message.text}
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📤 Отправить всем", callback_data="adm:send_mail"))
    kb.add(types.InlineKeyboardButton("❌ Отмена", callback_data="adm:cancel_mail"))
    bot.send_message(
        chat_id,
        f"Предпросмотр:\n\n{'─' * 20}\n{message.text}\n{'─' * 20}\n\nОтправляем?",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data == "adm:cancel_mail")
def mailing_cancel(call):
    STATES.pop(call.message.chat.id, None)
    safe_edit(call, "Рассылка отменена.")
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "adm:send_mail")
def mailing_send(call):
    chat_id = call.message.chat.id
    if chat_id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return

    state = STATES.pop(chat_id, {})
    text = state.get("text")
    if not text:
        safe_edit(call, "Текст потерялся. Начни заново.")
        bot.answer_callback_query(call.id)
        return

    ensure_dirs()
    ids = []
    for fname in os.listdir(CLIENTS_DIR):
        if fname.endswith(".json"):
            try:
                ids.append(int(fname[:-5]))
            except ValueError:
                continue

    safe_edit(call, f"📤 Отправляю {len(ids)} получателям…")
    bot.answer_callback_query(call.id)

    sent, failed = 0, 0
    for cid in ids:
        if cid == ADMIN_ID:
            continue
        try:
            bot.send_message(cid, text)
            sent += 1
        except Exception:
            failed += 1

    bot.send_message(
        chat_id,
        f"✅ Рассылка завершена\n\nДоставлено: {sent}\nНе доставлено: {failed}",
    )


# ─────────────────────────────────────────────────────────────
# АДМИН: ЭКСПОРТ
# ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: admin_only(m) and m.text == "📥 Экспорт")
def admin_export(message):
    chat_id = message.chat.id
    orders = all_orders()
    if not orders:
        bot.send_message(chat_id, "Заказов пока нет.")
        return

    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Заказы"
        ws.append([
            "ID", "Клиент", "@username", "Формат", "Цена, ₽",
            "Статус", "Оценка", "Создан", "Оплачен", "Отправлен", "Вопрос",
        ])
        for o in orders:
            ws.append([
                o.get("order_id"),
                o.get("name"),
                o.get("username"),
                o.get("format"),
                o.get("price_rub"),
                STATUS_LABEL.get(o.get("status"), ("", o.get("status", "")))[1],
                o.get("rating") or "",
                fmt_dt(o.get("created_at")),
                fmt_dt(o.get("paid_at")),
                fmt_dt(o.get("delivered_at")),
                (o.get("question") or "")[:500],
            ])
        for col, width in zip("ABCDEFGHIJK", [20, 20, 16, 12, 10, 16, 8, 16, 16, 16, 60]):
            ws.column_dimensions[col].width = width

        path = os.path.join(EXPORTS_DIR, f"orders_{now_msk().strftime('%Y%m%d_%H%M')}.xlsx")
        ensure_dirs()
        wb.save(path)
        with open(path, "rb") as f:
            bot.send_document(chat_id, f, caption=f"Выгрузка: {len(orders)} заказов")
    except ImportError:
        bot.send_message(chat_id, "Нужен openpyxl: добавь в requirements.txt.")
    except Exception as exc:
        log.error("export failed: %s", exc)
        bot.send_message(chat_id, f"Ошибка экспорта: {exc}")


# ─────────────────────────────────────────────────────────────
# FALLBACK
# ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: admin_only(m) and m.text == "🖥 Диск")
def admin_disk(message):
    writable, where = storage_check()
    ensure_dirs()
    orders_n = len([f for f in os.listdir(ORDERS_DIR) if f.endswith(".json")])
    clients_n = len([f for f in os.listdir(CLIENTS_DIR) if f.endswith(".json")])

    try:
        st = os.statvfs(DATA_DIR)
        total_gb = st.f_blocks * st.f_frsize / 1024 ** 3
        free_gb = st.f_bavail * st.f_frsize / 1024 ** 3
        space = f"Свободно: {free_gb:.2f} ГБ из {total_gb:.2f} ГБ"
    except (OSError, AttributeError):
        space = "Объём: неизвестно"

    persistent = bool(os.getenv("DATA_DIR"))
    state = read_json(os.path.join(DATA_DIR, "backup_state.json"), {})
    if BACKUP_EVERY_HOURS > 0:
        every = (f"каждые {BACKUP_EVERY_HOURS} ч"
                 if BACKUP_EVERY_HOURS < 48
                 else f"каждые {BACKUP_EVERY_HOURS // 24} дн")
        auto = f"🔄 Автобэкап: {every}\nПоследний: {fmt_dt(state.get('last_backup_at'))}"
    else:
        auto = "🔄 Автобэкап: выключен"

    bot.send_message(
        message.chat.id,
        "🖥 <b>Хранилище</b>\n\n"
        f"{'✅ Доступно на запись' if writable else '❌ НЕ доступно на запись'}\n"
        f"Путь: <code>{esc(where)}</code>\n"
        f"{space}\n\n"
        f"Файлов заказов: {orders_n}\n"
        f"Профилей клиентов: {clients_n}\n\n"
        f"{auto}\n\n"
        + ("Диск постоянный — данные переживут деплой."
           if persistent
           else "⚠️ DATA_DIR не задан: данные пропадут при перезапуске."),
        parse_mode="HTML",
    )


def build_backup():
    """Собирает zip со всеми JSON. Возвращает (путь, число файлов)."""
    import zipfile

    ensure_dirs()
    stamp = now_msk().strftime("%Y%m%d_%H%M")
    path = os.path.join(EXPORTS_DIR, f"backup_{stamp}.zip")
    count = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in (ORDERS_DIR, CLIENTS_DIR):
            for fname in sorted(os.listdir(folder)):
                if not fname.endswith(".json"):
                    continue
                z.write(
                    os.path.join(folder, fname),
                    os.path.join(os.path.basename(folder), fname),
                )
                count += 1
    return path, count


def send_backup(chat_id, note=""):
    """Отправляет бэкап в чат. Возвращает True при успехе."""
    path = None
    try:
        path, count = build_backup()
        size_kb = os.path.getsize(path) / 1024
        orders = all_orders()
        revenue = sum(
            o.get("price_rub", 0) for o in orders if o.get("status") in ("paid", "done")
        )
        with open(path, "rb") as f:
            bot.send_document(
                chat_id,
                f,
                caption=f"💾 Бэкап {now_msk().strftime('%d.%m.%Y %H:%M')} МСК{note}\n"
                        f"{count} файлов · {size_kb:.0f} КБ\n"
                        f"Заказов: {len(orders)} · выручка: {revenue}₽\n\n"
                        "Сохрани копию вне Render.",
            )
        return True
    except Exception as exc:
        log.error("backup failed: %s", exc)
        try:
            bot.send_message(chat_id, f"❌ Бэкап не собрался: {exc}")
        except Exception:
            pass
        return False
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


@bot.message_handler(func=lambda m: admin_only(m) and m.text == "💾 Бэкап")
def admin_backup(message):
    send_backup(message.chat.id)


# ─────────────────────────────────────────────────────────────
# АВТОБЭКАП ПО РАСПИСАНИЮ
# ─────────────────────────────────────────────────────────────

def backup_state_path():
    return os.path.join(DATA_DIR, "backup_state.json")


def autobackup_loop():
    """Фоновый цикл: раз в BACKUP_EVERY_HOURS отправляет бэкап админу."""
    import time

    if not ADMIN_ID or BACKUP_EVERY_HOURS <= 0:
        log.info("autobackup отключён")
        return

    log.info("autobackup включён: каждые %s ч", BACKUP_EVERY_HOURS)
    time.sleep(60)  # даём боту подняться

    while True:
        try:
            state = read_json(backup_state_path(), {})
            last_iso = state.get("last_backup_at")
            due = True
            if last_iso:
                try:
                    last = datetime.fromisoformat(last_iso)
                    if last.tzinfo is None:
                        last = MSK.localize(last)
                    due = now_msk() - last >= timedelta(hours=BACKUP_EVERY_HOURS)
                except ValueError:
                    due = True

            if due:
                if send_backup(ADMIN_ID, note=" · авто"):
                    write_json(
                        backup_state_path(),
                        {"last_backup_at": now_msk().isoformat()},
                    )
                    log.info("autobackup отправлен")
        except Exception as exc:
            log.error("autobackup loop error: %s", exc)

        time.sleep(1800)  # проверка каждые 30 минут


def start_autobackup():
    import threading

    t = threading.Thread(target=autobackup_loop, name="autobackup", daemon=True)
    t.start()


@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    chat_id = message.chat.id
    if chat_id == ADMIN_ID:
        bot.send_message(
            chat_id,
            "Не понял команду. Кнопки ниже, либо:\n"
            "/letter ID — написать письмо\n"
            "/reply CHAT_ID текст — ответить клиенту",
            reply_markup=kb_admin(),
        )
        return

    upsert_client(message.from_user)
    bot.send_message(
        chat_id,
        "Я отвечаю письмами, а не в переписке 🙂\n"
        "Чтобы задать вопрос Алисе — «💌 Заказать письмо».\n"
        "Нужна помощь — «❓ Помощь».",
        reply_markup=kb_client(),
    )


# ─────────────────────────────────────────────────────────────
# WEBHOOK / ЗАПУСК
# ─────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return "Alisa bot is running", 200


@app.route("/health", methods=["GET"])
def health():
    writable, where = storage_check()
    payload = {
        "status": "ok" if writable else "storage_error",
        "storage": where,
        "writable": writable,
        "persistent": bool(os.getenv("DATA_DIR")),
        "orders": len(all_orders()) if writable else 0,
        "time": now_msk().isoformat(),
    }
    return payload, (200 if writable else 503)


@app.route(f"/{TG_BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        update = Update.de_json(request.get_data().decode("utf-8"))
        bot.process_new_updates([update])
        return "", 200
    return "", 403


@app.route("/setwebhook", methods=["GET"])
def set_webhook():
    if not WEBHOOK_URL:
        return "WEBHOOK_URL не задан", 400
    bot.remove_webhook()
    ok = bot.set_webhook(url=f"{WEBHOOK_URL}/{TG_BOT_TOKEN}")
    return ("✅ Webhook установлен" if ok else "❌ Не удалось"), 200


if __name__ == "__main__":
    _ok, _where = storage_check()
    if _ok:
        log.info("storage ready: %s (persistent=%s)", _where, bool(os.getenv("DATA_DIR")))
    else:
        log.error("STORAGE NOT WRITABLE: %s", _where)
    start_autobackup()

    if WEBHOOK_URL:
        bot.remove_webhook()
        bot.set_webhook(url=f"{WEBHOOK_URL}/{TG_BOT_TOKEN}")
        log.info("webhook mode: %s", WEBHOOK_URL)
        app.run(host="0.0.0.0", port=PORT)
    else:
        log.info("polling mode (WEBHOOK_URL не задан)")
        bot.remove_webhook()
        bot.infinity_polling(skip_pending=True)
