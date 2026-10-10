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

# откат флагом
os.environ["MINIAPP_API_V2"] = "0"
from flask import Flask  # noqa: E402
bare = Flask("bare2")
A.APP = bare
V2.register(A, B)
assert not [r for r in bare.url_map.iter_rules() if r.rule.startswith(API + "/support")]
A.APP = B.app
os.environ.pop("MINIAPP_API_V2")
print("OK: miniapp v2 block 1")
