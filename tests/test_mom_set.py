"""Набор «Для мамы» (2 письма с открыткой + PDF «10 писем» + «20 вопросов» в подарок, 590 ₽): флаг, кнопки, цена,
оплата одним платежом, кредиты на письма, выдача PDF, API сайта. Устроен как набор «3 письма» (кредиты).
Запуск: python tests/test_mom_set.py (сеть и платные API не нужны)"""
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
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "tok"
os.environ["YOOKASSA_SHOP_ID"] = "shop"
os.environ["YOOKASSA_SECRET_KEY"] = "secret"
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["APP_ONLY"] = "0"
os.environ["MINIAPP_API"] = "1"
for k in ("MOM_SET", "MOM_SET_PRICE_RUB", "PDF_PRODUCTS", "YOOKASSA_RECEIPT", "PDF_QUESTIONS_PRICE_RUB"):
    os.environ.pop(k, None)

import bot_best as B  # noqa: E402
import miniapp_api as A  # noqa: E402
import pdf_products as P  # noqa: E402
import web_auth  # noqa: E402

TOKEN = os.environ["TG_BOT_TOKEN"]
API = "/api/v1"
A.USER_PER_MIN = 10000
SENT = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=len(SENT), photo=[pytypes.SimpleNamespace(file_id="PH")],
                                       chat=pytypes.SimpleNamespace(id=500))
    return f


for m in ["send_message", "send_photo", "send_animation", "send_chat_action", "answer_callback_query", "send_document",
          "send_invoice", "edit_message_text", "edit_message_reply_markup", "set_chat_menu_button", "delete_message",
          "send_media_group"]:
    setattr(B.bot, m, rec(m))


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title, previous="", wish=""):
        return {"letter": "Мам, помнишь, как мы пекли пирог? " * 14 + f"\n{sign}", "card_title": title,
                "card_line": "Ты лучшая."}


B.AI = FakeAI


def msg(text, chat=500):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=pytypes.SimpleNamespace(id=chat, first_name="Аня", last_name="", username="anya"))


def cb(data, chat=500):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=msg("", chat).from_user,
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), message_id=1))


def all_buttons(kb):
    return [(b.text, b.callback_data) for row in kb.keyboard for b in row]


def last(name):
    return [s for s in SENT if s[0] == name][-1]


def docs():
    return [s for s in SENT if s[0] == "send_document"]


def handler(name):
    for h in B.bot.callback_query_handlers:
        f = h.get("function") if isinstance(h, dict) else getattr(h, "function", None)
        if f and f.__name__ == name:
            return f
    raise AssertionError(name)


def go_family_letter(chat=500):
    """Анкета «Маме, папе, бабушке» до экрана оплаты/превью."""
    B.occ_go(cb("occ:go:family", chat))
    for a in ["Лена", "мама", "пирог с вишней по воскресеньям", "за то, что всегда ждала"]:
        B.occ_answer(msg(a, chat))
    B.occ_tone(cb("occ:tone:tender", chat))
    B.occ_answer(msg("Аня", chat))
    assert B.STATES[chat]["step"] == "occ_paywall", B.STATES[chat]


YK_SHOP = B.YOOKASSA_SHOP_ID
B.YOOKASSA_SHOP_ID = ""   # бот-часть идёт через Telegram Payments (send_invoice)

# ═══ 1. По умолчанию выключено ═══
assert not P.mom_enabled()
assert not any("Для мамы" in t for t, _ in all_buttons(B.occ_catalog_markup()))
B.occ_open_product(500, "family")
assert not any("Для мамы" in t for t, _ in all_buttons(last("send_message")[2]["reply_markup"]))
n = len(SENT)
handler("momset_callback")(cb("momset:info"))
handler("momset_callback")(cb("momset:buy"))
assert not [s for s in SENT[n:] if s[0] in ("edit_message_text", "send_invoice")], "выключено: ничего не происходит"
assert P.open_mom_order(500, msg("").from_user) is False
assert not [o for o in B.all_orders() if o.get("product") == "mom_set"]
B.cmd_start(pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=500), text="/start momset", from_user=msg("").from_user,
                                    content_type="text"))
assert not [o for o in B.all_orders() if o.get("product") == "mom_set"], "ссылка ?start=momset при выключенном флаге молчит"

# ═══ 2. Включаем: кнопки, состав, честная цена ═══
os.environ["MOM_SET"] = "1"
btn = [t for t, d in all_buttons(B.occ_catalog_markup()) if d == "momset:info"]
assert btn and "590" in btn[0] and "2 письма" in btn[0], btn
B.occ_open_product(500, "family")
assert any(d == "momset:info" for _, d in all_buttons(last("send_message")[2]["reply_markup"]))
B.occ_open_product(500, "birthday")
assert not any(d == "momset:info" for _, d in all_buttons(last("send_message")[2]["reply_markup"])), "набор только у повода family"

info = P.mom_info()
assert info["price"] == 590 and info["credits"] == 2 and info["regularPrice"] == 199 + 199 + 249 == 647 and info["savings"] == 57, info
assert [i["key"] for i in info["items"]] == ["letter", "letter2", "pdf_letters", "pdf_questions"]
assert info["items"][3]["price"] == 0 and info["items"][3].get("gift"), "«20 вопросов» подарок, в сумму не входит"
text = P.mom_text()
assert "590₽" in text and "647₽" in text and "выгода 57₽" in text and "199₽ + 199₽ + 249₽" in text and "в подарок" in text, text
os.environ["PDF_QUESTIONS_PRICE_RUB"] = "0"
assert P.mom_info()["regularPrice"] == 647, "цена «20 вопросов» на сумму не влияет"
os.environ.pop("PDF_QUESTIONS_PRICE_RUB")
os.environ["MOM_SET_PRICE_RUB"] = "490"
assert P.mom_info()["price"] == 490 and P.mom_info()["savings"] == 157
os.environ["MOM_SET_PRICE_RUB"] = "700"
assert P.mom_info()["savings"] == 0 and "выгода" not in P.mom_text(), "дороже частей: выгоду не обещаем"
os.environ.pop("MOM_SET_PRICE_RUB")

handler("momset_callback")(cb("momset:info"))
kb = (last("edit_message_text") if [s for s in SENT if s[0] == "edit_message_text"] else last("send_message"))[2]["reply_markup"]
assert ("💳 Купить набор · 590₽", "momset:buy") in all_buttons(kb)
assert any(d == "occ:p:family" for _, d in all_buttons(kb)), "есть путь «только письмо»"

# ═══ 3. Бот: один платёж 590, кредиты, PDF, письма по кредиту ═══
SENT.clear()
handler("momset_callback")(cb("momset:buy"))
inv = last("send_invoice")[2]
assert inv["prices"][0].amount == 59000 and inv["prices"][0].label == "Набор" and "Набор" in inv["title"], inv
oid = inv["invoice_payload"]
order = B.get_order(oid)
assert order["product"] == "mom_set" and order["price_rub"] == 590 and order["status"] == "pending"
assert not docs() and (B.get_client(500) or {}).get("credits", 0) == 0, "до оплаты ни файлов, ни кредитов"
handler("momset_callback")(cb("momset:buy"))
assert len([o for o in B.all_orders() if o.get("product") == "mom_set"]) == 1, "повторное нажатие: тот же заказ"
# напоминание о неоплаченном наборе не шлём
from datetime import timedelta  # noqa: E402
B.REMINDERS_SINCE = "2000-01-01"
noon = B.now_msk().replace(hour=12, minute=0)
order["created_at"] = (noon - timedelta(hours=3)).isoformat()
B.save_order(order)
SENT.clear()
B.send_reminders(noon)
assert not [s for s in SENT if s[0] == "send_message" and s[1][0] == 500]

m = msg("")
m.successful_payment = pytypes.SimpleNamespace(invoice_payload=oid, telegram_payment_charge_id="x", order_info=pytypes.SimpleNamespace(email="a@b.c"))
SENT.clear()
B.on_paid(m)
order = B.get_order(oid)
assert order["status"] == "done" and order["credits_granted"] == 2
assert (B.get_client(500) or {}).get("credits") == 2
assert [x[1][1].name.replace("\\", "/").split("/")[-1] for x in docs()] == ["10_pisem_blizkim.pdf", "20_voprosov_mame_i_pape.pdf"], docs()
assert order["bundle_files_sent"] == ["pdf_letters", "pdf_questions"]
mm = [s for s in SENT if s[0] == "send_message" and s[1][0] == 500 and "В наборе писем" in s[1][1]]
assert mm and ("🌷 Первое письмо: маме", "occ:p:family") in all_buttons(mm[0][2]["reply_markup"])
assert any(s[0] == "send_message" and s[1][0] == 1 and "Набор «Для мамы»" in s[1][1] for s in SENT), "админ видит выдачу"
B.fulfill_order(500, oid, "x", None)   # повторное уведомление об оплате
assert len(docs()) == 2 and (B.get_client(500) or {}).get("credits") == 2, "повтор не удваивает"
# кабинет: «Скачать ещё раз» только владельцу
btns = P.again_buttons([order])
assert len(btns) == 1 and btns[0].callback_data == f"pdfp:mom:{oid}"
for chat, expect in ((500, 2), (777, 0)):
    SENT.clear()
    handler("pdf_callback")(cb(f"pdfp:mom:{oid}", chat))
    assert len(docs()) == expect, (chat, len(docs()))

# письмо маме по кредиту: без счёта
SENT.clear()
go_family_letter()
prev = [s for s in SENT if s[2].get("reply_markup")][-1][2]["reply_markup"]
assert any(d == "occ:credit" and "осталось 2" in t for t, d in all_buttons(prev)), all_buttons(prev)
B.occ_credit(cb("occ:credit"))
assert not [s for s in SENT if s[0] == "send_invoice"] and (B.get_client(500) or {}).get("credits") == 1
assert any(s[0] == "send_photo" for s in SENT), "открытка к первому письму"
# второе письмо — другому близкому (адресата выбирает клиент): другой повод, тоже по кредиту
B.occ_go(cb("occ:go:friend"))
for a in ["Маша", "10 лет, познакомились в школе", "море и смех"]:
    B.occ_answer(msg(a))
B.occ_tone(cb("occ:tone:warm"))
B.occ_answer(msg("Аня"))
B.occ_credit(cb("occ:credit"))
assert (B.get_client(500) or {}).get("credits") == 0
letters = [o for o in B.all_orders() if o.get("paid_by") == "credit"]
assert len(letters) == 2 and {o["product"] for o in letters} == {"family", "friend"}

# другой неоплаченный заказ мешает покупке
B.REMINDERS_SINCE = "2999-01-01"
B.save_order({**order, "order_id": "ALI-OTHER", "product": "friend", "pain": "friend", "status": "pending", "chat_id": 501,
              "created_at": B.now_msk().isoformat()})
SENT.clear()
handler("momset_callback")(cb("momset:buy", 501))
assert any("Сначала оплати" in str(s[1]) for s in SENT) and not [s for s in SENT if s[0] == "send_invoice"]

# ссылка ?start=momset открывает заказ набора
B.cmd_start(pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=502), text="/start momset", from_user=msg("", 502).from_user,
                                    content_type="text"))
assert [o for o in B.all_orders() if o.get("product") == "mom_set" and o["chat_id"] == 502]

# ═══ 4. API сайта ═══
B.YOOKASSA_SHOP_ID = YK_SHOP
client = B.app.test_client()


def init_data(user_id):
    user = json.dumps({"id": user_id, "first_name": "Тест", "username": f"u{user_id}"}, ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time())), "query_id": "AAA", "user": user}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(pairs)


def call(method, path, user=600, data=None):
    h = {"Origin": "https://alisa-letters.pages.dev"}
    if user:
        h["X-Telegram-Init-Data"] = init_data(user)
    return getattr(client, method)(API + path, json=data, headers=h)


def j(resp):
    return json.loads(resp.get_data(as_text=True))


YK = {"status": "pending", "paid": False, "price": None}


def fake_yk(method, url, payload=None, idem=None):
    if method == "POST":
        YK["price"] = payload["amount"]["value"]
        return {"id": "pay-9", "confirmation": {"confirmation_url": "https://pay.example/9"}}
    return {"id": "pay-9", "status": YK["status"], "paid": YK["paid"], "amount": {"value": YK["price"]},
            "confirmation": {"confirmation_url": "https://pay.example/9"}}


B.yk_request = fake_yk
r = call("get", "/momset", user=None)
mi = j(r)
assert r.status_code == 200 and mi["enabled"] is True and mi["price"] == 590 and mi["savings"] == 57 and mi["regularPrice"] == 647 and mi["credits"] == 2
assert [i["key"] for i in mi["items"]] == ["letter", "letter2", "pdf_letters", "pdf_questions"]
assert call("post", "/momset/pay", user=None, data={}).status_code == 401
SENT.clear()
r = call("post", "/momset/pay", data={})
pay = j(r)
assert r.status_code == 200 and pay["paymentUrl"] == "https://pay.example/9" and pay["price"] == 590, pay
assert YK["price"] == "590.00"
moid = pay["orderId"]
assert B.get_order(moid)["product"] == "mom_set"
r = call("post", "/momset/pay", data={})
assert j(r)["orderId"] == moid, "повтор: тот же заказ"
st = j(call("post", f"/purchases/{moid}/check"))
assert st["paid"] is False and st["kind"] == "momset" and "files" not in st
assert call("get", f"/purchases/{moid}/file/pdf_letters.pdf").status_code == 404, "до оплаты файла нет"
assert call("post", f"/purchases/{moid}/check", user=601).status_code == 404
YK.update(status="succeeded", paid=True)
st = j(call("post", f"/purchases/{moid}/check"))
assert st["paid"] is True and st["kind"] == "momset" and st["credits"] == 2, st
assert [f["key"] for f in st["files"]] == ["pdf_letters", "pdf_questions"]
assert len(docs()) == 2, "пользователю Telegram файлы пришли и в чат"
for f in st["files"]:
    path = f["url"].replace(B.site_pages.SITE_URL, "")
    res = client.get(path)
    assert res.status_code == 200 and res.data[:5] == b"%PDF-" and res.mimetype == "application/pdf", f
    assert client.get(path.replace("sig=", "sig=0")).status_code == 401, "подпись подделать нельзя"
    assert call("get", path.replace(API, ""), user=601).status_code == 404, "чужой не скачает"
assert call("get", f"/purchases/{moid}/file/nope.pdf").status_code == 404
items = j(call("get", "/me/purchases"))["items"]
assert items and items[0]["kind"] == "momset" and items[0]["price"] == 590 and items[0]["status"] == "delivered"
assert j(call("get", "/momset"))["myCredits"] == 2

# гость сайта (канал web): кредиты без Telegram, на почте ссылка на страницу с PDF
sent_mail = []
web_auth.send_mail = lambda Bm, email, subj, text: sent_mail.append((subj, text)) or True
gid = 7_000_000_000_001
B.upsert_client(pytypes.SimpleNamespace(id=gid, first_name="Гость", last_name="", username=""))
go = P.new_mom_order(gid, B.get_client(gid))
go["channel"] = "web"
go["email"] = "g@x.ru"
B.save_order(go)
SENT.clear()
B.fulfill_order(gid, go["order_id"], "w", "g@x.ru")
assert (B.get_client(gid) or {}).get("credits") == 2 and not docs(), "гостю файлы в Telegram не шлём"
assert sent_mail and "Набор «Для мамы»" in sent_mail[-1][0] and "?paid=" in sent_mail[-1][1] and "PDF" in sent_mail[-1][1]

# ═══ 5. Флаг выключили: новые покупки закрыты, оплаченное остаётся ═══
os.environ.pop("MOM_SET")
assert j(call("get", "/momset", user=None))["enabled"] is False
r = call("post", "/momset/pay", user=603, data={})
assert r.status_code == 409 and j(r)["error"]["code"] == "momset_unavailable"
assert [f["key"] for f in j(call("post", f"/purchases/{moid}/check"))["files"]] == ["pdf_letters", "pdf_questions"]
print("test_mom_set: OK")
