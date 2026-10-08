"""Сквозной тест «Писем с открыткой» без сети: подменяем методы бота и ИИ.
Запуск: python tests/test_occasions_flow.py"""
import os
import sys
import tempfile
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "test"
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""

import bot_best as B  # noqa: E402

SENT = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=len(SENT))
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "send_media_group", "send_document"]:
    setattr(B.bot, m, rec(m))


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title):
        assert qa and sign
        return {"letter": "Катя, помнишь, как мы ели черешню на крыше? " * 12 + f"\n{sign}",
                "card_title": title, "card_line": "Ты смеёшься громче всех — и мир теплее."}


B.AI = FakeAI
U = pytypes.SimpleNamespace(id=500, first_name="Тест", last_name="", username="t")


def msg(text, chat=500):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=pytypes.SimpleNamespace(id=chat, first_name="Тест", last_name="",
                                                                     username="t"))


def call(data, chat=500):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=msg("", chat).from_user,
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=1))


def last(name):
    return [s for s in SENT if s[0] == name][-1]


# каталог и продукт
B.cmd_start(msg("/start occ"))
assert "Письма с открыткой" in last("send_message")[1][1]
B.occ_go(call("occ:go:birthday"))
answers = ["Катя", "подруга", "30", "смеётся громче всех, черешня на крыше"]
for a in answers:
    B.occ_answer(msg(a))
B.occ_tone(call("occ:tone:warm"))
B.occ_answer(msg("Серёжа"))
assert B.STATES[500]["step"] == "occ_paywall", B.STATES[500]
assert any(s[0] == "send_photo" for s in SENT), "нет превью открытки"

# оплата
B.occ_buy(call("occ:buy"))
inv = last("send_invoice")[2]
order_id = inv["invoice_payload"]
assert inv["prices"][0].amount == 19900
pay = pytypes.SimpleNamespace(invoice_payload=order_id, telegram_payment_charge_id="x",
                              order_info=pytypes.SimpleNamespace(email="a@b.c"))
m = msg("")
m.successful_payment = pay
B.on_paid(m)
order = B.get_order(order_id)
assert order["status"] == "done" and order.get("gift_code"), order
def _urls(s):
    mk = s[2].get("reply_markup")
    return [getattr(b, "url", "") or "" for row in getattr(mk, "keyboard", []) for b in row]


assert any(s[0] == "send_message" and s[1][0] == 500 and any("t.me/share/url" in u and "%2Fe%2F" + order["gift_code"] in u
                                                          for u in _urls(s)) for s in SENT), "кнопка «Отправить» ведёт на конверт"

# получатель вскрывает конверт
B.cmd_start(msg("/start g_" + order["gift_code"], chat=777))
assert any(s[0] == "send_message" and s[1][0] == 777 and "письмо от" in s[1][1] for s in SENT)
B.occ_envelope_open(call("env:open:" + order["gift_code"], chat=777))
order = B.get_order(order_id)
assert order.get("opened_at"), "не отмечено открытие"
assert any(s[0] == "send_message" and s[1][0] == 500 and "открыл" in s[1][1] for s in SENT)

# набор: покупка и списание
B.occ_pack(call("occ:pack"))
pack_id = last("send_invoice")[2]["invoice_payload"]
m2 = msg("")
m2.successful_payment = pytypes.SimpleNamespace(invoice_payload=pack_id, telegram_payment_charge_id="y",
                                                order_info=None)
B.on_paid(m2)
assert B.get_client(500)["credits"] == 3
B.occ_go(call("occ:go:sorry"))
for a in ["Лёша", "брат", "поругались из-за машины", "помириться"]:
    B.occ_answer(msg(a))
B.occ_answer(msg("Саша"))
assert B.STATES[500]["step"] == "occ_paywall"
B.occ_credit(call("occ:credit"))
assert B.get_client(500)["credits"] == 2

# кабинет и примеры не падают
text, kb = B.cabinet_view(500)
assert "наборе: 2" in text
B.show_examples(msg("📖 Примеры"))
print("OK: all occasion flow checks passed,", len(SENT), "calls")

# агрегаты для отчётов: без личных данных, ключ обязателен
st = B.bot_stats()
assert st["paid_total"] == 3 and st["revenue_total"] == 199 + 449 and st["gifts_sent"] == 2 and st["gifts_opened"] == 1, st
assert B.get_client(500)["source"] == "occ" and st["sources"].get("occ") == 1, st["sources"]
cl = B.app.test_client()
assert cl.get("/stats/wrong").status_code == 404
r = cl.get("/stats/" + B.STATS_KEY)
assert r.status_code == 200 and r.get_json()["clients_total"] >= 2
assert "Катя" not in r.get_data(as_text=True)
print("OK: stats endpoint")
