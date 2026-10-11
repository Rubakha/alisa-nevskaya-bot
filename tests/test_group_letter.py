"""Групповое письмо «от всех нас»: создание, участники, сборка, превью, оплата, inline-приглашение.
Запуск: python tests/test_group_letter.py"""
import os
import sys
import tempfile
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""

import bot_best as B  # noqa: E402
import group_letters as G  # noqa: E402

SENT, ANSWERS = [], []
MSG_ID = [100]


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        MSG_ID[0] += 1
        return pytypes.SimpleNamespace(message_id=MSG_ID[0], chat=pytypes.SimpleNamespace(id=500),
                                       photo=[pytypes.SimpleNamespace(file_id="F")])
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "edit_message_media", "edit_message_caption",
          "send_media_group", "send_document", "answer_inline_query"]:
    setattr(B.bot, m, rec(m))

GEN = []


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_group_letter(brief, name, who, parts, sign, title, previous="", wish=""):
        GEN.append({"name": name, "parts": parts, "previous": previous, "wish": wish})
        body = "\n".join(f"{p['memory']}\n— {p['sign']}" for p in parts)
        return {"letter": f"Мама, это от всех нас.\n{body}\nПервая половина. " * 3 + "СЕКРЕТНЫЙ_ФИНАЛ " * 8 + sign,
                "card_title": title, "card_line": "Мы тебя любим."}


B.AI = FakeAI


def user(chat, first="Аня"):
    return pytypes.SimpleNamespace(id=chat, first_name=first, last_name="", username="u%d" % chat)


def msg(text, chat):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=user(chat))


def call(data, chat, mid=1):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=user(chat),
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def last_text(chat=None):
    for s in reversed(SENT):
        if s[0] == "send_message" and (chat is None or s[1][0] == chat):
            return s[1][1]
    return ""


def cbs(markup):
    return [getattr(b, "callback_data", None) or getattr(b, "url", None) or "inline"
            for row in markup.keyboard for b in row]


# ── вход с сайта ?start=w_group: сразу выбор повода группового письма ──
B.cmd_start(msg("/start w_group", 499))
assert "Для кого письмо" in last_text(499) and "grp:occ:family" in cbs(SENT[-1][2]["reply_markup"])
B.cmd_start(msg("/start w_family", 498))                     # обычные поводы не сломаны
assert "Для кого письмо" not in last_text(498)

# ── организатор создаёт группу ──
B.occ_open_catalog(500)
assert "grp:new" in cbs(SENT[-1][2]["reply_markup"])
G.occasion_cb(call("grp:occ:family", 500))
assert B.STATES[500]["step"] == "grp_name"
G.organizer_text(msg("мама", 500))
G.organizer_text(msg("наша мама", 500))
G.organizer_text(msg("Твои дети", 500))
g = G.all_groups()[0]
code = g["code"]
assert g["organizer"] == 500 and g["occasion"] == "family" and g["name"] == "мама" and g["status"] == "open"
assert "сюрприз" in last_text(500) and "Написали: <b>0</b>" in last_text(500)

# ── участники по ссылке ──
n_before = len(B.client_files())
B.cmd_start(msg(f"/start grp_{code}", 601))
assert B.STATES[601]["step"] == "grp_p" and "мама" in last_text(601)
G.participant_text(msg("коротко", 601))                     # слишком коротко
assert B.STATES[601]["idx"] == 0
G.participant_text(msg("Как ты пекла пироги по воскресеньям и пела", 601))
G.participant_text(msg("Спасибо, что всегда ждала нас дома", 601))
assert B.STATES[601]["qkey"] == "sign"
G.sign_default_cb(call("grp:sign:default", 601))
assert 601 not in B.STATES
assert "Аня" in last_text(500) and "Уже: 1" in last_text(500), "организатору пришло уведомление"
assert B.get_client(601)["source"] == "group" and len(B.client_files()) == n_before + 1

B.cmd_start(msg(f"/start grp_{code}", 602))
G.participant_text(msg("Как мы вместе варили варенье на даче", 602))
G.participant_text(msg("Береги себя, мы рядом всегда", 602))
G.participant_text(msg("Паша", 602))
assert len(G.get_group(code)["participants"]) == 2

# повторное участие перезаписывает свою часть, а не дублирует
B.cmd_start(msg(f"/start grp_{code}", 602))
G.participant_text(msg("Новая версия воспоминания про варенье на даче", 602))
G.participant_text(msg("Береги себя, мы рядом всегда и везде", 602))
G.participant_text(msg("Паша", 602))
parts = G.get_group(code)["participants"]
assert len(parts) == 2 and any("Новая версия" in p["memory"] for p in parts)

# ── сборка: только организатор, нужно >= 2 частей ──
SENT.clear()
G.build_cb(call(f"grp:build:{code}", 999))  # посторонний
assert not GEN and B.STATES.get(999) is None
G.build_cb(call(f"grp:build:{code}", 500))
assert len(GEN) == 1 and len(GEN[0]["parts"]) == 2
st = B.STATES[500]
assert st["step"] == "occ_paywall" and st["group"] == code and st["price"] == 399
text_msg = [s for s in SENT if s[0] == "send_message"][-1]
assert "СЕКРЕТНЫЙ_ФИНАЛ" not in text_msg[1][1] and "▒" in text_msg[1][1]
assert "occ:buy" in cbs(text_msg[2]["reply_markup"])
assert "399" in str([b.text for row in text_msg[2]["reply_markup"].keyboard for b in row])

# перегенерация текста идёт через тот же голос участников
B.occ_retext(call("occ:retext", 500))
assert len(GEN) == 2 and GEN[1]["previous"].startswith("Мама, это от всех нас")

# ── оплата организатором ──
B.occ_buy(call("occ:buy", 500))
order = [o for o in B.all_orders() if o.get("group_id") == code][0]
assert order["price_rub"] == 399 and G.get_group(code)["status"] == "ordered"
# позднее участие закрыто
B.cmd_start(msg(f"/start grp_{code}", 603))
assert 603 not in B.STATES and "уже собрано" in last_text(603)
# отмена неоплаченного заказа снова открывает группу
B.cabinet_cancel(call(f"cab:cancel:{order['order_id']}", 500))
assert G.get_group(code)["status"] == "open"
B.STATES[500] = st
B.occ_buy(call("occ:buy", 500))
order = [o for o in B.all_orders() if o.get("group_id") == code and o["status"] == "pending"][0]
B.fulfill_order(500, order["order_id"], "ch-g", "a@b.ru")
assert B.get_order(order["order_id"])["status"] == "done"
assert G.report()["paid"] == 1 and G.report()["written"] == 2 and G.report()["built"] == 1

# ── inline-приглашение: только организатор ──
ANSWERS.clear()
B.bot.answer_inline_query = lambda qid, results, **k: ANSWERS.append(results)
G.inline_handler(pytypes.SimpleNamespace(id="q1", query=f"grp_{code}", from_user=user(500)))
assert ANSWERS == [[]], "после оплаты группа закрыта — приглашение не выдаётся"
g = G.get_group(code)
g["status"] = "open"
G.save_group(g)
G.inline_handler(pytypes.SimpleNamespace(id="q2", query=f"grp_{code}", from_user=user(500)))
G.inline_handler(pytypes.SimpleNamespace(id="q3", query=f"grpr_{code}", from_user=user(500)))
G.inline_handler(pytypes.SimpleNamespace(id="q4", query=f"grp_{code}", from_user=user(777)))
assert len(ANSWERS[1]) == 1 and len(ANSWERS[2]) == 1 and ANSWERS[3] == []
inv = ANSWERS[1][0].input_message_content.message_text
assert "сюрприз" in inv and ANSWERS[1][0].reply_markup.keyboard[0][0].url.endswith(f"start=grp_{code}")
assert "Напоминаю" in ANSWERS[2][0].input_message_content.message_text
print("OK: group letter flow")
