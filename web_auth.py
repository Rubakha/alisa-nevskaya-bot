"""Вход на сайте без Telegram: e-mail + код из 6 цифр, гостевой аккаунт `web<N>`.

Подключается из miniapp_api (current_user): заголовок `Authorization: Bearer <токен>` даёт пользователя с
большим числовым id (WEB_ID_BASE + N), чтобы заказы, лимиты и кабинет работали теми же функциями, что и для
Telegram. Пользователь помечен channel="web": выдача (fulfill) в Telegram ему не шлётся.

Ручки (префикс /api/v1): POST /auth/email/start, POST /auth/email/verify, GET /auth/me, POST /auth/logout.
Безопасность: код хранится хэшем (HMAC), живёт 15 минут, 5 попыток; лимит 5 кодов в час на e-mail и на IP;
токен подписан HMAC, срок 60 дней, отзывается выходом; в ответах нет ни кода, ни хэшей.
Почта: MAIL_BACKEND=stub (по умолчанию: пишет письмо в DATA_DIR/web_accounts/outbox.jsonl, наружу не шлёт) или
smtp (MAIL_SMTP_HOST/PORT/USER/PASSWORD/FROM; подходит Яндекс 360, Unisender Go SMTP, Resend SMTP).
Тесты: python tests/test_web_auth.py
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import threading
import time
from email.message import EmailMessage

WEB_ID_BASE = 9_000_000_000_000   # заведомо выше любого Telegram id
CODE_TTL = 15 * 60
CODE_TRIES = 5
CODES_PER_HOUR = 5
TOKEN_TTL = 60 * 86400
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s.]{2,24}$")
LOCK = threading.RLock()


def _dir():
    d = os.path.join(os.getenv("DATA_DIR", "data"), "web_accounts")
    os.makedirs(d, exist_ok=True)
    return d


def _load(name, default):
    try:
        with open(os.path.join(_dir(), name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(name, data):
    path = os.path.join(_dir(), name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _secret(B):
    base = os.getenv("WEB_AUTH_SECRET") or str(B.TG_BOT_TOKEN)
    return hashlib.sha256(b"web-auth:" + base.encode()).digest()


def norm_email(value):
    value = str(value or "").strip().lower()
    return value if EMAIL_RE.match(value) else ""


def is_web(user):
    return bool(user) and user.get("channel") == "web"


def mask(email):
    name, _, host = email.partition("@")
    return (name[:1] + "***@" + host) if name else email


# ── аккаунты и токены ────────────────────────────────────────────
def account_for(email):
    """Находит или создаёт аккаунт по e-mail; возвращает {id, n, email}."""
    with LOCK:
        acc = _load("accounts.json", {"next": 1, "by_email": {}})
        item = acc["by_email"].get(email)
        if not item:
            item = {"n": acc["next"], "created": int(time.time())}
            acc["next"] += 1
            acc["by_email"][email] = item
            _save("accounts.json", acc)
        return {"id": WEB_ID_BASE + item["n"], "n": item["n"], "email": email}


def email_of(user_id):
    n = int(user_id) - WEB_ID_BASE
    acc = _load("accounts.json", {"by_email": {}})
    for email, item in acc["by_email"].items():
        if item["n"] == n:
            return email
    return ""


def make_token(B, user_id):
    sid = secrets.token_urlsafe(9).replace(".", "-")
    exp = int(time.time()) + TOKEN_TTL
    with LOCK:
        now = time.time()
        sessions = {k: v for k, v in _load("sessions.json", {}).items() if v["exp"] > now}
        sessions[sid] = {"id": user_id, "exp": exp}
        _save("sessions.json", sessions)
    payload = f"{user_id}.{exp}.{sid}"
    sig = hmac.new(_secret(B), payload.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{payload}.{sig}"


def parse_token(B, token):
    """Возвращает (user_dict, sid) или (None, None)."""
    try:
        uid, exp, sid, sig = str(token).split(".")
        good = hmac.new(_secret(B), f"{uid}.{exp}.{sid}".encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(good, sig) or int(exp) < time.time():
            return None, None
        sess = _load("sessions.json", {}).get(sid)
        if not sess or sess["id"] != int(uid):
            return None, None
    except (ValueError, TypeError):
        return None, None
    return {"id": int(uid), "first_name": "", "last_name": "", "username": "", "channel": "web"}, sid


def bearer(request):
    head = request.headers.get("Authorization", "")
    return head[7:].strip() if head.lower().startswith("bearer ") else ""


def user_from_request(B, request):
    token = bearer(request)
    return parse_token(B, token)[0] if token else None


# ── почта ────────────────────────────────────────────────────────
def send_mail(B, to, subject, text):
    """stub: пишет в outbox.jsonl (без отправки); smtp: реальная отправка. True при успехе."""
    if os.getenv("MAIL_BACKEND", "stub").lower() == "smtp":
        msg = EmailMessage()
        msg["From"] = os.getenv("MAIL_FROM", os.getenv("MAIL_SMTP_USER", ""))
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(text)
        try:
            port = int(os.getenv("MAIL_SMTP_PORT", "465"))
            cls = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
            with cls(os.getenv("MAIL_SMTP_HOST", ""), port, timeout=15) as s:
                if cls is smtplib.SMTP:
                    s.starttls()
                s.login(os.getenv("MAIL_SMTP_USER", ""), os.getenv("MAIL_SMTP_PASSWORD", ""))
                s.send_message(msg)
            return True
        except Exception as exc:
            B.log.error("web_auth mail: %s", type(exc).__name__)
            return False
    with LOCK:
        with open(os.path.join(_dir(), "outbox.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "to": to, "subject": subject, "text": text},
                               ensure_ascii=False) + "\n")
    return True


def _code_hash(B, email, code):
    return hmac.new(_secret(B), f"{email}|{code}".encode(), hashlib.sha256).hexdigest()


# ── ручки ────────────────────────────────────────────────────────
def register(A, B):
    from flask import request

    @A.endpoint("/auth/email/start", methods=("POST",), auth=False)
    def auth_email_start():
        email = norm_email(A.body().get("email"))
        if not email:
            raise A.err("bad_email", "Проверь адрес: он выглядит неверно.")
        for key in (f"mail:{email}", f"mailip:{A.client_ip()}"):
            ok, wait = A.hit(key, CODES_PER_HOUR, 3600)
            if not ok:
                raise A.err("rate_limited", "Код уже отправляли. Подожди немного.", 429, retryAfter=wait)
        code = f"{secrets.randbelow(10**6):06d}"
        with LOCK:
            now = time.time()
            codes = {k: v for k, v in _load("codes.json", {}).items() if v["exp"] > now}
            codes[email] = {"h": _code_hash(B, email, code), "exp": now + CODE_TTL, "tries": 0}
            _save("codes.json", codes)
        text = (f"Твой код для входа: {code}\n\nОн действует 15 минут. "
                "Если это не ты, просто закрой письмо.\n\nАлиса Невская, цифровой персонаж на основе ИИ")
        if not send_mail(B, email, "Код для входа: " + code, text):
            raise A.err("mail_failed", "Не получилось отправить письмо. Попробуй позже.", 502)
        return {"ok": True, "email": mask(email), "ttl": CODE_TTL}

    @A.endpoint("/auth/email/verify", methods=("POST",), auth=False)
    def auth_email_verify():
        data = A.body()
        email = norm_email(data.get("email"))
        code = re.sub(r"\D", "", str(data.get("code", "")))
        if not email or len(code) != 6:
            raise A.err("bad_code", "Введи 6 цифр из письма.")
        with LOCK:
            codes = _load("codes.json", {})
            item = codes.get(email)
            if not item or item["exp"] < time.time() or item["tries"] >= CODE_TRIES:
                raise A.err("code_expired", "Код устарел. Запроси новый.", 400)
            item["tries"] += 1
            good = hmac.compare_digest(item["h"], _code_hash(B, email, code))
            if good:
                codes.pop(email, None)
            _save("codes.json", codes)
        if not good:
            raise A.err("bad_code", "Код не подошёл. Проверь цифры.", 400)
        acc = account_for(email)
        return {"token": make_token(B, acc["id"]), "user": {"id": acc["id"], "email": mask(email), "channel": "web"}}

    @A.endpoint("/auth/me", auth=True)
    def auth_me(user):
        if not is_web(user):
            return {"channel": "telegram", "id": user["id"]}
        return {"channel": "web", "id": user["id"], "email": mask(email_of(user["id"]))}

    @A.endpoint("/auth/logout", methods=("POST",), auth=True)
    def auth_logout(user):
        if is_web(user):
            sid = parse_token(B, bearer(request))[1]
            with LOCK:
                sessions = _load("sessions.json", {})
                sessions.pop(sid, None)
                _save("sessions.json", sessions)
        return {"ok": True}
