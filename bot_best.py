"""
АЛИСА НЕВСКАЯ — Telegram-бот писем.
Версия BEST: примеры до оплаты, резюме перед платежом, рабочий кабинет,
оплата ЮKassa, админ-панель с фильтрами, рассылка, экспорт в Excel.
"""

import os
import hashlib
import html
import json
import logging
import re
import secrets
import sys
import threading
import time
import urllib.parse
from datetime import datetime, timedelta

import pytz
import requests
from dotenv import load_dotenv
from flask import Flask, request
from telebot import TeleBot, types
from telebot.types import LabeledPrice, Update

import calendar_reminders
import cardbase
import group_letters
import occasions as OCC
import partners
import postcards
import sources

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
# Тестовые аккаунты владельца (chat_id через запятую): оплата 0 ₽ без ЮKassa, вне статистики, финансов и рассылок.
# Задаётся только переменной окружения — в коде и в данных списка нет.
TEST_USERS = {int(x) for x in re.split(r"[,\s]+", os.getenv("TEST_USERS", "")) if x.strip().isdigit()}
WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or "").rstrip("/")
BOT_USERNAME = os.getenv("BOT_USERNAME", "alisanevskaya_letters_bot")
PORT = int(os.getenv("PORT", "5000"))
# Провайдер оплаты ЮKassa, подключённый боту через @BotFather -> Payments.
YOOKASSA_PROVIDER_TOKEN = os.getenv("YOOKASSA_PROVIDER_TOKEN", "")
# Оплата на странице ЮKassa (все способы магазина: СБП, T-Pay, SberPay, карты, ЮMoney).
# Если ключи магазина заданы — используется она, иначе встроенная оплата Telegram.
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID", "").strip()
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY", "").strip()
YOOKASSA_RECEIPT = os.getenv("YOOKASSA_RECEIPT", "").lower() in ("1", "true", "yes")  # чек 54-ФЗ
YOOKASSA_VAT_CODE = int(os.getenv("YOOKASSA_VAT_CODE", "1"))  # 1 = без НДС
YK_API = "https://api.yookassa.ru/v3/payments"


def yk_enabled():
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


if not TG_BOT_TOKEN:
    raise ValueError("TG_BOT_TOKEN не задан в окружении")
if not (YOOKASSA_PROVIDER_TOKEN or yk_enabled()):
    log.warning("ни YOOKASSA_SHOP_ID/SECRET_KEY, ни YOOKASSA_PROVIDER_TOKEN не заданы — оплата будет недоступна")

app = Flask(__name__)
bot = TeleBot(TG_BOT_TOKEN, threaded=False)

MSK = pytz.timezone("Europe/Moscow")

# DATA_DIR указывает на постоянный диск (Render Disk).
# Локально по умолчанию ./data, на Render — путь монтирования, напр. /var/data.
DATA_DIR = os.getenv("DATA_DIR", "data")
ORDERS_DIR = os.path.join(DATA_DIR, "orders")
CLIENTS_DIR = os.path.join(DATA_DIR, "clients")
ANKETAS_DIR = os.path.join(DATA_DIR, "anketas")
EXPORTS_DIR = os.path.join(DATA_DIR, "exports")
GIFTS_DIR = os.path.join(DATA_DIR, "gifts")

# сколько неоплаченных заказов можно держать одновременно
MAX_PENDING = 1
# интервал автобэкапа в часах, 0 — выключить
BACKUP_EVERY_HOURS = int(os.getenv("BACKUP_EVERY_HOURS", "168"))

LETTER_PRICE_RUB = 299
# сколько уточняющих вопросов задаём перед зеркалом и письмом
DIAG_QUESTIONS_TOTAL = 5

PAIN_META = {
    "breakup":    {"icon": "💔", "title": "Расставание"},
    "resentment": {"icon": "😔", "title": "Обида"},
    "loneliness": {"icon": "🌙", "title": "Одиночество"},
    "fear":       {"icon": "🌫", "title": "Страх"},
    "unspoken":   {"icon": "💭", "title": "Недосказанность"},
    "other":      {"icon": "✍️", "title": "Своя история"},
}

CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/alisanevskaya_diary")
FREE_PDF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "assets", "50_fraz_dlya_trudnyh_razgovorov.pdf")
# реальная статистика показывается только от этого числа настоящих оценок
MIN_RATINGS_TO_SHOW = 5
# более ранние заказы — тестовые от знакомых, в публичную статистику не идут
STATS_SINCE = "2026-09-29"

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
    for path in (DATA_DIR, ORDERS_DIR, CLIENTS_DIR, ANKETAS_DIR, EXPORTS_DIR, GIFTS_DIR):
        os.makedirs(path, exist_ok=True)


try:
    cardbase.init(DATA_DIR, postcards.BGS)
except Exception as _exc:  # открытки продолжат работать с первым фоном повода
    log.error("cardbase init: %s", _exc)


def cb_event(chat_id, name, occasion=None, card_ref=None):
    """Событие для метрик открыток; тестовые аккаунты и сбои базы игнорируем."""
    if is_test_user(chat_id):
        return
    try:
        cardbase.log_event(chat_id, name, occasion, card_ref)
    except Exception as exc:
        log.error("cardbase event %s: %s", name, exc)


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


def client_path(chat_id):
    return os.path.join(CLIENTS_DIR, f"{chat_id}.json")


def order_path(order_id):
    return os.path.join(ORDERS_DIR, f"{order_id}.json")


def anketa_path(anketa_id):
    return os.path.join(ANKETAS_DIR, f"{anketa_id}.json")


def save_anketa(anketa):
    write_json(anketa_path(anketa["anketa_id"]), anketa)


def get_anketa(anketa_id):
    return read_json(anketa_path(anketa_id), None)


def pain_meta(order_or_key):
    """Принимает order (dict) или сам ключ боли, возвращает {icon, title}."""
    key = order_or_key.get("pain") if isinstance(order_or_key, dict) else order_or_key
    if key in OCC.PRODUCTS:
        p = OCC.PRODUCTS[key]
        return {"icon": p["icon"], "title": p["title"]}
    if key == OCC.PACK["key"]:
        return {"icon": OCC.PACK["icon"], "title": OCC.PACK["title"]}
    if key == OCC.DOC["key"]:
        return {"icon": OCC.DOC["icon"], "title": OCC.DOC["title"]}
    return PAIN_META.get(key, {"icon": "•", "title": key or "—"})


def order_summary(order, limit=400):
    """Короткое превью ответов клиента — замена старому order['question']."""
    answers = order.get("answers") or []
    text = " / ".join(a for a in answers if a)
    return text if len(text) <= limit else text[:limit] + "…"


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
    profile = get_client(order["chat_id"])
    if "source" not in order and profile and profile.get("source"):
        order["source"] = profile["source"]  # источник первого захода клиента — в заказ (first touch)
    write_json(order_path(order["order_id"]), order)
    if profile is not None:
        if order["order_id"] not in profile.get("orders", []):
            profile.setdefault("orders", []).append(order["order_id"])
            write_json(client_path(order["chat_id"]), profile)


def get_order(order_id):
    return read_json(order_path(order_id), None)


def is_test_user(chat_id):
    return bool(chat_id) and chat_id in TEST_USERS


TEST_USERS_FILE = os.path.join(DATA_DIR, "test_users.json")  # список, который ведёт админ командами /test_add


def load_test_users():
    """Тестовые аккаунты: переменная TEST_USERS + файл в DATA_DIR (его меняет только админ в боте)."""
    try:
        TEST_USERS.update(int(x) for x in read_json(TEST_USERS_FILE, []) if str(x).lstrip("-").isdigit())
    except (TypeError, ValueError):
        pass


def save_test_users():
    write_json(TEST_USERS_FILE, sorted(TEST_USERS))


def find_client_by_username(username):
    username = (username or "").lstrip("@").lower()
    for fname in os.listdir(CLIENTS_DIR) if os.path.isdir(CLIENTS_DIR) else []:
        if fname.endswith(".json"):
            c = read_json(os.path.join(CLIENTS_DIR, fname), None) or {}
            if username and (c.get("username") or "").lower() == username:
                return c
    return None


@bot.message_handler(commands=["test_add", "test_off", "test_list"])
def cmd_test_users(message):
    if not admin_only(message):
        return
    parts = (message.text or "").split()
    cmd = parts[0].split("@")[0]
    if cmd == "/test_list":
        rows = []
        for cid in sorted(TEST_USERS):
            c = get_client(cid) or {}
            rows.append(f"• {cid} {('@' + c['username']) if c.get('username') else ''} {c.get('name', '')}".strip())
        bot.send_message(message.chat.id, "🧪 Тестовые аккаунты:\n" + ("\n".join(rows) or "пока нет"))
        return
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Формат: /test_add @username — аккаунт должен хотя бы раз нажать /start в боте.")
        return
    client = find_client_by_username(parts[1])
    if not client:
        bot.send_message(message.chat.id, "Такого ника нет среди клиентов. Пусть этот аккаунт сначала нажмёт /start в боте.")
        return
    if client["chat_id"] == ADMIN_ID:
        bot.send_message(message.chat.id, "Это аккаунт админа — для него тест не включаю.")
        return
    if cmd == "/test_add":
        TEST_USERS.add(client["chat_id"])
        save_test_users()
        bot.send_message(message.chat.id, f"🧪 Тестовый режим включён для @{client['username']}: оплата 0 ₽ без ЮKassa, "
                                          "вне статистики, рассылок и начислений партнёрам.")
    else:
        TEST_USERS.discard(client["chat_id"])
        save_test_users()
        bot.send_message(message.chat.id, f"Тестовый режим выключен для @{client['username']}.")


load_test_users()


def all_orders(include_test=False):
    """Тестовые заказы (is_test) по умолчанию скрыты — вся статистика, финансы и напоминания их не видят."""
    ensure_dirs()
    items = []
    for fname in os.listdir(ORDERS_DIR):
        if not fname.endswith(".json"):
            continue
        data = read_json(os.path.join(ORDERS_DIR, fname), None)
        if data and (include_test or not data.get("is_test")):
            items.append(data)
    items.sort(key=lambda o: o.get("created_at", ""), reverse=True)
    return items


def client_files():
    """Файлы профилей клиентов без тестовых аккаунтов — для статистики и рассылок."""
    ensure_dirs()
    return [f for f in os.listdir(CLIENTS_DIR)
            if f.endswith(".json") and not (f[:-5].isdigit() and int(f[:-5]) in TEST_USERS)]


def client_orders(chat_id):
    return [o for o in all_orders(include_test=True) if o.get("chat_id") == chat_id]


def new_order_id():
    # секунда + 3 случайные цифры: два заказа в одну секунду не затирают друг друга
    while True:
        order_id = f"ALI-{int(now_msk().timestamp())}{secrets.randbelow(900) + 100}"
        if not os.path.exists(order_path(order_id)):
            return order_id


def new_anketa_id():
    return f"ANK-{int(now_msk().timestamp() * 1000)}"


def pending_count(chat_id):
    return len([o for o in client_orders(chat_id) if o.get("status") == "pending"])


def real_stats_line():
    """Только настоящие цифры из заказов; пусто, пока данных мало."""
    per_client = {}
    for o in reversed(all_orders()):  # от старых к новым: остаётся последняя оценка
        if (o.get("rating") and o.get("chat_id") != ADMIN_ID
                and o.get("created_at", "") >= STATS_SINCE):
            per_client[o.get("chat_id")] = o["rating"]
    if len(per_client) < MIN_RATINGS_TO_SHOW:
        return ""
    ratings = list(per_client.values())
    avg = round(sum(ratings) / len(ratings), 1)
    return f"⭐ {avg} из 5 — оценки {len(ratings)} человек, получивших письмо"


# ─────────────────────────────────────────────────────────────
# КЛАВИАТУРЫ
# ─────────────────────────────────────────────────────────────

def kb_client():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("🎀 Письмо с открыткой", "💌 Заказать письмо")
    kb.add("👤 Мой кабинет", "📖 Примеры")
    kb.add("✨ Бесплатно", "📖 Дневник Алисы")
    kb.add("❓ Помощь", "👥 Пригласить друга")
    return kb


def kb_admin():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add("📊 Заказы", "📈 Статистика")
    kb.add("📮 Рассылка", "📥 Экспорт")
    kb.add("💾 Бэкап", "🖥 Диск")
    kb.add("🤝 Партнёры", "🎟 Промокоды")
    kb.add("🤖 Помощник")
    return kb


def kb_pains(prefix="pain"):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for key, meta in PAIN_META.items():
        kb.add(
            types.InlineKeyboardButton(
                f"{meta['icon']} {meta['title']}",
                callback_data=f"{prefix}:{key}",
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


def safe_send(chat_id, text, markup=None, parse_mode="HTML"):
    """Отправка с гарантией доставки кнопок.

    Если Telegram отклонил разметку (кривой HTML из имени или текста клиента),
    отправляем то же сообщение без parse_mode — сообщение и клавиатура
    доходят, теряется только форматирование.
    """
    try:
        return bot.send_message(chat_id, text, parse_mode=parse_mode,
                                reply_markup=markup)
    except Exception as exc:
        log.warning("send with %s failed (%s), retry as plain", parse_mode, exc)
        import re as _re
        plain = _re.sub(r"</?[a-zA-Z][^>]*>", "", text)
        try:
            return bot.send_message(chat_id, plain, reply_markup=markup)
        except Exception as exc2:
            log.error("plain send failed too: %s", exc2)
            return None


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

def sources_report():
    """Воронка по источникам для /stats: источник -> старты, превью, оплаты, выручка за 7 и 30 дней."""
    out = {}
    for d in (7, 30):
        rows, total = sources.report(d)
        out[str(d)] = {"total": total, "rows": rows}
    return out


SOURCE_PREFIXES = ("ig_", "vk_", "yt_", "pin_", "tg_", "ad_")   # соцсети, посевы: ig_<тема>, ad_<канал>
SOURCE_TOPICS = {"father": "family", "mother": "family", "mama": "family", "papa": "family",
                 "parents": "family", "denotca": "family", "ny": "newyear", "birthday": "birthday",
                 "love": "love", "thanks": "thanks", "sorry": "sorry"}


def source_label(param):
    """Метка источника по параметру /start. Первый заход (first touch) закрепляется в профиле."""
    if param.startswith("g_"):
        return "gift"
    if param.startswith("ref_"):
        return "ref"
    if param.startswith("grp_"):
        return "group"
    if param.startswith("w_"):
        return "web"
    if param.startswith("v_"):
        return "vk"
    if param.startswith("pinvite_"):
        return "partner_invite"
    return param[:40] if param.startswith("p_") else param.lower()[:40]


def source_topic_key(param):
    """Тема после префикса ig_/vk_/yt_/pin_/tg_ -> ключ повода или None (тогда просто меню)."""
    topic = param.split("_", 1)[1].lower() if "_" in param else ""
    for cand in (topic, topic.replace("-", "_").split("_")[0]):
        key = SOURCE_TOPICS.get(cand, cand)
        if key in OCC.PRODUCTS:
            return key
    return None


VKADS_ALIASES = {"father": "family"}  # vkads_father → письмо папе


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
    is_new = profile is not None and profile.get("created_at", "") >= (now_msk() - timedelta(minutes=1)).isoformat()
    if is_new and "source" not in profile:
        # первая точка входа для аналитики: pdf / occ / g_<код> / ref_<id> / direct
        profile["source"] = source_label(parts[1] if len(parts) == 2 else "direct")
        write_json(client_path(chat_id), profile)

    if chat_id == ADMIN_ID:
        bot.send_message(
            chat_id,
            "👨‍💼 Панель администратора\n\nВыбери раздел ниже.",
            reply_markup=kb_admin(),
        )
        return

    if len(parts) == 2 and parts[1].startswith("g_"):
        occ_gift_start(chat_id, parts[1][2:])
        return
    if len(parts) == 2 and parts[1].startswith("grp_"):  # участник общего письма: сразу к вопросам
        group_letters.join(chat_id, message.from_user, parts[1][4:])
        return
    if len(parts) == 2 and parts[1].startswith("pinvite_"):  # партнёр открывает свой кабинет
        partners.bind_invite(chat_id, parts[1][8:])
        return

    greet = "Привет!" if is_new else f"С возвращением, {esc(profile['name'].split()[0])}!"
    invited = ""
    partner_hello = None
    if len(parts) == 2 and parts[1].startswith("p_"):  # партнёрская ссылка: закрепляем по правилам
        try:
            partner = partners.touch(chat_id, parts[1][2:], is_new)
        except Exception as exc:
            partner = None
            log.error("partner touch: %s", exc)
        if partner and partners.quote(chat_id, 100)["source"] == f"partner:{partner['code']}":
            partner_hello = f"\n🎁 Для тебя скидка {partner['discount']}% на первое письмо — применится сама.\n"
    if referred_by and is_new:
        inviter = get_client(referred_by)
        if inviter:
            invited = (f"\nТебя пригласил(а) {esc(inviter['name'])}. 💌 "
                       f"На первое письмо у тебя скидка {partners.FRIEND_DISCOUNT}% — применится сама.\n")
    invited = invited or partner_hello or ""

    stats = real_stats_line()
    text = (
        f"💌 <b>Алиса Невская</b>\n\n"
        f"{greet} Я пишу письма о том, что человека держит.\n"
        f"Не советы — разговор.\n"
        f"{invited}\n"
        + (f"{stats}\n" if stats else "")
        + "Выбери внизу, с чего начать 🤍"
    )
    bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb_client())
    partners.offer_cabinet(chat_id)

    if len(parts) == 2 and parts[1] == "pdf":
        send_free_pdf(chat_id)
    elif len(parts) == 2 and parts[1] == "papa_pdf":
        occ_doc_order(chat_id, message.from_user)
    elif len(parts) == 2 and parts[1] == "occ":
        occ_open_catalog(chat_id)
    elif len(parts) == 2 and parts[1].startswith("occ_"):
        occ_open_product(chat_id, parts[1][4:])
    elif len(parts) == 2 and parts[1].startswith(("w_", "v_")):  # переход с сайта / из услуг VK
        key = parts[1][2:]
        if key in OCC.PRODUCTS:
            occ_open_product(chat_id, key)
        else:
            occ_open_catalog(chat_id)
    elif len(parts) == 2 and parts[1].startswith(SOURCE_PREFIXES):  # ig_/vk_/yt_/pin_/tg_<тема>, ad_<канал>
        key = None if parts[1].startswith("ad_") else source_topic_key(parts[1])
        if key:
            occ_open_product(chat_id, key)
        elif parts[1].startswith("ad_"):
            occ_open_catalog(chat_id)
    elif len(parts) == 2 and parts[1].startswith("vkads_"):  # реклама VK: vkads_<повод>, источник = весь параметр
        key = VKADS_ALIASES.get(parts[1][6:], parts[1][6:])
        if key in OCC.PRODUCTS:
            occ_open_product(chat_id, key)


# ─────────────────────────────────────────────────────────────
# ДИАГНОСТИКА: ШАГ 1 — ВЫБОР БОЛИ
# ─────────────────────────────────────────────────────────────

def ask_diagnostic_start(chat_id, gift_for=None):
    if pending_count(chat_id) >= MAX_PENDING:
        pending = pending_orders(chat_id)[0]
        bot.send_message(
            chat_id,
            "⏳ У тебя уже есть неоплаченное письмо — оно готово и ждёт.\n"
            f"Заказ <code>{pending['order_id']}</code>\n\n"
            "Можно оплатить его или отменить и начать новое.",
            parse_mode="HTML",
            reply_markup=pending_markup(pending["order_id"]),
        )
        return

    STATES[chat_id] = {"step": "choosing_pain", "gift_for": gift_for}
    prefix = "giftpain" if gift_for else "pain"
    bot.send_message(
        chat_id,
        "Что сейчас держит тебя крепче всего?" if not gift_for else
        f"Что держит {esc(gift_for)} крепче всего?",
        reply_markup=kb_pains(prefix=prefix),
    )


@bot.message_handler(func=lambda m: m.text == "💌 Заказать письмо")
def order_start(message):
    ask_diagnostic_start(message.chat.id)


def pain_title_for(state):
    if state.get("pain") == "other":
        return "своя история"
    return PAIN_META[state["pain"]]["title"]


def start_pain(chat_id, pain_key, gift_for=None):
    if pain_key not in PAIN_META:
        return
    is_other = pain_key == "other"
    anketa_id = new_anketa_id()
    STATES[chat_id] = {
        "step": "diag_other_text" if is_other else "diag_q",
        "anketa_id": anketa_id,
        "pain": pain_key,
        "answers": [],
        "history": [],
        "q_index": 0,
        "gift_for": gift_for,
    }
    save_anketa({
        "anketa_id": anketa_id,
        "chat_id": chat_id,
        "pain": pain_key,
        "answers": [],
        "mirror_text": None,
        "letter_text": None,
        "order_id": None,
        "created_at": now_msk().isoformat(),
        "updated_at": now_msk().isoformat(),
    })

    if is_other:
        bot.send_message(
            chat_id,
            "Опиши, что тебя тревожит — максимально подробно, своими словами.\n\n"
            "Что происходит? Что сейчас чувствуешь? Что хочешь понять или услышать "
            "в ответ — чем подробнее, тем точнее выйдет письмо.",
        )
        return

    ask_next_question(chat_id)


def ask_next_question(chat_id):
    """Задаёт очередной уточняющий вопрос (в пределах DIAG_QUESTIONS_TOTAL)."""
    state = STATES[chat_id]
    pain_title = pain_title_for(state)
    bot.send_chat_action(chat_id, "typing")
    question = AI.diagnostic_question(pain_title, state["history"]) if (AI and AI.available()) else \
        "Расскажи чуть больше — своими словами, как получится."
    if question.startswith("[ai]"):  # сбой ИИ — клиенту не показываем текст ошибки
        log.error("diagnostic question failed: %s", question)
        question = "Расскажи чуть больше — своими словами, как получится."
    state["q_index"] += 1
    state["current_q"] = question
    state["step"] = "diag_q"
    bot.send_message(chat_id, question)


def finish_diagnostics(chat_id, state):
    """Зеркало + письмо + paywall — после того как собраны все ответы."""
    state["step"] = "generating"
    pain_title = pain_title_for(state)
    answers = state["answers"]
    bot.send_chat_action(chat_id, "typing")

    if AI and AI.available():
        mirror = AI.mirror_reflection(pain_title, answers)
    else:
        mirror = "Сейчас сложно всё разложить по полочкам — и это тоже честно."
    if mirror.startswith("[ai]"):
        log.error("mirror failed: %s", mirror)
        mirror = "Сейчас сложно всё разложить по полочкам — и это тоже честно."
    bot.send_message(chat_id, mirror)
    state["mirror_text"] = mirror
    save_anketa_update(state, mirror_text=mirror)

    bot.send_chat_action(chat_id, "typing")
    if AI and AI.available():
        letter = AI.generate_letter(pain_title, answers, mirror, state.get("gift_for"))
    else:
        letter = "[ai] Помощник выключен — письмо не сгенерировано."
    if letter.startswith("[ai]"):  # сбой ИИ: не показываем paywall с текстом ошибки
        STATES.pop(chat_id, None)
        bot.send_message(chat_id, "Не получилось написать письмо прямо сейчас 😔 "
                                  "Попробуй через пару минут или напиши в «❓ Помощь».")
        log.error("letter generation failed: %s", letter)
        return
    state["letter_text"] = letter
    state["step"] = "paywall"
    save_anketa_update(state, letter_text=letter)

    state["chat_id"] = chat_id
    msg = bot.send_message(chat_id, diag_paywall_text(state), parse_mode="HTML",
                           reply_markup=diag_paywall_markup(state))
    state["paywall_msg"] = msg.message_id


def price_base(state):
    """Цена до скидок для письма в текущем состоянии диалога."""
    if state.get("price"):
        return state["price"]
    if state.get("product") in OCC.PRODUCTS:
        return OCC.PRODUCTS[state["product"]]["price"]
    return LETTER_PRICE_RUB


def diag_paywall_text(state):
    letter = state["letter_text"]
    preview = letter if len(letter) <= 175 else letter[:175] + "…"
    q = partners.quote(state["chat_id"], price_base(state), state.get("promo"))
    lines = partners.price_lines(q)
    return (f"{esc(preview)}\n\n🔒 Дальше — продолжение письма, целиком, у тебя в чате."
            + (f"\n\n{lines}" if lines else ""))


def diag_paywall_markup(state):
    q = partners.quote(state["chat_id"], price_base(state), state.get("promo"))
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        f"🔓 Получить письмо целиком — {partners.button_price(q)}", callback_data="letter:buy"
    ))
    kb.add(types.InlineKeyboardButton("🎟 Есть промокод", callback_data="pr:enter"))
    kb.add(types.InlineKeyboardButton("❌ Отменить", callback_data="letter:cancel"))
    return kb


def refresh_paywall(chat_id, state):
    """Перерисовывает превью после ввода промокода (для обоих сценариев)."""
    if state.get("product") in OCC.PRODUCTS:
        occ_refresh_text(chat_id, state)
        return
    try:
        bot.edit_message_text(diag_paywall_text(state), chat_id, state["paywall_msg"], parse_mode="HTML",
                              reply_markup=diag_paywall_markup(state))
    except Exception as exc:
        log.error("diag paywall refresh: %s", exc)


@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "diag_other_text"
    and m.content_type == "text"
)
def diag_receive_other_text(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if len(text) < 15:
        bot.send_message(
            chat_id,
            "Опиши чуть подробнее — хотя бы несколько предложений, чтобы я поняла контекст.",
        )
        return
    if len(text) > 2000:
        bot.send_message(chat_id, "Слишком длинно. Сократи до 2000 знаков, пожалуйста.")
        return

    state = STATES[chat_id]
    state["answers"] = [text]
    state["history"] = [("Что тебя тревожит?", text)]
    state["q_index"] = 1
    save_anketa_update(state, answers=[text])

    if state["q_index"] < DIAG_QUESTIONS_TOTAL:
        ask_next_question(chat_id)
    else:
        finish_diagnostics(chat_id, state)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pain:"))
def order_pain_chosen(call):
    chat_id = call.message.chat.id
    key = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id)
    start_pain(chat_id, key)


@bot.callback_query_handler(func=lambda c: c.data.startswith("giftpain:"))
def gift_pain_chosen(call):
    chat_id = call.message.chat.id
    key = call.data.split(":", 1)[1]
    state = STATES.get(chat_id) or {}
    gift_for = state.get("gift_for")
    bot.answer_callback_query(call.id)
    if not gift_for:
        bot.send_message(chat_id, "Начни заново: «🎁 Подарочное письмо».")
        return
    start_pain(chat_id, key, gift_for=gift_for)


# ─────────────────────────────────────────────────────────────
# ДИАГНОСТИКА: УТОЧНЯЮЩИЕ ВОПРОСЫ
# ─────────────────────────────────────────────────────────────

@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "diag_q"
    and m.content_type == "text"
)
def diag_receive_answer(message):
    chat_id = message.chat.id
    answer = (message.text or "").strip()
    if len(answer) < 2:
        bot.send_message(chat_id, "Скажи чуть подробнее — хотя бы пару слов.")
        return
    if len(answer) > 2000:
        bot.send_message(chat_id, "Слишком длинно. Сократи до 2000 знаков, пожалуйста.")
        return

    state = STATES[chat_id]
    state["answers"] = state["answers"] + [answer]
    state["history"] = state["history"] + [(state.get("current_q", ""), answer)]
    save_anketa_update(state, answers=state["answers"])

    if state["q_index"] < DIAG_QUESTIONS_TOTAL:
        ask_next_question(chat_id)
    else:
        finish_diagnostics(chat_id, state)


@bot.callback_query_handler(func=lambda c: c.data == "letter:cancel")
def letter_cancel(call):
    STATES.pop(call.message.chat.id, None)
    safe_edit(call, "Хорошо, остановились здесь. Если захочешь вернуться — жми «💌 Заказать письмо».")
    bot.answer_callback_query(call.id)


def save_anketa_update(state, **fields):
    anketa = get_anketa(state.get("anketa_id"))
    if not anketa:
        return
    anketa.update(fields)
    anketa["updated_at"] = now_msk().isoformat()
    save_anketa(anketa)


# ─────────────────────────────────────────────────────────────
# ОПЛАТА
# ─────────────────────────────────────────────────────────────

@bot.callback_query_handler(func=lambda c: c.data == "letter:buy")
def order_create(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    letter = state.get("letter_text")
    answers = state.get("answers")

    if not letter or not answers:
        safe_edit(call, "Заказ устарел. Начни заново: «💌 Заказать письмо».")
        bot.answer_callback_query(call.id)
        return

    prev = get_order(state["order_id"]) if state.get("order_id") else None
    if prev and prev.get("status") == "pending" and state.get("order_sig") == (letter, state.get("promo")):
        bot.answer_callback_query(call.id)
        send_order_invoice(chat_id, prev)  # повторный тап «Получить письмо»: тот же заказ, а не второй
        return

    profile = upsert_client(call.from_user)
    order_id = new_order_id()
    order = {
        "order_id": order_id,
        "chat_id": chat_id,
        "name": profile["name"],
        "username": profile.get("username", ""),
        "pain": state["pain"],
        "answers": answers,
        "mirror_text": state.get("mirror_text"),
        "letter_text": letter,
        "price_rub": LETTER_PRICE_RUB,
        "status": "pending",
        "is_gift": bool(state.get("gift_for")),
        "gift_for": state.get("gift_for"),
        "created_at": now_msk().isoformat(),
        "paid_at": None,
        "delivered_at": None,
        "email": None,
        "rating": None,
    }
    partners.apply_to_order(order, chat_id, state.get("promo"))
    save_order(order)
    save_anketa_update(state, order_id=order_id)
    state["order_id"], state["order_sig"] = order_id, (letter, state.get("promo"))
    bot.answer_callback_query(call.id)

    if order["price_rub"] <= 0:  # скидка и баланс покрыли всё — выдаём без оплаты
        notify_admin_new_order(order)
        fulfill_order(chat_id, order_id, "free", None)
    elif send_order_invoice(chat_id, order):
        notify_admin_new_order(order)


def order_description(order):
    order_id = order["order_id"]
    if order.get("product") == OCC.PACK["key"]:
        return f"3 письма с открыткой, без срока. Заказ {order_id}."
    if order.get("product") == OCC.DOC["key"]:
        return f"PDF «Разговор с папой», сразу после оплаты. Заказ {order_id}."
    if order.get("product"):
        return f"Письмо и открытка — сразу после оплаты. Заказ {order_id}."
    return f"Целиком, сразу после оплаты. Заказ {order_id}."


def send_order_invoice(chat_id, order):
    """Отправляет окно оплаты для заказа. Возвращает True при успехе."""
    order_id = order["order_id"]
    if is_test_user(chat_id):
        return send_test_payment(chat_id, order)
    if yk_enabled():
        return send_yk_payment(chat_id, order)
    if not YOOKASSA_PROVIDER_TOKEN:
        bot.send_message(
            chat_id,
            "⚠️ Оплата временно недоступна. Напиши в «❓ Помощь».",
        )
        log.error("order %s: YOOKASSA_PROVIDER_TOKEN не задан", order_id)
        return False
    try:
        bot.send_invoice(
            chat_id=chat_id,
            title=f"Письмо от Алисы · {pain_meta(order)['title']}"[:32],
            description=order_description(order),
            invoice_payload=order_id,
            provider_token=YOOKASSA_PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(label="Письмо", amount=order["price_rub"] * 100)],  # в копейках
            need_email=True,
            send_email_to_provider=True,  # ЮKassa требует чек 54-ФЗ
        )
        return True
    except Exception as exc:
        log.error("invoice error %s: %s", order_id, exc)
        bot.send_message(
            chat_id,
            "⚠️ Не смог открыть окно оплаты.\n"
            f"Номер заказа: <code>{order_id}</code>\n"
            "Напиши в «❓ Помощь» — разберёмся вручную.",
            parse_mode="HTML",
        )
        return False


# ── Тестовая оплата 0 ₽ (только chat_id из TEST_USERS) ────────
def send_test_payment(chat_id, order):
    """Вместо ЮKassa — кнопка «Оплатить 0 ₽». Заказ помечается is_test и выпадает из статистики."""
    order["is_test"] = True
    order.setdefault("test_price_rub", order.get("price_rub"))
    order["price_rub"] = 0
    save_order(order)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🧪 Оплатить 0 ₽ (тест)", callback_data=f"test:pay:{order['order_id']}"))
    bot.send_message(
        chat_id,
        f"🧪 <b>Тестовый режим</b> · заказ <code>{order['order_id']}</code>\n"
        f"Настоящая цена — {order['test_price_rub']}₽, сейчас платить не нужно.",
        parse_mode="HTML", reply_markup=kb,
    )
    return True


@bot.callback_query_handler(func=lambda c: c.data.startswith("test:pay:"))
def test_pay(call):
    order = get_order(call.data.split(":", 2)[2])
    chat_id = call.message.chat.id
    if (not is_test_user(call.from_user.id) or not order or not order.get("is_test")
            or order.get("chat_id") != chat_id or order.get("status") != "pending"):
        bot.answer_callback_query(call.id, "Недоступно")
        return
    bot.answer_callback_query(call.id, "Тестовая оплата ✅")
    fulfill_order(chat_id, order["order_id"], f"test-{secrets.token_hex(4)}", None)


# ── Оплата на странице ЮKassa ────────────────────────────────
YK_LOCK = threading.Lock()
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def yk_request(method, url, payload=None, idem=None):
    headers = {"Content-Type": "application/json"}
    if idem:
        headers["Idempotence-Key"] = idem
    r = requests.request(method, url, json=payload, headers=headers,
                         auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY), timeout=20)
    r.raise_for_status()
    return r.json()


def yk_got_email(message, order_id):
    chat_id = message.chat.id
    email = (message.text or "").strip()
    order = get_order(order_id)
    if not order or order.get("status") != "pending":
        return
    if not EMAIL_RE.match(email):
        msg = bot.send_message(chat_id, "Не похоже на почту 🙈 Напиши, пожалуйста, в виде name@mail.ru")
        bot.register_next_step_handler(msg, yk_got_email, order_id)
        return
    profile = get_client(chat_id)
    if profile is not None:
        profile["email"] = email
        write_json(client_path(chat_id), profile)
    send_yk_payment(chat_id, order, email=email)


def send_yk_payment(chat_id, order, email=None):
    """Создаёт платёж ЮKassa и присылает кнопку на страницу оплаты."""
    order_id = order["order_id"]
    if YOOKASSA_RECEIPT and not email:
        email = order.get("email") or (get_client(chat_id) or {}).get("email")
        if not email:
            msg = bot.send_message(chat_id, "📧 Куда прислать чек? Напиши почту одним сообщением.")
            bot.register_next_step_handler(msg, yk_got_email, order_id)
            return True
    try:
        url = yk_create(order, email, f"https://t.me/{BOT_USERNAME}")
    except Exception as exc:
        log.error("yookassa create error %s: %s", order_id, exc)
        bot.send_message(
            chat_id,
            "⚠️ Не смог открыть страницу оплаты.\n"
            f"Номер заказа: <code>{order_id}</code>\n"
            "Попробуй ещё раз через минуту или напиши в «❓ Помощь».",
            parse_mode="HTML",
        )
        return False

    if url is None:  # заказ уже оплачен и выдан
        return True
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"💳 Оплатить {order['price_rub']}₽", url=url))
    kb.add(types.InlineKeyboardButton("✅ Я оплатил(а)", callback_data=f"yk:check:{order_id}"))
    bot.send_message(
        chat_id,
        f"Заказ <code>{order_id}</code> · {order['price_rub']}₽\n\n"
        "Оплата — на защищённой странице ЮKassa: СБП, T-Pay, SberPay, карта или ЮMoney.\n"
        "После оплаты вернись сюда — письмо придёт само в течение минуты 🤍",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return True


def yk_create(order, email, return_url):
    """Создаёт платёж ЮKassa для заказа (Telegram или VK) и возвращает ссылку на оплату."""
    order_id = order["order_id"]
    price = f"{order['price_rub']}.00"
    if order.get("yk_payment_id"):  # повторное «Оплатить»: не плодим платежи, иначе оплата по старой ссылке потеряется
        prev = yk_request("GET", f"{YK_API}/{order['yk_payment_id']}")
        if prev.get("status") == "succeeded" and prev.get("paid"):
            yk_check_order(order_id)  # уже оплачен — выдаём, новая ссылка не нужна
            return None
        url = (prev.get("confirmation") or {}).get("confirmation_url")
        if prev.get("status") == "pending" and url and (prev.get("amount") or {}).get("value") == price:
            return url
    payload = {
        "amount": {"value": price, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": order_description(order)[:128],
        "metadata": {"order_id": order_id, "chat_id": str(order["chat_id"])},
    }
    if YOOKASSA_RECEIPT:
        payload["receipt"] = {
            "customer": {"email": email},
            "items": [{
                "description": f"Письмо от Алисы · {pain_meta(order)['title']}"[:128],
                "quantity": "1.00",
                "amount": {"value": price, "currency": "RUB"},
                "vat_code": YOOKASSA_VAT_CODE,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }],
        }
    payment = yk_request("POST", YK_API, payload, idem=f"{order_id}-{secrets.token_hex(6)}")
    order["yk_payment_id"] = payment["id"]
    order["yk_created_at"] = now_msk().isoformat()
    if email:
        order["email"] = email
    save_order(order)
    return payment["confirmation"]["confirmation_url"]


def yk_check_order(order_id):
    """Сверяет платёж с ЮKassa; при успехе выдаёт заказ. Возвращает статус платежа."""
    with YK_LOCK:
        order = get_order(order_id)
        if not order or not order.get("yk_payment_id"):
            return None
        if order.get("status") not in ("pending", "cancelled"):  # отменённый заказ тоже сверяем: страница оплаты могла быть открыта
            return "succeeded"
        payment = yk_request("GET", f"{YK_API}/{order['yk_payment_id']}")
        status = payment.get("status")
        paid_ok = (
            status == "succeeded" and payment.get("paid")
            and payment.get("amount", {}).get("value") == f"{order['price_rub']}.00"
        )
        if paid_ok:
            fulfill_order(order["chat_id"], order_id, payment["id"], order.get("email"))
        elif status == "canceled":
            order.pop("yk_payment_id", None)
            save_order(order)
        return status


@bot.callback_query_handler(func=lambda c: c.data.startswith("yk:check:"))
def yk_check_button(call):
    order_id = call.data.split(":", 2)[2]
    order = get_order(order_id)
    if not order or order.get("chat_id") != call.message.chat.id:
        bot.answer_callback_query(call.id, "Заказ не найден")
        return
    try:
        status = yk_check_order(order_id)
    except Exception as exc:
        log.error("yookassa check error %s: %s", order_id, exc)
        status = None
    if status == "succeeded":
        bot.answer_callback_query(call.id, "Оплата получена ✅")
    elif status == "canceled":
        bot.answer_callback_query(call.id)
        bot.send_message(call.message.chat.id, "Платёж не прошёл. Можно попробовать ещё раз:",
                         reply_markup=pending_markup(order_id))
    else:
        bot.answer_callback_query(call.id, "Оплата пока не поступила. Если уже оплатил(а) — подожди минуту.",
                                  show_alert=True)


def yk_poll_loop():
    """Каждые 15 с проверяет неоплаченные заказы с платежом ЮKassa (до 24 ч)."""
    while True:
        try:
            for o in all_orders():
                if o.get("status") not in ("pending", "cancelled") or not o.get("yk_payment_id"):
                    continue
                created = datetime.fromisoformat(o.get("yk_created_at") or now_msk().isoformat())
                if now_msk() - created > timedelta(hours=24):
                    o.pop("yk_payment_id", None)
                    save_order(o)
                    continue
                try:
                    yk_check_order(o["order_id"])
                except Exception as exc:
                    log.error("yookassa poll %s: %s", o["order_id"], exc)
        except Exception as exc:
            log.error("yookassa poll loop error: %s", exc)
        time.sleep(15)


# ── Одно мягкое напоминание тем, кто дошёл до превью или оплаты и не забрал письмо ──
REMIND_AFTER = timedelta(hours=2)
REMIND_WINDOW = timedelta(hours=26)       # позже не пишем: VK разрешает ответ в течение суток
REMINDERS_SINCE = "2026-10-04T15:00"      # заказы до запуска напоминаний не трогаем


def reminder_due(created, now):
    return REMIND_AFTER <= now - created <= REMIND_WINDOW


def send_reminders(now=None):
    now = now or now_msk()
    if not 10 <= now.hour < 21:
        return
    for o in all_orders():
        if (o.get("status") != "pending" or o.get("reminded_at") or o.get("product") in (OCC.PACK["key"], OCC.DOC["key"])
                or o.get("created_at", "") < REMINDERS_SINCE):
            continue
        try:
            if not reminder_due(datetime.fromisoformat(o["created_at"]), now):
                continue
        except (KeyError, ValueError):
            continue
        o["reminded_at"] = now.isoformat()
        save_order(o)
        try:
            if o.get("channel") == "vk":
                import vk_shop
                vk_shop.remind_order(o)
            else:
                who = f" для {esc(o['gift_for'])}" if o.get("gift_for") else ""
                bot.send_message(o["chat_id"],
                                 f"💌 Твоё письмо{who} готово и ждёт. Открытка уже собрана — "
                                 "забрать можно в один клик. Если передумал(а) — просто отмени, я не обижусь 🤍",
                                 parse_mode="HTML", reply_markup=pending_markup(o["order_id"]))
        except Exception as exc:
            log.error("reminder %s: %s", o["order_id"], exc)
    for chat_id, st in list(STATES.items()):
        at = st.get("paywall_at")
        if st.get("step") == "occ_paywall" and at and not st.get("reminded") and reminder_due(at, now):
            st["reminded"] = True
            p = OCC.PRODUCTS.get(st.get("product"), {})
            kb = types.InlineKeyboardMarkup(row_width=1)
            kb.add(types.InlineKeyboardButton(f"🔓 Забрать письмо — {st.get('price') or p.get('price', '')}₽",
                                              callback_data="occ:buy"))
            kb.add(types.InlineKeyboardButton("❌ Не нужно", callback_data="letter:cancel"))
            try:
                bot.send_message(chat_id, f"💌 Письмо для {esc(st.get('name') or 'близкого человека')} ещё ждёт тебя — "
                                          "я сохранила его и открытку.", parse_mode="HTML", reply_markup=kb)
            except Exception as exc:
                log.error("preview reminder %s: %s", chat_id, exc)
    try:
        import vk_shop
        vk_shop.remind_previews(now)
    except Exception as exc:
        log.error("vk preview reminders: %s", exc)


def reminder_loop():
    while True:
        try:
            send_reminders()
        except Exception as exc:
            log.error("reminder loop: %s", exc)
        time.sleep(600)


def start_yk_poller():
    if yk_enabled():
        threading.Thread(target=yk_poll_loop, name="yk_poll", daemon=True).start()
        log.info("оплата: страница ЮKassa")


def pending_orders(chat_id):
    return [o for o in client_orders(chat_id) if o.get("status") == "pending"]


def pending_markup(order_id):
    order = get_order(order_id) or {}
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        f"💳 Оплатить {order.get('price_rub', LETTER_PRICE_RUB)}₽", callback_data=f"cab:pay:{order_id}"
    ))
    kb.add(types.InlineKeyboardButton(
        "🗑 Отменить и начать заново", callback_data=f"cab:cancel:{order_id}"
    ))
    return kb


@bot.callback_query_handler(func=lambda c: c.data.startswith("cab:pay:"))
def cabinet_pay(call):
    chat_id = call.message.chat.id
    order = get_order(call.data.split(":", 2)[2])
    if not order or order.get("chat_id") != chat_id or order.get("status") != "pending":
        bot.answer_callback_query(call.id, "Заказ уже недоступен")
        return
    bot.answer_callback_query(call.id)
    send_order_invoice(chat_id, order)


@bot.callback_query_handler(func=lambda c: c.data.startswith("cab:cancel:"))
def cabinet_cancel(call):
    chat_id = call.message.chat.id
    order = get_order(call.data.split(":", 2)[2])
    if not order or order.get("chat_id") != chat_id or order.get("status") != "pending":
        bot.answer_callback_query(call.id, "Заказ уже недоступен")
        return
    order["status"] = "cancelled"
    save_order(order)
    if order.get("group_id"):
        group_letters.reopen(order["group_id"], order["order_id"])
    bot.answer_callback_query(call.id, "Заказ отменён")
    ask_diagnostic_start(chat_id)


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
    payment = message.successful_payment
    fulfill_order(
        message.chat.id,
        payment.invoice_payload,
        payment.telegram_payment_charge_id,
        payment.order_info.email if payment.order_info else None,
    )


def fulfill_order(chat_id, order_id, charge_id, email):
    """Отмечает заказ оплаченным и выдаёт результат (общий путь для Telegram и ЮKassa)."""
    order = get_order(order_id)

    if not order:
        bot.send_message(
            chat_id,
            "Платёж получен, но заказ не найден. Напиши в «❓ Помощь», "
            f"укажи номер: <code>{order_id}</code>",
            parse_mode="HTML",
        )
        return

    if order.get("status") == "done":  # повторная доставка апдейта об оплате (таймаут вебхука) — не выдаём второй раз
        log.warning("fulfill_order: %s уже выдан, повтор пропущен", order_id)
        return

    order["status"] = "done"
    order["paid_at"] = now_msk().isoformat()
    order["delivered_at"] = now_msk().isoformat()
    order["charge_id"] = charge_id
    order["email"] = email or order.get("email")
    save_order(order)
    STATES.pop(chat_id, None)
    try:  # закрепление партнёра, начисление, промокод, баланс, бонус другу — сбой не должен мешать выдаче
        partners.on_paid(order)
    except Exception as exc:
        log.error("partners.on_paid %s: %s", order_id, exc)

    if order.get("channel") == "vk":
        import vk_shop
        try:
            vk_shop.on_paid(order)
        except Exception as exc:
            log.error("vk deliver %s: %s", order_id, exc)
        notify_admin_paid(order)
        return

    profile = get_client(chat_id)
    if profile:
        profile["total_spent_rub"] = profile.get("total_spent_rub", 0) + order["price_rub"]
        write_json(client_path(chat_id), profile)

    if order.get("product") == OCC.DOC["key"]:
        kb = types.InlineKeyboardMarkup(row_width=1)
        kb.add(types.InlineKeyboardButton("🎀 Письмо папе с открыткой", callback_data="occ:p:family"))
        try:
            with open(os.path.join(os.path.dirname(FREE_PDF_PATH), OCC.DOC["file"]), "rb") as f:
                bot.send_document(chat_id, f, caption="✅ Оплата прошла. Твой «Разговор с папой» 🤍\nНачни с одной фразы. Если захочется письма — я помогу собрать его в открытку.",
                                  reply_markup=kb)
        except OSError as exc:
            log.error("doc not sent %s: %s", order_id, exc)
            bot.send_message(chat_id, "Оплата прошла, но файл не отправился. Напиши в «❓ Помощь», пришлю вручную. "
                                      f"Номер: <code>{order_id}</code>", parse_mode="HTML")
        notify_admin_paid(order)
        return

    if order.get("product") == OCC.PACK["key"]:
        profile = get_client(chat_id) or {}
        profile["credits"] = profile.get("credits", 0) + OCC.PACK["credits"]
        write_json(client_path(chat_id), profile)
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🎀 Написать первое письмо", callback_data="occ:catalog"))
        bot.send_message(chat_id, f"✅ Оплата прошла. В твоём наборе: {profile['credits']} письма с открыткой 🎁\n"
                                  "Выбирай повод — оплачивать больше не нужно.", reply_markup=kb)
        notify_admin_paid(order)
        return

    if order.get("product") in OCC.PRODUCTS:
        bot.send_message(chat_id, f"✅ Оплата прошла · заказ <code>{order_id}</code>\n"
                                  + (f"Чек придёт на {esc(order['email'])}" if order.get("email") else ""),
                         parse_mode="HTML")
        occ_deliver(chat_id, order)
        notify_admin_paid(order)
        return

    meta = pain_meta(order)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("👤 Открыть кабинет", callback_data="cab:home"))
    kb.add(partners.friend_gift_markup(chat_id))

    receipt_line = (
        f"Чек придёт на {esc(order['email'])}\n\n" if order.get("email") else ""
    )
    bot.send_message(
        chat_id,
        "✅ <b>Оплата прошла</b>\n\n"
        f"Номер заказа: <code>{order_id}</code>\n"
        f"{meta['icon']} {meta['title']}\n"
        f"Оплачено: {order['price_rub']}₽\n\n"
        f"{receipt_line}"
        "Письмо — следующим сообщением.",
        parse_mode="HTML",
        reply_markup=kb,
    )

    letter = order["letter_text"]
    rate_kb = types.InlineKeyboardMarkup(row_width=5)
    rate_kb.row(
        *[
            types.InlineKeyboardButton("⭐" * i, callback_data=f"rate:{order_id}:{i}")
            for i in range(1, 6)
        ]
    )
    safe_letter = esc(letter)
    if len(safe_letter) <= 3900:
        bot.send_message(chat_id, safe_letter, parse_mode="HTML", reply_markup=rate_kb)
    else:
        for i in range(0, len(letter), 3800):
            chunk = letter[i:i + 3800]
            is_last = i + 3800 >= len(letter)
            bot.send_message(
                chat_id, esc(chunk), parse_mode="HTML",
                reply_markup=rate_kb if is_last else None,
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
    if profile.get("credits"):
        lines.append(f"🎁 Писем с открыткой в наборе: {profile['credits']}")
    lines.append("")

    if not orders:
        lines.append("Заказов пока нет. Первое письмо начинается с вопроса.")
    else:
        lines.append("<b>Мои заказы</b>")
        for o in orders[:10]:
            icon, label = STATUS_LABEL.get(o.get("status"), ("•", o.get("status", "")))
            meta = pain_meta(o)
            short = esc(order_summary(o, limit=40))
            lines.append(
                f"\n{icon} {meta['icon']} {meta['title']} — {label}\n"
                f"   «{short}»\n"
                f"   {fmt_dt(o.get('created_at'))} · <code>{o['order_id']}</code>"
            )

    kb = types.InlineKeyboardMarkup(row_width=1)
    for o in [o for o in orders if o.get("status") == "pending"][:3]:
        kb.add(types.InlineKeyboardButton(
            f"💳 Оплатить {o['order_id']}", callback_data=f"cab:pay:{o['order_id']}"
        ))
        kb.add(types.InlineKeyboardButton(
            f"🗑 Отменить {o['order_id']}", callback_data=f"cab:cancel:{o['order_id']}"
        ))
    for o in [o for o in done if o.get("product") != OCC.PACK["key"]][:5]:
        meta = pain_meta(o)
        kb.add(
            types.InlineKeyboardButton(
                f"📖 Читать: {meta['title']} · {fmt_dt(o.get('delivered_at'))}",
                callback_data=f"cab:read:{o['order_id']}",
            )
        )
    kb.add(types.InlineKeyboardButton("💌 Новое письмо", callback_data="cab:new"))
    kb.add(types.InlineKeyboardButton("🎀 Письмо с открыткой", callback_data="occ:catalog"))
    kb.add(types.InlineKeyboardButton("🔔 Напоминания о датах", callback_data="cal:menu"))
    return "\n".join(lines), kb


@bot.message_handler(func=lambda m: m.text == "👤 Мой кабинет")
def cabinet_open(message):
    text, kb = cabinet_view(message.chat.id)
    safe_send(message.chat.id, text, markup=kb)


@bot.callback_query_handler(func=lambda c: c.data == "cab:home")
def cabinet_home(call):
    text, kb = cabinet_view(call.message.chat.id)
    safe_edit(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "cab:new")
def cabinet_new_order(call):
    bot.answer_callback_query(call.id)
    ask_diagnostic_start(call.message.chat.id)


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

    meta = pain_meta(order)
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
    kb.add(types.InlineKeyboardButton("🎁 Отвечу за друга", callback_data="gift:self"))
    kb.add(types.InlineKeyboardButton("✍️ Пусть пройдёт сам", callback_data="gift:link"))
    kb.add(types.InlineKeyboardButton("↩️ Назад", callback_data="cab:home"))
    return kb


GIFT_TEXT = (
    "🎁 <b>Подарочное письмо</b>\n\n"
    "Два способа:\n\n"
    "🎁 <b>Отвечу за друга</b> — ты проходишь короткий разговор за него "
    "и оплачиваешь. Письмо адресовано ему.\n\n"
    "✍️ <b>Пусть пройдёт сам</b> — отправляешь ссылку, друг заказывает "
    "сам. Тебе 50₽ бонуса."
)


@bot.message_handler(func=lambda m: m.text == "🎁 Подарочное письмо")
def gift_open(message):
    occ_open_catalog(message.chat.id)


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
        f"У друга — скидка {partners.FRIEND_DISCOUNT}% на первое письмо. Когда он оплатит, тебе — "
        f"{partners.FRIEND_BONUS_RUB}₽ на баланс: они спишутся в твоём следующем заказе.\n\n"
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
        "🎁 <b>Подарок</b>\n\n"
        "Кому подарок? Напиши только имя — так, как ты его называешь.\n"
        "Номер и логин не нужны: письмо ты отправишь сам в один тап.",
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
        bot.send_message(chat_id, "Слишком коротко. Напиши имя.")
        return

    bot.send_message(chat_id, f"🎁 Подарок для: {esc(contact)}", parse_mode="HTML")
    ask_diagnostic_start(chat_id, gift_for=contact)


# ─────────────────────────────────────────────────────────────
# ПИСЬМА С ОТКРЫТКОЙ (поводы) — каталог, анкета, превью, конверт
# ─────────────────────────────────────────────────────────────

OCC_BUTTON = "🎀 Письмо с открыткой"


def gift_path(code):
    return os.path.join(GIFTS_DIR, f"{code}.json")


def occ_catalog_markup():
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(f"{p['icon']} {p['title']} · {p['price']}₽",
                                   callback_data=f"occ:p:{key}")
        for key in OCC.CATALOG_ORDER for p in [OCC.PRODUCTS[key]]
    ]
    for i in range(0, len(buttons), 2):
        kb.row(*buttons[i:i + 2])
    kb.add(types.InlineKeyboardButton(
        f"{OCC.PACK['icon']} {OCC.PACK['title']} · {OCC.PACK['price']}₽", callback_data="occ:pack"))
    kb.add(types.InlineKeyboardButton(
        f"{OCC.DOC['icon']} {OCC.DOC['title']} · {OCC.DOC['price']}₽", callback_data="occ:doc"))
    kb.add(types.InlineKeyboardButton(
        f"👥 Письмо от всех нас · {group_letters.GROUP_PRICE}₽", callback_data="grp:new"))
    kb.add(types.InlineKeyboardButton(
        f"💭 Глубокое письмо о том, что держит · {LETTER_PRICE_RUB}₽", callback_data="gift:menu"))
    return kb


def occ_catalog_text(chat_id):
    credits = (get_client(chat_id) or {}).get("credits", 0)
    credit_line = f"\n🎁 У тебя в наборе: <b>{credits}</b> — любое письмо без оплаты.\n" if credits else ""
    return (
        "🎀 <b>Письма с открыткой</b>\n\n"
        "Ты рассказываешь пару живых деталей — я пишу письмо, которое звучит как ты "
        "в свой лучший момент, и оформляю открытку с именем.\n\n"
        "✉️ Получатель открывает его по ссылке-конверту — как настоящий подарок. "
        "А ты узнаешь, когда письмо вскроют.\n\n"
        "Начало письма и открытку-превью видно до оплаты.\n"
        f"{credit_line}\n"
        "Кому пишем?"
    )


def occ_open_catalog(chat_id):
    STATES.pop(chat_id, None)
    bot.send_message(chat_id, occ_catalog_text(chat_id), parse_mode="HTML",
                     reply_markup=occ_catalog_markup())


@bot.message_handler(func=lambda m: m.text == OCC_BUTTON)
def occ_catalog_msg(message):
    occ_open_catalog(message.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "occ:catalog")
def occ_catalog_cb(call):
    bot.answer_callback_query(call.id)
    safe_edit(call, occ_catalog_text(call.message.chat.id), occ_catalog_markup())


def occ_product_text(key):
    p = OCC.PRODUCTS[key]
    return (
        f"{p['icon']} <b>{p['title']}</b> — {p['price']}₽\n\n"
        f"{p['pitch']}\n\n"
        "<b>Что внутри:</b>\n"
        "✍️ письмо по твоим деталям — не шаблон\n"
        "🖼 открытка с именем и твоей подписью\n"
        "✉️ ссылка-конверт: получатель «вскрывает» письмо, ты узнаёшь об этом\n\n"
        f"Несколько коротких вопросов — минуты 3. Начало письма и превью открытки — бесплатно."
    )


def occ_open_product(chat_id, key, call=None):
    if key not in OCC.PRODUCTS:
        return occ_open_catalog(chat_id)
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("✍️ Начать", callback_data=f"occ:go:{key}"))
    kb.add(types.InlineKeyboardButton("↩️ Все письма", callback_data="occ:catalog"))
    if call:
        safe_edit(call, occ_product_text(key), kb)
    else:
        bot.send_message(chat_id, occ_product_text(key), parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("occ:p:"))
def occ_product_cb(call):
    bot.answer_callback_query(call.id)
    occ_open_product(call.message.chat.id, call.data.split(":", 2)[2], call)


def occ_questions(key):
    p = OCC.PRODUCTS[key]
    qs = []
    for qkey, text in p["questions"]:
        if qkey == "tone" and p.get("tone"):
            continue
        if qkey == "sign" and p.get("sign"):
            continue
        qs.append((qkey, text))
    return qs


@bot.callback_query_handler(func=lambda c: c.data.startswith("occ:go:"))
def occ_go(call):
    chat_id = call.message.chat.id
    key = call.data.split(":", 2)[2]
    bot.answer_callback_query(call.id)
    if key not in OCC.PRODUCTS:
        return occ_open_catalog(chat_id)
    if pending_count(chat_id) >= MAX_PENDING:
        pending = pending_orders(chat_id)[0]
        bot.send_message(
            chat_id,
            "⏳ У тебя уже есть неоплаченное письмо — оно готово и ждёт.\n"
            f"Заказ <code>{pending['order_id']}</code>\n\nМожно оплатить его или отменить.",
            parse_mode="HTML", reply_markup=pending_markup(pending["order_id"]))
        return
    STATES[chat_id] = {"step": "occ_q", "product": key, "idx": 0, "qa": [], "data": {}}
    p = OCC.PRODUCTS[key]
    bot.send_message(chat_id, f"{p['icon']} Пишем: <b>{p['title']}</b>. Поехали 🤍",
                     parse_mode="HTML")
    occ_ask(chat_id)


def occ_ask(chat_id):
    state = STATES[chat_id]
    qs = occ_questions(state["product"])
    if state["idx"] >= len(qs):
        return occ_finish(chat_id)
    qkey, text = qs[state["idx"]]
    state["qkey"], state["qtext"] = qkey, text
    if qkey == "tone":
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(*[types.InlineKeyboardButton(label, callback_data=f"occ:tone:{t}")
                 for t, label in OCC.TONES.items()])
        bot.send_message(chat_id, text, reply_markup=kb)
    else:
        bot.send_message(chat_id, text)


def occ_store(chat_id, value):
    state = STATES[chat_id]
    state["data"][state["qkey"]] = value
    if state["qkey"] != "tone":
        state["qa"].append((state["qtext"], value))
    state["idx"] += 1
    occ_ask(chat_id)


@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "occ_q" and m.content_type == "text"
)
def occ_answer(message):
    chat_id = message.chat.id
    state = STATES[chat_id]
    text = (message.text or "").strip()
    if state.get("qkey") == "tone":
        bot.send_message(chat_id, "Выбери тон кнопкой выше 👆")
        return
    if len(text) < 1:
        bot.send_message(chat_id, "Напиши хотя бы пару слов 🙂")
        return
    if len(text) > 1500:
        bot.send_message(chat_id, "Слишком длинно — сократи до 1500 знаков, пожалуйста.")
        return
    occ_store(chat_id, text)


@bot.callback_query_handler(func=lambda c: c.data.startswith("occ:tone:"))
def occ_tone(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    bot.answer_callback_query(call.id)
    if state.get("step") != "occ_q" or state.get("qkey") != "tone":
        return
    tone = call.data.split(":", 2)[2]
    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    bot.send_message(chat_id, f"Тон: {OCC.TONES.get(tone, tone)}")
    occ_store(chat_id, tone)


def occ_finish(chat_id):
    state = STATES[chat_id]
    key = state["product"]
    p = OCC.PRODUCTS[key]
    data = state["data"]
    name = data.get("name", "").strip()
    sign = p.get("sign") or data.get("sign", "").strip()
    tone = p.get("tone") or data.get("tone", "simple")
    default_title = p["card_title"].format(name=name)[:40]
    state["step"] = "occ_generating"
    bot.send_message(chat_id, "✍️ Пишу… это займёт около минуты.")
    bot.send_chat_action(chat_id, "typing")

    if AI and AI.available() and hasattr(AI, "generate_occasion"):
        res = AI.generate_occasion(p["brief"], key, state["qa"], tone, sign, default_title)
    else:
        res = {"letter": "[ai] Помощник выключен — письмо не сгенерировано.",
               "card_title": default_title, "card_line": ""}
    if res["letter"].startswith("[ai]"):
        STATES.pop(chat_id, None)
        bot.send_message(chat_id, "Не получилось написать письмо прямо сейчас 😔 "
                                  "Попробуй через пару минут или напиши в «❓ Помощь».")
        log.error("occasion generation failed: %s", res["letter"])
        return

    ref = cardbase_pick(chat_id, key, [])
    state.update(step="occ_paywall", letter=res["letter"], card_title=res["card_title"],
                 card_line=res["card_line"], sign=sign, tone=tone, name=name, paywall_at=now_msk(),
                 card_ref=ref, card_seen=[ref] if ref else [], retexts=0, wish_used=False,
                 default_title=default_title)
    occ_send_preview(chat_id, state)


FREE_RETEXTS = 2  # бесплатных перегенераций текста; дальше — одна правка по пожеланию


def cardbase_pick(chat_id, key, seen):
    try:
        return cardbase.pick_ref(chat_id, key, seen)
    except Exception as exc:
        log.error("cardbase pick: %s", exc)
        return None


def occ_masked_preview(letter):
    """Первая четверть письма читается, остальное — «матовое стекло» из ▒ (спойлер Telegram не годится:
    он открывается одним тапом)."""
    cut = max(110, len(letter) // 4)
    head = letter[:cut].rsplit(None, 1)[0] if " " in letter[:cut] else letter[:cut]
    tail = re.sub(r"\S", "▒", letter[len(head):]).strip()
    tail = re.sub(r"\n{3,}", "\n\n", tail)
    return esc(head) + "…\n\n<code>" + tail[:420] + ("…" if len(tail) > 420 else "") + "</code>"


def occ_preview_markup(state):
    p = OCC.PRODUCTS[state["product"]]
    credits = 0 if state.get("group") else (get_client(state.get("chat_id", 0)) or {}).get("credits", 0)
    kb = types.InlineKeyboardMarkup(row_width=1)
    if credits:
        kb.add(types.InlineKeyboardButton(f"🎁 Забрать по набору (осталось {credits})",
                                          callback_data="occ:credit"))
    q = partners.quote(state.get("chat_id", 0), price_base(state), state.get("promo"))
    kb.add(types.InlineKeyboardButton(f"💳 Оплатить — {partners.button_price(q)}", callback_data="occ:buy"))
    kb.add(types.InlineKeyboardButton("🎟 Есть промокод", callback_data="pr:enter"))
    left = max(0, FREE_RETEXTS - state.get("retexts", 0))
    label = f"✏️ Изменить текст (ещё {left})" if left else "✏️ Изменить текст — по твоему пожеланию"
    if left == 0 and state.get("wish_used"):
        label = None
    if label:
        kb.add(types.InlineKeyboardButton(label, callback_data="occ:retext"))
    kb.add(types.InlineKeyboardButton("❌ Отменить", callback_data="letter:cancel"))
    return kb


def occ_preview_text(state):
    lines = partners.price_lines(partners.quote(state.get("chat_id", 0), price_base(state), state.get("promo")))
    return (occ_masked_preview(state["letter"])
            + "\n\n🔒 Продолжение письма, чистая открытка без надписи «превью» "
              "и ссылка-конверт для получателя — после оплаты."
            + (f"\n\n{lines}" if lines else ""))


def occ_photo_markup():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🖼 Другая картинка", callback_data="occ:pc:next"))
    return kb


def occ_preview_image(state):
    return postcards.render(state["product"], state["card_title"], state["card_line"], state["sign"],
                            preview=True, card_ref=state.get("card_ref"))


def occ_send_preview(chat_id, state):
    state["chat_id"] = chat_id
    try:
        msg = bot.send_photo(chat_id, occ_preview_image(state),
                             caption="Твоя открытка (превью) 🖼 Не нравится картинка — выбери другую, это бесплатно.",
                             reply_markup=occ_photo_markup())
        state["photo_msg"] = msg.message_id
    except Exception as exc:
        log.error("postcard preview failed: %s", exc)
    msg = bot.send_message(chat_id, occ_preview_text(state), parse_mode="HTML",
                           reply_markup=occ_preview_markup(state))
    state["text_msg"] = msg.message_id
    cb_event(chat_id, "preview", state["product"], state.get("card_ref"))


def occ_refresh_photo(chat_id, state):
    try:
        bot.edit_message_media(
            types.InputMediaPhoto(occ_preview_image(state),
                                  caption="Твоя открытка (превью) 🖼 Не нравится картинка — выбери другую, это бесплатно."),
            chat_id, state["photo_msg"], reply_markup=occ_photo_markup())
    except Exception as exc:
        log.error("preview photo refresh: %s", exc)


def occ_refresh_text(chat_id, state):
    try:
        bot.edit_message_text(occ_preview_text(state), chat_id, state["text_msg"], parse_mode="HTML",
                              reply_markup=occ_preview_markup(state))
    except Exception as exc:
        log.error("preview text refresh: %s", exc)


@bot.callback_query_handler(func=lambda c: c.data == "occ:pc:next")
def occ_pc_next(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    if state.get("step") not in ("occ_paywall", "occ_wish", "promo_enter"):
        bot.answer_callback_query(call.id, "Заказ устарел — начни заново", show_alert=True)
        return
    ref = cardbase_pick(chat_id, state["product"], state.get("card_seen", []))
    if not ref:
        bot.answer_callback_query(call.id, "Других картинок пока нет")
        return
    state["card_ref"] = ref
    state.setdefault("card_seen", []).append(ref)
    state["photo_msg"] = call.message.message_id
    bot.answer_callback_query(call.id)
    cb_event(chat_id, "pc_next", state["product"], ref)
    occ_refresh_photo(chat_id, state)


def occ_regenerate(chat_id, state, wish=""):
    """Новый вариант письма на тех же ответах; открытка пересобирается с новым заголовком."""
    p = OCC.PRODUCTS[state["product"]]
    bot.send_chat_action(chat_id, "typing")
    try:
        if state.get("group"):  # групповое письмо: тот же набор голосов участников
            res = group_letters.generate(state["group"], previous=state["letter"], wish=wish)
        else:
            res = AI.generate_occasion(p["brief"], state["product"], state["qa"], state["tone"], state["sign"],
                                       state["default_title"], previous=state["letter"], wish=wish)
    except Exception as exc:
        log.error("occ regenerate: %s", exc)
        res = {"letter": "[ai] ошибка"}
    if res["letter"].startswith("[ai]"):
        bot.send_message(chat_id, "Не получилось написать новый вариант 😔 Попробуй ещё раз чуть позже.")
        return False
    state.update(letter=res["letter"], card_title=res["card_title"], card_line=res["card_line"])
    occ_refresh_text(chat_id, state)
    occ_refresh_photo(chat_id, state)
    return True


@bot.callback_query_handler(func=lambda c: c.data == "occ:retext")
def occ_retext(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    if state.get("step") != "occ_paywall":
        bot.answer_callback_query(call.id, "Заказ устарел — начни заново", show_alert=True)
        return
    if not (AI and AI.available()):
        bot.answer_callback_query(call.id, "Помощник сейчас недоступен", show_alert=True)
        return
    if state.get("retexts", 0) < FREE_RETEXTS:
        bot.answer_callback_query(call.id, "Пишу другой вариант…")
        if occ_regenerate(chat_id, state):
            state["retexts"] = state.get("retexts", 0) + 1
            cb_event(chat_id, "retext", state["product"], state.get("card_ref"))
            occ_refresh_text(chat_id, state)  # обновить счётчик на кнопке
        return
    if state.get("wish_used"):
        bot.answer_callback_query(call.id, "Правки закончились — письмо можно забрать или начать заново",
                                  show_alert=True)
        return
    bot.answer_callback_query(call.id)
    state["step"] = "occ_wish"
    bot.send_message(chat_id, "Два бесплатных варианта позади 🤍 Напиши одним сообщением, что изменить: "
                              "тон, детали, длину, что убрать или добавить. Я учту это и перепишу письмо — "
                              "это последняя правка.\n\n/cancel — вернуться к письму.")


@bot.message_handler(
    func=lambda m: STATES.get(m.chat.id, {}).get("step") == "occ_wish" and m.content_type == "text"
)
def occ_wish_receive(message):
    chat_id = message.chat.id
    state = STATES[chat_id]
    text = (message.text or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        state["step"] = "occ_paywall"
        bot.send_message(chat_id, "Хорошо, оставляю как есть. Письмо ждёт выше 👆")
        return
    if len(text) < 3 or len(text) > 500:
        bot.send_message(chat_id, "Напиши пожелание коротко, до 500 знаков.")
        return
    state["step"] = "occ_paywall"
    state["wish_used"] = True
    cb_event(chat_id, "retext_wish", state["product"], state.get("card_ref"))
    if occ_regenerate(chat_id, state, wish=text):
        bot.send_message(chat_id, "Переписала с твоим пожеланием 👆 Если подходит — можно забирать.")
    else:
        state["wish_used"] = False


def occ_make_order(chat_id, user, state, status="pending", price=None):
    key = state["product"]
    p = OCC.PRODUCTS[key]
    profile = upsert_client(user)
    order = {
        "order_id": new_order_id(),
        "chat_id": chat_id,
        "name": profile["name"],
        "username": profile.get("username", ""),
        "pain": key,
        "product": key,
        "answers": [a for _, a in state["qa"]],
        "qa": state["qa"],
        "mirror_text": None,
        "letter_text": state["letter"],
        "card_title": state["card_title"],
        "card_line": state["card_line"],
        "card_ref": state.get("card_ref"),
        "sign": state["sign"],
        "price_rub": (state.get("price") or p["price"]) if price is None else price,
        "status": status,
        "is_gift": True,
        "gift_for": state.get("name"),
        "created_at": now_msk().isoformat(),
        "paid_at": None,
        "delivered_at": None,
        "email": None,
        "rating": None,
    }
    if calendar_reminders.is_from_reminder(chat_id):
        order["from_reminder"] = True  # человек пришёл с напоминания календаря (метрика)
    if price is None:  # обычная покупка: скидки (одна, наибольшая) и баланс; набор/бесплатно — без них
        partners.apply_to_order(order, chat_id, state.get("promo"))
    if state.get("group"):
        order["group_id"] = state["group"]
        group_letters.mark_ordered(state["group"], order["order_id"])
    save_order(order)
    return order


@bot.callback_query_handler(func=lambda c: c.data == "occ:buy")
def occ_buy(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    bot.answer_callback_query(call.id)
    if state.get("step") not in ("occ_paywall", "occ_wish", "promo_enter"):
        safe_edit(call, f"Заказ устарел. Начни заново: «{OCC_BUTTON}».")
        return
    state["step"] = "occ_paywall"
    prev = get_order(state["order_id"]) if state.get("order_id") else None
    if prev and prev.get("status") == "pending" and state.get("order_sig") == (state["letter"], state.get("promo")):
        send_order_invoice(chat_id, prev)  # повторный тап «Оплатить»: тот же заказ, а не второй
        return
    order = occ_make_order(chat_id, call.from_user, state)
    state["order_id"], state["order_sig"] = order["order_id"], (state["letter"], state.get("promo"))
    cb_event(chat_id, "buy", state["product"], state.get("card_ref"))
    if order["price_rub"] <= 0:  # скидка и баланс покрыли всё — выдаём без оплаты
        notify_admin_new_order(order)
        fulfill_order(chat_id, order["order_id"], "free", None)
    elif send_order_invoice(chat_id, order):
        notify_admin_new_order(order)


@bot.callback_query_handler(func=lambda c: c.data == "occ:credit")
def occ_credit(call):
    chat_id = call.message.chat.id
    state = STATES.get(chat_id) or {}
    profile = get_client(chat_id) or {}
    bot.answer_callback_query(call.id)
    if state.get("step") != "occ_paywall" or profile.get("credits", 0) < 1 or state.get("group"):
        safe_edit(call, f"Не получилось — начни заново: «{OCC_BUTTON}».")
        return
    profile["credits"] -= 1
    write_json(client_path(chat_id), profile)
    order = occ_make_order(chat_id, call.from_user, state, status="done", price=0)
    cb_event(chat_id, "buy", state["product"], state.get("card_ref"))
    order["paid_at"] = order["delivered_at"] = now_msk().isoformat()
    order["paid_by"] = "credit"
    save_order(order)
    STATES.pop(chat_id, None)
    bot.send_message(chat_id, f"🎁 Списано из набора. Осталось: {profile['credits']}.")
    occ_deliver(chat_id, order)
    notify_admin_paid(order)


@bot.callback_query_handler(func=lambda c: c.data == "occ:pack")
def occ_pack(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    if pending_count(chat_id) >= MAX_PENDING:
        pending = pending_orders(chat_id)[0]
        bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченный заказ.",
                         reply_markup=pending_markup(pending["order_id"]))
        return
    profile = upsert_client(call.from_user)
    order = {
        "order_id": new_order_id(), "chat_id": chat_id, "name": profile["name"],
        "username": profile.get("username", ""), "pain": OCC.PACK["key"],
        "product": OCC.PACK["key"], "answers": [], "letter_text": "",
        "price_rub": OCC.PACK["price"], "status": "pending", "is_gift": False,
        "created_at": now_msk().isoformat(), "paid_at": None, "delivered_at": None,
        "email": None, "rating": None,
    }
    partners.apply_to_order(order, chat_id)
    save_order(order)
    bot.send_message(
        chat_id,
        f"{OCC.PACK['icon']} <b>{OCC.PACK['title']}</b>\n\n"
        "Три любых письма с открыткой — маме, другу, любимому, на день рождения… "
        f"Вместо {3 * OCC.price_from()}₽ — {OCC.PACK['price']}₽. "
        "Письма из набора не сгорают: пишешь, когда появится повод.",
        parse_mode="HTML")
    if order["price_rub"] <= 0:
        fulfill_order(chat_id, order["order_id"], "free", None)
    else:
        send_order_invoice(chat_id, order)


def occ_doc_order(chat_id, user):
    pending = pending_orders(chat_id)
    if pending and pending[0].get("product") == OCC.DOC["key"]:  # повторный переход по ссылке papa_pdf
        send_order_invoice(chat_id, pending[0])
        return
    if pending:
        bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченный заказ.",
                         reply_markup=pending_markup(pending[0]["order_id"]))
        return
    profile = upsert_client(user)
    order = {
        "order_id": new_order_id(), "chat_id": chat_id, "name": profile["name"],
        "username": profile.get("username", ""), "pain": OCC.DOC["key"],
        "product": OCC.DOC["key"], "answers": [], "letter_text": "",
        "price_rub": OCC.DOC["price"], "status": "pending", "is_gift": False,
        "created_at": now_msk().isoformat(), "paid_at": None, "delivered_at": None,
        "email": None, "rating": None,
    }
    partners.apply_to_order(order, chat_id)
    save_order(order)
    bot.send_message(
        chat_id,
        f"{OCC.DOC['icon']} <b>{OCC.DOC['title']}</b>\n\n"
        "30 фраз для пяти ситуаций (давно не говорили, после ссоры, строгий отец, расстояние, его нет рядом), "
        "три начала письма и правила разговора. 10 страниц, приходит сразу после оплаты.",
        parse_mode="HTML")
    send_order_invoice(chat_id, order)


@bot.callback_query_handler(func=lambda c: c.data == "occ:doc")
def occ_doc(call):
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)
    if pending_count(chat_id) >= MAX_PENDING:
        pending = pending_orders(chat_id)[0]
        bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченный заказ.",
                         reply_markup=pending_markup(pending["order_id"]))
        return
    occ_doc_order(chat_id, call.from_user)


# ── Новые фоны открыток: вечерняя подборка владельцу с кнопками ✅/❌ ──
CARD_REVIEW_HOUR = 19      # МСК
CARD_REVIEW_BATCH = 30     # не заваливаем чат: остальные придут на следующий вечер
CARD_REVIEW_MARK = os.path.join(DATA_DIR, "cards_review_last.txt")


def send_card_review(limit=CARD_REVIEW_BATCH):
    """Присылает владельцу ещё не показанные фоны со статусом review. Возвращает, сколько отправлено."""
    if not ADMIN_ID:
        return 0
    cardbase.sync_catalog()
    sent = 0
    for card in cardbase.review_queue()[:limit]:
        occ, _, stem = card["id"].partition("-")
        path = os.path.join(postcards.BGS, occ, f"{stem}.jpg")
        if not os.path.exists(path):
            continue
        title = OCC.PRODUCTS.get(occ, {}).get("title", occ)
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(types.InlineKeyboardButton("✅ В базу", callback_data=f"cbr:ok:{card['id']}"),
               types.InlineKeyboardButton("❌ Убрать", callback_data=f"cbr:no:{card['id']}"))
        try:
            with open(path, "rb") as f:
                bot.send_photo(ADMIN_ID, f, reply_markup=kb,
                               caption=f"🖼 Новый фон · {title}\n{card['id']} · стиль {card['style']} · сезон {card['season']}")
            cardbase.mark_review_sent(card["id"])
            sent += 1
        except Exception as exc:
            log.error("card review send %s: %s", card["id"], exc)
    if sent:
        bot.send_message(ADMIN_ID, f"🖼 Подборка фонов: {sent} шт. Одобренные сразу попадут в «Другая картинка».")
    return sent


@bot.callback_query_handler(func=lambda c: c.data.startswith("cbr:"))
def card_review_cb(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    _, verdict, card_id = call.data.split(":", 2)
    status = "active" if verdict == "ok" else "retired"
    if not cardbase.set_status(card_id, status):
        bot.answer_callback_query(call.id, "Фон не найден")
        return
    bot.answer_callback_query(call.id, "В базе ✅" if status == "active" else "Убрано ❌")
    try:
        bot.edit_message_caption(f"{'✅ В базе' if status == 'active' else '❌ Убрано'} · {card_id}",
                                 call.message.chat.id, call.message.message_id)
    except Exception as exc:
        log.debug("review caption: %s", exc)


@bot.message_handler(commands=["review"])
def cmd_card_review(message):
    if not admin_only(message):
        return
    if not send_card_review():
        bot.send_message(message.chat.id, "Новых фонов на проверку нет.")


@bot.message_handler(commands=["cardstats"])
def cmd_card_stats(message):
    if not admin_only(message):
        return
    r = cardbase.report(30)
    top = "\n".join(f"  {cid}: показов {d['preview']}, оплат {d['paid']}" for cid, d in r["top"]) or "  —"
    st = r["statuses"]
    bot.send_message(
        message.chat.id,
        "🖼 <b>Открытки за 30 дней</b>\n\n"
        f"Превью показано: {r['previews']}\n"
        f"Нажали «Оплатить»: {r['buy']}\n"
        f"Оплачено: {r['paid']} (конверсия {r['conversion']}%)\n"
        f"«Другая картинка»: {r['pc_next']}\n"
        f"Перегенераций текста: {r['retext']} + по пожеланию {r['retext_wish']}\n\n"
        f"Фоны в базе: активных {st.get('active', 0)}, на проверке {st.get('review', 0)}, "
        f"убрано {st.get('retired', 0)}\n\nЛучшие фоны:\n{top}",
        parse_mode="HTML")


def card_review_loop():
    while True:
        try:
            now = now_msk()
            last = ""
            try:
                with open(CARD_REVIEW_MARK, encoding="utf-8") as f:
                    last = f.read().strip()
            except OSError:
                pass
            if now.hour >= CARD_REVIEW_HOUR and last != now.date().isoformat():
                send_card_review()
                with open(CARD_REVIEW_MARK, "w", encoding="utf-8") as f:
                    f.write(now.date().isoformat())
        except Exception as exc:
            log.error("card review loop: %s", exc)
        time.sleep(600)


def occ_gift_code(order):
    code = order.get("gift_code")
    if not code:
        code = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
        order["gift_code"] = code
        save_order(order)
        write_json(gift_path(code), {"order_id": order["order_id"]})
    return code


def occ_gift_link(order):
    return f"https://t.me/{BOT_USERNAME}?start=g_{occ_gift_code(order)}"


def occ_web_envelope(order):
    """Конверт в браузере — для получателей без Telegram (заказы из VK)."""
    import site_pages
    return f"{site_pages.SITE_URL}/e/{occ_gift_code(order)}"


def occ_postcard(order, preview=False):
    return postcards.render(order["product"], order.get("card_title", ""),
                            order.get("card_line", ""), order.get("sign", ""), preview=preview,
                            card_ref=order.get("card_ref"))


def occ_deliver(chat_id, order):
    if order.get("card_ref") and not order.get("is_test"):
        try:  # эта картинка больше не предлагается этому человеку
            cardbase.mark_taken(order["chat_id"], order["card_ref"])
        except Exception as exc:
            log.error("cardbase mark_taken: %s", exc)
    cb_event(order["chat_id"], "paid", order.get("product"), order.get("card_ref"))
    try:
        msg = bot.send_photo(chat_id, occ_postcard(order), caption="🖼 Твоя открытка")
        # file_id нужен inline-отправке: открытка уходит в чат получателя от имени автора
        order["card_file_id"] = msg.photo[-1].file_id
        save_order(order)
    except Exception as exc:
        log.error("postcard deliver failed %s: %s", order["order_id"], exc)
    rate_kb = types.InlineKeyboardMarkup(row_width=5)
    rate_kb.row(*[types.InlineKeyboardButton("⭐" * i, callback_data=f"rate:{order['order_id']}:{i}")
                  for i in range(1, 6)])
    send_long(chat_id, order["letter_text"], markup=rate_kb)

    link = occ_gift_link(order)
    share_text = f"Тебе письмо 💌 Открой конверт:"
    share_url = ("https://t.me/share/url?url=" + urllib.parse.quote(link)
                 + "&text=" + urllib.parse.quote(share_text))
    name = (order.get("gift_for") or "").strip()
    label = f"💌 Отправить {name}" if name and len(name) <= 20 else "💌 Отправить получателю"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        label,
        switch_inline_query_chosen_chat=types.SwitchInlineQueryChosenChat(
            query=f"card_{occ_gift_code(order)}", allow_user_chats=True, allow_group_chats=True),
    ))
    kb.add(types.InlineKeyboardButton("📤 Отправить ссылкой (WhatsApp, другие чаты)", url=share_url))
    kb.add(partners.friend_gift_markup(order["chat_id"]))
    kb.add(types.InlineKeyboardButton("🎀 Ещё одно письмо с открыткой", callback_data="occ:catalog"))
    bot.send_message(
        chat_id,
        "✉️ <b>Как подарить</b>\n\n"
        "Нажми «Отправить» и выбери человека из своих чатов — открытка придёт ему "
        "от тебя, а не от бота. Номер телефона не нужен.\n"
        "Я сообщу тебе, когда он откроет письмо ✨\n\n"
        "Другой способ — ссылка на конверт:\n"
        f"<code>{link}</code>\n"
        "Или просто перешли ему открытку и письмо выше.",
        parse_mode="HTML", reply_markup=kb)
    try:
        calendar_reminders.offer_after_order(chat_id, order)
    except Exception as exc:
        log.error("calendar offer: %s", exc)


@bot.inline_handler(func=lambda q: (q.query or "").startswith("card_"))
def occ_inline_send(query):
    """Автор открытки отправляет её в чат получателя в один тап."""
    code = query.query[len("card_"):].strip()
    ref = read_json(gift_path(code), None) if code.isalnum() else None
    order = get_order(ref["order_id"]) if ref else None
    if not order or order.get("status") != "done" or order.get("chat_id") != query.from_user.id:
        bot.answer_inline_query(query.id, [], cache_time=0, is_personal=True)
        return

    who = order.get("sign") or "близкого человека"
    name = (order.get("gift_for") or "").strip()
    teaser = (f"💌 {name}, тебе письмо от {who}" if name else f"💌 Тебе письмо от {who}") \
        + "\nВнутри — несколько слов, написанных специально для тебя."
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Открыть письмо 💌", url=occ_gift_link(order)))
    file_id = order.get("card_file_id")
    if file_id:
        result = types.InlineQueryResultCachedPhoto(
            id=code, photo_file_id=file_id, caption=teaser, reply_markup=kb)
    else:
        result = types.InlineQueryResultArticle(
            id=code, title="Отправить письмо 💌",
            input_message_content=types.InputTextMessageContent(message_text=teaser),
            reply_markup=kb)
    bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)


def occ_gift_start(chat_id, code):
    ref = read_json(gift_path(code), None)
    order = get_order(ref["order_id"]) if ref else None
    if not order or order.get("status") != "done":
        bot.send_message(chat_id, "Конверт не найден 😔 Возможно, ссылка скопирована не полностью.",
                         reply_markup=kb_client())
        return
    who = order.get("sign") or "близкого человека"
    name = order.get("gift_for") or ""
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✉️ Вскрыть конверт", callback_data=f"env:open:{code}"))
    bot.send_message(
        chat_id,
        f"💌 {esc(name) + ', тебе' if name else 'Тебе'} письмо от <b>{esc(who)}</b>.\n\n"
        "Внутри — открытка и несколько слов, написанных специально для тебя.",
        parse_mode="HTML", reply_markup=kb)


@bot.callback_query_handler(func=lambda c: c.data.startswith("env:open:"))
def occ_envelope_open(call):
    chat_id = call.message.chat.id
    code = call.data.split(":", 2)[2]
    bot.answer_callback_query(call.id, "✨")
    ref = read_json(gift_path(code), None)
    order = get_order(ref["order_id"]) if ref else None
    if not order:
        bot.send_message(chat_id, "Конверт не найден 😔")
        return
    try:
        bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    try:
        bot.send_photo(chat_id, occ_postcard(order))
    except Exception as exc:
        log.error("envelope postcard failed: %s", exc)
    send_long(chat_id, order["letter_text"])

    if not order.get("opened_at") and chat_id != order["chat_id"]:
        order["opened_at"] = now_msk().isoformat()
        order["opened_by"] = chat_id
        save_order(order)
        try:
            bot.send_message(order["chat_id"],
                             f"✨ {esc(order.get('gift_for') or 'Получатель')} только что открыл(а) твоё письмо.",
                             parse_mode="HTML")
        except Exception:
            pass

    sign = order.get("sign") or "отправителю"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💌 Написать ответное письмо", callback_data="occ:catalog"))
    kb.add(types.InlineKeyboardButton("📖 Дневник Алисы", url=CHANNEL_URL))
    bot.send_message(
        chat_id,
        f"Если захочется ответить {esc(sign)} так же — я помогу подобрать слова 🤍\n\n"
        "Я — Алиса, цифровая девушка из Петербурга. Пишу письма к поводам, "
        "когда трудно найти слова самому.",
        parse_mode="HTML", reply_markup=kb)


_EXAMPLES_CACHE = []


def occ_examples_media():
    if not _EXAMPLES_CACHE:
        samples = [
            ("birthday", "С днём рождения, Катя!",
             "Ты всё так же смеёшься громче всех — и мир от этого теплее.", "Серёжа"),
            ("love", "Аня, это тебе",
             "Я до сих пор помню, как ты уронила мороженое и рассмеялась.", "твой Дима"),
            ("santa", "Миша, тебе письмо из Великого Устюга",
             "Я видел, как ты научился кататься на велосипеде. Горжусь!", "Дед Мороз"),
        ]
        for key, title, line, sign in samples:
            _EXAMPLES_CACHE.append(postcards.render(key, title, line, sign))
    return [types.InputMediaPhoto(img) for img in _EXAMPLES_CACHE]


# ─────────────────────────────────────────────────────────────
# ПРИМЕРЫ, ПОМОЩЬ, ПРИГЛАШЕНИЕ
# ─────────────────────────────────────────────────────────────

@bot.message_handler(func=lambda m: m.text == "📖 Примеры")
def show_examples(message):
    bot.send_message(
        message.chat.id,
        "Вот как это звучит 👇\n"
        "<i>(демонстрационный текст, не реальный заказ)</i>\n\n"
        "─────────────────────\n"
        "<i>Ты ищешь ответ, потому что боишься ошибиться.\n"
        "Но самые живые истории получаются у тех, кто ошибался часто.\n\n"
        "Я не дам совет. Я дам разрешение — идти дальше.\n"
        "Твой ответ уже внутри, я только помогу его назвать.</i>\n"
        "─────────────────────\n\n"
        "Короткий разговор о том, что тебя держит — и начало письма бесплатно.\n"
        f"Целиком — {LETTER_PRICE_RUB}₽, сразу в чате.",
        parse_mode="HTML",
        reply_markup=kb_client(),
    )
    try:
        bot.send_media_group(message.chat.id, occ_examples_media())
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🎀 Выбрать повод", callback_data="occ:catalog"))
        bot.send_message(message.chat.id, f"А так выглядят письма с открыткой — от {OCC.price_from()}₽ 🎀",
                         reply_markup=kb)
    except Exception as exc:
        log.error("examples postcards failed: %s", exc)


def send_free_pdf(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📖 Подписаться на дневник — фраза каждый вечер", url=CHANNEL_URL))
    kb.add(types.InlineKeyboardButton("🎀 Письмо с открыткой к поводу", callback_data="occ:catalog"))
    kb.add(types.InlineKeyboardButton("💌 Начало моего письма — бесплатно",
                                      callback_data="free:letter"))
    try:
        with open(FREE_PDF_PATH, "rb") as f:
            bot.send_document(
                chat_id, f,
                caption=(
                    "Шпаргалка «50 фраз для трудных разговоров» 🤍\n\n"
                    "Это начало. В моём дневнике каждый вечер в 20:00 — новая история "
                    "и одна фраза, которую можно забрать себе 🔖 Подпишись, чтобы не потерять."
                ),
                reply_markup=kb,
            )
    except OSError as exc:
        log.error("free pdf not sent: %s", exc)
        bot.send_message(
            chat_id,
            "Шпаргалка сейчас лежит в закрепе моего дневника 👇",
            reply_markup=kb,
        )


@bot.message_handler(func=lambda m: m.text == "✨ Бесплатно")
def free_menu(message):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💌 Начало моего письма", callback_data="free:letter"))
    kb.add(types.InlineKeyboardButton("📄 Шпаргалка «50 фраз»", callback_data="free:pdf"))
    kb.add(types.InlineKeyboardButton("📖 Дневник и челлендж «7 дней — 7 слов»",
                                      url=CHANNEL_URL))
    bot.send_message(
        message.chat.id,
        "✨ <b>Бесплатно</b>\n\n"
        "💌 <b>Начало твоего письма.</b> Пара вопросов о том, что держит, — "
        "и я покажу, как начнётся письмо именно тебе.\n\n"
        "📄 <b>Шпаргалка «50 фраз для трудных разговоров».</b> "
        "Маме, папе, другу после ссоры, себе.\n\n"
        "📖 <b>Дневник.</b> Мои записи из Петербурга и маленькие задания "
        "«7 дней — 7 слов» каждый вечер в 20:00.",
        parse_mode="HTML",
        reply_markup=kb,
    )


@bot.message_handler(func=lambda m: m.text == "📖 Дневник Алисы")
def diary_link(message):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📖 Открыть дневник", url=CHANNEL_URL))
    bot.send_message(
        message.chat.id,
        "Там я пишу про свои дни в Петербурге и про слова, "
        "которые мы откладываем на потом 🤍",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data in ("free:letter", "free:pdf"))
def free_callbacks(call):
    bot.answer_callback_query(call.id)
    if call.data == "free:pdf":
        send_free_pdf(call.message.chat.id)
    else:
        ask_diagnostic_start(call.message.chat.id)


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
        "<b>Сроки.</b> Письмо приходит сразу после оплаты.\n"
        f"<b>Цена.</b> {LETTER_PRICE_RUB}₽ за письмо.\n"
        "<b>Оплата.</b> Картой через ЮKassa, внутри приложения.\n"
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
    meta = pain_meta(order)
    gift = f"\n🎁 Подарок для: {esc(order['gift_for'])}" if order.get("is_gift") else ""
    safe_send(
        ADMIN_ID,
        ("🧪 <b>ТЕСТ</b> · " if order.get("is_test") else "")
        + "🆕 <b>Заказ создан</b> (ждёт оплаты)\n\n"
        f"{esc(order['name'])} (@{esc(order.get('username')) or '—'}){gift}\n"
        f"{meta['icon']} {meta['title']} · {order['price_rub']}₽\n"
        f"<code>{order['order_id']}</code>",
    )


def notify_admin_paid(order):
    if not ADMIN_ID:
        return
    meta = pain_meta(order)
    gift = f"\n🎁 Подарок для: {order['gift_for']}" if order.get("is_gift") else ""
    prev = [
        o for o in client_orders(order["chat_id"])
        if o.get("status") == "done" and o.get("order_id") != order["order_id"]
    ]
    who = f"\n🔁 Возвращается, писем до этого: {len(prev)}" if prev else "\n🆕 Пишет впервые"

    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton(
            "🔁 Перегенерировать и переслать", callback_data=f"adm:regen:{order['order_id']}"
        )
    )
    kb.add(
        types.InlineKeyboardButton(
            "🗂 Карточка", callback_data=f"card:show:{order['chat_id']}"
        )
    )
    email_line = f"\n✉️ {esc(order['email'])}" if order.get("email") else ""
    letter_text = order.get("letter_text") or ""
    letter_preview = esc(letter_text if len(letter_text) <= 250 else letter_text[:250] + "…")
    safe_send(
        ADMIN_ID,
        ("🧪 <b>ТЕСТ, 0 ₽ — в статистику не идёт</b>\n" if order.get("is_test") else "")
        + "💳 <b>ОПЛАЧЕНО, письмо отправлено</b>\n\n"
        f"{esc(order['name'])} (@{esc(order.get('username')) or '—'}){gift}{who}{email_line}\n"
        f"{meta['icon']} {meta['title']} · {order['price_rub']}₽\n\n"
        f"Письмо (начало):\n<i>{letter_preview}</i>\n\n"
        f"<code>{order['order_id']}</code>",
        markup=kb,
    )


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
        meta = pain_meta(o)
        short = esc(order_summary(o, limit=60))
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

    by_pain = {}
    for o in paid:
        by_pain[o.get("pain")] = by_pain.get(o.get("pain"), 0) + 1
    pain_lines = "\n".join(
        f"  {pain_meta(p)['icon']} {pain_meta(p)['title']}: {n}" for p, n in by_pain.items()
    ) or "  —"

    ensure_dirs()
    clients_total = len(client_files())

    bot.send_message(
        message.chat.id,
        "📈 <b>Статистика</b>\n\n"
        f"Клиентов: {clients_total}\n"
        f"Заказов всего: {len(orders)}\n"
        f"Оплачено: {len(paid)}\n"
        f"Не оплачено: {len([o for o in orders if o.get('status') == 'pending'])}\n\n"
        f"Выручка: <b>{revenue}₽</b>\n"
        f"Средний чек: {round(revenue / len(paid)) if paid else 0}₽\n"
        f"Средняя оценка: {avg}\n\n"
        f"По болям:\n{pain_lines}",
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
        lines.append("\n<b>С чем приходил раньше:</b>")
        for o in done[:4]:
            meta = pain_meta(o)
            q = order_summary(o, limit=110)
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
    safe_send(
        message.chat.id,
        f"👤 <b>{esc(profile['name'])}</b> (@{esc(profile.get('username')) or '—'})\n"
        f"chat_id: <code>{target}</code>\n\n" + client_card(target),
        markup=kb_card(target, order_id=True),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:show:"))
def card_show(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    profile = get_client(target) or {}
    safe_send(
        ADMIN_ID,
        f"👤 <b>{esc(profile.get('name', target))}</b>\n"
        f"chat_id: <code>{target}</code>\n\n" + client_card(target),
        markup=kb_card(target, order_id=True),
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("card:note:"))
def card_note_start(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    profile = get_client(target) or {}
    STATES[ADMIN_ID] = {"step": "card_note", "target": target, "ts": time.time()}
    current = profile.get("notes") or "пусто"
    bot.send_message(
        ADMIN_ID,
        f"📝 Заметка о {esc(profile.get('name', target))}\n\n"
        f"Сейчас: <i>{esc(current)}</i>\n\n"
        "Пришли новый текст — я покажу его и спрошу, сохранять ли. "
        f"/cancel — отмена. Ожидание — {CARD_NOTE_TTL // 60} мин.",
        parse_mode="HTML",
    )
    bot.answer_callback_query(call.id)


CARD_NOTE_TTL = 300  # секунд: после этого «ожидание заметки» сбрасывается само


def card_note_active(m):
    st = STATES.get(m.chat.id, {})
    if st.get("step") != "card_note":
        return False
    if time.time() - st.get("ts", 0) > CARD_NOTE_TTL:
        STATES.pop(m.chat.id, None)  # протухло — сообщение не перехватываем
        return False
    return True


@bot.message_handler(
    func=lambda m: admin_only(m) and m.content_type == "text" and card_note_active(m)
)
def card_note_save(message):
    st = STATES[message.chat.id]
    text = (message.text or "").strip()
    if text.startswith("/"):
        return
    st["pending_text"] = text
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ Сохранить заметку", callback_data="card:notesave"),
        types.InlineKeyboardButton("✖️ Отмена", callback_data="card:notecancel"),
    )
    profile = get_client(st["target"]) or {}
    bot.send_message(
        message.chat.id,
        f"Сохранить как заметку о {esc(profile.get('name', st['target']))}?\n\n"
        f"<i>{esc(text[:600])}</i>",
        parse_mode="HTML", reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data in ("card:notesave", "card:notecancel"))
def card_note_confirm(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    st = STATES.get(ADMIN_ID, {})
    if st.get("step") != "card_note" or not st.get("pending_text"):
        bot.answer_callback_query(call.id, "Уже неактуально")
        return
    STATES.pop(ADMIN_ID, None)
    if call.data == "card:notecancel":
        bot.answer_callback_query(call.id, "Отменено")
        bot.send_message(ADMIN_ID, "Заметка не сохранена.")
        return
    profile = get_client(st["target"])
    if not profile:
        bot.answer_callback_query(call.id, "Клиент не найден")
        return
    profile["notes"] = st["pending_text"]
    write_json(client_path(st["target"]), profile)
    bot.answer_callback_query(call.id, "Сохранено")
    bot.send_message(ADMIN_ID, "✅ Заметка сохранена.")


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
        meta = pain_meta(o)
        body = o["letter_text"]
        body = body if len(body) <= 3000 else body[:3000] + "…"
        bot.send_message(
            ADMIN_ID,
            f"{meta['icon']} <b>{meta['title']}</b> · {fmt_dt(o.get('delivered_at'))}\n"
            f"Ответы: <i>{esc(order_summary(o, limit=150))}</i>\n\n"
            f"{esc(body)}",
            parse_mode="HTML",
        )


def admin_regenerate(chat_id, order_id):
    """Перегенерирует письмо тем же ИИ (на тех же ответах анкеты) — ручная
    страховка на случай неудачного автоматического результата."""
    order = get_order(order_id)
    if not order:
        bot.send_message(chat_id, f"Заказ {order_id} не найден.")
        return
    if not order.get("answers"):
        bot.send_message(chat_id, "У заказа нет данных анкеты — перегенерировать нечем.")
        return
    if not (AI and AI.available()):
        bot.send_message(chat_id, "🤖 Помощник выключен — перегенерировать нечем.")
        return

    STATES.pop(chat_id, None)  # сбросить зависшее «ожидание заметки» и прочие шаги админа
    bot.send_chat_action(chat_id, "typing")
    meta = pain_meta(order)
    draft = AI.generate_letter(
        meta["title"], order["answers"], order.get("mirror_text", ""), order.get("gift_for")
    )
    STATES[chat_id] = {"step": "admin_regen", "order_id": order_id, "draft": draft}

    gift = f"\n🎁 Для: {esc(order['gift_for'])}" if order.get("is_gift") else ""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(
        "📤 Отправить клиенту вместо старого", callback_data=f"adm:send_regen:{order_id}"
    ))
    kb.add(types.InlineKeyboardButton("🔁 Ещё вариант", callback_data=f"adm:regen:{order_id}"))
    kb.add(types.InlineKeyboardButton(
        "🗂 Карточка", callback_data=f"card:show:{order['chat_id']}"
    ))
    send_long(
        chat_id, draft,
        f"✏️ <b>Новый вариант для {esc(order['name'])}</b>{gift}\n"
        f"{meta['icon']} {meta['title']}\n{'─' * 25}\n\n",
        kb,
    )
    safe_send(  # карточка — после варианта, чтобы кнопка «Заметка» не стояла перед текстом письма
        chat_id,
        client_card(order["chat_id"], exclude_order=order_id),
        markup=kb_card(order["chat_id"], order_id=order_id),
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:regen:"))
def admin_regen_cb(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    bot.answer_callback_query(call.id, "Перегенерирую…")
    admin_regenerate(call.message.chat.id, call.data.split(":", 2)[2])


@bot.message_handler(commands=["letter"])
def admin_letter_cmd(message):
    if not admin_only(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(message.chat.id, "Формат: /letter ALI-1234567890")
        return
    admin_regenerate(message.chat.id, parts[1].strip())


@bot.message_handler(func=lambda m: admin_only(m) and (m.text or "").startswith("/write_"))
def admin_write_shortcut(message):
    order_id = message.text[len("/write_"):].replace("_", "-")
    admin_regenerate(message.chat.id, order_id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm:send_regen:"))
def admin_send_regen(call):
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    order_id = call.data.split(":", 2)[2]
    st = STATES.get(ADMIN_ID) or {}
    draft = st.get("draft")
    if not draft or st.get("order_id") != order_id:
        bot.answer_callback_query(call.id, "Черновик потерялся, сделай новый")
        return

    order = get_order(order_id)
    if not order:
        bot.answer_callback_query(call.id, "Заказ не найден")
        return

    order["letter_text"] = draft
    order["resent_at"] = now_msk().isoformat()
    save_order(order)
    STATES.pop(ADMIN_ID, None)
    bot.answer_callback_query(call.id, "Отправляю…")

    try:
        safe_draft = esc(draft)
        if len(safe_draft) <= 3900:
            bot.send_message(order["chat_id"], safe_draft, parse_mode="HTML")
        else:
            for i in range(0, len(draft), 3800):
                bot.send_message(order["chat_id"], draft[i:i + 3800])
        bot.send_message(
            order["chat_id"],
            "Уточнение к письму — выше. Если откликнулось, поставь оценку в кабинете.",
        )
        bot.send_message(ADMIN_ID, f"✅ Переслано: {order['name']} · {order_id}")
    except Exception as exc:
        log.error("resend letter failed %s: %s", order_id, exc)
        bot.send_message(ADMIN_ID, f"❌ Не доставлено: {exc}")


@bot.message_handler(func=lambda m: admin_only(m) and m.text == "👤 Мой кабинет")
def admin_cabinet(message):
    """Кабинет админа (его собственные заказы и статистика)."""
    text, kb = cabinet_view(message.chat.id)
    safe_send(message.chat.id, text, markup=kb)


@bot.message_handler(commands=["cancel"])
def admin_cancel(message):
    st = STATES.get(message.chat.id) or {}
    if st.get("step") == "promo_enter":  # отмена ввода промокода не должна терять письмо
        st["step"] = st.pop("promo_back", "occ_paywall" if st.get("product") else "paywall")
        bot.send_message(message.chat.id, "Хорошо, оставляю как есть.")
        return
    STATES.pop(message.chat.id, None)
    bot.send_message(message.chat.id, "Отменено.")


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
def ai_analyze_legacy(call):
    """Совместимость со старыми уведомлениями — письма теперь генерируются
    автоматически, разбор больше не нужен как отдельный шаг."""
    bot.answer_callback_query(call.id, "Устарело — используй «Перегенерировать»")


@bot.message_handler(func=lambda m: admin_only(m) and m.text == "🤖 Помощник")
def ai_menu(message):
    # меню открываем всегда: без ключа нужна кнопка проверки, чтобы понять причину
    if not AI:
        bot.send_message(message.chat.id,
                         "❌ Файл ai_assistant.py не найден рядом с bot_best.py.")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🔌 Проверить подключение", callback_data="ai:selftest"))
    kb.add(types.InlineKeyboardButton("📊 Сводка за 7 дней", callback_data="ai:digest:7"))
    kb.add(types.InlineKeyboardButton("📊 Сводка за 30 дней", callback_data="ai:digest:30"))
    bot.send_message(
        message.chat.id,
        "🤖 <b>Помощник</b>\n\n"
        "Где он работает:\n"
        "· диагностика, зеркало и письмо клиенту — автоматически\n"
        "· «🔁 Перегенерировать и переслать» в уведомлении об оплате\n"
        "· метки для карточки клиента\n"
        "· черновик ответа в поддержку\n"
        "· сводка по заказам",
        parse_mode="HTML",
        reply_markup=kb,
    )


@bot.callback_query_handler(func=lambda c: c.data == "ai:selftest")
def ai_selftest(call):
    """Проверка связки с Claude. Доступна и без ключа — покажет причину."""
    if call.message.chat.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "Недоступно")
        return
    bot.answer_callback_query(call.id, "Проверяю…")
    if not AI:
        bot.send_message(ADMIN_ID, "❌ Модуль ai_assistant.py не найден рядом с ботом.")
        return
    bot.send_chat_action(ADMIN_ID, "typing")
    bot.send_message(ADMIN_ID, AI.selftest())


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
    questions = [order_summary(o) for o in orders]

    result = AI.weekly_digest(stats, questions)
    send_long(ADMIN_ID, result, f"📊 СВОДКА ЗА {days} ДНЕЙ\n{'─' * 25}\n\n")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ai:tags:"))
def ai_tags(call):
    """Предлагает метки для карточки клиента."""
    if not ai_guard(call):
        bot.answer_callback_query(call.id, "Недоступно")
        return
    target = int(call.data.split(":", 2)[2])
    orders = [o for o in client_orders(target) if o.get("answers")]
    if not orders:
        bot.answer_callback_query(call.id, "Нет заказов для анализа")
        return

    bot.answer_callback_query(call.id, "Смотрю историю…")
    bot.send_chat_action(ADMIN_ID, "typing")
    result = AI.suggest_tags([order_summary(o) for o in orders])
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
    total = len(client_files())
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
    for fname in client_files():
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
            "ID", "Клиент", "@username", "Боль", "Цена, ₽",
            "Статус", "Оценка", "Создан", "Оплачен", "Отправлен", "Ответы",
        ])
        for o in orders:
            ws.append([
                o.get("order_id"),
                o.get("name"),
                o.get("username"),
                pain_meta(o)["title"],
                o.get("price_rub"),
                STATUS_LABEL.get(o.get("status"), ("", o.get("status", "")))[1],
                o.get("rating") or "",
                fmt_dt(o.get("created_at")),
                fmt_dt(o.get("paid_at")),
                fmt_dt(o.get("delivered_at")),
                order_summary(o, limit=500),
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


group_letters.register(sys.modules[__name__])  # до fallback: его обработчик ловит любой текст
calendar_reminders.register(sys.modules[__name__])
partners.register(sys.modules[__name__])
sources.register(sys.modules[__name__])


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

@app.route("/ping", methods=["GET"])
def index():
    return "Alisa bot is running", 200


import site_pages  # noqa: E402  сайт «Письма Алисы» на том же Flask
site_pages.register(app, DATA_DIR)


# Ключ для /stats выводится из токена бота: локальный отчёт считает его так же,
# отдельный секрет не нужен. Наружу отдаются только агрегаты, без личных данных.
# ── Конверт в браузере (получатели из VK и без Telegram) ─────
BOT_UA = ("bot", "vkshare", "telegram", "facebookexternalhit", "preview", "crawler", "spider", "whatsapp")
ENVELOPE_CSS = ("body{margin:0;background:#f6f0e6;color:#3b2f2a;font:18px/1.6 Georgia,serif}"
                ".w{max-width:640px;margin:0 auto;padding:32px 16px 48px}img{width:100%;border-radius:10px;"
                "box-shadow:0 8px 24px rgba(0,0,0,.15)}.b{display:inline-block;margin-top:20px;padding:14px 26px;"
                "background:#8a4b3c;color:#fff;border-radius:999px;text-decoration:none;font-family:sans-serif}"
                ".l{white-space:pre-wrap;margin:28px 0}.f{font:14px/1.5 sans-serif;color:#7a6a60;margin-top:36px}")


def envelope_order(code):
    ref = read_json(gift_path(os.path.basename(code)), None)
    order = get_order(ref["order_id"]) if ref else None
    return order if order and order.get("status") == "done" else None


def envelope_html(title, body):
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'<title>{esc(title)}</title><style>{ENVELOPE_CSS}</style></head><body><div class="w">{body}</div></body></html>')


@app.route("/e/<code>", methods=["GET"])
def envelope_closed(code):
    order = envelope_order(code)
    if not order:
        return envelope_html("Конверт не найден", "<h2>Конверт не найден 😔</h2><p>Возможно, ссылка скопирована не полностью.</p>"), 404
    name, who = order.get("gift_for") or "", order.get("sign") or "близкого человека"
    return envelope_html("Тебе письмо 💌",
                         f'<h1>💌 {esc(name) + ", тебе" if name else "Тебе"} письмо</h1>'
                         f'<p>От <b>{esc(who)}</b>. Внутри — открытка и несколько слов, написанных специально для тебя.</p>'
                         f'<a class="b" href="/e/{esc(code)}/open">✉️ Вскрыть конверт</a>')


@app.route("/e/<code>/open", methods=["GET"])
def envelope_open(code):
    order = envelope_order(code)
    if not order:
        return envelope_closed(code)
    ua = (request.headers.get("User-Agent") or "").lower()
    if not order.get("opened_at") and ua and not any(b in ua for b in BOT_UA):
        order["opened_at"] = now_msk().isoformat()
        order["opened_by"] = "web"
        save_order(order)
        try:
            if order.get("channel") == "vk":
                import vk_shop
                vk_shop.notify_opened(order)
            else:
                bot.send_message(order["chat_id"],
                                 f"✨ {esc(order.get('gift_for') or 'Получатель')} только что открыл(а) твоё письмо.",
                                 parse_mode="HTML")
        except Exception as exc:
            log.error("envelope notify %s: %s", order["order_id"], exc)
    return envelope_html("Письмо 💌",
                         f'<img src="/e/{esc(code)}.jpg" alt="Открытка"><div class="l">{esc(order.get("letter_text", ""))}</div>'
                         '<div class="f">Это письмо собрала Алиса Невская — цифровая героиня проекта «Когда трудно сказать '
                         'важное — здесь находятся слова». Захочется ответить так же — '
                         f'<a href="https://vk.me/alisanevskaya_diary">напиши Алисе во ВКонтакте</a> или '
                         f'<a href="https://t.me/{BOT_USERNAME}?start=occ">в Telegram</a>.</div>')


@app.route("/e/<code>.jpg", methods=["GET"])
def envelope_card(code):
    order = envelope_order(code)
    if not order:
        return "", 404
    return occ_postcard(order), 200, {"Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400"}


@app.route("/pv/<token>.jpg", methods=["GET"])
def vk_card_preview(token):
    """Открытка для покупателя из VK, если ключу сообщества не дали права на фото."""
    import vk_shop
    jpg = vk_shop.PREVIEWS.get(token)
    if not jpg:
        return "", 404
    return jpg, 200, {"Content-Type": "image/jpeg", "Cache-Control": "private, max-age=86400"}


def vk_shop_deps():
    from types import SimpleNamespace
    return SimpleNamespace(save_order=save_order, get_order=get_order, new_order_id=new_order_id,
                           now_msk=now_msk, yk_create=yk_create, yk_check=yk_check_order,
                           receipt_required=YOOKASSA_RECEIPT, notify_new=notify_admin_new_order, notify_paid=notify_admin_paid,
                           envelope_url=occ_web_envelope, data_dir=DATA_DIR, reminder_due=reminder_due)


STATS_KEY = hashlib.sha256(f"{TG_BOT_TOKEN}:stats".encode()).hexdigest()[:24]


def bot_stats(days=62):
    ensure_dirs()
    since = (now_msk() - timedelta(days=days)).date().isoformat()
    daily = {}

    def day(key):
        return daily.setdefault(key, {"new_clients": 0, "orders": 0, "paid": 0, "revenue": 0})

    sources, products, clients_total, pending = {}, {}, 0, 0
    for fname in client_files():
        c = read_json(os.path.join(CLIENTS_DIR, fname), None) or {}
        clients_total += 1
        d = (c.get("created_at") or "")[:10]
        if d >= since:
            day(d)["new_clients"] += 1
            src = c.get("source", "unknown")
            sources[src] = sources.get(src, 0) + 1
    vk_dir = os.path.join(DATA_DIR, "vk_clients")
    for fname in (os.listdir(vk_dir) if os.path.isdir(vk_dir) else []):
        c = read_json(os.path.join(vk_dir, fname), None) or {}
        clients_total += 1
        d = (c.get("created_at") or "")[:10]
        if d >= since:
            day(d)["new_clients"] += 1
            src = c.get("source") if str(c.get("source", "")).startswith("vk_") else "vk_dm"
            sources[src] = sources.get(src, 0) + 1
    revenue_total, paid_total, gifts_sent, gifts_opened = 0, 0, 0, 0
    for o in all_orders():
        d = (o.get("created_at") or "")[:10]
        if d >= since:
            day(d)["orders"] += 1
        if o.get("status") == "pending":
            pending += 1
        if o.get("status") == "done":
            paid_total += 1
            revenue_total += o.get("price_rub", 0)
            pd = (o.get("paid_at") or o.get("created_at") or "")[:10]
            if pd >= since:
                day(pd)["paid"] += 1
                day(pd)["revenue"] += o.get("price_rub", 0)
            key = o.get("product") or "letter"
            products[key] = products.get(key, 0) + 1
            if o.get("gift_code"):
                gifts_sent += 1
                gifts_opened += bool(o.get("opened_at"))
    site_views = {d: sum(v.values()) for d, v in read_json(os.path.join(DATA_DIR, "site_views.json"), {}).items()
                  if d >= since}
    return {
        "generated_at": now_msk().isoformat(),
        "site_views": site_views,
        "clients_total": clients_total, "paid_total": paid_total, "revenue_total": revenue_total,
        "pending": pending, "gifts_sent": gifts_sent, "gifts_opened": gifts_opened,
        "sources": sources, "products": products, "daily": dict(sorted(daily.items())),
        "cards": cardbase.report(days),
        "groups": group_letters.report(),
        "calendar": calendar_reminders.report(days),
        "partners": partners.report(),
        "sources_funnel": sources_report(),
    }


@app.route("/stats/<key>", methods=["GET"])
def stats_endpoint(key):
    if not secrets.compare_digest(key, STATS_KEY):
        return "", 404
    return bot_stats(), 200


RSS_CHANNEL = os.getenv("RSS_CHANNEL", "alisanevskaya_diary")
_RSS_CACHE = {"at": 0.0, "xml": ""}


def channel_rss_items(limit=15):
    """Посты публичного канала с t.me/s/<канал>: текст, фото, ссылка, дата."""
    html_page = requests.get(f"https://t.me/s/{RSS_CHANNEL}", timeout=20,
                             headers={"User-Agent": "Mozilla/5.0"}).text
    items = []
    for block in html_page.split('class="tgme_widget_message_wrap')[1:]:
        pid = re.search(r'data-post="([^"]+)"', block)
        when = re.search(r'<time datetime="([^"]+)"', block)
        if not pid or not when:
            continue
        text_m = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', block, re.S)
        raw = text_m.group(1) if text_m else ""
        start = re.search(r'href="https?://t\.me/' + BOT_USERNAME + r'(?:\?start=([\w-]+))?"', raw)
        raw = re.sub(r"<br\s*/?>", "\n", raw)
        raw = re.sub(r"<i class=\"emoji\"[^>]*><b>(.*?)</b></i>", r"\1", raw)
        text = html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()
        photo = re.search(r"tgme_widget_message_photo_wrap[^>]*background-image:url\('([^']+)'\)", block)
        if not text and not photo:
            continue
        items.append({"link": f"https://t.me/{pid.group(1)}", "date": when.group(1),
                      "text": text, "photo": photo.group(1) if photo else None,
                      "bot_start": (start.group(1) or "occ") if start else None})
    return items[-limit:]


VK_CHAT = "vk.me/alisanevskaya_diary"


def vk_adapt(item):
    """Пост канала для стены VK: ссылки на Telegram-бота → заказ прямо в сообщениях сообщества."""
    text = item["text"]
    bot_re = r"(?:https?://)?t\.me/" + BOT_USERNAME + r"(?:\?start=([\w-]+))?|@" + BOT_USERNAME
    found = re.search(bot_re, text)
    start = item.get("bot_start") or (found.group(1) or "occ" if found else None)
    if not start:
        return text
    link = f"{VK_CHAT}?ref=tg_{start}"
    text = re.sub(bot_re, link, text)
    if link not in text:
        text += f"\n\n💌 Заказать письмо с открыткой прямо здесь, во ВКонтакте: {link}"
    return text


def build_rss():
    from email.utils import format_datetime
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0"><channel>',
        "<title>Дневник Алисы Невской</title>",
        f"<link>https://t.me/{RSS_CHANNEL}</link>",
        "<description>Когда трудно сказать важное — здесь находятся слова</description>",
    ]
    # только свежие посты, чтобы при подключении импорта VK не залил стену старыми
    since = os.getenv("RSS_SINCE", "2026-10-03")
    items = channel_rss_items()
    fresh = [it for it in items if it["date"][:10] >= since] or items[-1:]
    for it in reversed(fresh):
        title = it["text"].split("\n", 1)[0][:100] or "Дневник Алисы"
        pub = format_datetime(datetime.fromisoformat(it["date"]))
        parts.append("<item>")
        parts.append(f"<title>{html.escape(title)}</title>")
        parts.append(f"<link>{it['link']}</link><guid>{it['link']}</guid><pubDate>{pub}</pubDate>")
        parts.append(f"<description>{html.escape(vk_adapt(it))}</description>")
        if it["photo"]:
            parts.append(f'<enclosure url="{html.escape(it["photo"])}" type="image/jpeg" length="0"/>')
        parts.append("</item>")
    parts.append("</channel></rss>")
    return "\n".join(parts)


@app.route("/rss.xml", methods=["GET"])
def rss_feed():
    """RSS канала для импорта в VK (Управление → Дополнительно → RSS)."""
    if time.time() - _RSS_CACHE["at"] > 300 or not _RSS_CACHE["xml"]:
        try:
            _RSS_CACHE.update(at=time.time(), xml=build_rss())
        except Exception as exc:
            log.error("rss build error: %s", exc)
            if not _RSS_CACHE["xml"]:
                return "", 503
    return _RSS_CACHE["xml"], 200, {"Content-Type": "application/rss+xml; charset=utf-8"}


def start_stats_server():
    """В режиме polling Flask не запущен — поднимаем его в фоне ради /health и /stats."""
    port = int(os.getenv("STATS_PORT", "80"))
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=port, use_reloader=False),
                     name="stats_http", daemon=True).start()


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
    start_yk_poller()
    threading.Thread(target=reminder_loop, name="reminders", daemon=True).start()
    threading.Thread(target=card_review_loop, name="card_review", daemon=True).start()
    threading.Thread(target=calendar_reminders.loop, name="calendar", daemon=True).start()
    try:
        import vk_bridge
        import vk_shop
        vk_shop.init(vk_shop_deps())
        vk_bridge.start()
    except Exception as exc:
        log.error("vk bridge: %s", exc)

    if WEBHOOK_URL:
        bot.remove_webhook()
        bot.set_webhook(url=f"{WEBHOOK_URL}/{TG_BOT_TOKEN}")
        log.info("webhook mode: %s", WEBHOOK_URL)
        app.run(host="0.0.0.0", port=PORT)
    else:
        log.info("polling mode (WEBHOOK_URL не задан)")
        try:
            start_stats_server()
        except Exception as exc:
            log.error("stats server: %s", exc)
        bot.remove_webhook()
        bot.infinity_polling(skip_pending=True)
