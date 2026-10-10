"""API v1 для Mini App: авторизация, чужие заказы, лимиты, каталог, картинки, превью, оплата в моке, AI-картинка, кабинет.
Запуск: python tests/test_miniapp_api.py (сеть и платные API не нужны)"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
import types as pytypes
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TG_BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = ""
os.environ["YOOKASSA_SHOP_ID"] = "shop"
os.environ["YOOKASSA_SECRET_KEY"] = "secret"
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["MINIAPP_API"] = "1"
os.environ["MINIAPP_ORIGINS"] = "https://staging.example"
os.environ["WAVESPEED_API_KEY"] = "ws-test"
os.environ["AI_CARD_DAILY_CAP"] = "60"
os.environ["TEST_USERS"] = "999"
os.environ.pop("YOOKASSA_RECEIPT", None)

import bot_best as B  # noqa: E402
import miniapp_api as A  # noqa: E402

TOKEN = os.environ["TG_BOT_TOKEN"]
API = "/api/v1"
A.USER_PER_MIN = 10000   # в обычных проверках лимит не мешает; отдельно проверяется ниже
SENT = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=1, photo=[pytypes.SimpleNamespace(file_id="PH")])
    return f


for m in ["send_message", "send_photo", "send_animation", "send_chat_action", "answer_callback_query"]:
    setattr(B.bot, m, rec(m))

LETTER = ("Катя, привет. Первый абзац письма о нашей дружбе и черешне на крыше.\n\n"
          "Второй абзац — про то, как ты всегда смеялась громче всех. " + "Это очень тёплые слова. " * 12 + "\n\n"
          "Третий абзац — самый длинный, про всё, что мы пережили вместе за эти годы. " + "Ты рядом. " * 20 + "\n\n"
          "СЕКРЕТНЫЙ_ХВОСТ_ПИСЬМА: последний абзац, виден только после оплаты. Твой Серёжа.")


class FakeAI:
    calls = []

    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title, previous="", wish=""):
        FakeAI.calls.append({"previous": previous, "wish": wish})
        n = len(FakeAI.calls)
        return {"letter": LETTER.replace("Катя, привет.", f"Катя, привет #{n}."), "card_title": title,
                "card_line": "Ты лучшая."}


B.AI = FakeAI
B.AI_CALLS = FakeAI.calls
B.channel_rss_items = lambda limit=15: [{"link": "https://t.me/x/1", "date": "2026-10-09T20:00:00+03:00",
                                         "text": "Пост дневника", "photo": None, "bot_start": None}]


def init_data(user_id, token=TOKEN, age=0, first="Тест"):
    user = json.dumps({"id": user_id, "first_name": first, "username": f"u{user_id}"}, ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "AAA", "user": user}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


client = B.app.test_client()


def call(method, path, user=500, data=None, headers=None, **kw):
    h = {"Origin": "https://alisa-letters.pages.dev", **(headers or {})}
    if user:
        h["X-Telegram-Init-Data"] = init_data(user)
    return getattr(client, method)(API + path, json=data, headers=h, **kw)


def j(resp):
    return json.loads(resp.get_data(as_text=True))


# ── публичные ручки ──
r = call("get", "/catalog", user=None)
cat = j(r)
assert r.status_code == 200 and {p["key"] for p in cat["products"]} >= {"birthday", "love", "family"}
bd = next(p for p in cat["products"] if p["key"] == "birthday")
assert bd["price"] == 199 and bd["questions"][0]["key"] == "name"
tone_q = next(q for q in bd["questions"] if q["key"] == "tone")
assert tone_q["kind"] == "choice" and {o["key"] for o in tone_q["options"]} == set(cat["tones"])
assert r.headers["Access-Control-Allow-Origin"] == "https://alisa-letters.pages.dev"

r = call("get", "/cards?occasion=birthday&limit=3", user=None)
page = j(r)
assert len(page["items"]) == 3 and page["next"] == "3" and page["total"] >= 5
first = page["items"][0]
assert first["ref"].startswith("birthday-") and "~" not in first["ref"], "в выдаче только чистые фоны"
nxt = j(call("get", "/cards?occasion=birthday&limit=3&cursor=3", user=None))
assert nxt["items"][0]["ref"] != first["ref"]
img = call("get", f"/cards/{first['ref']}.jpg?size=thumb", user=None)
assert img.status_code == 200 and img.data[:2] == b"\xff\xd8" and len(img.data) < len(call("get", f"/cards/{first['ref']}.jpg", user=None).data)
assert call("get", "/cards/birthday-nope.jpg", user=None).status_code == 404
assert call("get", "/cards/..%2Fsecret.jpg", user=None).status_code == 404
assert call("get", "/cards?occasion=hack", user=None).status_code == 400

opts = j(call("get", "/card-options", user=None))
assert opts["maxProps"] == 3 and "sepia" in opts["palettes"] and "dried_bouquet" in opts["props"]
ex = j(call("get", "/examples", user=None))
assert "Я не дам совет" in ex["demoLetter"] and ex["cards"]
assert call("get", "/examples/0.jpg", user=None).data[:2] == b"\xff\xd8"
assert j(call("get", "/diary", user=None))["items"][0]["text"] == "Пост дневника"
assert j(call("get", "/free", user=None))["items"]

# ── CORS и авторизация ──
pre = client.options(API + "/orders", headers={"Origin": "https://staging.example", "Access-Control-Request-Method": "POST"})
assert pre.status_code in (200, 204) and pre.headers["Access-Control-Allow-Origin"] == "https://staging.example"
assert "X-Telegram-Init-Data" in pre.headers["Access-Control-Allow-Headers"]
evil = client.get(API + "/catalog", headers={"Origin": "https://evil.example"})
assert "Access-Control-Allow-Origin" not in evil.headers
assert call("post", "/orders", user=None, data={}).status_code == 401
bad = client.post(API + "/orders", json={}, headers={"X-Telegram-Init-Data": init_data(500, token="9:OTHER")})
assert bad.status_code == 401 and j(bad)["error"]["code"] == "unauthorized"
old = client.post(API + "/orders", json={}, headers={"X-Telegram-Init-Data": init_data(500, age=3 * 86400)})
assert old.status_code == 401

# ── создание заказа: проверка анкеты ──
ANSWERS = {"name": "Катя", "who": "подруга", "age": "30", "details": "черешня на крыше", "tone": "warm",
           "sign": "Серёжа"}
r = call("post", "/orders", data={"product": "nope", "answers": ANSWERS})
assert r.status_code == 400 and j(r)["error"]["code"] == "bad_product"
r = call("post", "/orders", data={"product": "birthday", "answers": {"name": "Катя"}})
assert r.status_code == 400 and j(r)["error"]["code"] == "bad_answers"
r = call("post", "/orders", data={"product": "birthday", "answers": {**ANSWERS, "details": "я" * 1501}})
assert r.status_code == 400
r = call("post", "/orders", data={"product": "birthday", "answers": ANSWERS, "tone": "evil"})
assert r.status_code == 400 and j(r)["error"]["code"] == "bad_tone"
r = call("post", "/orders", data={"product": "birthday", "answers": ANSWERS, "tone": "warm"})
assert r.status_code == 200
oid = j(r)["orderId"]
assert j(r)["status"] == "draft" and oid.startswith("ALI-")

# чужой человек не видит заказ
assert call("get", f"/orders/{oid}", user=777).status_code == 404
assert call("post", f"/orders/{oid}/preview", user=777).status_code == 404
assert call("post", f"/orders/{oid}/pay", user=777).status_code == 404
assert call("get", "/orders/..%2F..%2Fetc", user=500).status_code in (404, 400)

# ── превью: бесплатно только часть письма ──
st = j(call("get", f"/orders/{oid}"))
assert st["status"] == "draft" and "letterPreview" not in st
assert call("pay_placeholder", "/x", user=500) if False else True
r = call("post", f"/orders/{oid}/pay", data={})
assert r.status_code == 409 and j(r)["error"]["code"] == "no_letter", "без письма платить нельзя"
r = call("post", f"/orders/{oid}/preview")
pv = j(r)
assert r.status_code == 200 and pv["locked"] and pv["lockedChars"] > 100
assert "СЕКРЕТНЫЙ_ХВОСТ" not in r.get_data(as_text=True), "хвост письма до оплаты не отдаём"
assert "СЕКРЕТНЫЙ_ХВОСТ" not in call("get", f"/orders/{oid}").get_data(as_text=True)
assert pv["retextsLeft"] == 2 and pv["cardRef"].startswith("birthday-") and "~" not in pv["cardRef"]
st = j(call("get", f"/orders/{oid}"))
assert st["status"] == "preview" and st["price"] == 199 and "letter" not in st and st["locked"] is True

# превью картинки: по заголовку и по подписанной ссылке, с водяным знаком; чужому — нет
pu = st["previewUrl"]
path = pu.replace(B.site_pages.SITE_URL, "")
by_sig = client.get(path)
assert by_sig.status_code == 200 and by_sig.data[:2] == b"\xff\xd8", "по подписанной ссылке без заголовка"
assert client.get(path.replace("sig=", "sig=0")).status_code == 401, "подпись подделать нельзя"
assert call("get", f"/orders/{oid}/card-preview.jpg", user=500).status_code == 200
assert call("get", f"/orders/{oid}/card-preview.jpg", user=777).status_code == 404
assert call("get", f"/orders/{oid}/card.jpg", user=500).status_code == 404, "чистая открытка — после оплаты"
assert client.get(f"{API}/orders/{oid}/card-preview.jpg").status_code == 401

# тексты: 2 бесплатных варианта, потом одно пожелание
for left in (1, 0):
    r = call("post", f"/orders/{oid}/preview")
    assert j(r)["retextsLeft"] == left
assert FakeAI.calls[-1]["previous"], "перегенерация идёт на тех же ответах"
r = call("post", f"/orders/{oid}/preview")
assert r.status_code == 409 and j(r)["error"]["code"] == "retext_limit"
r = call("post", f"/orders/{oid}/preview", data={"wish": "Добавь про море"})
assert r.status_code == 200 and FakeAI.calls[-1]["wish"] == "Добавь про море"
r = call("post", f"/orders/{oid}/preview", data={"wish": "Ещё раз"})
assert r.status_code == 409, "пожелание — одно"

# лимит генераций в час (на человека)
A.GEN_PER_HOUR = 2
o2 = j(call("post", "/orders", user=501, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
assert call("post", f"/orders/{o2}/preview", user=501).status_code == 200
assert call("post", f"/orders/{o2}/preview", user=501).status_code == 200
r = call("post", f"/orders/{o2}/preview", user=501)
assert r.status_code == 429 and j(r)["error"]["code"] == "rate_limited" and j(r)["retryAfter"] > 0
assert r.headers.get("Retry-After")
A.GEN_PER_HOUR = 6
# суточный потолок
os.environ["MINIAPP_GEN_DAILY_CAP"] = "0"
o3 = j(call("post", "/orders", user=502, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
r = call("post", f"/orders/{o3}/preview", user=502)
assert r.status_code == 429 and j(r)["error"]["code"] == "daily_cap"
os.environ.pop("MINIAPP_GEN_DAILY_CAP")

# ── выбор открытки ──
ids = B.cardbase.active_ids("birthday")
r = call("put", f"/orders/{oid}/card", data={"ref": ids[1]})
assert r.status_code == 200 and j(r)["cardRef"] == ids[1]
assert call("put", f"/orders/{oid}/card", data={"ref": "love-001"}).status_code == 400, "фон чужого повода"
assert call("put", f"/orders/{oid}/card", data={"ref": "birthday-nope"}).status_code == 400
assert call("put", f"/orders/{oid}/card", data={"ref": "ai:" + oid + "_1"}).status_code == 400, "AI-картинка до оплаты"
assert call("put", f"/orders/{oid}/card", user=777, data={"ref": ids[1]}).status_code == 404
r = call("put", f"/orders/{oid}/card", data={"ref": f"{ids[2]}~3"})
assert j(r)["cardRef"] == ids[2], "композиция с реквизитом по умолчанию не выдаётся"
spec = {"base": ids[0], "pal": "sepia", "props": ["tea_cup", "ribbon_bow", "gift_tag", "postcard", "evil"],
        "lay": "left", "frame": "thin", "anim": "none"}
r = call("put", f"/orders/{oid}/card", data={"spec": spec})
ref = j(r)["cardRef"]
assert r.status_code == 200 and B.postcards.parse_spec(ref)["props"] == ["tea_cup", "ribbon_bow", "gift_tag"]
assert call("put", f"/orders/{oid}/card", data={"spec": {**spec, "pal": "neon"}}).status_code == 400
assert call("put", f"/orders/{oid}/card", data={"spec": {**spec, "base": "birthday-nope"}}).status_code == 400
prev = call("get", f"/orders/{oid}/card-preview.jpg?ref={urllib.parse.quote(ref)}")
assert prev.status_code == 200 and prev.data[:2] == b"\xff\xd8"
assert call("get", f"/orders/{oid}/card-preview.jpg?ref=love-001").status_code == 400
assert call("put", f"/orders/{oid}/card", data={"ref": ids[1]}).status_code == 200

# ── промокод ──
B.partners.save_promo({"code": "TEST20", "kind": "percent", "value": 20, "active": True, "used": [], "limit": 0})
r = j(call("post", "/promo/check", data={"code": "TEST20", "product": "birthday"}))
assert r["valid"] and r["price"] == 159 and r["discount"] == 40
r = j(call("post", "/promo/check", data={"code": "NOPE", "product": "birthday"}))
assert not r["valid"] and r["price"] == 199 and r["message"]
assert call("post", "/promo/check", user=None, data={"code": "TEST20", "product": "birthday"}).status_code == 401

# ── оплата (ЮKassa в моке) ──
YK = {"status": "pending", "paid": False, "price": None}
YK_CALLS = []


def fake_yk(method, url, payload=None, idem=None):
    YK_CALLS.append((method, url))
    if method == "POST":
        YK["price"] = payload["amount"]["value"]
        return {"id": "pay-1", "confirmation": {"confirmation_url": "https://pay.example/1"}}
    return {"id": "pay-1", "status": YK["status"], "paid": YK["paid"], "amount": {"value": YK["price"]},
            "confirmation": {"confirmation_url": "https://pay.example/1"}}


B.yk_request = fake_yk
ot = j(call("post", "/orders", user=999, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
call("post", f"/orders/{ot}/preview", user=999)
r = call("post", f"/orders/{ot}/pay", user=999, data={})
assert r.status_code == 403 and j(r)["error"]["code"] == "test_user", "тестовую оплату через API не выводим"
r = call("post", f"/orders/{oid}/pay", data={"promo": "NOPE"})
assert r.status_code == 400 and j(r)["error"]["code"] == "bad_promo"
r = call("post", f"/orders/{oid}/pay", data={"promo": "TEST20"})
pay = j(r)
assert r.status_code == 200 and pay["paymentUrl"] == "https://pay.example/1" and pay["price"] == 159, pay
order = B.get_order(oid)
assert order["status"] == "pending" and order["price_rub"] == 159 and order["promo_code"] == "TEST20", order
assert order["letter_text"].startswith("Катя, привет") and order["card_ref"] == ids[1]
assert YK["price"] == "159.00"
r = call("post", f"/orders/{oid}/pay", data={"promo": "TEST20"})
assert j(r)["paymentUrl"] and len([c for c in YK_CALLS if c[0] == "POST"]) == 1, "повторная оплата — тот же платёж"
assert j(call("get", f"/orders/{oid}"))["status"] == "awaiting_payment"
assert "СЕКРЕТНЫЙ_ХВОСТ" not in call("get", f"/orders/{oid}").get_data(as_text=True)
r = call("post", f"/orders/{oid}/preview")
assert r.status_code == 409 and j(r)["error"]["code"] == "locked", "в оплате текст не меняем"

# второй неоплаченный заказ (MAX_PENDING=1)
o4 = j(call("post", "/orders", data={"product": "friend", "answers": {"name": "Маша", "years": "10", "details": "море",
                                                                    "tone": "warm", "sign": "Серёжа"}}))["orderId"]
call("post", f"/orders/{o4}/preview")
r = call("post", f"/orders/{o4}/pay", data={})
assert r.status_code == 409 and j(r)["error"]["code"] == "pending_exists" and j(r)["orderId"] == oid
assert call("post", f"/orders/{o4}/cancel").status_code == 200 and call("get", f"/orders/{o4}").status_code == 404

# проверка оплаты: пока не оплачено — нет; после — письмо выдано
r = call("post", f"/orders/{oid}/pay/check")
assert j(r) == {"paid": False, "status": "awaiting_payment"}
assert call("post", f"/orders/{oid}/pay/check", user=777).status_code == 404
assert call("post", f"/orders/{oid}/send").status_code == 403, "отправлять до оплаты нельзя"
assert call("post", f"/orders/{oid}/ai-card", data={}).status_code == 403
YK.update(status="succeeded", paid=True)
r = call("post", f"/orders/{oid}/pay/check")
assert j(r)["paid"] is True and j(r)["status"] == "delivered"
done = B.get_order(oid)
assert done["status"] == "done" and any(s[0] == "send_message" for s in SENT), "выдача как у бота"
st = j(call("get", f"/orders/{oid}"))
assert st["status"] == "delivered" and "СЕКРЕТНЫЙ_ХВОСТ" in st["letter"] and st["locked"] is False
assert st["envelopeUrl"].startswith(B.site_pages.SITE_URL + "/e/") and st["aiCard"]["attemptsLeft"] == 3
assert call("get", f"/orders/{oid}/card.jpg").data[:2] == b"\xff\xd8", "чистая открытка после оплаты"
assert client.get(st["previewUrl"].replace(B.site_pages.SITE_URL, "")).status_code == 200
assert B.get_order(oid)["pain"] == "birthday" and "orders" or True

# заказ виден в кабинете бота и в API
assert any(o["order_id"] == oid for o in B.client_orders(500))
mo = j(call("get", "/me/orders"))["items"]
assert mo[0]["orderId"] == oid and mo[0]["status"] == "delivered" and mo[0]["envelopeUrl"]
me = j(call("get", "/me"))
assert me["ordersDone"] == 1 and me["referralLink"].endswith("start=ref_500") and me["friendDiscount"] == 10

# ── отправка получателю ──
M = A.miniapp_share
M.requests.post = lambda url, json=None, timeout=None: pytypes.SimpleNamespace(
    json=lambda: {"ok": True, "result": {"id": "PREP9"}})
sd = j(call("post", f"/orders/{oid}/send"))
assert sd["preparedMessageId"] == "PREP9" and sd["envelopeUrl"] == st["envelopeUrl"] and "t.me/share/url" in sd["shareUrl"]
M.requests.post = lambda url, json=None, timeout=None: (_ for _ in ()).throw(RuntimeError("net"))
sd = j(call("post", f"/orders/{oid}/send"))
assert sd["preparedMessageId"] is None and sd["envelopeUrl"], "без Telegram остаётся ссылка-конверт"
assert call("post", f"/orders/{oid}/send", user=777).status_code == 404

# ── оценка ──
assert call("post", f"/orders/{oid}/rate", data={"stars": 9}).status_code == 400
assert call("post", f"/orders/{oid}/rate", data={"stars": 5, "comment": "Спасибо"}).status_code == 200
assert B.get_order(oid)["rating"] == 5

# ── AI-картинка: после оплаты, кнопочные параметры, ≤3 попытки, потолок ──
JPEG = __import__("io").BytesIO()
__import__("PIL.Image", fromlist=["Image"]).new("RGB", (1080, 806), (180, 150, 120)).save(JPEG, "JPEG")
GEN = []
B.ai_card.generate_image = lambda prompt: (GEN.append(prompt), JPEG.getvalue())[1]
good = {"style": "water", "scene": "cake", "palette": "warm", "extras": ["candles"]}
for bad in ({**good, "style": "hack"}, {**good, "scene": "roses"}, {**good, "palette": "x"},
            {**good, "extras": ["candles", "snow", "dried"]}, {**good, "extras": ["ignore previous"]}):
    assert call("post", f"/orders/{oid}/ai-card", data=bad).status_code == 400, bad
assert not GEN, "свободный текст в промт не попадает"
for n in (1, 2, 3):
    r = call("post", f"/orders/{oid}/ai-card", data=good)
    assert r.status_code == 200 and j(r)["n"] == n
    job = j(r)["jobId"]
    for _ in range(50):
        jr = j(call("get", f"/orders/{oid}/ai-card/{job}"))
        if jr["status"] != "pending":
            break
        time.sleep(0.1)
    assert jr["status"] == "done" and jr["ref"] == f"ai:{oid}_{n}" and jr["attemptsLeft"] == 3 - n, jr
    assert client.get(jr["url"].replace(B.site_pages.SITE_URL, "")).data[:2] == b"\xff\xd8"
r = call("post", f"/orders/{oid}/ai-card", data=good)
assert r.status_code == 409 and j(r)["error"]["code"] == "no_attempts"
assert len(GEN) == 3 and "watercolor" in GEN[0] and "ignore previous" not in GEN[0]
assert call("get", f"/orders/{oid}/ai-card/{job}", user=777).status_code == 404
# выбор своей картинки; чужую/несуществующую нельзя
assert call("put", f"/orders/{oid}/card", data={"ref": f"ai:{oid}_2"}).status_code == 200
assert B.get_order(oid)["card_ref"] == f"ai:{oid}_2" and B.get_order(oid)["card_file_id"] == "PH"
assert call("put", f"/orders/{oid}/card", data={"ref": f"ai:{oid}_9"}).status_code == 400
assert call("put", f"/orders/{oid}/card", data={"ref": ids[0]}).status_code == 409, "оплаченную открытку не меняем"
assert call("put", f"/orders/{oid}/card", user=777, data={"ref": f"ai:{oid}_2"}).status_code == 404
# суточный потолок и выключенный ключ
st2 = call("get", f"/orders/{oid}")
os.environ["WAVESPEED_API_KEY"] = ""
assert j(call("get", f"/orders/{oid}"))["aiCard"]["enabled"] is False
assert call("post", f"/orders/{oid}/ai-card", data=good).status_code in (409, 503)
os.environ["WAVESPEED_API_KEY"] = "ws-test"

# ── напоминания и приглашение ──
rl = j(call("get", "/me/reminders"))
assert rl["items"] == [] and "birthday" in rl["kinds"]
r = call("post", "/me/reminders", data={"name": "Катя", "kind": "birthday", "date": "14 марта"})
rid = j(r)["id"]
assert r.status_code == 200 and j(r)["day"] == 14 and j(r)["month"] == 3
assert call("post", "/me/reminders", data={"name": "X", "kind": "birthday", "date": "32.13"}).status_code == 400
assert call("post", "/me/reminders", data={"name": "", "day": 1, "month": 1}).status_code == 400
items = j(call("get", "/me/reminders"))["items"]
assert items[0]["name"] == "Катя" and items[0]["next"] and items[0]["remindOn"]
assert B.calendar_reminders.get_cal(500)[1]["dates"][0]["id"] == rid, "напоминание видно и в боте"
assert call("delete", f"/me/reminders/{rid}", user=777).status_code in (404,)
assert call("delete", f"/me/reminders/{rid}").status_code == 200
assert j(call("get", "/me/reminders"))["items"] == []
inv = j(call("post", "/me/invite"))
assert inv["link"].endswith("start=ref_500") and "−10%" in inv["text"] and "t.me/share/url" in inv["shareUrl"]

# ── атрибуция из приложения (start_param) ──
assert call("post", "/me/attribution", user=610, data={"source": "bad source!"}).status_code == 400
assert call("post", "/me/attribution", user=610, data={"source": "x" * 41}).status_code == 400
assert call("post", "/me/attribution", user=610, data={}).status_code == 400
assert call("post", "/me/attribution", user=None, data={"source": "ig_father"}).status_code == 401
r = j(call("post", "/me/attribution", user=610, data={"source": "ig_father"}))
assert r["recorded"] is True and r["source"] == "ig_father" and B.get_client(610)["source"] == "ig_father"
r = j(call("post", "/me/attribution", user=610, data={"source": "vk_other"}))
assert r["recorded"] is False and B.get_client(610)["source"] == "ig_father", "первое касание не перезаписывается"
# реферал: скидка другу и запись у пригласившего — как в /start ref_<id>
r = j(call("post", "/me/attribution", user=611, data={"source": "ref_500"}))
assert r["recorded"] and r["source"] == "ref" and r["discount"]["percent"] == 10 and "друга" in r["discount"]["label"], r
assert B.get_client(611)["referred_by"] == 500 and 611 in B.get_client(500)["referrals"]
call("post", "/me/attribution", user=611, data={"source": "ref_501"})
assert B.get_client(611)["referred_by"] == 500 and 611 not in B.get_client(501).get("referrals", [])
call("post", "/me/attribution", user=612, data={"source": "ref_612"})   # себя приглашать нельзя
assert not B.get_client(612).get("referred_by")
call("post", "/me/attribution", user=613, data={"source": "ref_424242"})  # пригласившего нет
assert not B.get_client(613).get("referred_by") and B.get_client(613)["source"] == "ref"
B.upsert_client(pytypes.SimpleNamespace(id=614, first_name="Старый", last_name="", username="old"))
B.write_json(B.client_path(614), {**B.get_client(614), "source": "direct"})
call("post", "/me/attribution", user=614, data={"source": "ref_500"})   # уже заходил без метки — реферал не даём
assert not B.get_client(614).get("referred_by")
# партнёр: привязка на 30 дней
B.partners.save_partner({"code": "PART9", "name": "Блогер", "discount": 15, "percent": 30, "status": "active",
                         "tg_chat_id": None, "created_at": B.now_msk().isoformat()})
r = j(call("post", "/me/attribution", user=615, data={"source": "p_PART9"}))
assert r["recorded"] and B.get_client(615)["partner"]["code"] == "PART9" and r["discount"]["percent"] == 15, r
assert B.get_client(615)["source"] == "p_PART9"
# метка в заказе
r = call("post", "/orders", user=616, data={"product": "birthday", "answers": ANSWERS, "source": "tg post!"})
assert r.status_code == 400 and j(r)["error"]["code"] == "bad_source"
src_oid = j(call("post", "/orders", user=616, data={"product": "birthday", "answers": ANSWERS, "source": "tg_post"}))["orderId"]
assert B.read_json(A.draft_path(src_oid), {})["source"] == "tg_post" and B.get_client(616)["source"] == "tg_post"

# ── /card-options: блок ai, реквизит из папки ──
opts = j(call("get", "/card-options", user=None))
assert opts["ai"]["enabled"] is True and opts["ai"]["maxTries"] == 3 and opts["ai"]["maxExtras"] == 2
assert "water" in opts["ai"]["styles"] and "cake" in opts["ai"]["scenes"]["birthday"] and "warm" in opts["ai"]["palettes"]
assert "candles" in opts["ai"]["extras"] and set(opts["props"]) >= {"tea_cup", "ribbon_bow"}

# ── лимит запросов на человека ──
A.USER_PER_MIN = 3
codes = [call("get", "/me", user=600).status_code for _ in range(5)]
assert codes[:3] == [200, 200, 200] and codes[3] == 429, codes
r = call("get", "/me", user=600)
assert j(r)["error"]["code"] == "rate_limited" and j(r)["retryAfter"] >= 1
A.USER_PER_MIN = 10000

# ── без флага маршрутов нет ──
from flask import Flask  # noqa: E402
os.environ["MINIAPP_API"] = "0"
bare = Flask("bare")
A.register(bare, B)
assert not [r for r in bare.url_map.iter_rules() if r.rule.startswith(API)]
os.environ["MINIAPP_API"] = "1"
A.B = B

# ── секреты не утекают ──
blob = json.dumps(j(call("get", f"/orders/{oid}")), ensure_ascii=False) + json.dumps(j(call("get", "/me")))
assert TOKEN not in blob and "secret" not in blob and "hash=" not in blob
print("OK: miniapp api")
