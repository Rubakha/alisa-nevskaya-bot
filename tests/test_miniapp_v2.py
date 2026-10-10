"""API v2 для Mini App: поддержка, подписки, набор «3 письма», PDF, голосовое письмо, письмо группой.
Запуск: python tests/test_miniapp_v2.py (сеть и платные API не нужны)"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
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
os.environ["TEST_USERS"] = "999"
os.environ["TEST_USERNAMES"] = "nobody_here"
os.environ.pop("YOOKASSA_RECEIPT", None)
os.environ.pop("MINIAPP_API_V2", None)

import bot_best as B  # noqa: E402
import miniapp_api as A  # noqa: E402
import miniapp_api_v2 as V2  # noqa: E402
import support_tickets as ST  # noqa: E402

TOKEN = os.environ["TG_BOT_TOKEN"]
API = "/api/v1"
A.USER_PER_MIN = 10000
SENT = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=1, photo=[pytypes.SimpleNamespace(file_id="PH")],
                                       voice=pytypes.SimpleNamespace(file_id="VOICE1"))
    return f


for m in ["send_message", "send_photo", "send_animation", "send_chat_action", "answer_callback_query",
          "send_document", "send_voice", "send_audio", "set_chat_menu_button", "delete_message"]:
    setattr(B.bot, m, rec(m))

client = B.app.test_client()
UNAME = {}


def init_data(user_id, token=TOKEN, age=0):
    user = json.dumps({"id": user_id, "first_name": "Тест", "username": UNAME.get(user_id, f"u{user_id}")},
                      ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "AAA", "user": user}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


def call(method, path, user=500, data=None, headers=None, **kw):
    h = {"Origin": "https://alisa-letters.pages.dev", **(headers or {})}
    if user:
        h["X-Telegram-Init-Data"] = init_data(user)
    return getattr(client, method)(API + path, json=data, headers=h, **kw)


def j(resp):
    return json.loads(resp.get_data(as_text=True))


def sent_to(chat, since=0):
    return [s for s in SENT[since:] if s[0] == "send_message" and s[1] and s[1][0] == chat]


# ═══ Блок 1: поддержка и подписки ═══
n = len(SENT)
assert call("post", "/support", user=None, data={"text": "Помогите"}).status_code == 401
assert call("post", "/support", data={"text": "эй"}).status_code == 400
assert call("post", "/support", data={"text": "я" * 1001}).status_code == 400
r = call("post", "/support", data={"topic": "payment", "text": "Оплатила, а письмо не пришло"})
tk = j(r)
assert r.status_code == 200 and tk["status"] == "sent" and tk["ticketId"].startswith("T-")
adm = sent_to(1, n)
assert adm and "Обращение в поддержку" in adm[0][1][1] and "Оплата" in adm[0][1][1]
assert "Оплатила, а письмо не пришло" in adm[0][1][1] and "/reply 500 текст" in adm[0][1][1], "формат как у sup:new"
call("post", "/support", data={"topic": "strange", "text": "Второй вопрос по приложению"})
sl = j(call("get", "/me/support"))["items"]
assert [x["text"] for x in sl] == ["Второй вопрос по приложению", "Оплатила, а письмо не пришло"]
assert sl[0]["topic"] == "other" and sl[1]["topic"] == "payment" and sl[0]["status"] == "sent" and sl[0]["reply"] is None
call("post", "/support", data={"text": "Третий вопрос про всё"})
r = call("post", "/support", data={"text": "Четвёртый вопрос подряд"})
assert r.status_code == 429 and j(r)["error"]["code"] == "rate_limited" and j(r)["retryAfter"] > 0, "3 обращения в час"
assert call("get", "/me/support", user=777).status_code == 200 and j(call("get", "/me/support", user=777))["items"] == []
# ответ владельца: приходит клиенту в бота и отмечается в приложении
admin_msg = pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=1, type="private"), text="/reply 500 Нашла, письмо у тебя в боте",
                                    content_type="text", from_user=pytypes.SimpleNamespace(id=1), message_id=1)
n = len(SENT)
B.admin_reply(admin_msg)
assert any("Ответ от Алисы" in s[1][1] and "Нашла" in s[1][1] for s in sent_to(500, n)), "ответ в чате бота"
sl = j(call("get", "/me/support"))["items"]
assert all(x["status"] == "answered" and x["reply"] == "Нашла, письмо у тебя в боте" and x["repliedAt"] for x in sl)

# подписки
sub = j(call("get", "/me/subscriptions"))
keys = {h["key"] for h in sub["holidays"]}
assert {"mother", "father", "newyear", "feb14", "feb23", "mar8"} <= keys and sub["mailing"] is True
assert all(h["next"] and not h["subscribed"] for h in sub["holidays"]) and sub["reminderDates"] == 0
r = j(call("put", "/me/subscriptions", data={"holidays": ["mother", "newyear", "hack", "mother"]}))
assert {h["key"] for h in r["holidays"] if h["subscribed"]} == {"mother", "newyear"} and r["mailing"] is True
assert B.calendar_reminders.get_cal(500)[1]["subs"] == ["mother", "newyear"], "подписки видны и в боте"
r = j(call("put", "/me/subscriptions", data={"mailing": False}))
assert r["mailing"] is False and {h["key"] for h in r["holidays"] if h["subscribed"]} == {"mother", "newyear"}
assert B.get_client(500)["mailing_off"] is True
assert call("put", "/me/subscriptions", data={"mailing": "нет"}).status_code == 400
assert call("put", "/me/subscriptions", data={"holidays": "mother"}).status_code == 400
assert j(call("get", "/me"))["mailing"] is False
r = j(call("put", "/me/subscriptions", data={"holidays": []}))
assert not [h for h in r["holidays"] if h["subscribed"]] and r["mailing"] is False
assert call("get", "/me/subscriptions", user=None).status_code == 401
# рассылка владельца пропускает отписавшихся
B.upsert_client(pytypes.SimpleNamespace(id=700, first_name="Мама", last_name="", username="m"))
B.upsert_client(pytypes.SimpleNamespace(id=701, first_name="Папа", last_name="", username="p"))
c = B.get_client(701)
c["mailing_off"] = True
B.write_json(B.client_path(701), c)
B.STATES[1] = {"step": "mailing_confirm", "text": "Новости от Алисы"}
n = len(SENT)
B.mailing_send(pytypes.SimpleNamespace(id="c", data="adm:send_mail", from_user=pytypes.SimpleNamespace(id=1),
                                       message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=1), message_id=3)))
got = {s[1][0] for s in SENT[n:] if s[0] == "send_message" and s[1][1:2] == ("Новости от Алисы",)}
assert 700 in got and 701 not in got and 500 not in got, got

# ═══ Блок 2: набор «3 письма», PDF, кредиты ═══
PAYS = {}


def fake_yk(method, url, payload=None, idem=None):
    if method == "POST":
        oid = payload["metadata"]["order_id"]
        pid = f"pay-{oid}"
        PAYS[pid] = {"price": payload["amount"]["value"], "status": "pending", "paid": False}
        return {"id": pid, "confirmation": {"confirmation_url": f"https://pay.example/{oid}"}}
    pid = url.rsplit("/", 1)[1]
    st = PAYS[pid]
    return {"id": pid, "status": st["status"], "paid": st["paid"], "amount": {"value": st["price"]},
            "confirmation": {"confirmation_url": f"https://pay.example/{pid}"}}


def mark_paid(order_id):
    PAYS[f"pay-{order_id}"].update(status="succeeded", paid=True)


B.yk_real = B.yk_request
B.yk_request = fake_yk
B.partners.save_promo({"code": "TEST20", "kind": "percent", "value": 20, "active": True, "used": [], "limit": 0})

pk = j(call("get", "/pack", user=None))
assert pk == {"key": "pack3", "title": "Набор: 3 письма с открыткой", "price": 449, "credits": 3,
              "regularPrice": 597, "savings": 148}, pk
assert j(call("get", "/pack", user=500))["myCredits"] == 0
assert call("post", "/pack/pay", user=None, data={}).status_code == 401
r = call("post", "/pack/pay", user=500, data={"promo": "NOPE"})
assert r.status_code == 400 and j(r)["error"]["code"] == "bad_promo"
r = call("post", "/pack/pay", user=500, data={"promo": "TEST20"})
pp = j(r)
assert r.status_code == 200 and pp["price"] == 359 and pp["paymentUrl"].startswith("https://pay.example/"), pp
pack_oid = pp["orderId"]
po = B.get_order(pack_oid)
assert po["product"] == "pack3" and po["status"] == "pending" and po["price_rub"] == 359 and po["chat_id"] == 500
assert PAYS[f"pay-{pack_oid}"]["price"] == "359.00"
assert j(call("post", "/pack/pay", user=500, data={}))["orderId"] == pack_oid, "повторно — тот же заказ"
r = call("post", "/doc/pay", user=500, data={})
assert r.status_code == 409 and j(r)["error"]["code"] == "pending_exists" and j(r)["orderId"] == pack_oid
assert call("post", f"/purchases/{pack_oid}/check", user=777).status_code == 404
st = j(call("post", f"/purchases/{pack_oid}/check", user=500))
assert st["paid"] is False and st["status"] == "awaiting_payment" and st["kind"] == "pack"
mp = j(call("get", "/me/purchases", user=500))["items"]
assert mp[0]["orderId"] == pack_oid and mp[0]["status"] == "awaiting_payment" and mp[0]["title"].startswith("Набор")
mark_paid(pack_oid)
st = j(call("post", f"/purchases/{pack_oid}/check", user=500))
assert st["paid"] is True and st["credits"] == 3 and st["status"] == "delivered", st
assert B.get_client(500)["credits"] == 3 and B.get_order(pack_oid)["status"] == "done"
assert j(call("get", "/pack", user=500))["myCredits"] == 3 and j(call("get", "/me", user=500))["credits"] == 3
assert j(call("post", f"/purchases/{pack_oid}/check", user=500))["credits"] == 3, "повторный опрос не начисляет второй раз"
assert B.get_client(500)["credits"] == 3

# оплата письма кредитом набора
ANSWERS = {"name": "Катя", "who": "подруга", "age": "30", "details": "черешня на крыше", "tone": "warm", "sign": "Серёжа"}
LETTER = ("Катя, привет. Первый абзац письма." + chr(10) * 2 + "Второй абзац — про дружбу. " * 12 + chr(10) * 2
          + "Последний абзац. Твой Серёжа.")


class FakeAI:
    calls = []

    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title, previous="", wish=""):
        FakeAI.calls.append((key, previous, wish))
        return {"letter": LETTER, "card_title": title, "card_line": "Ты лучшая."}


B.AI = FakeAI
c_oid = j(call("post", "/orders", user=500, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
call("post", f"/orders/{c_oid}/preview", user=500)
assert j(call("get", f"/orders/{c_oid}", user=500))["creditsAvailable"] == 3
r = call("post", f"/orders/{c_oid}/pay", user=500, data={"useCredit": True})
cr = j(r)
assert r.status_code == 200 and cr == {"paid": True, "price": 0, "credit": True, "creditsLeft": 2, "orderId": c_oid}, cr
co = B.get_order(c_oid)
assert co["status"] == "done" and co["price_rub"] == 0 and co["paid_by"] == "credit" and co["letter_text"].startswith("Катя")
assert B.get_client(500)["credits"] == 2
assert j(call("get", f"/orders/{c_oid}", user=500))["status"] == "delivered"
assert any(s[0] == "send_photo" for s in SENT), "выдача как у бота: открытка в чат"
# нет кредитов / чужие условия
c2 = j(call("post", "/orders", user=600, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
call("post", f"/orders/{c2}/preview", user=600)
assert j(call("get", f"/orders/{c2}", user=600))["creditsAvailable"] == 0
r = call("post", f"/orders/{c2}/pay", user=600, data={"useCredit": True})
assert r.status_code == 409 and j(r)["error"]["code"] == "no_credits"
assert call("post", f"/orders/{c2}/pay", user=500, data={"useCredit": True}).status_code == 404, "чужой заказ"
assert B.get_client(600).get("credits", 0) == 0 and B.get_order(c2) is None, "без кредита заказ не создаётся"

# PDF «Разговор с папой»
d = j(call("get", "/doc", user=None))
assert d["price"] == 149 and d["pages"] == 10 and d["title"].startswith("Разговор с папой")
r = call("post", "/doc/pay", user=501, data={})
dp = j(r)
doc_oid = dp["orderId"]
assert r.status_code == 200 and dp["price"] == 149 and dp["paymentUrl"].endswith(doc_oid)
assert B.get_order(doc_oid)["product"] == "papa_pdf"
assert call("get", f"/doc/{doc_oid}/file.pdf", user=501).status_code == 404, "до оплаты файла нет"
ds = j(call("get", f"/doc/{doc_oid}", user=501))
assert ds["status"] == "awaiting_payment" and "downloadUrl" not in ds
n = len(SENT)
mark_paid(doc_oid)
st = j(call("post", f"/purchases/{doc_oid}/check", user=501))
assert st["paid"] and st["kind"] == "doc" and st["downloadUrl"], st
assert any(s[0] == "send_document" and s[1][0] == 501 for s in SENT[n:]), "файл ещё и в чате бота"
dl = client.get(st["downloadUrl"].replace(B.site_pages.SITE_URL, ""))
assert dl.status_code == 200 and dl.mimetype == "application/pdf" and dl.data[:4] == b"%PDF"
assert client.get(st["downloadUrl"].replace(B.site_pages.SITE_URL, "").replace("sig=", "sig=0")).status_code == 401
assert call("get", f"/doc/{doc_oid}/file.pdf", user=501).data[:4] == b"%PDF"
assert call("get", f"/doc/{doc_oid}/file.pdf", user=777).status_code == 404 and call("get", f"/doc/{doc_oid}", user=777).status_code == 404
assert j(call("get", f"/doc/{doc_oid}", user=501))["downloadUrl"], "новую ссылку выдаёт GET /doc/<id>"
# тестер: набор и PDF бесплатно
tp = j(call("post", "/doc/pay", user=999, data={}))
assert tp["paid"] and tp["test"] and tp["price"] == 0 and tp["realPrice"] == 149
assert j(call("get", f"/doc/{tp['orderId']}", user=999))["downloadUrl"] and B.get_order(tp["orderId"])["is_test"]
tk = j(call("post", "/pack/pay", user=999, data={}))
assert tk["paid"] and tk["test"] and B.get_client(999)["credits"] == 3
# отмена неоплаченной покупки
call("post", "/pack/pay", user=502, data={})
pend = B.pending_orders(502)[0]["order_id"]
assert call("post", f"/purchases/{pend}/cancel", user=777).status_code == 404
assert j(call("post", f"/purchases/{pend}/cancel", user=502)) == {"ok": True} and B.get_order(pend)["status"] == "cancelled"
assert call("post", "/doc/pay", user=502, data={}).status_code == 200, "после отмены можно купить другое"

# ═══ Блок 3: голосовое письмо ═══
import io as _io  # noqa: E402

VLM = B.voice_flow.VL
B.voice_flow.ASYNC = False
MAKES = []


def fake_make(text, gender="f", seed=""):
    MAKES.append((gender, seed))
    return {"ogg": b"OggS-voice", "mp3": b"ID3-voice", "chars": 1200, "music": True}


VLM.make = fake_make
os.environ["ELEVENLABS_API_KEY"] = "k"
os.environ["ELEVENLABS_VOICE_ID_F"] = "vf"
os.environ["ELEVENLABS_VOICE_ID_M"] = "vm"
os.environ["VOICE_UPSELL_PRICE"] = "100"

vo = j(call("get", "/voice/options", user=None))
assert vo == {"enabled": True, "price": 100, "genders": [{"key": "f", "label": "Женский голос"},
                                                          {"key": "m", "label": "Мужской голос"}],
              "music": True, "maxLetterChars": 9000}, vo

# до оплаты: переключатель добавляет +100 ₽ в сумму
v_oid = j(call("post", "/orders", user=510, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
assert call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": True, "gender": "f"}).status_code == 409, "без письма нельзя"
call("post", f"/orders/{v_oid}/preview", user=510)
assert call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": True, "gender": "x"}).status_code == 400
assert call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": "да"}).status_code == 400
assert call("put", f"/orders/{v_oid}/voice", user=777, data={"enabled": True, "gender": "f"}).status_code == 404
r = j(call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": True, "gender": "f"}))
assert r == {"ok": True, "price": 299, "voice": {"enabled": True, "gender": "f", "price": 100}}, r
st = j(call("get", f"/orders/{v_oid}", user=510))
assert st["price"] == 299 and st["voice"]["gender"] == "f" and st["creditsAvailable"] == 0, st
assert j(call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": False}))["price"] == 199
assert j(call("get", f"/orders/{v_oid}", user=510))["voice"] is None
call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": True, "gender": "m"})
assert j(call("get", f"/orders/{v_oid}/voice", user=510))["selected"] is True
r = call("post", f"/orders/{v_oid}/pay", user=510, data={"promo": "TEST20"})
assert j(r)["price"] == 239, j(r)   # (199 + 100) − 20%
vo_order = B.get_order(v_oid)
assert vo_order["voice"] is True and vo_order["voice_gender"] == "m" and vo_order["price_rub"] == 239
assert call("put", f"/orders/{v_oid}/voice", user=510, data={"enabled": False}).status_code == 409, "оплата начата"
assert j(call("get", f"/orders/{v_oid}/voice", user=510))["selected"] is True
n = len(MAKES)
mark_paid(v_oid)
st = j(call("post", f"/orders/{v_oid}/pay/check", user=510))
assert st["paid"] is True
assert len(MAKES) == n + 1 and MAKES[-1][0] == "m", "озвучка стартовала сама после оплаты"
vs = j(call("get", f"/orders/{v_oid}/voice", user=510))
assert vs["status"] == "ready" and vs["gender"] == "m" and vs["chars"] == 1200 and vs["price"] == 100, vs
assert vs["audioUrl"] and vs["oggUrl"] and vs["error"] is None
assert client.get(vs["audioUrl"].replace(B.site_pages.SITE_URL, "")).data == b"ID3-voice"
assert client.get(vs["oggUrl"].replace(B.site_pages.SITE_URL, "")).data == b"OggS-voice"
assert client.get(vs["audioUrl"].replace(B.site_pages.SITE_URL, "").replace("sig=", "sig=0")).status_code == 401
assert call("get", f"/orders/{v_oid}/voice.mp3", user=510).mimetype == "audio/mpeg"
assert call("get", f"/orders/{v_oid}/voice.mp3", user=777).status_code == 404
assert any(s[0] == "send_voice" for s in SENT), "голосовое ещё и в чате бота, как раньше"
assert j(call("get", f"/orders/{v_oid}", user=510))["voice"] == {"status": "ready", "gender": "m"}
r = call("post", f"/orders/{v_oid}/voice/pay", user=510, data={"gender": "f"})
assert r.status_code == 409 and j(r)["error"]["code"] == "already_voiced"

# после оплаты: доплата +100 ₽
p_oid = j(call("post", "/orders", user=511, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
assert call("post", f"/orders/{p_oid}/voice/pay", user=511, data={"gender": "f"}).status_code in (403, 409), "до оплаты письма нельзя"
call("post", f"/orders/{p_oid}/preview", user=511)
assert call("post", f"/orders/{p_oid}/voice/pay", user=511, data={"gender": "f"}).status_code == 403
call("post", f"/orders/{p_oid}/pay", user=511, data={})
mark_paid(p_oid)
call("post", f"/orders/{p_oid}/pay/check", user=511)
assert j(call("get", f"/orders/{p_oid}/voice", user=511))["status"] == "none"
assert call("post", f"/orders/{p_oid}/voice/pay", user=511, data={"gender": "z"}).status_code == 400
n = len(MAKES)
r = call("post", f"/orders/{p_oid}/voice/pay", user=511, data={"gender": "m"})
vp = j(r)
assert r.status_code == 200 and vp["price"] == 100 and vp["paymentUrl"] and vp["voiceOrderId"].startswith("ALI-"), vp
addon = B.get_order(vp["voiceOrderId"])
assert addon["product"] == "voice_addon" and addon["voice_for"] == p_oid and addon["price_rub"] == 100
assert j(call("get", f"/orders/{p_oid}/voice", user=511))["status"] == "awaiting_payment"
assert j(call("post", f"/orders/{p_oid}/voice/pay", user=511, data={"gender": "m"}))["voiceOrderId"] == vp["voiceOrderId"], "тот же заказ"
assert j(call("post", f"/orders/{p_oid}/voice/check", user=511)) == {"paid": False, "status": "awaiting_payment"}
mark_paid(vp["voiceOrderId"])
assert j(call("post", f"/orders/{p_oid}/voice/check", user=511)) == {"paid": True, "status": "ready"}
assert len(MAKES) == n + 1 and B.get_order(p_oid)["voice_addon_order"] == vp["voiceOrderId"]
vs = j(call("get", f"/orders/{p_oid}/voice", user=511))
assert vs["status"] == "ready" and client.get(vs["audioUrl"].replace(B.site_pages.SITE_URL, "")).data == b"ID3-voice"
assert j(call("get", f"/orders/{p_oid}", user=511))["voice"]["status"] == "ready"

# лимиты символов (сутки и месяц) и недоступность — отказ ДО оплаты, деньги не берём
c_oid = j(call("post", "/orders", user=512, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
call("post", f"/orders/{c_oid}/preview", user=512)
os.environ["VOICE_DAILY_CAP"] = "1"
assert j(call("get", "/voice/options", user=None))["enabled"] is False
r = call("put", f"/orders/{c_oid}/voice", user=512, data={"enabled": True, "gender": "f"})
assert r.status_code == 429 and j(r)["error"]["code"] == "voice_cap" and j(r)["retryAfter"] > 0
assert not j(call("get", f"/orders/{c_oid}", user=512))["voice"]
os.environ.pop("VOICE_DAILY_CAP")
os.environ["VOICE_MONTHLY_CAP"] = "1000"   # в журнале уже 2400 знаков за месяц
r = call("put", f"/orders/{c_oid}/voice", user=512, data={"enabled": True, "gender": "f"})
assert r.status_code == 429 and j(r)["error"]["code"] == "voice_cap"
os.environ.pop("VOICE_MONTHLY_CAP")
os.environ["ELEVENLABS_API_KEY"] = ""
assert j(call("get", "/voice/options", user=None))["enabled"] is False
r = call("put", f"/orders/{c_oid}/voice", user=512, data={"enabled": True, "gender": "f"})
assert r.status_code == 503 and j(r)["error"]["code"] == "voice_unavailable"
os.environ["ELEVENLABS_API_KEY"] = "k"
long_order = {**B.get_order(p_oid), "order_id": "ALI-9100000001", "chat_id": 513, "letter_text": "Слово. " * 2000,
              "voice_addon_order": None, "voice": False, "voice_status": None}
long_order.pop("gift_code", None)
B.save_order(long_order)
r = call("post", "/orders/ALI-9100000001/voice/pay", user=513, data={"gender": "f"})
assert r.status_code == 409 and j(r)["error"]["code"] == "letter_too_long"

# тестер: озвучка бесплатно, свой суточный потолок
t_oid = j(call("post", "/orders", user=999, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
call("post", f"/orders/{t_oid}/preview", user=999)
call("post", f"/orders/{t_oid}/pay", user=999, data={})
r = call("post", f"/orders/{t_oid}/voice/pay", user=999, data={"gender": "f"})
tv = j(r)
assert r.status_code == 200 and tv["paid"] and tv["test"] and tv["price"] == 0 and tv["realPrice"] == 100 and tv["status"] == "queued", tv
assert j(call("get", f"/orders/{t_oid}/voice", user=999))["status"] == "ready"
assert B.get_order(tv["voiceOrderId"])["is_test"] is True
t2 = j(call("post", "/orders", user=999, data={"product": "birthday", "answers": ANSWERS}))["orderId"]
call("post", f"/orders/{t2}/preview", user=999)
call("post", f"/orders/{t2}/pay", user=999, data={})
os.environ["MINIAPP_TESTER_VOICE_CAP"] = "1"
r = call("post", f"/orders/{t2}/voice/pay", user=999, data={"gender": "f"})
assert r.status_code == 429 and j(r)["error"]["code"] == "voice_cap"
os.environ.pop("MINIAPP_TESTER_VOICE_CAP")

# сбой озвучки: статус failed, письмо и открытка на месте
def failing_make(text, gender="f", seed=""):
    raise RuntimeError("ElevenLabs down")


VLM.make = failing_make
r = call("post", f"/orders/{t2}/voice/pay", user=999, data={"gender": "m"})
assert r.status_code == 200
fs = j(call("get", f"/orders/{t2}/voice", user=999))
assert fs["status"] == "failed" and "вручную" in fs["error"] and fs["audioUrl"] is None, fs
assert call("get", f"/orders/{t2}/voice.mp3", user=999).status_code == 404
assert j(call("get", f"/orders/{t2}", user=999))["status"] == "delivered"
VLM.make = fake_make

# откат флагом
os.environ["MINIAPP_API_V2"] = "0"
from flask import Flask  # noqa: E402
bare = Flask("bare2")
A.APP = bare
V2.register(A, B)
assert not [r for r in bare.url_map.iter_rules() if r.rule.startswith(API + "/support")]
A.APP = B.app
os.environ.pop("MINIAPP_API_V2")
print("OK: miniapp v2")
