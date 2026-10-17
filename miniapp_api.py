"""API v1 для Mini App Алисы (сайт-приложение alisa-letters.pages.dev ходит сюда по HTTPS).

Бизнес-логику не копируем: заказ, письмо, картинки, оплата, выдача, цены и скидки — те же функции, что у бота
(bot_best, occasions, postcards, cardbase, constructor, ai_card, partners, calendar_reminders). Заказ из Mini App —
обычный заказ в тех же файлах: виден в кабинете бота и наоборот.

Включается переменной MINIAPP_API=1 (без неё маршрутов нет). Безопасность:
- авторизация: заголовок X-Telegram-Init-Data, подпись на токене бота (miniapp_share.init_data_user), срок 24 ч;
- заказ читает и меняет только автор; полное письмо и чистая открытка — только после оплаты;
- CORS только для https://alisa-letters.pages.dev и MINIAPP_ORIGINS;
- лимиты: 30 запросов в минуту на человека, 6 генераций письма в час, суточный потолок MINIAPP_GEN_DAILY_CAP;
- секреты и initData в ответы и логи не попадают.
Контракт: strategy/MINIAPP_API.md штаба; список функций бота и что вошло в v1 — docs/MINIAPP_API.md.
"""
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from functools import wraps
from types import SimpleNamespace

from flask import Response, request

import miniapp_share
import web_auth

B = None
APP = None
API = "/api/v1"
DEFAULT_ORIGIN = "https://alisa-letters.pages.dev"
USER_PER_MIN = 30
PUBLIC_PER_MIN = 120
GEN_PER_HOUR = 6
MAX_ANSWER = 1500
MAX_SIGN = 60
FREE_RETEXTS = 2
URL_TTL = 3600
LOCK = threading.RLock()
HITS = defaultdict(deque)         # ключ → метки времени запросов
GENS = defaultdict(deque)         # user_id → метки времени генераций письма
GEN_DAY = {"day": "", "count": 0}
TESTER_GEN = {}                   # user_id → (день, сколько писем сегодня): отдельный потолок тестеров
BUSY = set()                      # order_id, для которых идёт генерация письма
JOBS = {}                         # jobId → состояние AI-картинки
INFLIGHT = defaultdict(int)       # order_id → сколько AI-картинок рисуется сейчас
_DIARY = {"at": 0, "items": []}
SAFE_REF = re.compile(r"^[a-z0-9]+-[A-Za-z0-9_]+$")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")
SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")   # метка источника (start_param): коды партнёров — в верхнем регистре

DEMO_LETTER = ("Ты ищешь ответ, потому что боишься ошибиться.\n"
               "Но самые живые истории получаются у тех, кто ошибался часто.\n\n"
               "Я не дам совет. Я дам разрешение — идти дальше.\n"
               "Твой ответ уже внутри, я только помогу его назвать.")


class ApiError(Exception):
    def __init__(self, code, message, status=400, **extra):
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


def err(code, message, status=400, **extra):
    return ApiError(code, message, status, **extra)


# ── настройки ────────────────────────────────────────────────────
def enabled():
    return os.getenv("MINIAPP_API", "").lower() in ("1", "true", "yes", "on")


def origins():
    extra = [o.strip().rstrip("/") for o in os.getenv("MINIAPP_ORIGINS", "").split(",") if o.strip()]
    return {DEFAULT_ORIGIN, *extra}


def tester_gen_cap():
    try:
        return int(os.getenv("MINIAPP_TESTER_GEN_CAP", "30"))
    except ValueError:
        return 30


def tester_ai_cap():
    try:
        return int(os.getenv("MINIAPP_TESTER_AI_CAP", "10"))
    except ValueError:
        return 10


def gen_daily_cap():
    try:
        return int(os.getenv("MINIAPP_GEN_DAILY_CAP", "300"))
    except ValueError:
        return 300


def site():
    return B.site_pages.SITE_URL


def return_url(order_id):
    return os.getenv("MINIAPP_RETURN_URL", DEFAULT_ORIGIN).rstrip("/") + f"/?paid={order_id}"


# ── лимиты ───────────────────────────────────────────────────────
def hit(key, limit, window=60):
    """Скользящее окно: True, если запрос разрешён; иначе вернёт секунды ожидания в retry."""
    now = time.time()
    with LOCK:
        q = HITS[key] if window == 60 else GENS[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False, max(1, int(window - (now - q[0])))
        q.append(now)
        return True, 0


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() or request.remote_addr or "?")


# ── подпись ссылок на картинки (в <img> нет заголовков) ───────────
def _key():
    return hashlib.sha256(b"miniapp-img:" + str(B.TG_BOT_TOKEN).encode()).digest()


def sign(order_id, exp):
    return hmac.new(_key(), f"{order_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:24]


def signed(order_id, path, **query):
    exp = int(time.time()) + URL_TTL
    query = {k: v for k, v in query.items() if v is not None}
    qs = "&".join(f"{k}={v}" for k, v in {**query, "exp": exp, "sig": sign(order_id, exp)}.items())
    return f"{site()}{API}{path}?{qs}"


def sig_ok(order_id):
    try:
        exp = int(request.args.get("exp", "0"))
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sign(order_id, exp), request.args.get("sig", ""))


# ── авторизация и обёртка ручек ───────────────────────────────────
def current_user(required=True):
    user = web_auth.user_from_request(B, request)   # сайт без Telegram: Bearer-токен после входа по e-mail
    if user:
        return user
    user = miniapp_share.init_data_user(request.headers.get("X-Telegram-Init-Data", ""), B.TG_BOT_TOKEN)
    if not user and required:
        raise err("unauthorized", "Открой приложение из Telegram — так я пойму, кто ты.", 401)
    if user:
        try:
            B.maybe_register_tester(tg_user(user))   # ник из TEST_USERNAMES → запоминаем chat_id (как /test_add)
        except Exception as exc:
            B.log.error("tester register: %s", exc)
    return user


def tg_user(user):
    return SimpleNamespace(id=user["id"], first_name=user.get("first_name", ""), last_name=user.get("last_name", ""),
                           username=user.get("username", ""))


def endpoint(rule, methods=("GET",), auth=True):
    """Ручка: проверка лимитов и авторизации, единый формат ошибок."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                if auth == "sig":   # картинка: заголовок или подпись ссылки
                    user = current_user(required=False)
                    kwargs["user"] = user
                    if not user and not sig_ok(kwargs.get("order_id", "")):
                        raise err("unauthorized", "Ссылка устарела — открой приложение заново.", 401)
                elif auth:
                    user = current_user()
                    kwargs["user"] = user
                    ok, wait = hit(f"u{user['id']}", USER_PER_MIN)
                    if not ok:
                        raise err("rate_limited", "Слишком часто — подожди немного.", 429, retryAfter=wait)
                else:
                    ok, wait = hit(f"ip{client_ip()}", PUBLIC_PER_MIN)
                    if not ok:
                        raise err("rate_limited", "Слишком часто — подожди немного.", 429, retryAfter=wait)
                result = fn(*args, **kwargs)
                if isinstance(result, Response):
                    return result
                return Response(json.dumps(result, ensure_ascii=False), 200, mimetype="application/json")
            except ApiError as exc:
                body = {"error": {"code": exc.code, "message": exc.message}, **exc.extra}
                headers = {"Retry-After": str(exc.extra["retryAfter"])} if "retryAfter" in exc.extra else {}
                return Response(json.dumps(body, ensure_ascii=False), exc.status, mimetype="application/json",
                                headers=headers)
            except Exception as exc:  # непредвиденное — без подробностей наружу
                B.log.error("miniapp api %s: %s", rule, exc)
                body = {"error": {"code": "server_error", "message": "Что-то пошло не так. Попробуй ещё раз."}}
                return Response(json.dumps(body, ensure_ascii=False), 500, mimetype="application/json")
        APP.add_url_rule(API + rule, "api_" + fn.__name__, wrapper, methods=list(methods))
        return wrapper
    return deco


def body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


# ── черновики заказа ─────────────────────────────────────────────
def drafts_dir():
    d = os.path.join(B.DATA_DIR, "miniapp_drafts")
    os.makedirs(d, exist_ok=True)
    return d


def draft_path(order_id):
    return os.path.join(drafts_dir(), f"{order_id}.json")


def load_draft(order_id):
    if not SAFE_ID.match(order_id or ""):
        return None
    return B.read_json(draft_path(order_id), None)


def save_draft(d):
    if d.get("synthetic"):   # «черновик» собран из заказа, созданного в самом боте: отдельный файл не нужен
        return
    d["updated_at"] = B.now_msk().isoformat()
    B.write_json(draft_path(d["order_id"]), d)


def synthetic_draft(order):
    """Заказ, созданный в самом боте (черновика нет): состояние для API берём из самого заказа."""
    return {"order_id": order["order_id"], "synthetic": True, "chat_id": order["chat_id"], "product": order["product"],
            "name": order.get("gift_for"), "sign": order.get("sign", ""), "tone": order.get("tone"),
            "letter": order.get("letter_text") or "", "card_title": order.get("card_title"),
            "card_line": order.get("card_line"), "card_ref": order.get("card_ref"),
            "default_title": order.get("card_title") or "", "card_seen": [], "retexts": FREE_RETEXTS,
            "wish_used": True, "promo": order.get("promo_code"), "qa": order.get("qa") or [],
            "created_at": order.get("created_at")}


def find_draft(order_id):
    """Черновик Mini App или, если его нет, заказ из бота (оплаченный или ждущий оплаты)."""
    d = load_draft(order_id)
    if d:
        return d
    if not SAFE_ID.match(order_id or ""):
        return None
    o = B.get_order(order_id)
    if o and o.get("product") in B.OCC.PRODUCTS and o.get("status") in ("done", "pending"):
        return synthetic_draft(o)
    return None


def own_draft(order_id, user):
    d = find_draft(order_id)
    if not d or d.get("chat_id") != user["id"]:
        raise err("not_found", "Заказ не найден.", 404)
    return d


def real_order(d):
    o = B.get_order(d["order_id"])
    return o if o and o.get("chat_id") == d["chat_id"] else None


def checkout(user, order, data):
    """Оплата готового заказа (письмо, набор, PDF, озвучка): тестер → 0 ₽ без ЮKassa; полная скидка → выдача;
    иначе ссылка ЮKassa. Общий хвост для всех покупок."""
    order_id = order["order_id"]
    if B.is_test_user(user["id"]):   # как send_test_payment в боте — 0 ₽, ЮKassa не трогаем, в статистику не идёт
        order["is_test"] = True
        order.setdefault("test_price_rub", order.get("price_rub"))
        order["price_rub"] = 0
        B.save_order(order)
        try:
            B.fulfill_order(user["id"], order_id, f"test-{secrets.token_hex(4)}", None)
        except Exception as exc:
            B.log.error("miniapp test fulfill %s: %s", order_id, exc)
        return {"paid": True, "price": 0, "realPrice": order["test_price_rub"], "test": True, "orderId": order_id}
    if order["price_rub"] <= 0:   # скидка и баланс покрыли всё
        B.notify_admin_new_order(order)
        try:
            B.fulfill_order(user["id"], order_id, "free", None)
        except Exception as exc:
            B.log.error("miniapp free fulfill %s: %s", order_id, exc)
        return {"paid": True, "price": 0, "orderId": order_id}
    if not B.yk_enabled():
        raise err("payment_unavailable", "Оплата в приложении сейчас недоступна — оплати в боте.", 503,
                  botUrl=f"https://t.me/{B.BOT_USERNAME}")
    email = str(data.get("email") or "").strip()[:120] or order.get("email") or \
        (B.get_client(user["id"]) or {}).get("email") or (web_auth.email_of(user["id"]) if web_auth.is_web(user) else "")
    if B.YOOKASSA_RECEIPT and not (email and B.EMAIL_RE.match(email)):
        raise err("email_required", "Укажи почту — на неё придёт чек.", 400)
    try:
        url = B.yk_create(order, email, return_url(order_id))
    except Exception as exc:
        B.log.error("miniapp yk_create %s: %s", order_id, exc)
        raise err("payment_failed", "Не получилось открыть страницу оплаты. Попробуй через минуту.", 502)
    if url is None:
        return {"paid": True, "price": order["price_rub"], "orderId": order_id}
    B.notify_admin_new_order(B.get_order(order_id))
    return {"paymentUrl": url, "price": order["price_rub"], "orderId": order_id}


def pay_with_credit(user, d, order):
    """Оплата письма кредитом набора (как occ:credit в боте): заказ выдаётся сразу, кредит списывается."""
    if d.get("group") or d.get("voice"):
        raise err("credit_not_allowed", "Письмо группой и письмо с озвучкой кредитом не оплачиваются.", 409)
    profile = B.get_client(user["id"]) or {}
    if profile.get("credits", 0) < 1:
        raise err("no_credits", "В наборе не осталось писем.", 409)
    if order and order.get("status") == "pending" and order.get("yk_payment_id"):
        raise err("locked", "Оплата уже начата. Заверши её или отмени заказ.", 409)
    profile["credits"] -= 1
    B.write_json(B.client_path(user["id"]), profile)
    order = B.occ_make_order(user["id"], tg_user(user), {**d, "qa": [tuple(x) for x in d["qa"]]}, status="done", price=0)
    B.cb_event(user["id"], "buy", d["product"], d.get("card_ref"))
    order["paid_at"] = order["delivered_at"] = B.now_msk().isoformat()
    order["paid_by"] = "credit"
    if d.get("source"):
        order["source"] = d["source"]
    B.save_order(order)
    try:
        B.occ_deliver(user["id"], order)
        B.notify_admin_paid(order)
    except Exception as exc:
        B.log.error("miniapp credit deliver %s: %s", order["order_id"], exc)
    return {"paid": True, "price": 0, "credit": True, "creditsLeft": profile["credits"], "orderId": order["order_id"]}


def product_of(key):
    if key not in B.OCC.PRODUCTS:
        raise err("bad_product", "Такого повода нет.", 400)
    return B.OCC.PRODUCTS[key]


def questions_of(key):
    """Вопросы анкеты, как в боте (B.occ_questions): тон и подпись скрыты, если зашиты в продукт."""
    out = []
    for qkey, text in B.occ_questions(key):
        if qkey == "tone":
            out.append({"key": "tone", "text": text, "kind": "choice",
                        "options": [{"key": k, "label": v} for k, v in B.OCC.TONES.items()]})
        else:
            out.append({"key": qkey, "text": text, "kind": "text"})
    return out


def status_of(d, order):
    if order:
        if order.get("status") == "done":
            return "delivered"
        if order.get("status") == "pending":
            return "awaiting_payment"
        if order.get("status") == "cancelled":
            return "cancelled"
    return "preview" if d.get("letter") else "draft"


def VL():
    return B.voice_flow.VL


def base_price(d):
    """Цена до скидок: письмо (у группового — своя) и озвучка, если клиент включил её до оплаты."""
    base = d.get("price") or B.OCC.PRODUCTS[d["product"]]["price"]
    if d.get("voice") and not d.get("synthetic"):
        base += VL().upsell_price()
    return base


def quote(d, promo=None):
    return B.partners.quote(d["chat_id"], base_price(d), promo if promo is not None else d.get("promo"))


# ── озвучка: состояние, файлы, лимиты символов ───────────────────
def voice_dir():
    d = os.path.join(B.DATA_DIR, "voice")
    os.makedirs(d, exist_ok=True)
    return d


def voice_file(order_id, ext):
    return os.path.join(voice_dir(), f"{order_id}.{ext}")


def pending_voice_order(order):
    """Неоплаченная доплата за озвучку этого письма."""
    for o in B.client_orders(order["chat_id"]):
        if (o.get("product") == VL().ADDON_KEY and o.get("voice_for") == order["order_id"]
                and o.get("status") == "pending"):
            return o
    return None


def voice_info(order):
    """Озвучка выданного письма: none | awaiting_payment | queued | working | ready | failed."""
    addon = B.get_order(order["voice_addon_order"]) if order.get("voice_addon_order") else None
    src = order if order.get("voice") else addon
    out = {"status": "none", "gender": None, "chars": None, "error": None}
    if src is None:
        if pending_voice_order(order):
            out.update(status="awaiting_payment", gender=pending_voice_order(order).get("voice_gender"))
        return out
    out["gender"] = src.get("voice_gender")
    out["chars"] = src.get("voice_chars")
    vs = src.get("voice_status")
    out["status"] = {"working": "working", "done": "ready", "failed": "failed"}.get(vs, "queued")
    if vs == "failed":
        out["error"] = "voice_failed"
    out["updatedAt"] = src.get("voice_delivered_at") or src.get("paid_at")
    return out


def voice_summary(d, order):
    """Поле voice в состоянии заказа: что выбрано до оплаты или как идёт озвучка после."""
    if not VL().enabled():
        return None
    if order and order.get("status") == "done":
        info = voice_info(order)
        return None if info["status"] == "none" else {k: info[k] for k in ("status", "gender")}
    if d.get("voice"):
        return {"enabled": True, "gender": d.get("voice_gender"), "price": VL().upsell_price()}
    return None


def voice_estimate(text):
    return len(VL().clean_text(text or ""))


def voice_usage(prefix, chat_id=None):
    """Символы озвучки по журналу voice_usage.jsonl за период (префикс даты: 2026-10-10 или 2026-10); chat_id — только человека."""
    total = 0
    try:
        with open(os.path.join(B.DATA_DIR, "voice_usage.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not str(r.get("at", "")).startswith(prefix):
                    continue
                if chat_id is not None:
                    o = B.get_order(r.get("order_id", "")) or {}
                    if o.get("chat_id") != chat_id:
                        continue
                total += int(r.get("chars") or 0)
    except OSError:
        pass
    return total


def voice_reserved():
    """Символы оплаченных, но ещё не озвученных писем (они уйдут в журнал позже)."""
    total = 0
    for o in B.all_orders(include_test=True):
        if o.get("status") != "done" or o.get("voice_status") in ("done", "failed", "working"):
            continue
        if o.get("voice") or o.get("product") == VL().ADDON_KEY:
            total += voice_estimate(o.get("letter_text"))
    return total


def tester_voice_cap():
    try:
        return int(os.getenv("MINIAPP_TESTER_VOICE_CAP", "3000"))
    except ValueError:
        return 3000


def voice_cap_left(chat_id=None):
    """Сколько знаков ещё можно озвучить: суточный и месячный потолки (VOICE_DAILY_CAP, VOICE_MONTHLY_CAP) и свой суточный у тестера."""
    try:
        daily = int(os.getenv("VOICE_DAILY_CAP", "8000"))
        monthly = int(os.getenv("VOICE_MONTHLY_CAP", "15000"))
    except ValueError:
        daily, monthly = 8000, 15000
    now = B.now_msk()
    left = min(daily - voice_usage(now.strftime("%Y-%m-%d")), monthly - voice_usage(now.strftime("%Y-%m"))) \
        - voice_reserved()
    if chat_id is not None and B.is_test_user(chat_id):
        left = min(left, tester_voice_cap() - voice_usage(now.strftime("%Y-%m-%d"), chat_id))
    return max(0, left)


def check_voice_allowed(chat_id, letter):
    """Проверки до приёма денег и до включения озвучки. Бросает ApiError."""
    if not VL().enabled() or VL().upsell_price() <= 0:
        raise err("voice_unavailable", "Озвучка сейчас недоступна — письмо можно получить текстом.", 503)
    need = voice_estimate(letter)
    if need > VL().MAX_TOTAL:
        raise err("letter_too_long", "Письмо слишком длинное для озвучки.", 409)
    if need > voice_cap_left(chat_id):
        raise err("voice_cap", "На сегодня озвучка закончилась — загляни завтра или получи письмо текстом.", 429,
                  retryAfter=3600)


def title_line(d):
    return d.get("card_title") or d["default_title"], d.get("card_line") or ""


def render_preview(d, ref, preview=True):
    title, line = title_line(d)
    return B.postcards.render(d["product"], title, line, d.get("sign", ""), preview=preview, card_ref=ref)


PREVIEW_MAX = 220      # символов в превью, не больше
PREVIEW_SHARE = 0.25   # и не больше четверти письма


def masked_head(letter):
    """Превью письма: только приветствие и первая короткая фраза-зацепка (≤220 знаков и ≤25% текста), обрыв по слову с «…».
    Остальное, включая концовку, подпись и имена из конца, наружу не отдаём вообще (не прячем CSS-ом).
    Возвращает (текст, скрытых знаков, скрытых абзацев)."""
    letter = (letter or "").strip()
    limit = max(40, min(PREVIEW_MAX, int(len(letter) * PREVIEW_SHARE)))
    if len(letter) < 160:   # совсем короткое письмо: показываем только четверть
        limit = max(1, len(letter) // 4)
    head = letter[:limit]
    if len(letter) > limit and not letter[limit].isspace() and len(head.split()) > 1:
        head = head.rsplit(None, 1)[0]   # обрыв по слову
    head = head.rstrip(" ,;:—-")
    shown_chars = len(head)
    paragraphs = [p for p in letter.split(chr(10) * 2) if p.strip()]
    pos, touched = 0, 0
    for p in paragraphs:   # сколько абзацев затронуто превью (последний может быть показан частично)
        idx = letter.find(p, pos)
        if idx < shown_chars:
            touched += 1
        pos = idx + len(p)
    return head + "…", max(0, len(letter) - shown_chars), max(0, len(paragraphs) - touched)


def ai_today_for(chat_id):
    """Сколько AI-картинок человек нарисовал сегодня (по журналу ai_card)."""
    day = B.now_msk().date().isoformat()
    n = 0
    try:
        with open(B.ai_card._ledger_path(), encoding="utf-8") as f:
            for line in f:
                if f'"ts": "{day}' not in line:
                    continue
                try:
                    o = B.get_order(json.loads(line).get("order", ""))
                except ValueError:
                    continue
                if o and o.get("chat_id") == chat_id:
                    n += 1
    except OSError:
        pass
    return n


def clean_source(value):
    value = str(value or "").strip()
    return value if SOURCE_RE.match(value) else None


def apply_attribution(user, param):
    """Метка источника из приложения (start_param) — те же эффекты, что у /start <метка>:
    first touch в профиле, партнёрская привязка (p_), реферал и скидка другу (ref_). Идемпотентно."""
    chat_id = user["id"]
    profile = B.upsert_client(tg_user(user))
    first_touch = "source" not in profile
    if first_touch:
        profile["source"] = B.source_label(param)
        B.write_json(B.client_path(chat_id), profile)
    if param.startswith("p_"):
        try:
            B.partners.touch(chat_id, param[2:], first_touch)   # привязка на 30 дней по тем же правилам
        except Exception as exc:
            B.log.error("miniapp partner touch: %s", exc)
    elif param.startswith("ref_") and first_touch:
        try:
            inviter = int(param[4:])
        except ValueError:
            inviter = None
        profile = B.get_client(chat_id) or profile
        if (inviter and inviter != chat_id and B.get_client(inviter) and not profile.get("referred_by")
                and not B.partners.paid_orders(chat_id)):
            profile["referred_by"] = inviter
            B.write_json(B.client_path(chat_id), profile)
            B.add_referral(inviter, chat_id)
    q = B.partners.quote(chat_id, 100)
    return {"recorded": first_touch, "source": (B.get_client(chat_id) or {}).get("source"),
            "discount": {"percent": q["discount"], "label": q["label"]} if q["discount"] else None}


def ai_state(order):
    """Состояние AI-картинки заказа: только оплаченный заказ и только если включён ключ генерации."""
    AC = B.ai_card
    if not order or order.get("status") != "done" or order.get("product") not in AC.SUBJECTS:
        return None
    used = AC.used_for_order(order["order_id"]) + INFLIGHT[order["order_id"]]
    items = []
    for n in range(1, AC.MAX_TRIES + 1):
        if os.path.exists(os.path.join(AC._dir(), f"{order['order_id']}_{n}.jpg")):
            items.append({"n": n, "ref": f"ai:{order['order_id']}_{n}",
                          "url": signed(order["order_id"], f"/orders/{order['order_id']}/ai-card/{n}.jpg")})
    return {"enabled": AC.enabled(), "attemptsLeft": max(0, AC.MAX_TRIES - used), "maxTries": AC.MAX_TRIES,
            "items": items}


def present(d):
    order = real_order(d)
    done = bool(order and order.get("status") == "done")
    oid = d["order_id"]
    ref = (order or d).get("card_ref") or d.get("card_ref")
    q = quote(d) if not done else None
    out = {
        "orderId": oid, "product": d["product"], "status": status_of(d, order),
        "giftFor": d.get("name"), "sign": d.get("sign"), "tone": d.get("tone"),
        "price": ((order.get("test_price_rub") if order.get("is_test") else order["price_rub"]) if done else q["payable"]),
        "test": bool(order and order.get("is_test")),
        "basePrice": d.get("price") or B.OCC.PRODUCTS[d["product"]]["price"], "voice": voice_summary(d, order),
        "discount": ({"rub": q["discount"], "label": q["label"], "balanceUsed": q["balance_used"]} if q and
                     (q["discount"] or q["balance_used"]) else None),
        "cardRef": ref, "cardTitle": title_line(d)[0], "cardLine": title_line(d)[1],
        "createdAt": d.get("created_at"),
    }
    if ref:
        out["previewUrl"] = signed(oid, f"/orders/{oid}/card-preview.jpg", ref=ref) if not done else \
            signed(oid, f"/orders/{oid}/card.jpg")
    if not done:
        credits = (B.get_client(d["chat_id"]) or {}).get("credits", 0)
        out["creditsAvailable"] = credits if not (d.get("group") or d.get("voice")) else 0
    if d.get("letter") and not done:
        head, locked, hidden_paragraphs = masked_head(d["letter"])
        out.update(letterPreview=head, locked=True, previewHidden=True, lockedChars=locked,
                   hiddenParagraphs=hidden_paragraphs)
        left = max(0, FREE_RETEXTS - d.get("retexts", 0))
        out["retextsLeft"] = left
        out["wishAvailable"] = left == 0 and not d.get("wish_used")
    if done:
        out.update(letter=order["letter_text"], locked=False, envelopeCode=B.occ_gift_code(order),
                   envelopeUrl=B.occ_web_envelope(order), opened=bool(order.get("opened_at")),
                   rating=order.get("rating"), aiCard=ai_state(order))
    return out


# ── генерация письма ────────────────────────────────────────────
def gen_guard(user_id):
    ok, wait = hit(user_id, GEN_PER_HOUR, window=3600)
    if not ok:
        raise err("rate_limited", "Письма пишутся не чаще нескольких раз в час — попробуй чуть позже.", 429,
                  retryAfter=wait)
    day = B.now_msk().strftime("%Y-%m-%d")
    if B.is_test_user(user_id):   # тестеру — свой потолок на человека, общий потолок он не расходует
        with LOCK:
            used = TESTER_GEN.get(user_id, ("", 0))
            used = used[1] if used[0] == day else 0
            if used >= tester_gen_cap():
                GENS[user_id].pop()
                raise err("daily_cap", "Тестовый лимит писем на сегодня исчерпан — продолжим завтра.", 429,
                          retryAfter=3600)
            TESTER_GEN[user_id] = (day, used + 1)
        return
    with LOCK:
        if GEN_DAY["day"] != day:
            GEN_DAY.update(day=day, count=0)
        if GEN_DAY["count"] >= gen_daily_cap():
            GENS[user_id].pop()
            raise err("daily_cap", "Сегодня я написала очень много писем — загляни завтра 🤍", 429, retryAfter=3600)
        GEN_DAY["count"] += 1


def generate_letter(d, previous="", wish=""):
    ai = B.AI
    if d.get("group"):   # общее письмо: тот же набор голосов участников (group_letters.generate)
        if not (ai and ai.available() and hasattr(ai, "generate_group_letter")):
            raise err("ai_unavailable", "Помощник сейчас недоступен. Попробуй позже.", 503)
        res = B.group_letters.generate(d["group"], previous=previous, wish=wish)
        if not res or res["letter"].startswith("[ai]"):
            B.log.error("miniapp group letter failed: %s", (res or {}).get("letter", "")[:80])
            raise err("ai_failed", "Не получилось собрать письмо прямо сейчас. Попробуй через пару минут.", 502)
        return res
    if not (ai and ai.available() and hasattr(ai, "generate_occasion")):
        raise err("ai_unavailable", "Помощник сейчас недоступен. Попробуй позже.", 503)
    p = B.OCC.PRODUCTS[d["product"]]
    res = ai.generate_occasion(p["brief"], d["product"], d["qa"], d["tone"], d["sign"], d["default_title"],
                               previous=previous, wish=wish) if previous else \
        ai.generate_occasion(p["brief"], d["product"], d["qa"], d["tone"], d["sign"], d["default_title"])
    if not res or res["letter"].startswith("[ai]"):
        B.log.error("miniapp letter failed: %s", (res or {}).get("letter", "")[:80])
        raise err("ai_failed", "Не получилось написать письмо прямо сейчас. Попробуй через пару минут.", 502)
    return res


# ── картинки: проверка спецификации ────────────────────────────────
def card_ref_from(d, payload, paid=False):
    """Принимает {ref} или {spec} и возвращает безопасную ссылку открытки (как у конструктора бота)."""
    PC, CC = B.postcards, B.constructor
    product = d["product"]
    ref = payload.get("ref")
    if isinstance(payload.get("spec"), dict):
        s = payload["spec"]
        props = [x for x in (s.get("props") or []) if x in CC.prop_labels()][:PC.MAX_PROPS]
        base = str(s.get("base", ""))
        if base not in B.cardbase.active_ids(product):
            raise err("bad_card", "Такой картинки нет в этом поводе.", 400)
        for key, allowed in (("pal", CC.PAL_LABELS), ("lay", CC.LAY_LABELS), ("frame", CC.FRAME_LABELS),
                             ("anim", CC.ANIM_LABELS)):
            if s.get(key, "none" if key != "lay" else "corners") not in allowed:
                raise err("bad_card", "Неизвестная настройка открытки.", 400)
        return PC.encode_spec(base, s.get("pal", "none"), props, s.get("lay", "corners"), s.get("frame", "none"),
                              s.get("anim", "none"))
    if not isinstance(ref, str):
        raise err("bad_card", "Нужна ссылка на картинку.", 400)
    if ref.startswith("ai:"):
        order = real_order(d)
        name = ref[3:]
        if not (order and order.get("status") == "done" and re.match(rf"^{re.escape(d['order_id'])}_[1-3]$", name)
                and os.path.exists(os.path.join(B.ai_card._dir(), name + ".jpg"))):
            raise err("bad_card", "Эта картинка недоступна.", 400)
        return ref
    spec = PC.parse_spec(ref)
    base = spec["base"] if spec else PC.split_ref(ref)[0]
    if base not in B.cardbase.active_ids(product):
        raise err("bad_card", "Такой картинки нет в этом поводе.", 400)
    if spec:
        return PC.encode_spec(spec["base"], spec["pal"], spec["props"], spec["lay"], spec["frame"], spec["anim"])
    return base


# ── подключение ─────────────────────────────────────────────────
def register(app, bot_module):
    global B, APP
    B = bot_module
    if not enabled():
        return
    APP = app
    cors(app)
    routes()
    import miniapp_api_v2   # v2: поддержка, подписки, набор, PDF, голос, группы (флаг MINIAPP_API_V2)
    miniapp_api_v2.register(sys.modules[__name__], B)
    web_auth.register(sys.modules[__name__], B)
    B.log.info("miniapp api v1: включён, origins=%s", ",".join(sorted(origins())))


def cors(app):
    @app.after_request
    def add_cors(resp):
        if request.path.startswith(API):
            origin = request.headers.get("Origin", "").rstrip("/")
            if origin in origins():
                resp.headers["Access-Control-Allow-Origin"] = origin
                resp.headers["Vary"] = "Origin"
                resp.headers["Access-Control-Allow-Headers"] = "X-Telegram-Init-Data, X-Guest-Token, Authorization, Content-Type"
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
                resp.headers["Access-Control-Max-Age"] = "600"
        return resp

    @app.route(API + "/<path:_any>", methods=["OPTIONS", "GET", "POST", "PUT", "DELETE"], endpoint="api_fallback")
    def fallback(_any):
        if request.method == "OPTIONS":   # предзапрос CORS
            return Response("", 204)
        body = {"error": {"code": "not_found", "message": "Такой ручки нет."}}
        return Response(json.dumps(body, ensure_ascii=False), 404, mimetype="application/json")


def routes():
    # ── публичные ───────────────────────────────────────────────
    @endpoint("/catalog", auth=False)
    def catalog():
        items = []
        for key in B.OCC.CATALOG_ORDER:
            p = B.OCC.PRODUCTS.get(key)
            if not p:
                continue
            items.append({"key": key, "icon": p["icon"], "title": p["title"], "price": p["price"],
                          "pitch": p["pitch"], "questions": questions_of(key), "cardTitle": p["card_title"]})
        return {"products": items, "tones": B.OCC.TONES, "priceFrom": B.OCC.price_from()}

    @endpoint("/cards", auth=False)
    def cards():
        occasion = request.args.get("occasion", "")
        product_of(occasion)
        try:
            limit = max(1, min(48, int(request.args.get("limit", 24))))
            offset = max(0, int(request.args.get("cursor") or 0))
        except ValueError:
            raise err("bad_request", "Неверные параметры.", 400)
        ids = B.cardbase.active_ids(occasion)
        season = request.args.get("season", "")
        if season:
            keep = {c for c in ids if (B.cardbase.get_card(c) or {}).get("season", "all") in (season, "all")}
            ids = [c for c in ids if c in keep]
        user = current_user(required=False)
        if user:  # новые для человека — первыми, уже забранные — в конце
            taken = B.cardbase.taken_refs(user["id"])
            ids = [c for c in ids if c not in taken] + [c for c in ids if c in taken]
        page = ids[offset:offset + limit]
        base = site() + API
        nxt = offset + limit if offset + limit < len(ids) else None
        return {"items": [{"ref": c, "occasion": occasion, "thumbUrl": f"{base}/cards/{c}.jpg?size=thumb",
                           "fullUrl": f"{base}/cards/{c}.jpg?size=full"} for c in page],
                "next": str(nxt) if nxt is not None else None, "total": len(ids)}

    @endpoint("/cards/<ref>.jpg", auth=False)
    def card_image(ref):
        if not SAFE_REF.match(ref) or ref not in B.cardbase.active_ids(ref.split("-", 1)[0]):
            raise err("not_found", "Картинки нет.", 404)
        path = B.postcards.bg_path(ref)
        size = request.args.get("size", "full")
        if size == "thumb":
            from PIL import Image
            cache = os.path.join(B.DATA_DIR, "api_thumbs")
            os.makedirs(cache, exist_ok=True)
            tp = os.path.join(cache, ref + ".jpg")
            if not os.path.exists(tp):
                im = Image.open(path).convert("RGB")
                im.thumbnail((420, 420))
                im.save(tp, "JPEG", quality=80, optimize=True)
            path = tp
        with open(path, "rb") as f:
            data = f.read()
        return Response(data, 200, mimetype="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    @endpoint("/card-options", auth=False)
    def card_options():
        CC, PC = B.constructor, B.postcards
        AC = B.ai_card
        return {"palettes": CC.PAL_LABELS, "props": CC.prop_labels(), "layouts": CC.LAY_LABELS,
                "frames": CC.FRAME_LABELS, "animations": CC.ANIM_LABELS, "maxProps": PC.MAX_PROPS,
                "default": {"pal": "none", "props": [], "lay": "corners", "frame": "none", "anim": "none"},
                "ai": {"enabled": AC.enabled(), "maxTries": AC.MAX_TRIES, "maxExtras": AC.MAX_EXTRAS,
                       "styles": {k: v[0] for k, v in AC.STYLES.items()},
                       "scenes": {occ: {k: v[0] for k, v in scenes.items()} for occ, scenes in AC.SUBJECTS.items()},
                       "palettes": {k: v[0] for k, v in AC.PALETTES.items()},
                       "extras": {k: v[0] for k, v in AC.EXTRAS.items()}}}

    @endpoint("/examples", auth=False)
    def examples():
        B.occ_examples_media()  # заполняет кэш примеров
        return {"demoLetter": DEMO_LETTER,
                "cards": [{"n": i, "imageUrl": f"{site()}{API}/examples/{i}.jpg"}
                          for i in range(len(B._EXAMPLES_CACHE))],
                "priceFrom": B.OCC.price_from()}

    @endpoint("/examples/<int:n>.jpg", auth=False)
    def example_image(n):
        B.occ_examples_media()
        if not 0 <= n < len(B._EXAMPLES_CACHE):
            raise err("not_found", "Примера нет.", 404)
        return Response(B._EXAMPLES_CACHE[n], 200, mimetype="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

    @endpoint("/diary", auth=False)
    def diary():
        if time.time() - _DIARY["at"] > 600:
            try:
                _DIARY["items"] = B.channel_rss_items(limit=15)
                _DIARY["at"] = time.time()
            except Exception as exc:
                B.log.error("miniapp diary: %s", exc)
        items = [{"link": it["link"], "date": it["date"], "text": it["text"][:700], "photo": it["photo"]}
                 for it in reversed(_DIARY["items"])]
        return {"channelUrl": B.CHANNEL_URL, "items": items}

    @endpoint("/free", auth=False)
    def free():
        bot = f"https://t.me/{B.BOT_USERNAME}"
        return {"items": [
            {"key": "letter", "title": "💌 Начало моего письма",
             "text": "Пара вопросов о том, что держит, — и я покажу, как начнётся письмо именно тебе.",
             "kind": "bot", "url": bot},
            {"key": "pdf", "title": "📄 Шпаргалка «50 фраз для трудных разговоров»",
             "text": "Маме, папе, другу после ссоры, себе. Присылает бот.", "kind": "bot", "url": bot},
            {"key": "diary", "title": "📖 Дневник и челлендж «7 дней — 7 слов»",
             "text": "Мои записи из Петербурга и маленькие задания каждый вечер в 20:00.",
             "kind": "link", "url": B.CHANNEL_URL}]}

    # ── заказ письма с открыткой ────────────────────────────────────
    @endpoint("/orders", methods=("POST",))
    def create_order(user):
        data = body()
        key = data.get("product")
        p = product_of(key)
        answers = data.get("answers") if isinstance(data.get("answers"), dict) else {}
        qa, flat = [], {}
        for qkey, text in B.occ_questions(key):
            if qkey == "tone":
                continue
            if qkey == "sign" and not str(answers.get("sign") or data.get("sign") or "").strip():
                continue
            val = str(answers.get(qkey) if qkey != "sign" else (answers.get("sign") or data.get("sign")) or "").strip()
            if not val:
                raise err("bad_answers", "Ответь на все вопросы анкеты.", 400, field=qkey)
            if len(val) > MAX_ANSWER:
                raise err("bad_answers", "Слишком длинный ответ — сократи до 1500 знаков.", 400, field=qkey)
            qa.append([text, val])
            flat[qkey] = val
        source = clean_source(data.get("source")) if data.get("source") else None
        if data.get("source") and not source:
            raise err("bad_source", "Метка источника: только латиница, цифры, _ и -, до 40 знаков.", 400)
        tone = p.get("tone") or str(data.get("tone") or answers.get("tone") or "simple")
        if tone not in B.OCC.TONES:
            raise err("bad_tone", "Неизвестный тон письма.", 400)
        sign = (p.get("sign") or flat.get("sign") or str(data.get("sign") or "").strip())[:MAX_SIGN]
        if not sign:
            raise err("bad_answers", "Подпиши письмо — как тебя зовёт получатель.", 400, field="sign")
        name = flat.get("name", "").strip()
        # не плодим черновики: не больше 10 неоплаченных за сутки
        day_ago = time.time() - 86400
        recent = [f for f in os.listdir(drafts_dir())
                  if os.path.getmtime(os.path.join(drafts_dir(), f)) > day_ago
                  and (B.read_json(os.path.join(drafts_dir(), f), {}) or {}).get("chat_id") == user["id"]]
        if len(recent) >= 10:
            raise err("too_many_drafts", "Слишком много черновиков за сутки. Оплати или удали старые.", 429)
        B.upsert_client(tg_user(user))
        d = {"order_id": B.new_order_id(), "chat_id": user["id"], "user": {k: user.get(k, "") for k in
             ("first_name", "last_name", "username")}, "product": key, "qa": qa, "data": flat, "tone": tone,
             "sign": sign, "name": name, "default_title": p["card_title"].format(name=name)[:40],
             "letter": None, "card_title": None, "card_line": None, "card_ref": None, "card_seen": [],
             "retexts": 0, "wish_used": False, "promo": None, "source": source,
             "created_at": B.now_msk().isoformat()}
        d["api_order_id"] = d["order_id"]
        save_draft(d)
        if source:
            apply_attribution(user, source)   # первое касание, если ещё не записано
        B.cb_event(user["id"], "api_order", key, None)
        return {"orderId": d["order_id"], "status": "draft"}

    @endpoint("/orders/<order_id>/preview", methods=("POST",))
    def preview(order_id, user):
        d = own_draft(order_id, user)
        if real_order(d):
            raise err("locked", "Письмо уже в оплате — изменить текст нельзя. Отмени заказ и начни заново.", 409)
        data = body()
        wish = str(data.get("wish") or "").strip()
        with LOCK:
            if order_id in BUSY:
                raise err("busy", "Письмо ещё пишется — подожди минуту.", 409)
            BUSY.add(order_id)
        try:
            if d.get("letter"):
                if d.get("retexts", 0) < FREE_RETEXTS:
                    gen_guard(user["id"])
                    res = generate_letter(d, previous=d["letter"])
                    d["retexts"] += 1
                elif wish and not d.get("wish_used") and 3 <= len(wish) <= 500:
                    gen_guard(user["id"])
                    res = generate_letter(d, previous=d["letter"], wish=wish)
                    d["wish_used"] = True
                else:
                    raise err("retext_limit", "Бесплатные варианты закончились: письмо можно забрать или "
                                              "написать пожелание (до 500 знаков) — это последняя правка.", 409)
                B.cb_event(user["id"], "retext", d["product"], d.get("card_ref"))
            else:
                gen_guard(user["id"])
                res = generate_letter(d)
                d["card_ref"] = B.cardbase_pick(user["id"], d["product"], [])
                d["card_seen"] = [d["card_ref"]] if d["card_ref"] else []
                B.cb_event(user["id"], "preview", d["product"], d["card_ref"])
            d.update(letter=res["letter"], card_title=res["card_title"], card_line=res["card_line"])
            save_draft(d)
        finally:
            with LOCK:
                BUSY.discard(order_id)
        out = present(d)
        return {"letterPreview": out["letterPreview"], "locked": True, "previewHidden": True,
                "lockedChars": out["lockedChars"], "hiddenParagraphs": out["hiddenParagraphs"],
                "retextsLeft": out["retextsLeft"], "wishAvailable": out["wishAvailable"],
                "cardRef": out["cardRef"], "previewUrl": out.get("previewUrl")}

    @endpoint("/orders/<order_id>")
    def get_order(order_id, user):
        return present(own_draft(order_id, user))

    @endpoint("/orders/<order_id>/card", methods=("PUT",))
    def put_card(order_id, user):
        d = own_draft(order_id, user)
        order = real_order(d)
        if order and order.get("status") == "done":
            ref = card_ref_from(d, body())
            if not ref.startswith("ai:"):
                raise err("paid", "Оплаченная открытка не меняется — можно выбрать свою нарисованную картинку.", 409)
            order["card_ref"] = ref
            try:  # как у бота: новая картинка → новый file_id для отправки получателю
                msg = B.bot.send_photo(order["chat_id"], B.occ_postcard(order), caption="🖼 Твоя открытка — с твоей картинкой")
                order["card_file_id"] = msg.photo[-1].file_id
            except Exception as exc:
                B.log.error("miniapp ai card use %s: %s", order_id, exc)
                order["card_file_id"] = None
            order.pop("card_anim_file_id", None)
            B.save_order(order)
            d["card_ref"] = ref
            save_draft(d)
            B.cb_event(user["id"], "ai_use", d["product"], ref)
            return {"cardRef": ref}
        if not d.get("letter"):
            raise err("no_letter", "Сначала нужно получить письмо.", 409)
        ref = card_ref_from(d, body())
        d["card_ref"] = ref
        d.setdefault("card_seen", []).append(ref)
        save_draft(d)
        if order and order.get("status") == "pending":
            order["card_ref"] = ref
            B.save_order(order)
        B.cb_event(user["id"], "pc_next" if "!" not in ref else "cc_done", d["product"], ref)
        return {"cardRef": ref, "previewUrl": signed(order_id, f"/orders/{order_id}/card-preview.jpg", ref=ref)}

    @endpoint("/orders/<order_id>/card-preview.jpg", auth="sig")
    def card_preview(order_id, user):
        d = find_draft(order_id)
        if not d or (user and d.get("chat_id") != user["id"]):
            raise err("not_found", "Заказ не найден.", 404)
        ref = request.args.get("ref") or d.get("card_ref")
        if not ref or not isinstance(ref, str) or len(ref) > 200:
            raise err("bad_card", "Нужна картинка.", 400)
        base = ref.split("!", 1)[0].split("~", 1)[0]
        if not ref.startswith("ai:") and base not in B.cardbase.active_ids(d["product"]):
            raise err("bad_card", "Такой картинки нет в этом поводе.", 400)
        if ref.startswith("ai:") and not (user or sig_ok(order_id)):
            raise err("bad_card", "Недоступно.", 400)
        order = real_order(d)
        done = bool(order and order.get("status") == "done")
        return Response(render_preview(d, ref, preview=not done), 200, mimetype="image/jpeg",
                        headers={"Cache-Control": "private, max-age=300"})

    @endpoint("/orders/<order_id>/card.jpg", auth="sig")
    def card_final(order_id, user):
        d = find_draft(order_id)
        order = real_order(d) if d else None
        if not order or order.get("status") != "done" or (user and d.get("chat_id") != user["id"]):
            raise err("not_found", "Открытка станет доступна после оплаты.", 404)
        return Response(B.occ_postcard(order), 200, mimetype="image/jpeg",
                        headers={"Cache-Control": "private, max-age=300"})

    @endpoint("/promo/check", methods=("POST",))
    def promo_check(user):
        data = body()
        code = str(data.get("code") or "").strip()[:40]
        p = product_of(data.get("product"))
        if not code:
            raise err("bad_request", "Введи промокод.", 400)
        ok, res = B.partners.check_promo(user["id"], code)
        q = B.partners.quote(user["id"], p["price"], code if ok else None)
        return {"valid": ok, "applied": bool(q.get("promo_code")), "price": q["payable"], "basePrice": p["price"],
                "discount": q["discount"], "label": q["label"],
                "message": "" if ok else res}

    @endpoint("/orders/<order_id>/pay", methods=("POST",))
    def pay(order_id, user):
        d = own_draft(order_id, user)
        test = B.is_test_user(user["id"])
        if not d.get("letter"):
            raise err("no_letter", "Сначала получи письмо.", 409)
        order = real_order(d)
        if order and order.get("status") == "done":
            return {"paid": True, "price": order["price_rub"], **({"test": True} if order.get("is_test") else {})}
        data = body()
        if data.get("useCredit"):
            return pay_with_credit(user, d, order)
        promo = str(data.get("promo") or d.get("promo") or "").strip()[:40] or None
        if promo and promo != d.get("promo"):
            ok, res = B.partners.apply_promo_code(user["id"], promo)   # как «Есть промокод» в боте (и привязка партнёра)
            if not ok:
                raise err("bad_promo", res, 400)
        sig_now = [d["letter"], promo, bool(d.get("voice")), d.get("voice_gender")]
        if order and order.get("status") == "pending" and (d.get("synthetic") or d.get("order_sig") == sig_now):
            pass   # повторное «Оплатить» или заказ, созданный в боте, — тот же заказ
        else:
            if B.pending_count(user["id"]) >= B.MAX_PENDING and not (order and order.get("status") == "pending"):
                other = B.pending_orders(user["id"])[0]
                raise err("pending_exists", "У тебя есть неоплаченное письмо — оплати или отмени его.", 409,
                          orderId=other["order_id"])
            if order and order.get("status") == "pending" and order.get("yk_payment_id"):
                raise err("locked", "Оплата уже начата. Заверши её или отмени заказ.", 409)
            d["promo"] = promo
            order = B.occ_make_order(user["id"], tg_user(user), {**d, "qa": [tuple(x) for x in d["qa"]],
                                                                 "promo": promo, "price": base_price(d)})
            if web_auth.is_web(user):
                order["channel"] = "web"   # выдача без Telegram: bot_best.fulfill_order → web_auth.on_paid
                B.save_order(order)
            d["order_sig"] = sig_now
            if d.get("source"):
                order["source"] = d["source"]   # отчёт /sources считает заказы по этой метке
                B.save_order(order)
            save_draft(d)
            B.cb_event(user["id"], "buy", d["product"], d.get("card_ref"))
        return checkout(user, order, data)

    @endpoint("/orders/<order_id>/pay/check", methods=("POST",))
    def pay_check(order_id, user):
        d = own_draft(order_id, user)
        order = real_order(d)
        if not order:
            return {"paid": False, "status": "draft"}
        if order.get("status") != "done":
            try:
                B.yk_check_order(order_id)
            except Exception as exc:
                B.log.error("miniapp pay check %s: %s", order_id, exc)
            order = B.get_order(order_id)
        return {"paid": order.get("status") == "done", "status": status_of(d, order)}

    @endpoint("/orders/<order_id>/cancel", methods=("POST",))
    def cancel(order_id, user):
        d = own_draft(order_id, user)
        order = real_order(d)
        if order and order.get("status") == "done":
            raise err("paid", "Оплаченный заказ отменить нельзя.", 409)
        if order and order.get("status") == "pending":
            order["status"] = "cancelled"
            B.save_order(order)
            if order.get("group_id"):   # как в боте: группа снова принимает части
                B.group_letters.reopen(order["group_id"], order["order_id"])
        try:
            os.remove(draft_path(order_id))
        except OSError:
            pass
        return {"ok": True}

    @endpoint("/orders/<order_id>/ai-card", methods=("POST",))
    def ai_card_start(order_id, user):
        AC = B.ai_card
        d = own_draft(order_id, user)
        order = real_order(d)
        if not order or order.get("status") != "done" or order.get("product") not in AC.SUBJECTS:
            raise err("not_paid", "Нарисовать свою картинку можно после оплаты письма.", 403)
        if not AC.enabled():
            raise err("ai_card_off", "Рисование картинок сейчас недоступно.", 503)
        data = body()
        occ = order["product"]
        style, scene, pal = data.get("style"), data.get("scene"), data.get("palette")
        extras = data.get("extras") if isinstance(data.get("extras"), list) else []
        if (style not in AC.STYLES or scene not in AC.SUBJECTS[occ] or pal not in AC.PALETTES
                or any(not isinstance(x, str) or x not in AC.EXTRAS for x in extras)
                or len(set(extras)) > AC.MAX_EXTRAS):
            raise err("bad_params", "Выбери стиль, сюжет, палитру и до двух деталей из списка.", 400)
        with LOCK:
            used = AC.used_for_order(order_id) + INFLIGHT[order_id]
            if used >= AC.MAX_TRIES:
                raise err("no_attempts", "Попытки закончились — выбери одну из готовых картинок.", 409)
            if B.is_test_user(user["id"]):   # тестер: свой потолок картинок в день, общий не расходует
                if ai_today_for(user["id"]) + INFLIGHT[order_id] >= tester_ai_cap():
                    raise err("daily_cap", "Тестовый лимит картинок на сегодня исчерпан.", 429, retryAfter=3600)
            elif AC.today_count() + sum(INFLIGHT.values()) >= AC.daily_cap():
                raise err("daily_cap", "Сегодня я рисую слишком много — попробуй завтра 🤍", 429, retryAfter=3600)
            INFLIGHT[order_id] += 1
            n = used + 1
            job_id = f"{order_id}-{n}-{int(time.time())}"
            JOBS[job_id] = {"order": order_id, "chat": user["id"], "n": n, "status": "pending"}
        sel = {"order": order_id, "occ": occ, "style": style, "subject": scene, "pal": pal,
               "extras": list(dict.fromkeys(extras))}
        threading.Thread(target=run_ai_job, args=(job_id, sel, order), daemon=True).start()
        return {"jobId": job_id, "n": n, "attemptsLeft": AC.MAX_TRIES - used - 1}

    @endpoint("/orders/<order_id>/ai-card/<job_id>")
    def ai_card_job(order_id, job_id, user):
        own_draft(order_id, user)
        job = JOBS.get(job_id)
        if not job or job["order"] != order_id or job["chat"] != user["id"]:
            raise err("not_found", "Задание не найдено.", 404)
        out = {"status": job["status"], "n": job["n"]}
        if job["status"] == "done":
            out.update(url=signed(order_id, f"/orders/{order_id}/ai-card/{job['n']}.jpg"),
                       ref=f"ai:{order_id}_{job['n']}")
        if job["status"] == "failed":
            out["error"] = "Не получилось нарисовать, попытка не потрачена."
        order = B.get_order(order_id)
        out["attemptsLeft"] = (ai_state(order) or {}).get("attemptsLeft", 0)
        return out

    @endpoint("/orders/<order_id>/ai-card/<int:n>.jpg", auth="sig")
    def ai_card_image(order_id, n, user):
        d = find_draft(order_id)
        order = real_order(d) if d else None
        path = os.path.join(B.ai_card._dir(), f"{order_id}_{n}.jpg")
        if not order or order.get("status") != "done" or (user and d.get("chat_id") != user["id"]) \
                or not 1 <= n <= B.ai_card.MAX_TRIES or not os.path.exists(path):
            raise err("not_found", "Картинки нет.", 404)
        card = B.postcards.render(order["product"], order.get("card_title", ""), order.get("card_line", ""),
                                  order.get("sign", ""), card_ref=f"ai:{order_id}_{n}")
        return Response(card, 200, mimetype="image/jpeg", headers={"Cache-Control": "private, max-age=300"})

    @endpoint("/orders/<order_id>/send", methods=("POST",))
    def send(order_id, user):
        d = own_draft(order_id, user)
        order = real_order(d)
        if not order or order.get("status") != "done":
            raise err("not_paid", "Отправить можно после оплаты.", 403)
        code = B.occ_gift_code(order)
        prepared = None
        try:
            prepared = miniapp_share.save_prepared(B.TG_BOT_TOKEN, user["id"],
                                                   miniapp_share.prepared_result(order, code, site()))
        except Exception as exc:
            B.log.error("miniapp prepare %s: %s", order_id, exc)
        return {"envelopeUrl": B.occ_web_envelope(order), "envelopeCode": code, "preparedMessageId": prepared,
                "shareUrl": B.occ_share_url(order), "shareText": B.occ_share_text(order)}

    @endpoint("/orders/<order_id>/rate", methods=("POST",))
    def rate(order_id, user):
        d = own_draft(order_id, user)
        order = real_order(d)
        if not order or order.get("status") != "done":
            raise err("not_paid", "Оценить можно оплаченное письмо.", 403)
        data = body()
        try:
            stars = int(data.get("stars"))
        except (TypeError, ValueError):
            stars = 0
        if not 1 <= stars <= 5:
            raise err("bad_request", "Оценка — от 1 до 5.", 400)
        order["rating"] = stars
        comment = str(data.get("comment") or "").strip()[:500]
        if comment:
            order["rating_comment"] = comment
        B.save_order(order)
        if B.ADMIN_ID:
            try:
                B.bot.send_message(B.ADMIN_ID, f"⭐ Оценка {stars}/5 (приложение) · {order['order_id']}"
                                   + (f"\n{comment}" if comment else ""))
            except Exception:
                pass
        return {"ok": True}

    # ── кабинет и профиль ────────────────────────────────────────
    @endpoint("/me")
    def me(user):
        profile = B.upsert_client(tg_user(user))
        orders = B.client_orders(user["id"])
        done = [o for o in orders if o.get("status") == "done"]
        P = B.partners
        return {"id": user["id"], "name": profile.get("name"), "username": profile.get("username"),
                "ordersDone": len(done), "spentRub": profile.get("total_spent_rub", 0),
                "bonusRub": profile.get("bonus_rub", 0), "credits": profile.get("credits", 0),
                "referralLink": P.friend_link(user["id"]), "friendDiscount": P.FRIEND_DISCOUNT,
                "friendBonusRub": P.FRIEND_BONUS_RUB,
                "invitedFriends": len(profile.get("referrals", [])),
                "isTester": B.is_test_user(user["id"]), "mailing": not profile.get("mailing_off"),
                "voiceAvailable": bool(VL().enabled() and VL().upsell_price() > 0)}

    @endpoint("/me/orders")
    def me_orders(user):
        out, seen = [], set()
        for o in sorted(B.client_orders(user["id"]), key=lambda x: x.get("created_at", ""), reverse=True):
            if o.get("product") not in B.OCC.PRODUCTS or o.get("status") == "cancelled" or o.get("hidden_by_client"):
                continue
            seen.add(o["order_id"])
            done = o.get("status") == "done"
            item = {"orderId": o["order_id"], "product": o["product"], "icon": B.OCC.PRODUCTS[o["product"]]["icon"],
                    "title": B.OCC.PRODUCTS[o["product"]]["title"], "giftFor": o.get("gift_for"),
                    "status": "delivered" if done else "awaiting_payment",
                    "price": (o.get("test_price_rub") if o.get("is_test") else o.get("price_rub")),
                    "test": bool(o.get("is_test")), "opened": bool(o.get("opened_at")),
                    "createdAt": o.get("created_at"), "cardRef": o.get("card_ref")}
            if done:
                item.update(envelopeUrl=B.occ_web_envelope(o), opened=bool(o.get("opened_at")),
                            previewUrl=signed(o["order_id"], f"/orders/{o['order_id']}/card.jpg"))
            out.append(item)
        for f in sorted(os.listdir(drafts_dir()), reverse=True)[:50]:
            d = B.read_json(os.path.join(drafts_dir(), f), None)
            if d and d.get("chat_id") == user["id"] and d["order_id"] not in seen and d.get("letter"):
                out.append({"orderId": d["order_id"], "product": d["product"],
                            "icon": B.OCC.PRODUCTS[d["product"]]["icon"], "title": B.OCC.PRODUCTS[d["product"]]["title"],
                            "giftFor": d.get("name"), "status": "preview", "opened": False,
                            "createdAt": d.get("created_at")})
        return {"items": out}

    @endpoint("/me/orders/clear-opened", methods=("POST",))
    def clear_opened(user):
        """Скрыть у клиента все выданные письма, которые получатель уже открыл. Записи и деньги не трогаем."""
        n = 0
        for o in B.client_orders(user["id"]):
            if o.get("status") == "done" and o.get("opened_at") and not o.get("hidden_by_client"):
                o["hidden_by_client"] = True
                B.save_order(o)
                n += 1
        return {"ok": True, "hidden": n}

    @endpoint("/me/orders/<order_id>", methods=("DELETE",))
    def delete_order(order_id, user):
        """Неоплаченный заказ — отмена; выданный — только скрыть (ссылка-конверт у получателя работает)."""
        order = B.get_order(order_id) if SAFE_ID.match(order_id or "") else None
        # неоплаченный заказ любого вида отменяем всегда: иначе «У тебя есть неоплаченное письмо» не снять
        # (в списке неоплаченных бывают заказы старых или других продуктов, которых нет в OCC.PRODUCTS)
        if order and order.get("chat_id") == user["id"] and (order.get("product") in B.OCC.PRODUCTS or order.get("status") == "pending"):
            if order.get("status") == "pending":
                order["status"] = "cancelled"
                action = "cancelled"
                if order.get("group_id"):
                    B.group_letters.reopen(order["group_id"], order["order_id"])
            elif order.get("status") in ("done", "cancelled"):
                order["hidden_by_client"] = True
                action = "hidden"
            else:
                raise err("not_found", "Заказ не найден.", 404)
            B.save_order(order)
            try:
                os.remove(draft_path(order_id))
            except OSError:
                pass
            return {"ok": True, "action": action}
        d = load_draft(order_id)   # черновик, который ещё не стал заказом
        if d and d.get("chat_id") == user["id"]:
            os.remove(draft_path(order_id))
            return {"ok": True, "action": "deleted"}
        raise err("not_found", "Заказ не найден.", 404)

    @endpoint("/me/reminders")
    def reminders_list(user):
        CR = B.calendar_reminders
        profile, cal = CR.get_cal(user["id"])
        if profile is None:
            B.upsert_client(tg_user(user))
            profile, cal = CR.get_cal(user["id"])
        today = B.now_msk().date()
        items = []
        for x in cal["dates"]:
            nxt = CR.next_occurrence(x["day"], x["month"], today)
            items.append({"id": x["id"], "name": x["name"], "kind": x["kind"], "day": x["day"], "month": x["month"],
                          "next": nxt.isoformat() if nxt else None,
                          "remindOn": (nxt - CR.timedelta(days=CR.REMIND_DAYS)).isoformat() if nxt else None})
        return {"items": items, "kinds": {k: {"icon": v[0], "title": v[1], "product": v[2]}
                                          for k, v in CR.KINDS.items()}, "remindDays": CR.REMIND_DAYS}

    @endpoint("/me/reminders", methods=("POST",))
    def reminders_add(user):
        CR = B.calendar_reminders
        data = body()
        name = str(data.get("name") or "").strip()[:40]
        kind = data.get("kind") if data.get("kind") in CR.KINDS else "other"
        date = CR.parse_date(str(data.get("date") or "")) if data.get("date") else None
        if date is None:
            try:
                day, month = int(data.get("day")), int(data.get("month"))
                date = (day, month) if CR.parse_date(f"{day}.{month}") else None
            except (TypeError, ValueError):
                date = None
        if not name or date is None:
            raise err("bad_request", "Укажи имя и дату (например, 14.03).", 400)
        B.upsert_client(tg_user(user))
        if len(CR.get_cal(user["id"])[1]["dates"]) >= 30:
            raise err("limit", "Можно сохранить до 30 дат.", 409)
        entry = CR.add_date(user["id"], name, kind, date[0], date[1])
        return {"id": entry["id"], "name": name, "kind": kind, "day": date[0], "month": date[1]}

    @endpoint("/me/reminders/<rid>", methods=("DELETE",))
    def reminders_delete(rid, user):
        CR = B.calendar_reminders
        profile, cal = CR.get_cal(user["id"])
        if profile is None:
            raise err("not_found", "Напоминание не найдено.", 404)
        before = len(cal["dates"])
        cal["dates"] = [x for x in cal["dates"] if x["id"] != rid]
        if len(cal["dates"]) == before:
            raise err("not_found", "Напоминание не найдено.", 404)
        CR._save(user["id"], profile)
        return {"ok": True}

    @endpoint("/me/attribution", methods=("POST",))
    def attribution(user):
        source = clean_source(body().get("source"))
        if not source:
            raise err("bad_source", "Метка источника: только латиница, цифры, _ и -, до 40 знаков.", 400)
        return apply_attribution(user, source)

    @endpoint("/me/invite", methods=("POST",))
    def invite(user):
        import urllib.parse
        P = B.partners
        link = P.friend_link(user["id"])
        text = f"Дарю тебе −{P.FRIEND_DISCOUNT}% на первое письмо от Алисы Невской 🤍"
        return {"link": link, "text": text, "discount": P.FRIEND_DISCOUNT, "bonusRub": P.FRIEND_BONUS_RUB,
                "shareUrl": "https://t.me/share/url?url=" + urllib.parse.quote(link) + "&text=" + urllib.parse.quote(text)}


def run_ai_job(job_id, sel, order):
    """Рисует картинку в фоне. Попытка тратится только при успехе (как в боте)."""
    AC = B.ai_card
    order_id = order["order_id"]
    try:
        img = AC.generate_image(AC.build_prompt(sel))
        with LOCK:
            n = JOBS[job_id]["n"]
            with open(os.path.join(AC._dir(), f"{order_id}_{n}.jpg"), "wb") as f:
                f.write(img)
            AC.log_gen(order_id)
        B.cb_event(order["chat_id"], "ai_gen", order["product"], f"ai:{order_id}_{n}")
        JOBS[job_id]["status"] = "done"
    except Exception as exc:
        B.log.error("miniapp ai card %s: %s", order_id, exc)
        JOBS[job_id]["status"] = "failed"
    finally:
        with LOCK:
            INFLIGHT[order_id] = max(0, INFLIGHT[order_id] - 1)
