"""Путь «старт → выбор → превью → оплата → выдача» перед рекламой с 13.10 (пик — День отца 18.10).
Без сети: подменяем Telegram, ЮKassa и Claude API.
Запуск: python tests/test_funnel_1310.py"""
import os
import sys
import tempfile
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["TEST_USERS"] = ""
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["YOOKASSA_SECRET_KEY"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""

import bot_best as B  # noqa: E402
import group_letters as G  # noqa: E402
import occasions as OCC  # noqa: E402
import partners as P  # noqa: E402
import sources as S  # noqa: E402

SENT = []
NEXT = []
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
B.bot.register_next_step_handler = lambda msg, fn, *a: NEXT.append((fn, a))

AI_FAIL = [False]
LONG_LETTER = [None]


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title, previous="", wish=""):
        if AI_FAIL[0]:
            return {"letter": "[ai] Слишком часто. Подожди минуту и повтори.", "card_title": title, "card_line": ""}
        return {"letter": LONG_LETTER[0] or ("Катя, помнишь черешню на крыше? " * 14 + f"\n{sign}"),
                "card_title": title, "card_line": "Ты смеёшься громче всех."}

    @staticmethod
    def diagnostic_question(pain_title, history):
        return "[ai] Слишком часто. Подожди минуту и повтори." if AI_FAIL[0] else "Расскажи подробнее?"

    @staticmethod
    def mirror_reflection(pain_title, answers):
        return "[ai] Слишком часто. Подожди минуту и повтори." if AI_FAIL[0] else "Я слышу тебя."

    @staticmethod
    def generate_letter(pain_title, answers, mirror, gift_for=None):
        return "[ai] Слишком часто. Подожди минуту и повтори." if AI_FAIL[0] else "Письмо про тебя. " * 30


B.AI = FakeAI


def user(chat):
    return pytypes.SimpleNamespace(id=chat, first_name="Тест", last_name="", username="u%d" % chat)


def msg(text, chat):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=user(chat))


def call(data, chat, mid=1):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=user(chat),
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def texts(chat, kind="send_message"):
    return [s[1][1] if len(s[1]) > 1 else "" for s in SENT if s[0] == kind and s[1] and s[1][0] == chat]


def invoices(chat):
    return [s[2]["invoice_payload"] for s in SENT if s[0] == "send_invoice" and s[2].get("chat_id") == chat]


def orders_of(chat):
    return [o for o in B.all_orders(include_test=True) if o["chat_id"] == chat]


def start(chat, payload=None):
    B.cmd_start(msg("/start" + (f" {payload}" if payload is not None else ""), chat))


def to_preview(chat, product="birthday"):
    """Каталог → анкета → превью. Возвращает состояние диалога."""
    B.occ_go(call(f"occ:go:{product}", chat))
    for qkey, _ in B.occ_questions(product):
        st = B.STATES[chat]
        if st["qkey"] == "tone":
            B.occ_tone(call("occ:tone:warm", chat))
        else:
            B.occ_answer(msg("Катя" if qkey == "name" else "живая деталь про черешню", chat))
    assert B.STATES[chat]["step"] == "occ_paywall", B.STATES[chat]
    return B.STATES[chat]


def pay_invoice(chat, order_id):
    m = msg("", chat)
    m.successful_payment = pytypes.SimpleNamespace(invoice_payload=order_id, telegram_payment_charge_id="ch" + order_id,
                                                   order_info=None)
    B.on_paid(m)


def buy_and_pay(chat, product="birthday"):
    to_preview(chat, product)
    B.occ_buy(call("occ:buy", chat))
    oid = invoices(chat)[-1]
    pay_invoice(chat, oid)
    return B.get_order(oid)


def make_partner(code="PARTN1", percent=30, discount=15):
    P.save_partner({"code": code, "name": "Блог", "contact": "@b", "percent": percent, "discount": discount,
                    "status": "active", "payout_note": "", "created_at": B.now_msk().isoformat()})
    return code


# ───────────────────────── 1. точки входа: метки и payload ─────────────────────────
ENTRY = [
    # payload, ожидаемая метка источника, ожидаемый открытый повод (None = меню/каталог)
    ("ad_tgchannel", "ad_tgchannel", "catalog"),
    ("ad_father", "ad_father", "catalog"),
    ("vkads_father", "vkads_father", "family"),
    ("vkads_birthday", "vkads_birthday", "birthday"),
    ("vkads_unknownzzz", "vkads_unknownzzz", None),
    ("ig_father", "ig_father", "family"),
    ("vk_love", "vk_love", "love"),
    ("yt_sorry", "yt_sorry", "sorry"),
    ("pin_ny", "pin_ny", "newyear"),
    ("tg_birthday", "tg_birthday", "birthday"),
    ("ig_nonsense", "ig_nonsense", None),
    ("occ", "occ", "catalog"),
    ("occ_family", "occ_family", "family"),
    ("w_love", "web", "love"),
    ("v_zzz", "vk", "catalog"),
    ("pdf", "pdf", None),
    ("direct_x", "direct_x", None),
]
chat = 1000
for payload, label, opened in ENTRY:
    chat += 1
    start(chat, payload)
    assert B.get_client(chat)["source"] == label, (payload, B.get_client(chat)["source"])
    sent = texts(chat)
    assert sent, payload
    if opened == "catalog":
        assert any("Письма с открыткой" in t for t in sent), (payload, sent)
    elif opened:
        assert any(OCC.PRODUCTS[opened]["title"] in t and "Что внутри" in t for t in sent), (payload, sent)
    else:
        assert not any("Что внутри" in t for t in sent), payload
print("OK: метки ad_/vkads_/ig_/vk_/yt_/pin_/tg_/occ/w_/v_/pdf")

# пустой, неизвестный и очень длинный payload — меню, без падения
for i, payload in enumerate([None, "", "   ", "ref_", "ref_abc", "ref_99999999999999999999", "g_", "g_nonexistent",
                             "grp_", "grp_nonexistent", "p_", "p_NOSUCH", "pinvite_", "pinvite_bad",
                             "x" * 64, "ig_" + "я" * 200, "vkads_", "ad_", "papa", "<b>html</b>"]):
    chat += 1
    start(chat, payload)
    assert texts(chat) or texts(chat, "send_photo"), repr(payload)
    src = (B.get_client(chat) or {}).get("source", "")
    assert len(src) <= 40, (payload, src)
print("OK: пустой/неизвестный/длинный payload")

# ───────────────────────── 2. сквозной путь для каждой метки ─────────────────────────
for payload, label, product in [("ad_tgchannel", "ad_tgchannel", "birthday"), ("vkads_father", "vkads_father", "family"),
                                ("ig_love", "ig_love", "love"), ("vk_sorry", "vk_sorry", "sorry"),
                                ("yt_birthday", "yt_birthday", "birthday"), ("pin_ny", "pin_ny", "newyear"),
                                ("tg_father", "tg_father", "family")]:
    chat += 1
    start(chat, payload)
    order = buy_and_pay(chat, product)
    assert order["status"] == "done" and order["source"] == label, (payload, order)
    assert order["price_rub"] == OCC.PRODUCTS[product]["price"] and order.get("gift_code"), order
    assert any("Оплата прошла" in t for t in texts(chat)) and any("Как подарить" in t for t in texts(chat))
    assert B.STATES.get(chat) is None
rows, total = S.report(7)
assert {"ad_tgchannel", "vkads_father", "ig_love"} <= {r["source"] for r in rows}
assert total["paid"] >= 7 and any(r["source"] == "ad_tgchannel" and r["paid"] == 1 for r in rows)
print("OK: старт → превью → оплата → выдача по меткам")

# ───────────────────────── 3. повторный клиент, first touch ─────────────────────────
chat += 1
ret = chat
start(ret, "ad_tgchannel")
o1 = buy_and_pay(ret, "birthday")
prof = B.get_client(ret)                        # клиент вернулся через пару дней
prof["created_at"] = (B.now_msk() - B.timedelta(days=2)).isoformat()
B.write_json(B.client_path(ret), prof)
start(ret, "vkads_father")                      # другая метка
start(ret, "p_NOSUCH")
assert B.get_client(ret)["source"] == "ad_tgchannel", "first touch затёрт"
assert any("С возвращением" in t for t in texts(ret))
o2 = buy_and_pay(ret, "family")
assert o2["source"] == "ad_tgchannel" and o2["price_rub"] == 199
assert len(B.get_client(ret)["orders"]) == 2
# повторный /start сразу после первого (в пределах минуты) тоже не меняет метку
chat += 1
start(chat, "ig_father")
start(chat, "vk_love")
assert B.get_client(chat)["source"] == "ig_father"
print("OK: повторный клиент, first touch")

# ───────────────────────── 4. двойное «Оплатить» ─────────────────────────
chat += 1
dbl = chat
start(dbl, "ad_tgchannel")
to_preview(dbl, "birthday")
B.occ_buy(call("occ:buy", dbl))
B.occ_buy(call("occ:buy", dbl))                 # двойной тап
pend = [o for o in orders_of(dbl) if o["status"] == "pending"]
assert len(pend) == 1, f"двойное «Оплатить» создало заказов: {len(pend)}"
pay_invoice(dbl, pend[0]["order_id"])
assert [o["status"] for o in orders_of(dbl)] == ["done"]
# платёж за уже выданный заказ повторно не выдаёт письмо второй раз
n_before = len(SENT)
pay_invoice(dbl, pend[0]["order_id"])
assert B.get_client(dbl)["total_spent_rub"] == 199, B.get_client(dbl)["total_spent_rub"]
print("OK: двойное «Оплатить»")

# ───────────────────────── 5. оплата после перезапуска бота (ЮKassa) ─────────────────────────
B.YOOKASSA_PROVIDER_TOKEN = ""
B.YOOKASSA_SHOP_ID, B.YOOKASSA_SECRET_KEY, B.YOOKASSA_RECEIPT = "shop", "key", False
PAYMENTS = {}


def fake_yk(method, url, payload=None, idem=None):
    if method == "POST":
        pid = f"pay-{len(PAYMENTS) + 1}"
        PAYMENTS[pid] = {"id": pid, "status": "pending", "paid": False, "amount": payload["amount"],
                         "confirmation": {"confirmation_url": f"https://yk/{pid}"}}
        return PAYMENTS[pid]
    return PAYMENTS[url.rsplit("/", 1)[1]]


B.yk_request = fake_yk
chat += 1
rst = chat
start(rst, "ad_tgchannel")
to_preview(rst, "family")
B.occ_buy(call("occ:buy", rst))
oid = orders_of(rst)[0]["order_id"]
pid = B.get_order(oid)["yk_payment_id"]
B.STATES.clear()                                # «перезапуск»: диалоги в памяти потеряны
PAYMENTS[pid].update(status="succeeded", paid=True)
assert B.yk_check_order(oid) == "succeeded"     # то, что делает фоновый опрос после старта
o = B.get_order(oid)
assert o["status"] == "done" and o.get("gift_code") and any("Как подарить" in t for t in texts(rst))
n = len(SENT)
assert B.yk_check_order(oid) == "succeeded" and len(SENT) == n, "повторная проверка выдала письмо снова"
# платёж на неверную сумму не выдаёт заказ
chat += 1
bad = chat
start(bad, "ad_x")
to_preview(bad, "family")
B.occ_buy(call("occ:buy", bad))
oid2 = orders_of(bad)[0]["order_id"]
PAYMENTS[B.get_order(oid2)["yk_payment_id"]].update(status="succeeded", paid=True, amount={"value": "1.00"})
B.yk_check_order(oid2)
assert B.get_order(oid2)["status"] == "pending"
print("OK: оплата после перезапуска (ЮKassa)")

# ───────────────────────── 6. PDF «Разговор с папой» 149 ₽ ─────────────────────────
chat += 1
pdf = chat
start(pdf, "papa_pdf")
start(pdf, "papa_pdf")                          # повторный переход по рекламной ссылке
pend = [o for o in orders_of(pdf) if o["status"] == "pending"]
assert len(pend) == 1 and pend[0]["price_rub"] == 149, [o["order_id"] for o in pend]
PAYMENTS[pend[0]["yk_payment_id"]].update(status="succeeded", paid=True)
B.yk_check_order(pend[0]["order_id"])
assert B.get_order(pend[0]["order_id"])["status"] == "done"
assert any(s[0] == "send_document" and s[1][0] == pdf for s in SENT), "PDF не отправлен"
assert B.get_client(pdf)["source"] == "papa_pdf"
print("OK: PDF 149")

# ───────────────────────── 7. промокоды: партнёрский код только на первый заказ ─────────────────────────
code = make_partner()
chat += 1
pr = chat
start(pr, f"p_{code}")
assert B.get_client(pr)["source"] == f"p_{code}" and B.get_client(pr)["partner"]["code"] == code
q = P.quote(pr, 199)
assert q["discount"] == 30 and q["payable"] == 169
B.YOOKASSA_SHOP_ID, B.YOOKASSA_SECRET_KEY, B.YOOKASSA_PROVIDER_TOKEN = "", "", "x"
o_first = buy_and_pay(pr, "birthday")
assert o_first["price_rub"] == 169 and o_first["partner_code"] == code
# второй заказ: скидки партнёра нет, а ввод кода партнёра как промокода — тоже не даёт скидку
q2 = P.quote(pr, 199)
assert q2["discount"] == 0, q2
st = to_preview(pr, "family")
B.STATES[pr]["step"] = "promo_enter"
B.STATES[pr]["promo_back"] = "occ_paywall"
P.promo_text(msg(code, pr))
q3 = P.quote(pr, 199, B.STATES[pr].get("promo"))
assert q3["discount"] == 0, f"код партнёра дал скидку на второй заказ: {q3}"
B.occ_buy(call("occ:buy", pr))
assert B.get_order(invoices(pr)[-1])["price_rub"] == 199
# обычный промокод второй раз
P.save_promo({"code": "SALE20", "kind": "percent", "value": 20, "expires": None, "limit": None,
              "once_per_user": True, "partner": None, "active": True, "used": []})
chat += 1
pm = chat
start(pm, "ad_tgchannel")
to_preview(pm, "birthday")
B.STATES[pm]["step"] = "promo_enter"
B.STATES[pm]["promo_back"] = "occ_paywall"
P.promo_text(msg("sale20", pm))
assert B.STATES[pm]["promo"] == "SALE20"
B.occ_buy(call("occ:buy", pm))
oid = invoices(pm)[-1]
assert B.get_order(oid)["price_rub"] == 159
pay_invoice(pm, oid)
assert len(P.get_promo("SALE20")["used"]) == 1
to_preview(pm, "birthday")
B.STATES[pm]["step"] = "promo_enter"
B.STATES[pm]["promo_back"] = "occ_paywall"
P.promo_text(msg("SALE20", pm))
assert "promo" not in B.STATES[pm] and "уже использовал" in texts(pm)[-1], texts(pm)[-1]
B.occ_buy(call("occ:buy", pm))
assert B.get_order(invoices(pm)[-1])["price_rub"] == 199
print("OK: промокоды")

# ───────────────────────── 8. «Подари подруге» (ref_) ─────────────────────────
chat += 1
inviter = chat
start(inviter, "ad_tgchannel")
chat += 1
friend = chat
start(friend, f"ref_{inviter}")
assert B.get_client(friend)["source"] == "ref" and B.get_client(friend)["referred_by"] == inviter
assert P.quote(friend, 199)["discount"] == 20
of = buy_and_pay(friend, "birthday")
assert of["price_rub"] == 179
assert B.get_client(inviter)["bonus_rub"] == 50
assert P.quote(friend, 199)["discount"] == 0, "скидка друга действует только на первый заказ"
og = buy_and_pay(friend, "birthday")
assert B.get_client(inviter)["bonus_rub"] == 50, "бонус пригласившему начислен дважды"
assert P.quote(inviter, 199)["balance_used"] == 50
print("OK: подари подруге")

# ───────────────────────── 9. групповое письмо 399 ₽ ─────────────────────────
chat += 1
org = chat
start(org, "ad_tgchannel")
B.STATES[org] = {}
g = {"code": "GRPTEST1", "organizer": org, "occasion": "birthday", "name": "Мама", "who": "мама", "sign": "Все мы",
     "status": "open", "participants": [{"chat_id": 9001, "name": "Аня", "answers": ["любит чай", "обнимает"]}],
     "created_at": B.now_msk().isoformat()}
G.save_group(g)
assert G.GROUP_PRICE == 399
B.STATES[org] = {"step": "occ_paywall", "product": "birthday", "price": 399, "group": "GRPTEST1", "qa": [("q", "a")],
                 "data": {}, "letter": "Общее письмо. " * 40, "card_title": "Маме", "card_line": "Мы с тобой",
                 "sign": "Все мы", "tone": "warm", "name": "Мама", "card_ref": None, "card_seen": [], "chat_id": org}
B.occ_buy(call("occ:buy", org))
B.occ_buy(call("occ:buy", org))
pend = [o for o in orders_of(org) if o["status"] == "pending"]
assert len(pend) == 1 and pend[0]["price_rub"] == 399 and pend[0]["group_id"] == "GRPTEST1", pend
pay_invoice(org, pend[0]["order_id"])
assert B.get_order(pend[0]["order_id"])["status"] == "done"
print("OK: групповое письмо 399")

# ───────────────────────── 10. сбой ИИ не продаёт текст ошибки ─────────────────────────
chat += 1
aif = chat
start(aif, "ad_tgchannel")
AI_FAIL[0] = True
B.STATES[aif] = {"step": "choosing_pain"}
B.order_pain_chosen(call("pain:breakup", aif))
for _ in range(6):
    if B.STATES.get(aif, {}).get("step") == "diag_q":
        B.diag_receive_answer(msg("мне было очень тяжело после расставания", aif))
assert not any("[ai]" in t for t in texts(aif)), "клиенту показан текст сбоя ИИ"
assert not any(s[0] == "send_message" and s[1][0] == aif and s[2].get("reply_markup")
               and "letter:buy" in str(s[2]["reply_markup"].to_json()) for s in SENT), "продаётся письмо при сбое ИИ"
AI_FAIL[0] = False
# превью повода при сбое ИИ
chat += 1
aif2 = chat
start(aif2)
B.occ_go(call("occ:go:birthday", aif2))
AI_FAIL[0] = True
for qkey, _ in B.occ_questions("birthday"):
    if B.STATES[aif2]["qkey"] == "tone":
        B.occ_tone(call("occ:tone:warm", aif2))
    else:
        B.occ_answer(msg("Катя", aif2))
AI_FAIL[0] = False
assert B.STATES.get(aif2) is None and not orders_of(aif2)
print("OK: сбой ИИ")

# ───────────────────────── 11. очень длинный текст ─────────────────────────
chat += 1
lng = chat
start(lng)
B.occ_go(call("occ:go:family", lng))
B.occ_answer(msg("я" * 1501, lng))
assert B.STATES[lng]["idx"] == 0 and "Слишком длинно" in texts(lng)[-1]
B.occ_answer(msg("   ", lng))
assert B.STATES[lng]["idx"] == 0
B.occ_answer(msg("<b>&Катя", lng))             # HTML-символы в ответе
assert B.STATES[lng]["idx"] == 1
LONG_LETTER[0] = "Дорогой папа, " + "слово & <i> " * 300
B.STATES.pop(lng)
st = to_preview(lng, "family")
previews = [t for t in texts(lng) if "Вторая половина" in t]
assert previews and all(len(t) <= 4096 for t in previews), [len(t) for t in previews]
B.occ_buy(call("occ:buy", lng))
pay_invoice(lng, invoices(lng)[-1])
assert all(len(t) <= 4096 for t in texts(lng)), [len(t) for t in texts(lng) if len(t) > 4096]
LONG_LETTER[0] = None
print("OK: длинный текст")

# ───────────────────────── 12. тестовые аккаунты и /refund ─────────────────────────
chat += 1
tst = chat
start(tst, "ad_tgchannel")
B.bot.send_message(1, "x")
B.cmd_test_users(msg(f"/test_add @u{tst}", 1))
assert tst in B.TEST_USERS
for product in ("papa_pdf", "pack3", "family", "group"):
    if product == "papa_pdf":
        start(tst, "papa_pdf")
        oid = [o for o in orders_of(tst) if o["status"] == "pending"][-1]["order_id"]
    elif product == "pack3":
        B.occ_pack(call("occ:pack", tst))
        oid = [o for o in orders_of(tst) if o["status"] == "pending"][-1]["order_id"]
    elif product == "family":
        to_preview(tst, "family")
        B.occ_buy(call("occ:buy", tst))
        oid = [o for o in orders_of(tst) if o["status"] == "pending"][-1]["order_id"]
    else:
        continue
    o = B.get_order(oid)
    assert o["is_test"] and o["price_rub"] == 0, o
    B.test_pay(call(f"test:pay:{oid}", tst))
    assert B.get_order(oid)["status"] == "done", product
assert not invoices(tst), "тестовому аккаунту выставлен настоящий счёт"
assert B.get_client(tst)["credits"] == 3
assert all(r["source"] != "x" for r in S.report(7)[0])
assert S.report(7)[1]["paid"] == len([o for o in B.all_orders() if o["status"] == "done" and o["price_rub"] > 0])
B.cmd_test_users(msg(f"/test_off @u{tst}", 1))
assert tst not in B.TEST_USERS
# возврат
done = [o for o in B.all_orders() if o["status"] == "done" and o["price_rub"] > 0][0]
P.cmd_refund(msg(f"/refund {done['order_id']}", 1))
assert B.get_order(done["order_id"])["refunded"]
P.cmd_refund(msg(f"/refund {done['order_id']}", 1))
assert "Уже отмечен" in texts(1)[-1]
P.cmd_refund(msg("/refund", 1))
P.cmd_refund(msg("/refund NOPE", 1))
assert "не найден" in texts(1)[-1]
print("OK: тестовые аккаунты, /refund")

# ───────────────────────── 13. повторное «Оплатить» не теряет платёж ЮKassa ─────────────────────────
B.YOOKASSA_PROVIDER_TOKEN, B.YOOKASSA_SHOP_ID, B.YOOKASSA_SECRET_KEY = "", "shop", "key"
chat += 1
rep = chat
start(rep, "ad_tgchannel")
to_preview(rep, "family")
B.occ_buy(call("occ:buy", rep))
oid = orders_of(rep)[0]["order_id"]
posts_before = len(PAYMENTS)
first_pid = B.get_order(oid)["yk_payment_id"]
B.cabinet_pay(call(f"cab:pay:{oid}", rep))      # из напоминания / кабинета
B.cabinet_pay(call(f"cab:pay:{oid}", rep))
assert len(PAYMENTS) == posts_before, "повторное «Оплатить» создало ещё один платёж ЮKassa"
assert B.get_order(oid)["yk_payment_id"] == first_pid
PAYMENTS[first_pid].update(status="succeeded", paid=True)   # клиент оплатил по первой ссылке
B.yk_check_order(oid)
assert B.get_order(oid)["status"] == "done"
# оплатил уже после повторного «Оплатить» — кнопка сама видит успешный платёж и выдаёт заказ
chat += 1
rep2 = chat
start(rep2, "ad_tgchannel")
to_preview(rep2, "family")
B.occ_buy(call("occ:buy", rep2))
oid = orders_of(rep2)[0]["order_id"]
PAYMENTS[B.get_order(oid)["yk_payment_id"]].update(status="succeeded", paid=True)
B.cabinet_pay(call(f"cab:pay:{oid}", rep2))
assert B.get_order(oid)["status"] == "done" and not [o for o in orders_of(rep2) if o["status"] == "pending"]

# ───────────────────────── 14. оплата заказа, отменённого после выставления счёта ─────────────────────────
chat += 1
can = chat
start(can, "ad_tgchannel")
to_preview(can, "family")
B.occ_buy(call("occ:buy", can))
oid = orders_of(can)[0]["order_id"]
B.cabinet_cancel(call(f"cab:cancel:{oid}", can))
assert B.get_order(oid)["status"] == "cancelled"
PAYMENTS[B.get_order(oid)["yk_payment_id"]].update(status="succeeded", paid=True)   # страница оплаты была открыта


class _Stop(Exception):
    pass


def _sleep(_):
    raise _Stop


real_sleep, B.time.sleep = B.time.sleep, _sleep
try:
    B.yk_poll_loop()                            # один проход фонового опроса
except _Stop:
    pass
B.time.sleep = real_sleep
assert B.get_order(oid)["status"] == "done" and any("Как подарить" in t for t in texts(can)), \
    "клиент оплатил, а письмо не выдано"
print("OK: повторное «Оплатить» и оплата отменённого заказа (ЮKassa)")

print("ALL OK: funnel 13.10")
