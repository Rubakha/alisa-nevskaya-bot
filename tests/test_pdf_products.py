"""PDF-товары «20 вопросов маме и папе» и «10 писем»: флаг, заказ, оплата, выдача файла (без сети).
Запуск: python tests/test_pdf_products.py"""
import os
import sys
import tempfile
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "tok"
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ.pop("PDF_PRODUCTS", None)

import bot_best as B  # noqa: E402
import pdf_products as P  # noqa: E402

SENT = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=len(SENT), chat=pytypes.SimpleNamespace(id=500))
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_document", "answer_callback_query",
          "set_chat_menu_button", "send_chat_action", "delete_message", "edit_message_reply_markup", "edit_message_text"]:
    setattr(B.bot, m, rec(m))

user = pytypes.SimpleNamespace(id=500, first_name="Аня", username="anya", last_name=None, language_code="ru")


def pdf_orders():
    return [o for o in B.all_orders() if o.get("product") in P.PRODUCTS]


def catalog_texts():
    return [b.text for row in B.occ_catalog_markup().keyboard for b in row]


# 0. файлы лежат в assets и это настоящие PDF
for key, p in P.PRODUCTS.items():
    path = P.file_path(key)
    assert os.path.exists(path), path
    assert open(path, "rb").read(5) == b"%PDF-", path

# 1. по умолчанию выключено: ничего не видно и не создаётся
assert not P.enabled()
assert not any("PDF" in t and ("вопрос" in t or "писем близким" in t) for t in catalog_texts())
assert P.open_order(500, user, "pdf_questions") is False
B.cmd_start(pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=500), text="/start pdf_questions", from_user=user,
                                    content_type="text"))
assert not pdf_orders(), "при выключенном флаге заказ не создаётся"
assert not any(n == "send_invoice" for n, *_ in SENT)

# 2. включаем: кнопки в каталоге, цены 199 и 249
os.environ["PDF_PRODUCTS"] = "1"
texts = catalog_texts()
assert any("20 вопросов" in t and "199" in t for t in texts), texts
assert any("10 писем" in t and "249" in t for t in texts), texts

for key, price in [("pdf_questions", 199), ("pdf_letters", 249)]:
    SENT.clear()
    assert P.open_order(500, user, key) is True
    orders = [o for o in pdf_orders() if o["product"] == key]
    assert len(orders) == 1 and orders[0]["price_rub"] == price and orders[0]["status"] == "pending", orders
    inv = [s for s in SENT if s[0] == "send_invoice"]
    assert len(inv) == 1, "нет окна оплаты"
    assert inv[0][2]["prices"][0].amount == price * 100 and inv[0][2]["prices"][0].label == "PDF"
    assert inv[0][2]["title"] == P.PRODUCTS[key]["short"][:32], inv[0][2]["title"]
    # повторный переход по ссылке не плодит заказы
    P.open_order(500, user, key)
    assert len([o for o in pdf_orders() if o["product"] == key]) == 1
    # чужой неоплаченный заказ мешает: сначала оплати/отмени
    if key == "pdf_questions":
        SENT.clear()
        P.open_order(500, user, "pdf_letters")
        assert not [o for o in pdf_orders() if o["product"] == "pdf_letters"]
        assert any("Сначала оплати" in str(s[1]) for s in SENT)
    # напоминание о неоплаченном не шлём (как у PDF «Разговор с папой»)
    SENT.clear()
    from datetime import timedelta  # noqa: E402
    B.REMINDERS_SINCE = "2000-01-01"
    noon = B.now_msk().replace(hour=12, minute=0)
    o = orders[0]
    o["created_at"] = (noon - timedelta(hours=3)).isoformat()
    B.save_order(o)
    B.send_reminders(noon)
    assert not [s for s in SENT if s[0] == "send_message"], SENT
    # подтверждённая оплата -> файл
    SENT.clear()
    B.fulfill_order(500, o["order_id"], "ch-" + key, None)
    docs = [s for s in SENT if s[0] == "send_document"]
    assert len(docs) == 1, SENT
    assert docs[0][1][1].name.endswith(P.PRODUCTS[key]["file"]), docs[0][1][1].name
    assert B.get_order(o["order_id"])["status"] == "done"
    # повторная доставка апдейта об оплате не выдаёт второй файл
    B.fulfill_order(500, o["order_id"], "ch-" + key, None)
    assert len([s for s in SENT if s[0] == "send_document"]) == 1
    assert any(s[0] == "send_message" and s[1][0] == 1 and "PDF" in s[1][1] for s in SENT), "админ не уведомлён"

# 3. кабинет: «Скачать ещё раз» и повторная отправка только владельцу
done = [o for o in pdf_orders() if o["status"] == "done"]
assert len(done) == 2
btns = P.again_buttons(done)
assert len(btns) == 2 and all(b.callback_data.startswith("pdfp:again:") for b in btns)
SENT.clear()
cq = lambda data, chat: pytypes.SimpleNamespace(id="c", data=data, from_user=user,
                                                message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), message_id=1))
B.bot.callback_query_handlers = getattr(B.bot, "callback_query_handlers", [])
handler = [h for h in B.bot.callback_query_handlers if h["filters"] and h["filters"].get("func") and h["filters"]["func"](cq("pdfp:x", 1))]
assert handler, "обработчик pdfp: не зарегистрирован"
handler[0]["function"](cq(btns[0].callback_data, 500))
assert len([s for s in SENT if s[0] == "send_document"]) == 1
SENT.clear()
handler[0]["function"](cq(btns[0].callback_data, 999))   # чужой чат
assert not [s for s in SENT if s[0] == "send_document"]

# 4. чек ЮKassa: позиция называется по товару, а не «Письмо от Алисы»
API = []


def fake_api(method, url, payload=None, idem=None):
    API.append((method, url, payload))
    return {"id": "yk-pdf", "confirmation": {"confirmation_url": "https://yoomoney.ru/pay/yk-pdf"}}


B.yk_request = fake_api
B.YOOKASSA_RECEIPT = True
B.YK_API = "https://api.test/payments"
o2 = {"order_id": B.new_order_id(), "chat_id": 501, "name": "Б", "pain": "pdf_letters", "product": "pdf_letters",
      "price_rub": 249, "status": "pending", "created_at": B.now_msk().isoformat()}
B.save_order(o2)
assert B.yk_create(o2, "a@b.ru", "https://t.me/bot") == "https://yoomoney.ru/pay/yk-pdf"
payload = API[-1][2]
assert payload["amount"]["value"] == "249.00"
assert "Письмо от Алисы" not in payload["receipt"]["items"][0]["description"]
assert "10 писем" in payload["receipt"]["items"][0]["description"]
assert payload["description"].startswith("PDF «10 писем")

# 5. файл пропал: клиент получает сообщение, админ видит тревогу, заказ всё равно done
os.environ["PDF_QUESTIONS_PRICE_RUB"] = "299"
assert P.price("pdf_questions") == 299
os.environ.pop("PDF_QUESTIONS_PRICE_RUB")
real = P.PRODUCTS["pdf_letters"]["file"]
P.PRODUCTS["pdf_letters"]["file"] = "net_takogo.pdf"
o3 = dict(o2, order_id=B.new_order_id(), chat_id=502, status="pending")
B.save_order(o3)
SENT.clear()
B.fulfill_order(502, o3["order_id"], "ch-x", None)
assert any("файл не отправился" in str(s[1]) for s in SENT), SENT
P.PRODUCTS["pdf_letters"]["file"] = real

print("OK: pdf products")
