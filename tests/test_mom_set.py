"""Набор «Для мамы» (письмо + открытка + два PDF, 590 ₽): флаг, кнопки, цена, оплата одним платежом, выдача, API сайта.
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
os.environ.pop("MOM_SET", None)
os.environ.pop("MOM_SET_PRICE_RUB", None)
os.environ.pop("PDF_PRODUCTS", None)
os.environ.pop("YOOKASSA_RECEIPT", None)

import bot_best as B  # noqa: E402
import miniapp_api as A  # noqa: E402
import pdf_products as P  # noqa: E402

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


def family_questions(chat=500):
    """Проходит анкету «Маме, папе, бабушке» до экрана оплаты."""
    for a in ["Лена", "мама", "пирог с вишней по воскресеньям", "за то, что всегда ждала", ]:
        B.occ_answer(msg(a, chat))
    B.occ_tone(cb("occ:tone:tender", chat))
    B.occ_answer(msg("Аня", chat))
    assert B.STATES[chat]["step"] == "occ_paywall", B.STATES[chat]


YK_SHOP = B.YOOKASSA_SHOP_ID
B.YOOKASSA_SHOP_ID = ""   # бот-часть идёт через Telegram Payments (send_invoice)

# ═══ 1. По умолчанию выключено ═══
assert not P.mom_enabled()
assert not any("Для мамы" in t for t, _ in all_buttons(B.occ_catalog_markup()))
n = len(SENT)
B.occ_open_product(500, "family")
assert not any("Для мамы" in t for t, _ in all_buttons(last("send_message")[2]["reply_markup"])), "страница повода без набора"
B.momset_cb(cb("momset:info"))
B.momset_cb(cb("momset:go"))
assert 500 not in B.STATES and not [s for s in SENT[n:] if s[0] == "edit_message_text"], "выключено: ничего не происходит"
assert P.mom_info()["enabled"] is False

# ═══ 2. Включаем: кнопки, честная цена ═══
os.environ["MOM_SET"] = "1"
btn = [t for t, d in all_buttons(B.occ_catalog_markup()) if d == "momset:info"]
assert btn and "590" in btn[0] and "письмо, открытка, два гида" in btn[0], btn
B.occ_open_product(500, "family")
assert any(d == "momset:info" for _, d in all_buttons(last("send_message")[2]["reply_markup"]))
B.occ_open_product(500, "birthday")
assert not any(d == "momset:info" for _, d in all_buttons(last("send_message")[2]["reply_markup"])), "набор только у повода family"

info = P.mom_info()
assert info["price"] == 590 and info["regularPrice"] == 199 + 249 + 199 and info["savings"] == 57, info
text = P.mom_text()
assert "590₽" in text and "647₽" in text and "выгода 57₽" in text and "199₽" in text and "249₽" in text, text
# выгода не обещается, если «20 вопросов» бесплатны и набор получается дороже частей
os.environ["PDF_QUESTIONS_PRICE_RUB"] = "0"
info0 = P.mom_info()
assert info0["regularPrice"] == 448 and info0["savings"] == 0 and "выгода" not in P.mom_text()
os.environ.pop("PDF_QUESTIONS_PRICE_RUB")
os.environ["MOM_SET_PRICE_RUB"] = "490"
assert P.mom_info()["price"] == 490 and P.mom_info()["savings"] == 157
os.environ.pop("MOM_SET_PRICE_RUB")

B.momset_cb(cb("momset:info"))
kb = last("edit_message_text")[2]["reply_markup"] if [s for s in SENT if s[0] == "edit_message_text"] else None
if kb is None:   # safe_edit может слать новым сообщением
    kb = last("send_message")[2]["reply_markup"]
assert ("✍️ Собрать набор · 590₽", "momset:go") in all_buttons(kb)
assert any(d == "occ:p:family" for _, d in all_buttons(kb)), "есть путь «только письмо»"

# ═══ 3. Бот: анкета, один платёж 590, выдача письма, открытки и двух PDF ═══
B.momset_cb(cb("momset:go"))
st = B.STATES[500]
assert st["product"] == "family" and st["price"] == 590 and st["bundle"] == "mom_set", st
family_questions()
prev_markup = all_buttons(last("send_message")[2]["reply_markup"]) if last("send_message")[2].get("reply_markup") else []
assert any("590" in t for t, d in prev_markup if d == "occ:buy"), prev_markup
assert not any(d == "occ:credit" for _, d in prev_markup), "кредит набора 3 писем набор «Для мамы» не оплачивает"
assert "два PDF" in B.occ_preview_text(B.STATES[500]), "в превью видно, что входит в оплату"

B.occ_buy(cb("occ:buy"))
inv = last("send_invoice")[2]
assert inv["prices"][0].amount == 59000 and inv["prices"][0].label == "Набор", inv
assert "Набор" in inv["title"] and "два PDF" in inv["description"]
oid = inv["invoice_payload"]
order = B.get_order(oid)
assert order["bundle"] == "mom_set" and order["product"] == "family" and order["price_rub"] == 590 and order["status"] == "pending"
assert not docs(), "до оплаты файлов нет"

pay = pytypes.SimpleNamespace(invoice_payload=oid, telegram_payment_charge_id="x", order_info=pytypes.SimpleNamespace(email="a@b.c"))
m = msg("")
m.successful_payment = pay
SENT.clear()
B.on_paid(m)
order = B.get_order(oid)
assert order["status"] == "done" and order["price_rub"] == 590
assert any(s[0] == "send_photo" for s in SENT), "открытка выдана"
d = docs()
assert [x[1][1].name.split("\\")[-1].split("/")[-1] for x in d] == ["10_pisem_blizkim.pdf", "20_voprosov_mame_i_pape.pdf"], d
assert order["bundle_files_sent"] == ["pdf_letters", "pdf_questions"]
assert any(s[0] == "send_message" and s[1][0] == 1 and "Набор «Для мамы»" in s[1][1] for s in SENT), "админ видит выдачу"
# повторное уведомление об оплате не выдаёт файлы второй раз
B.fulfill_order(500, oid, "x", None)
assert len(docs()) == 2
# кабинет: кнопка «скачать ещё раз» и повторная выдача только владельцу
btns = P.again_buttons([order])
assert len(btns) == 1 and btns[0].callback_data == f"pdfp:mom:{oid}"
for chat, expect in ((500, 2), (777, 0)):
    SENT.clear()
    c = cb(f"pdfp:mom:{oid}", chat)
    # вызываем обработчик напрямую через зарегистрированную функцию
    for h in B.bot.callback_query_handlers:
        f = h.get("function") if isinstance(h, dict) else getattr(h, "function", None)
        if f and f.__name__ == "pdf_callback":
            f(c)
    assert len(docs()) == expect, (chat, len(docs()))

# обычное письмо «Маме» без набора: 199 ₽ и никаких PDF
B.STATES.pop(500, None)
B.occ_go(cb("occ:go:family"))
assert "bundle" not in B.STATES[500] and "price" not in B.STATES[500]
family_questions()
SENT.clear()
B.occ_buy(cb("occ:buy"))
inv = last("send_invoice")[2]
assert inv["prices"][0].amount == 19900 and inv["prices"][0].label == "Письмо"
o2 = B.get_order(inv["invoice_payload"])
assert not o2.get("bundle")
m = msg("")
m.successful_payment = pytypes.SimpleNamespace(invoice_payload=o2["order_id"], telegram_payment_charge_id="y",
                                               order_info=pytypes.SimpleNamespace(email="a@b.c"))
B.on_paid(m)
assert not docs(), "без набора PDF не выдаются"

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
ANS = {"name": "Лена", "who": "мама", "memory": "пирог", "thanks": "за ожидание", "tone": "tender", "sign": "Аня"}

# публичная ручка
r = call("get", "/momset", user=None)
mi = j(r)
assert r.status_code == 200 and mi["enabled"] is True and mi["price"] == 590 and mi["savings"] == 57 and mi["regularPrice"] == 647
assert [i["key"] for i in mi["items"]] == ["letter", "pdf_letters", "pdf_questions"]
# набор только для повода family
r = call("post", "/orders", data={"product": "birthday", "bundle": "mom_set", "answers": {"name": "К", "who": "п", "age": "3", "details": "д", "tone": "warm", "sign": "С"}})
assert r.status_code == 409 and j(r)["error"]["code"] == "bundle_unavailable"
r = call("post", "/orders", data={"product": "family", "bundle": "evil", "answers": ANS})
assert r.status_code == 409
r = call("post", "/orders", data={"product": "family", "bundle": "mom_set", "answers": ANS})
oid = j(r)["orderId"]
call("post", f"/orders/{oid}/preview")
st = j(call("get", f"/orders/{oid}"))
assert st["bundle"] == "mom_set" and st["price"] == 590 and st["basePrice"] == 590 and st["creditsAvailable"] == 0, st
# до оплаты ссылок на файлы нет, голос и кредит недоступны
assert "bonusFiles" not in st
r = call("put", f"/orders/{oid}/voice", data={"enabled": True})
assert r.status_code in (409, 404), r.status_code
r = call("post", f"/orders/{oid}/pay", data={"useCredit": True})
assert r.status_code == 409 and j(r)["error"]["code"] == "credit_not_allowed"
r = call("post", f"/orders/{oid}/pay", data={})
pay = j(r)
assert r.status_code == 200 and pay["paymentUrl"] == "https://pay.example/9" and pay["price"] == 590, pay
assert YK["price"] == "590.00" and B.get_order(oid)["bundle"] == "mom_set"
# файлы до оплаты не отдаются
fake_path = f"{API}/orders/{oid}/bundle/pdf_letters.pdf"
assert client.get(fake_path).status_code == 401
assert call("get", f"/orders/{oid}/bundle/pdf_letters.pdf").status_code == 404
SENT.clear()
YK.update(status="succeeded", paid=True)
r = call("post", f"/orders/{oid}/pay/check")
assert j(r)["paid"] is True
assert len(docs()) == 2, "пользователю Telegram файлы пришли и в чат"
st = j(call("get", f"/orders/{oid}"))
assert st["status"] == "delivered" and st["bundle"] == "mom_set" and "letter" in st
files = st["bonusFiles"]
assert [f["key"] for f in files] == ["pdf_letters", "pdf_questions"] and all(f["url"] for f in files), files
for f in files:
    path = f["url"].replace(B.site_pages.SITE_URL, "")
    res = client.get(path)
    assert res.status_code == 200 and res.data[:5] == b"%PDF-" and res.mimetype == "application/pdf", f
    assert client.get(path.replace("sig=", "sig=0")).status_code == 401, "подпись подделать нельзя"
    assert call("get", path.replace(API, ""), user=601).status_code == 404, "чужой не скачает"
assert call("get", f"/orders/{oid}/bundle/nope.pdf").status_code == 404
# обычный заказ без набора: bundle в состоянии нет
o3 = j(call("post", "/orders", user=602, data={"product": "family", "answers": ANS}))["orderId"]
call("post", f"/orders/{o3}/preview", user=602)
s3 = j(call("get", f"/orders/{o3}", user=602))
assert "bundle" not in s3 and s3["price"] == 199

# гость сайта: письмо на почту содержит упоминание PDF
sent_mail = []
import web_auth  # noqa: E402
web_auth.send_mail = lambda Bm, email, subj, text: sent_mail.append(text) or True
web_auth.on_paid(B, {"order_id": "ALI-X", "email": "g@x.ru", "bundle": "mom_set"})
assert "Два PDF" in sent_mail[-1]
web_auth.on_paid(B, {"order_id": "ALI-Y", "email": "g@x.ru"})
assert "Два PDF" not in sent_mail[-1]

# ═══ 5. Выключили флаг обратно: API закрыт, оплаченное остаётся доступным ═══
os.environ.pop("MOM_SET")
assert j(call("get", "/momset", user=None))["enabled"] is False
r = call("post", "/orders", user=603, data={"product": "family", "bundle": "mom_set", "answers": ANS})
assert r.status_code == 409
assert j(call("get", f"/orders/{oid}"))["bonusFiles"], "уже оплаченное скачать можно и при выключенном флаге"
print("test_mom_set: OK")
