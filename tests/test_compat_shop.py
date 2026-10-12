"""Разбор совместимости на сайте: заказ → оплата → привязка к паре. Без сети (ЮKassa подменена). Запуск: python tests/test_compat_shop.py"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["YOOKASSA_SHOP_ID"] = "shop"
os.environ["YOOKASSA_SECRET_KEY"] = "key"
os.environ["YOOKASSA_RECEIPT"] = "1"
os.environ["COMPAT_PRICE_RUB"] = "149"

import bot_best as B  # noqa: E402
import compat_shop as C  # noqa: E402

B.safe_send = lambda *a, **k: None
client = B.app.test_client()
H = {"X-Compat-Secret": "s3cret"}
RET = "https://alisa-letters.pages.dev/sovmestimost/"
PAY = {"state": "pending", "calls": []}


def fake_request(method, url, payload=None, idem=None):
    PAY["calls"].append((method, url, payload))
    if method == "POST":
        return {"id": "pay-1", "confirmation": {"confirmation_url": "https://yk.test/pay"}}
    if PAY["state"] == "paid":
        return {"status": "succeeded", "paid": True, "amount": {"value": "149.00"}}
    return {"status": PAY["state"], "paid": False, "amount": {"value": "149.00"}}


B.yk_request = fake_request

# выключено без секрета
os.environ.pop("COMPAT_SECRET", None)
assert client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": RET}, headers=H).status_code == 503
os.environ["COMPAT_SECRET"] = "s3cret"
assert client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": RET}).status_code == 403
assert client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": RET}, headers={"X-Compat-Secret": "bad"}).status_code == 403

# проверки ввода
assert client.post("/compat/order", json={"email": "не почта", "returnUrl": RET}, headers=H).status_code == 400
assert client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": "https://evil.example/x"}, headers=H).status_code == 400
assert client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": "https://alisa-letters.pages.dev.evil.example/"}, headers=H).status_code == 400

# заказ
r = client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": RET}, headers=H)
assert r.status_code == 200, r.data
d = r.get_json()
oid = d["orderId"]
assert oid.startswith("CMP-") and d["paymentUrl"] == "https://yk.test/pay" and d["price"] == 149
create = [c for c in PAY["calls"] if c[0] == "POST"][0][2]
assert create["confirmation"]["return_url"] == f"{RET}?o={oid}", "возврат на страницу с номером заказа"
assert create["receipt"]["customer"]["email"] == "a@b.ru" and create["amount"]["value"] == "149.00"

# статус до оплаты и привязка без оплаты
assert client.get(f"/compat/order/{oid}", headers=H).get_json()["status"] == "pending"
assert client.post(f"/compat/claim/{oid}", json={"pair": "abc123"}, headers=H).status_code == 402

# оплата
PAY["state"] = "paid"
assert client.get(f"/compat/order/{oid}", headers=H).get_json() == {"status": "paid", "price": 149}
assert client.get(f"/compat/order/{oid}", headers=H).get_json()["status"] == "paid", "повторный запрос не ломается"

# привязка к паре: первая закрепляется, чужая пара отклоняется, повтор той же — ok
assert client.post(f"/compat/claim/{oid}", json={"pair": "abc123"}, headers=H).status_code == 200
assert client.post(f"/compat/claim/{oid}", json={"pair": "abc123"}, headers=H).status_code == 200
assert client.post(f"/compat/claim/{oid}", json={"pair": "zzz999"}, headers=H).status_code == 409
assert client.post(f"/compat/claim/{oid}", json={"pair": "Имена И Даты"}, headers=H).status_code == 400

# чужие и кривые номера
assert client.get("/compat/order/CMP-notexist1", headers=H).status_code == 404
assert client.get("/compat/order/..%2Fx", headers=H).status_code in (400, 404)
assert client.post("/compat/claim/CMP-notexist1", json={"pair": "abc"}, headers=H).status_code == 404

# в заказе нет имён и дат; в общем списке писем его нет
saved = open(C._path(oid), encoding="utf-8").read()
assert "Анна" not in saved and "1990" not in saved
assert not any(o.get("order_id") == oid for o in B.all_orders()), "разборы не смешиваются с письмами"

# отмена платежа
r2 = client.post("/compat/order", json={"email": "a@b.ru", "returnUrl": RET}, headers=H).get_json()
PAY["state"] = "canceled"
assert client.get(f"/compat/order/{r2['orderId']}", headers=H).get_json()["status"] == "cancelled"
print("OK: compat shop")
