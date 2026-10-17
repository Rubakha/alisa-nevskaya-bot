"""Вход по e-mail без Telegram: код, токен, лимиты, выход, заказ гостя. Запуск: python tests/test_web_auth.py"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["ENVELOPE_BASE"] = "off"
os.environ["TG_BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["MINIAPP_API"] = "1"
os.environ["MAIL_BACKEND"] = "stub"

import bot_best as B  # noqa: E402
import miniapp_api as A  # noqa: E402
import web_auth as W  # noqa: E402

API = "/api/v1"
client = B.app.test_client()
OK = []


def check(name, cond):
    OK.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + name)


def post(path, data=None, token=None, ip="1.1.1.1"):
    h = {"X-Forwarded-For": ip}
    if token:
        h["Authorization"] = "Bearer " + token
    return client.post(API + path, json=data or {}, headers=h)


def last_code():
    with open(os.path.join(os.environ["DATA_DIR"], "web_accounts", "outbox.jsonl"), encoding="utf-8") as f:
        mail = json.loads(f.readlines()[-1])
    return mail["text"].split(": ")[1][:6]


r = post("/auth/email/start", {"email": "не-почта"})
check("плохой адрес → 400", r.status_code == 400 and r.get_json()["error"]["code"] == "bad_email")

r = post("/auth/email/start", {"email": "Katya@Example.com"})
check("код отправлен, адрес замаскирован", r.status_code == 200 and r.get_json()["email"] == "k***@example.com")
code = last_code()
check("код не попал в ответ", code not in r.get_data(as_text=True))

r = post("/auth/email/verify", {"email": "katya@example.com", "code": "000000" if code != "000000" else "111111"})
check("неверный код → 400", r.status_code == 400 and r.get_json()["error"]["code"] == "bad_code")

r = post("/auth/email/verify", {"email": "katya@example.com", "code": code})
tok = r.get_json().get("token", "")
check("верный код → токен", r.status_code == 200 and tok.count(".") == 3)
uid = r.get_json()["user"]["id"]
check("id выше telegram-диапазона", uid > W.WEB_ID_BASE)

r = post("/auth/email/verify", {"email": "katya@example.com", "code": code})
check("код одноразовый", r.status_code == 400 and r.get_json()["error"]["code"] == "code_expired")

r = client.get(API + "/auth/me", headers={"Authorization": "Bearer " + tok})
check("/auth/me по токену", r.status_code == 200 and r.get_json()["channel"] == "web" and r.get_json()["id"] == uid)

r = client.get(API + "/auth/me", headers={"Authorization": "Bearer " + tok[:-2] + "xx"})
check("подделанный токен → 401", r.status_code == 401)
r = client.get(API + "/auth/me")
check("без токена → 401", r.status_code == 401)

post("/auth/email/start", {"email": "katya@example.com"}, ip="2.2.2.2")
r2 = post("/auth/email/verify", {"email": "katya@example.com", "code": last_code()}, ip="2.2.2.2")
check("повторный вход → тот же аккаунт", r2.get_json()["user"]["id"] == uid)

r = post("/auth/email/start", {"email": "brute@example.com"}, ip="3.3.3.3")
good = last_code()
bad = "000000" if good != "000000" else "111111"
for _ in range(W.CODE_TRIES):
    post("/auth/email/verify", {"email": "brute@example.com", "code": bad}, ip="3.3.3.3")
r = post("/auth/email/verify", {"email": "brute@example.com", "code": good}, ip="3.3.3.3")
check("после 5 неверных попыток верный код уже не принимается", r.status_code == 400)

for i in range(W.CODES_PER_HOUR + 1):
    r = post("/auth/email/start", {"email": "spam@example.com"}, ip=f"9.9.9.{i}")
check("лимит кодов на e-mail → 429", r.status_code == 429)

r = post("/auth/logout", token=tok)
check("выход", r.status_code == 200)
r = client.get(API + "/auth/me", headers={"Authorization": "Bearer " + tok})
check("после выхода токен мёртв", r.status_code == 401)

tok2 = r2.get_json()["token"]
r = client.get(API + "/catalog", headers={"Authorization": "Bearer " + tok2})
check("публичные ручки работают с токеном", r.status_code == 200)
r = client.options(API + "/orders", headers={"Origin": "https://alisa-letters.pages.dev",
                                              "Access-Control-Request-Method": "POST"})
check("CORS предзапрос", r.status_code in (200, 204))
r = client.get(API + "/catalog", headers={"Origin": "https://alisa-letters.pages.dev"})
check("CORS-заголовок Authorization", "Authorization" in r.headers.get("Access-Control-Allow-Headers", ""))

ANSWERS = {"name": "Катя", "who": "подруга", "age": "30", "details": "черешня на крыше", "tone": "warm",
           "sign": "Серёжа"}
H2 = {"Authorization": "Bearer " + tok2}
r = client.post(API + "/orders", json={"product": "birthday", "answers": ANSWERS}, headers=H2)
oid = (r.get_json() or {}).get("orderId")
check("гость создаёт заказ без Telegram", r.status_code == 200 and oid)
r = client.get(API + f"/orders/{oid}", headers=H2)
check("гость читает свой заказ", r.status_code == 200)
post("/auth/email/start", {"email": "other@example.com"}, ip="4.4.4.4")
tok3 = post("/auth/email/verify", {"email": "other@example.com", "code": last_code()}, ip="4.4.4.4").get_json()["token"]
r = client.get(API + f"/orders/{oid}", headers={"Authorization": "Bearer " + tok3})
check("чужой гость заказ не видит", r.status_code in (403, 404))

GH = {"X-Guest-Token": "g_" + "ab12" * 8}
r = client.post(API + "/orders", json={"product": "birthday", "answers": ANSWERS}, headers=GH)
gid = (r.get_json() or {}).get("orderId")
check("гостевой токен создаёт заказ", r.status_code == 200 and gid)
check("плохой токен → 401", client.post(API + "/orders", json={}, headers={"X-Guest-Token": "zzz"}).status_code == 401)
r = client.get(API + f"/orders/{gid}", headers={"X-Guest-Token": "g_" + "cd34" * 8})
check("чужой гостевой токен заказ не видит", r.status_code in (403, 404))
d = A.load_draft(gid)
d["letter"] = "Катя, привет.\n\nПисьмо."
A.save_draft(d)
B.YOOKASSA_SHOP_ID, B.YOOKASSA_SECRET_KEY = "s", "k"
B.yk_create = lambda order, email, ret: "https://pay.example/x"
body = {"orderId": gid, "email": "Guest@Mail.ru", "returnUrl": "https://alisa-letters.pages.dev/pay/return/?paid=" + gid,
        "consent": {"offer": True, "privacy": True, "at": "2026-10-17T10:00:00Z", "version": "draft-2026-10"}}
r = client.post(API + "/guest/orders", json={**body, "consent": {"offer": True}}, headers=GH)
check("без согласия → consent_required", r.status_code == 400 and r.get_json()["error"]["code"] == "consent_required")
r = client.post(API + "/guest/orders", json={**body, "returnUrl": "https://evil.example/pay/return/"}, headers=GH)
check("чужой returnUrl → bad_return_url", r.get_json()["error"]["code"] == "bad_return_url")
r = client.post(API + "/guest/orders", json=body, headers=GH)
check("оплата гостя: ссылка ЮKassa", r.status_code == 200 and r.get_json().get("paymentUrl") == "https://pay.example/x"
      and r.get_json()["price"] > 0)
check("e-mail и согласие сохранены в заказе", (B.get_order(gid) or {}).get("email") == "guest@mail.ru"
      and (B.get_order(gid) or {}).get("consent", {}).get("version") == "draft-2026-10")

sent = []
B.bot.send_message = lambda *a, **k: sent.append(a)
B.bot.send_document = B.bot.send_photo = B.bot.send_message
B.notify_admin_paid = lambda order: None
oid2 = B.new_order_id()
guest_id = W.account_for("katya@example.com")["id"]
B.save_order({"order_id": oid2, "chat_id": guest_id, "status": "pending", "price_rub": 199, "product": "birthday",
              "channel": "web", "email": "katya@example.com", "letter_text": "Привет"})
B.fulfill_order(guest_id, oid2, "ch-1", "katya@example.com")
o = B.get_order(oid2)
check("оплата гостя: заказ done", o["status"] == "done")
check("оплата гостя: в Telegram ничего не шлём", not sent)
with open(os.path.join(os.environ["DATA_DIR"], "web_accounts", "outbox.jsonl"), encoding="utf-8") as f:
    mail = json.loads(f.readlines()[-1])
check("оплата гостя: ссылка ушла на почту", mail["to"] == "katya@example.com" and oid2 in mail["text"])

print(f"\n{sum(OK)}/{len(OK)}")
sys.exit(0 if all(OK) else 1)
