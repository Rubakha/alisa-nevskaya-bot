"""Продажа «Разбора совместимости пары» на сайте (без Telegram): заказ → ЮKassa → статус → привязка к паре.

Имена и даты рождения сюда НЕ приходят: расчёт и тексты живут на сайте (функции Cloudflare Pages), бот знает только
заказ, оплату, почту для чека и обезличенный ключ пары (чтобы одну оплату нельзя было раздать на много пар).
Заказы лежат отдельно от писем (DATA_DIR/compat), поэтому отчёты, напоминания и статистика по письмам их не видят.

Включается переменной COMPAT_SECRET (общий секрет с функциями сайта; без неё все ручки отвечают 503).
  COMPAT_SECRET            общий секрет для заголовка X-Compat-Secret
  COMPAT_PRICE_RUB         цена разбора, ₽ (по умолчанию 149)
  COMPAT_RETURN_ORIGINS    допустимые адреса возврата после оплаты, через запятую (по умолчанию https://alisa-letters.pages.dev)
"""
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

from flask import request

B = None
ID_RE = re.compile(r"^CMP-[A-Za-z0-9_-]{8,24}$")
PAIR_RE = re.compile(r"^[a-z0-9]{1,16}$")
LOCK = threading.Lock()


def price():
    try:
        return max(1, int(os.getenv("COMPAT_PRICE_RUB", "149")))
    except ValueError:
        return 149


def _dir():
    d = os.path.join(B.DATA_DIR, "compat")
    os.makedirs(d, exist_ok=True)
    return d


def _path(order_id):
    return os.path.join(_dir(), f"{order_id}.json")


def load(order_id):
    if not ID_RE.match(order_id or ""):
        return None
    try:
        with open(_path(order_id), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(order):
    tmp = _path(order["order_id"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(order, f, ensure_ascii=False)
    os.replace(tmp, _path(order["order_id"]))


def authorized():
    secret = os.getenv("COMPAT_SECRET", "")
    got = request.headers.get("X-Compat-Secret", "")
    return bool(secret) and hmac.compare_digest(got.encode(), secret.encode())


def return_ok(url):
    origins = [o.strip().rstrip("/") for o in os.getenv("COMPAT_RETURN_ORIGINS", "https://alisa-letters.pages.dev").split(",") if o.strip()]
    return any(url == o or url.startswith(o + "/") for o in origins)


def create_payment(order, return_url):
    amount = f"{order['price_rub']}.00"
    payload = {
        "amount": {"value": amount, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": f"Разбор совместимости пары {order['order_id']}"[:128],
        "metadata": {"order_id": order["order_id"], "kind": "compat"},
    }
    if B.YOOKASSA_RECEIPT:
        payload["receipt"] = {
            "customer": {"email": order["email"]},
            "items": [{
                "description": "Разбор совместимости пары (развлекательный текст, цифровой файл)",
                "quantity": "1.00",
                "amount": {"value": amount, "currency": "RUB"},
                "vat_code": B.YOOKASSA_VAT_CODE,
                "payment_mode": "full_payment",
                "payment_subject": "service",
            }],
        }
    payment = B.yk_request("POST", B.YK_API, payload, idem=f"{order['order_id']}-{secrets.token_hex(6)}")
    order["yk_payment_id"] = payment["id"]
    order["yk_created_at"] = B.now_msk().isoformat()
    save(order)
    return payment["confirmation"]["confirmation_url"]


def check(order_id):
    """Сверяет платёж с ЮKassa и отмечает оплаченным. Возвращает заказ (или None)."""
    with LOCK:
        order = load(order_id)
        if not order or order.get("status") == "paid" or not order.get("yk_payment_id"):
            return order
        payment = B.yk_request("GET", f"{B.YK_API}/{order['yk_payment_id']}")
        status = payment.get("status")
        if status == "succeeded" and payment.get("paid") and (payment.get("amount") or {}).get("value") == f"{order['price_rub']}.00":
            order["status"] = "paid"
            order["paid_at"] = B.now_msk().isoformat()
            save(order)
            try:
                if B.ADMIN_ID:
                    B.safe_send(B.ADMIN_ID, f"💰 Совместимость: +{order['price_rub']} ₽ · <code>{order['order_id']}</code>")
            except Exception as exc:
                B.log.error("compat notify %s: %s", order_id, exc)
        elif status == "canceled":
            order["status"] = "cancelled"
            save(order)
        return order


def poll_loop():
    while True:
        try:
            base = _dir()
            for fn in os.listdir(base):
                if not fn.endswith(".json"):
                    continue
                o = load(fn[:-5])
                if not o or o.get("status") != "pending" or not o.get("yk_payment_id"):
                    continue
                if B.now_msk() - datetime.fromisoformat(o["yk_created_at"]) > timedelta(hours=24):
                    o["status"] = "expired"
                    save(o)
                    continue
                try:
                    check(o["order_id"])
                except Exception as exc:
                    B.log.error("compat poll %s: %s", o["order_id"], exc)
        except Exception as exc:
            B.log.error("compat poll loop: %s", exc)
        time.sleep(20)


def register(app, bot_module):
    global B
    B = bot_module

    def guard():
        if not os.getenv("COMPAT_SECRET"):
            return {"error": "disabled"}, 503
        if not authorized():
            return {"error": "forbidden"}, 403
        return None

    @app.route("/compat/order", methods=["POST"])
    def compat_order():
        bad = guard()
        if bad:
            return bad
        if not B.yk_enabled():
            return {"error": "payment_unavailable"}, 503
        body = request.get_json(silent=True) or {}
        email = str(body.get("email") or "").strip()[:120]
        ret = str(body.get("returnUrl") or "").strip()[:300]
        if B.YOOKASSA_RECEIPT and not B.EMAIL_RE.match(email):
            return {"error": "email_required"}, 400
        if not return_ok(ret):
            return {"error": "bad_return_url"}, 400
        order = {"order_id": "CMP-" + secrets.token_urlsafe(9), "kind": "compat", "status": "pending", "price_rub": price(),
                 "email": email, "created_at": B.now_msk().isoformat(), "pair": None}
        save(order)
        sep = "&" if "?" in ret else "?"
        try:
            url = create_payment(order, f"{ret}{sep}o={order['order_id']}")
        except Exception as exc:
            B.log.error("compat create %s: %s", order["order_id"], exc)
            return {"error": "payment_failed"}, 502
        return {"orderId": order["order_id"], "paymentUrl": url, "price": order["price_rub"]}, 200

    @app.route("/compat/order/<order_id>", methods=["GET"])
    def compat_status(order_id):
        bad = guard()
        if bad:
            return bad
        order = load(order_id)
        if not order:
            return {"error": "not_found"}, 404
        if order.get("status") == "pending":
            try:
                order = check(order_id) or order
            except Exception as exc:
                B.log.error("compat status %s: %s", order_id, exc)
        return {"status": order.get("status"), "price": order.get("price_rub")}, 200

    @app.route("/compat/claim/<order_id>", methods=["POST"])
    def compat_claim(order_id):
        bad = guard()
        if bad:
            return bad
        order = load(order_id)
        if not order:
            return {"error": "not_found"}, 404
        if order.get("status") != "paid":
            return {"error": "not_paid"}, 402
        pair = str((request.get_json(silent=True) or {}).get("pair") or "")
        if not PAIR_RE.match(pair):
            return {"error": "bad_pair"}, 400
        with LOCK:
            order = load(order_id)
            if order.get("pair") and order["pair"] != pair:
                return {"error": "other_pair"}, 409
            if not order.get("pair"):
                order["pair"] = pair
                order["claimed_at"] = B.now_msk().isoformat()
                save(order)
        return {"ok": True}, 200

    if not getattr(register, "_started", False):
        register._started = True
        threading.Thread(target=poll_loop, name="compat_poll", daemon=True).start()
