"""Календарь дат близких: согласие, напоминание за 3 дня, лимиты, отписка, заказ из напоминания.
Запуск: python tests/test_calendar.py"""
import os
import sys
import tempfile
import types as pytypes
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["APP_ONLY"] = "0"   # этот тест проверяет старое меню; режим приложения — в test_app_only.py

import bot_best as B  # noqa: E402
import calendar_reminders as C  # noqa: E402

# ── чистая логика дат ──
assert C.parse_date("14.03") == (14, 3) and C.parse_date("14.03.1990") == (14, 3)
assert C.parse_date("14 марта") == (14, 3) and C.parse_date(" 5/12 ") == (5, 12)
assert C.parse_date("31.02") is None and C.parse_date("абв") is None and C.parse_date("32.01") is None
assert C.next_occurrence(29, 2, date(2026, 1, 1)) == date(2026, 2, 28), "29.02 в невисокосный год — 28.02"
assert C.next_occurrence(10, 10, date(2026, 10, 10)) == date(2026, 10, 10)
assert C.next_occurrence(9, 10, date(2026, 10, 10)) == date(2027, 10, 9)
assert C.HOLIDAYS["mother"]["date"](2026) == date(2026, 11, 29), "День матери — последнее воскресенье ноября"
assert C.HOLIDAYS["father"]["date"](2026) == date(2026, 10, 18), "День отца — третье воскресенье октября"

SENT = []
MSG_ID = [100]


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        MSG_ID[0] += 1
        return pytypes.SimpleNamespace(message_id=MSG_ID[0], chat=pytypes.SimpleNamespace(id=500),
                                       photo=[pytypes.SimpleNamespace(file_id="F")])
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "edit_message_media", "send_media_group",
          "send_document"]:
    setattr(B.bot, m, rec(m))


def user(chat):
    return pytypes.SimpleNamespace(id=chat, first_name="Т", last_name="", username="u%d" % chat)


def msg(text, chat):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=user(chat))


def call(data, chat, mid=1):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=user(chat),
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def texts(chat):
    return [s[1][1] for s in SENT if s[0] == "send_message" and s[1][0] == chat]


def cbs(markup):
    return [b.callback_data for row in markup.keyboard for b in row]


# ── после заказа предлагаем напомнить (по согласию) ──
B.cmd_start(msg("/start", 500))
order = {"order_id": "ALI-1", "chat_id": 500, "product": "birthday", "gift_for": "Катя", "status": "done",
         "price_rub": 199, "created_at": B.now_msk().isoformat()}
B.save_order(order)
SENT.clear()
C.offer_after_order(500, order)
offer = [s for s in SENT if s[0] == "send_message"][-1]
assert "Катя" in offer[1][1] and cbs(offer[2]["reply_markup"]) == ["cal:add:ALI-1", "cal:menu", "cal:no"]
profile, cal = C.get_cal(500)
assert cal["dates"] == [] and cal["subs"] == [], "без согласия ничего не сохраняется"
C.add_cb(call("cal:add:ALI-1", 500))
assert B.STATES[500]["step"] == "cal_date"
C.text_cb(msg("не знаю", 500))
assert B.STATES[500]["step"] == "cal_date" and "Не поняла" in texts(500)[-1]
C.text_cb(msg("14 марта", 500))
_, cal = C.get_cal(500)
assert cal["dates"][0]["name"] == "Катя" and (cal["dates"][0]["day"], cal["dates"][0]["month"]) == (14, 3)
assert 500 not in B.STATES
C.offer_after_order(500, order)
assert len([1 for s in SENT if s[0] == "send_message" and "напомню за 3 дня" in s[1][1]]) == 1, "повторно не спрашиваем"

# ── напоминание: окно 3 дня, тихие часы, один раз ──
def at(d, hour=12):
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=B.now_msk().tzinfo)


SENT.clear()
assert C.run_once(at(date(2026, 3, 10, ))) == 0, "за 4 дня рано"
assert C.run_once(at(date(2026, 3, 11), hour=9)) == 0, "тихие часы"
assert C.run_once(at(date(2026, 3, 11), hour=21)) == 0, "тихие часы"
assert C.run_once(at(date(2026, 3, 11))) == 1
rem = [s for s in SENT if s[0] == "send_message" and s[1][0] == 500][-1]
assert "Катя" in rem[1][1] and "3 дн" in rem[1][1]
assert cbs(rem[2]["reply_markup"]) == [f"cal:write:{cal['dates'][0]['id']}", f"cal:off:d:{cal['dates'][0]['id']}"]
assert C.run_once(at(date(2026, 3, 12))) == 0, "то же событие второй раз не шлём"
assert C.report(3650)["reminded"] == 1

# ── из напоминания — сразу сценарий с именем ──
SENT.clear()
C.write_cb(call(f"cal:write:{cal['dates'][0]['id']}", 500))
st = B.STATES[500]
assert st["step"] == "occ_q" and st["product"] == "birthday" and st["idx"] == 1 and st["data"]["name"] == "Катя"
assert "Катя" in texts(500)[0]
B.occ_answer(msg("подруга", 500))
B.occ_answer(msg("30", 500))
B.occ_answer(msg("черешня на крыше", 500))
B.occ_tone(call("occ:tone:warm", 500))


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(*a, **k):
        return {"letter": "Катя, привет. " * 30, "card_title": "С днём рождения, Катя!", "card_line": "Ты лучшая."}


B.AI = FakeAI
B.occ_answer(msg("Серёжа", 500))
B.occ_buy(call("occ:buy", 500))
o = [x for x in B.all_orders() if x.get("from_reminder")][0]
B.fulfill_order(500, o["order_id"], "ch", "a@b.ru")
assert C.report(3650)["orders"] == 1 and C.report(3650)["revenue"] == 199

# ── отписка одной кнопкой ──
SENT.clear()
C.off_cb(call(f"cal:off:d:{cal['dates'][0]['id']}", 500))
_, cal = C.get_cal(500)
assert cal["dates"] == []

# ── праздники по подписке; не чаще раза в неделю ──
C.toggle_cb(call("cal:tog:mother", 500))
C.toggle_cb(call("cal:tog:father", 500))
_, cal = C.get_cal(500)
assert set(cal["subs"]) == {"mother", "father"}
cal["last_at"] = None
profile, _ = C.get_cal(500)
B.write_json(B.client_path(500), profile)
SENT.clear()
assert C.run_once(at(date(2026, 10, 15))) == 1                      # до Дня отца 18.10 — 3 дня
assert "День отца" in texts(500)[-1]
assert C.run_once(at(date(2026, 10, 16))) == 0, "не чаще раза в 7 дней"
C.off_cb(call("cal:off:h:father", 500))
_, cal = C.get_cal(500)
assert cal["subs"] == ["mother"]
assert C.run_once(at(date(2026, 11, 26))) == 1 and "День матери" in texts(500)[-1]
SENT.clear()
C.holiday_cb(call("cal:h:mother", 500))
assert any("Пишем" in str(s) or "письмо" in str(s).lower() for s in SENT)

# ── заблокировал бота — больше не пишем ──
def boom(*a, **k):
    raise Exception("Forbidden: bot was blocked by the user (403)")


profile, cal = C.get_cal(500)
cal["last_at"] = None
cal["sent"] = {}
B.write_json(B.client_path(500), profile)
B.bot.send_message = boom
assert C.run_once(at(date(2026, 11, 27))) == 0
assert C.get_cal(500)[1]["inactive"] is True

# ── тестовый аккаунт не попадает в метрики ──
B.TEST_USERS.add(777)
n = C.report(3650)["date_added"]
B.bot.send_message = rec("send_message")
B.cmd_start(msg("/start", 777))
C.add_date(777, "Тест", "birthday", 1, 1)
assert C.report(3650)["date_added"] == n
print("OK: calendar flow")
