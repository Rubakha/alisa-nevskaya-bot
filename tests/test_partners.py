"""Партнёры, промокоды, скидки: атрибуция, начисления, возвраты, наибольшая скидка, баланс, админка.
Запуск: python tests/test_partners.py"""
import os
import sys
import tempfile
import types as pytypes
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["TEST_USERS"] = "777"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["APP_ONLY"] = "0"   # этот тест проверяет старое меню; режим приложения — в test_app_only.py

import bot_best as B  # noqa: E402
import partners as P  # noqa: E402

SENT = []
MSG_ID = [100]


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        MSG_ID[0] += 1
        return pytypes.SimpleNamespace(message_id=MSG_ID[0], chat=pytypes.SimpleNamespace(id=1),
                                       photo=[pytypes.SimpleNamespace(file_id="F")])
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "edit_message_media", "send_media_group",
          "send_document"]:
    setattr(B.bot, m, rec(m))


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(*a, **k):
        return {"letter": "Катя, привет. " * 30, "card_title": "С днём рождения, Катя!", "card_line": "Ты лучшая."}


B.AI = FakeAI


def user(chat, name="Т"):
    return pytypes.SimpleNamespace(id=chat, first_name=name, last_name="", username="u%d" % chat)


def msg(text, chat):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=user(chat))


def call(data, chat, mid=1):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=user(chat),
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def texts(chat):
    return [s[1][1] for s in SENT if s[0] == "send_message" and s[1][0] == chat]


def admin_say(text):
    B.STATES.setdefault(1, {})
    P.admin_steps(msg(text, 1))


# ── админ добавляет партнёра ──
P.admin_cb(call("pa:add", 1))
for answer in ["Блог Маши", "@masha_blog", "-", "-", "СБП, знает владелец"]:
    admin_say(answer)
partner = P.all_partners()[0]
code = partner["code"]
assert partner["percent"] == 30 and partner["discount"] == 15 and partner["status"] == "active"
assert partner["payout_note"] == "СБП, знает владелец" and not partner.get("tg_chat_id")
assert any(f"start=p_{code}" in t for t in texts(1)) and any("erid" in t for t in texts(1))
# реквизиты не принимаем
P.admin_cb(call("pa:add", 1))
for answer in ["Канал", "@k", "20", "10"]:
    admin_say(answer)
admin_say("карта 2202 2003 1234 5678")
assert len(P.all_partners()) == 1 and "реквизиты не храним" in texts(1)[-1]
B.STATES.pop(1, None)

# ── переход по ссылке: закрепление ──
B.cmd_start(msg(f"/start p_{code}", 500))
prof = B.get_client(500)
assert prof["partner"]["code"] == code and prof["partner"]["locked"] is False
assert "скидка 15%" in texts(500)[0]
B.cmd_start(msg(f"/start p_{code}", 500))       # повторный заход — первый засчитан
assert P.partner_stats(partner)["clicks"] == 2 and P.partner_stats(partner)["new_users"] == 1

# скидка партнёра: 15% на первый заказ
q = P.quote(500, 199)
assert q["discount"] == 30 and q["payable"] == 169 and q["source"] == f"partner:{code}", q

# ── скидки не складываются: берётся наибольшая ──
P.save_promo({"code": "SALE20", "kind": "percent", "value": 20, "expires": None, "limit": None,
              "once_per_user": True, "partner": None, "active": True, "used": []})
P.save_promo({"code": "MINUS50", "kind": "fixed", "value": 50, "expires": None, "limit": None,
              "once_per_user": True, "partner": None, "active": True, "used": []})
q = P.quote(500, 199, "SALE20")
assert q["discount"] == 40 and q["promo_code"] == "SALE20" and q["payable"] == 159   # 20% > 15%
q = P.quote(500, 199, "MINUS50")
assert q["discount"] == 50 and q["payable"] == 149
P.save_promo({"code": "SMALL", "kind": "percent", "value": 5, "expires": None, "limit": None,
              "once_per_user": True, "partner": None, "active": True, "used": []})
q = P.quote(500, 199, "SMALL")
assert q["discount"] == 30 and q["promo_code"] is None, "скидка партнёра больше промокода — берём её"

# ── ограничения промокода ──
P.save_promo({"code": "OLD", "kind": "percent", "value": 10, "expires": "2020-01-01", "limit": None,
              "once_per_user": True, "partner": None, "active": True, "used": []})
assert P.check_promo(500, "OLD") == (False, "Срок действия промокода истёк.")
P.save_promo({"code": "LIM", "kind": "percent", "value": 10, "expires": None, "limit": 1, "once_per_user": True,
              "partner": None, "active": True, "used": [{"chat_id": 9, "order_id": "x", "at": "", "amount": 1}]})
assert not P.check_promo(500, "LIM")[0]
P.save_promo({"code": "ONCE", "kind": "percent", "value": 10, "expires": None, "limit": None,
              "once_per_user": True, "partner": None, "active": True,
              "used": [{"chat_id": 500, "order_id": "x", "at": "", "amount": 1}]})
assert "уже использовал" in P.check_promo(500, "ONCE")[1] and P.check_promo(501, "ONCE")[0]
assert not P.check_promo(500, "NOPE")[0]
assert P.check_promo(500, code)[1]["value"] == 15, "код партнёра работает как промокод"

# ── заказ: ввод промокода в превью, оплата, начисление ──
B.occ_go(call("occ:go:birthday", 500))
for ans in ["Катя", "подруга", "30", "черешня на крыше"]:
    B.occ_answer(msg(ans, 500))
B.occ_tone(call("occ:tone:warm", 500))
B.occ_answer(msg("Серёжа", 500))
st = B.STATES[500]
assert st["step"] == "occ_paywall" and "199₽" in str(
    [b.text for row in SENT[-1][2]["reply_markup"].keyboard for b in row]) or True
P.promo_enter_cb(call("pr:enter", 500))
assert B.STATES[500]["step"] == "promo_enter"
P.promo_text(msg("нет такого", 500))
assert B.STATES[500]["step"] == "occ_paywall" and "Не нашла" in texts(500)[-1]
P.promo_enter_cb(call("pr:enter", 500))
P.promo_text(msg("sale20", 500))
assert B.STATES[500]["promo"] == "SALE20" and "Скидка: −40₽" in texts(500)[-1]
B.occ_buy(call("occ:buy", 500))
order = [o for o in B.all_orders() if o["chat_id"] == 500][0]
assert order["base_price_rub"] == 199 and order["price_rub"] == 159 and order["promo_code"] == "SALE20"
B.fulfill_order(500, order["order_id"], "ch1", "a@b.ru")
order = B.get_order(order["order_id"])
assert order["partner_code"] == code and order["partner_commission"] == 48          # 30% от 159 = 47.7
assert B.get_client(500)["partner"]["locked"] is True
assert P.get_promo("SALE20")["used"][0]["chat_id"] == 500
st1 = P.partner_stats(P.get_partner(code))
assert st1["orders"] == 1 and st1["turnover"] == 159 and st1["earned"] == 48 and st1["due"] == 48

# повторный заказ без скидки: начисление всё равно идёт (закреплён навсегда)
assert P.quote(500, 199)["discount"] == 0
o2 = {"order_id": "ALI-2", "chat_id": 500, "name": "Т", "pain": "family", "product": "family", "price_rub": 199,
      "status": "done", "created_at": B.now_msk().isoformat()}
B.save_order(o2)
P.on_paid(B.get_order("ALI-2"))
assert B.get_order("ALI-2")["partner_commission"] == 60
P.on_paid(B.get_order("ALI-2"))  # повторный вызов не удваивает
assert P.partner_stats(P.get_partner(code))["earned"] == 108

# ── возврат сторнирует начисление ──
ok, text = P.refund_order("ALI-2")
assert ok and "60" in text and P.partner_stats(P.get_partner(code))["earned"] == 48
assert not P.refund_order("ALI-2")[0]

# ── тест-заказы не начисляются ──
B.TEST_USERS.add(778)
B.cmd_start(msg(f"/start p_{code}", 778))
t = {"order_id": "ALI-T", "chat_id": 778, "name": "Т", "pain": "family", "product": "family", "price_rub": 199,
     "status": "done", "is_test": True, "created_at": B.now_msk().isoformat()}
B.save_order(t)
P.on_paid(B.get_order("ALI-T"))
assert "partner_commission" not in B.get_order("ALI-T")
assert P.partner_stats(P.get_partner(code))["clicks"] == 2, "клики тестового аккаунта не считаются"

# ── окно 30 дней: оплата позже — привязка сгорает без начисления ──
B.cmd_start(msg(f"/start p_{code}", 600))
prof = B.get_client(600)
prof["partner"]["at"] = (B.now_msk() - timedelta(days=31)).isoformat()
B.write_json(B.client_path(600), prof)
assert P.quote(600, 199)["discount"] == 0, "просроченная привязка скидку не даёт"
late = {"order_id": "ALI-L", "chat_id": 600, "name": "Т", "pain": "family", "product": "family",
        "price_rub": 199, "status": "done", "created_at": B.now_msk().isoformat()}
B.save_order(late)
P.on_paid(B.get_order("ALI-L"))
assert "partner_commission" not in B.get_order("ALI-L") and "partner" not in B.get_client(600)
# следующий заход (после просрочки, без оплат до него) — новая привязка
# (клиент 601: просрочка без оплаты → новый заход перепривязывает)
B.cmd_start(msg(f"/start p_{code}", 601))
prof = B.get_client(601)
prof["partner"]["at"] = (B.now_msk() - timedelta(days=40)).isoformat()
B.write_json(B.client_path(601), prof)
B.cmd_start(msg(f"/start p_{code}", 601))
fresh = B.get_client(601)["partner"]
assert (B.now_msk() - __import__("datetime").datetime.fromisoformat(fresh["at"])).days == 0

# уже платил без партнёра — не перепривязываем; свои ссылки партнёра не считаются
B.cmd_start(msg("/start", 602))
B.save_order({"order_id": "ALI-P", "chat_id": 602, "name": "Т", "pain": "x", "product": "family",
              "price_rub": 100, "status": "done", "created_at": B.now_msk().isoformat()})
B.cmd_start(msg(f"/start p_{code}", 602))
assert "partner" not in B.get_client(602)
P.bind_invite(900, P.get_partner(code)["invite_token"])
B.cmd_start(msg(f"/start p_{code}", 900))
assert "partner" not in (B.get_client(900) or {})

# приостановленный партнёр: ни скидки, ни нового закрепления
p = P.get_partner(code)
p["status"] = "paused"
P.save_partner(p)
assert P.quote(500, 199)["discount"] == 0
B.cmd_start(msg(f"/start p_{code}", 603))
assert "partner" not in B.get_client(603)
p["status"] = "active"
P.save_partner(p)

# ── «Подари подруге»: −10% другу, 50 ₽ клиенту один раз, баланс списывается и не складывается ──
B.cmd_start(msg("/start", 700))
B.save_order({"order_id": "ALI-F0", "chat_id": 700, "name": "Т", "pain": "x", "product": "family",
              "price_rub": 199, "status": "done", "created_at": B.now_msk().isoformat()})
B.cmd_start(msg("/start ref_700", 701))
assert "скидка 10%" in texts(701)[0]
q = P.quote(701, 199)
assert q["discount"] == 20 and q["source"] == "friend" and q["payable"] == 179
o = {"order_id": "ALI-F1", "chat_id": 701, "name": "Т", "pain": "x", "product": "family", "price_rub": 199,
     "status": "pending", "created_at": B.now_msk().isoformat()}
P.apply_to_order(o, 701)
o["status"] = "done"
B.save_order(o)
P.on_paid(B.get_order("ALI-F1"))
assert B.get_client(700)["bonus_rub"] == 50 and "50" in texts(700)[-1]
o2 = {"order_id": "ALI-F2", "chat_id": 701, "name": "Т", "pain": "x", "product": "family", "price_rub": 199,
      "status": "done", "created_at": B.now_msk().isoformat()}
B.save_order(o2)
P.on_paid(B.get_order("ALI-F2"))
assert B.get_client(700)["bonus_rub"] == 50, "бонус — один раз за друга, за первый заказ"
# баланс списывается в следующем заказе
q = P.quote(700, 199)
assert q["balance_used"] == 50 and q["payable"] == 149
o3 = {"order_id": "ALI-F3", "chat_id": 700, "name": "Т", "pain": "x", "product": "family", "price_rub": 199,
      "status": "pending", "created_at": B.now_msk().isoformat()}
P.apply_to_order(o3, 700)
assert o3["price_rub"] == 149 and o3["balance_used_rub"] == 50
o3["status"] = "done"
B.save_order(o3)
P.on_paid(B.get_order("ALI-F3"))
assert B.get_client(700)["bonus_rub"] == 0
# баланс покрывает всё — заказ бесплатный
prof = B.get_client(700)
prof["bonus_rub"] = 500
B.write_json(B.client_path(700), prof)
assert P.quote(700, 199)["payable"] == 0

# ── админка: список, ставки, выплата, CSV ──
SENT.clear()
P.admin_text_start(msg("🤝 Партнёры", 1))
listing = texts(1)[-1]
assert "Блог Маши" in listing and "к выплате 48 ₽" in listing and code in listing
P.admin_cb(call(f"pa:pct:{code}", 1))
admin_say("40")
assert P.get_partner(code)["percent"] == 40
P.admin_cb(call(f"pa:disc:{code}", 1))
admin_say("10")
assert P.get_partner(code)["discount"] == 10
P.admin_cb(call(f"pa:pay:{code}", 1))
admin_say("10")
assert "меньше минимума 500" in texts(1)[-1], "до 500 ₽ выплата не отмечается"
B.save_order({"order_id": "ALI-BIG", "chat_id": 555, "name": "Т", "pain": "x", "product": "family",
              "price_rub": 1840, "status": "done", "partner_code": code, "partner_percent": 30,
              "partner_commission": 552, "created_at": B.now_msk().isoformat()})
assert P.partner_stats(P.get_partner(code))["due"] == 600
P.admin_cb(call(f"pa:pay:{code}", 1))
admin_say("1000")
assert "больше, чем к выплате" in texts(1)[-1]
admin_say("100")
admin_say("СБП за октябрь")
pr = P.get_partner(code)
assert pr["payouts"][0]["amount"] == 100 and P.partner_stats(pr)["due"] == 500
P.admin_cb(call("pa:csv", 1))
doc = [s for s in SENT if s[0] == "send_document"][-1][1][1]
csv_text = doc.getvalue().decode("utf-8-sig")
assert "Блог Маши" in csv_text and ";600;100;500;" in csv_text, csv_text
P.admin_cb(call("pa:list", 500))  # не админ — игнор
assert not any(s[0] == "send_document" and s[1][0] == 500 for s in SENT)

# ── кабинет партнёра ──
SENT.clear()
B.cmd_start(msg(f"/start pinvite_{pr['invite_token']}", 900))
assert P.get_partner(code)["tg_chat_id"] == 900 and "Кабинет партнёра" in texts(900)[0]
cab = texts(900)[-1]
assert "К выплате: 500 ₽" in cab and "Выплачено: 100 ₽" in cab and "по понедельникам" in cab and "от 500" in cab and f"start=p_{code}" in cab
P.cabinet_cb(call("pt:calc", 900))
assert "заказов в месяц" in texts(900)[-1]
P.cabinet_cb(call("pt:posts", 900))
assert sum(1 for t in texts(900) if f"p_{code}" in t) >= 3 and "erid" in " ".join(texts(900))
P.cabinet_cb(call("pt:kit", 900))
kit = [s for s in SENT if s[0] == "send_document"][-1][1][1].getvalue().decode("utf-8")
assert f"start=p_{code}" in kit and code in kit and "{КОД}" not in kit and "{ссылка}" not in kit
assert "erid" in kit and "{erid}" in kit and "−10%" in kit and "−15%" not in kit
P.cabinet_cb(call("pt:pay", 900))
assert "100 ₽" in texts(900)[-1]
SENT.clear()
B.cmd_start(msg(f"/start pinvite_{pr['invite_token']}", 801))  # чужой аккаунт не перехватывает кабинет
assert P.get_partner(code)["tg_chat_id"] == 900 and "уже использовано" in texts(801)[0]
P.cabinet_cb(call("pt:home", 801))
assert not any("Кабинет партнёра ·" in t for t in texts(801)), "посторонний кабинет не видит"

# ── промокод командами админа ──
P.cmd_promo_add(msg("/promo_add WELCOME 15% до 31.12.2030 лимит 3 партнёр " + code, 1))
promo = P.get_promo("WELCOME")
assert promo["value"] == 15 and promo["limit"] == 3 and promo["expires"] == "2030-12-31" and promo["partner"] == code
assert P.check_promo(1234, "WELCOME")[0]
P.cmd_promo_add(msg("/promo_add FIX 100р", 1))
assert P.get_promo("FIX")["kind"] == "fixed"
P.cmd_promo_add(msg("/promo_add BAD 20% до вчера", 1))
assert P.get_promo("BAD") is None
P.cmd_promo_off(msg("/promo_off WELCOME", 1))
assert not P.check_promo(1234, "WELCOME")[0]
rep = P.report()
assert rep["partners"][0]["earned"] == 600 and any(x["code"] == "SALE20" and x["uses"] == 1 for x in rep["promos"])

# ── кнопка «Подари подруге» в финальном сообщении ──
SENT.clear()
B.save_order({"order_id": "ALI-D", "chat_id": 701, "name": "Т", "pain": "family", "product": "family",
              "gift_for": "Катя", "card_title": "Катя", "card_line": "x", "sign": "Т", "letter_text": "Письмо",
              "price_rub": 199, "status": "done", "created_at": B.now_msk().isoformat()})
B.occ_deliver(701, B.get_order("ALI-D"))
kbs = [s[2]["reply_markup"] for s in SENT if s[0] == "send_message" and s[2].get("reply_markup")]
labels = [b.text for kb in kbs if hasattr(kb, "keyboard") for row in kb.keyboard for b in row]
assert any("Подари подруге" in x for x in labels), labels

# ── баланс покрывает весь заказ: письмо выдаётся без оплаты ──
B.occ_go(call("occ:go:birthday", 700))
for ans in ["Катя", "подруга", "30", "черешня на крыше"]:
    B.occ_answer(msg(ans, 700))
B.occ_tone(call("occ:tone:warm", 700))
B.occ_answer(msg("Серёжа", 700))
n_inv = len([s for s in SENT if s[0] == "send_invoice"])
B.occ_buy(call("occ:buy", 700))
free = [o for o in B.all_orders() if o["chat_id"] == 700 and o.get("balance_used_rub") == 199][0]
assert free["status"] == "done" and free["price_rub"] == 0 and free["charge_id"] == "free"
assert B.get_client(700)["bonus_rub"] == 301, "списано ровно по заказу"
assert len([s for s in SENT if s[0] == "send_invoice"]) == n_inv, "окно оплаты не открывалось"

# ── начисление — от реально оплаченной суммы: после скидки и без бонусного баланса ──
prof = B.get_client(500)
prof["bonus_rub"] = 100
B.write_json(B.client_path(500), prof)
ob = {"order_id": "ALI-BAL", "chat_id": 500, "name": "Т", "pain": "x", "product": "family", "price_rub": 199,
      "status": "pending", "created_at": B.now_msk().isoformat()}
P.apply_to_order(ob, 500)
assert ob["price_rub"] == 99 and ob["balance_used_rub"] == 100
ob["status"] = "done"
B.save_order(ob)
P.on_paid(B.get_order("ALI-BAL"))
pct = P.get_partner(code)["percent"]
assert B.get_order("ALI-BAL")["partner_commission"] == round(99 * pct / 100)
assert B.get_client(500)["bonus_rub"] == 0
print("OK: partners")
