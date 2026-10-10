"""Метки источников в /start (first touch), источник в заказе, отчёт /sources.
Запуск: python tests/test_sources.py"""
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
os.environ["APP_ONLY"] = "0"   # этот тест проверяет старое меню; режим приложения — в test_app_only.py

import bot_best as B  # noqa: E402
import cardbase  # noqa: E402
import sources as S  # noqa: E402

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


def user(chat):
    return pytypes.SimpleNamespace(id=chat, first_name="Т", last_name="", username="u%d" % chat)


def msg(text, chat):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=user(chat))


def texts(chat):
    return [s[1][1] for s in SENT if s[0] == "send_message" and s[1][0] == chat]


# ── метки: что пишется в профиль при первом старте ──
cases = {
    "ig_father": "ig_father", "vk_mother": "vk_mother", "yt_Sorry": "yt_sorry", "pin_birthday": "pin_birthday",
    "tg_family": "tg_family", "ad_kanal-1": "ad_kanal-1", "vkads_father": "vkads_father",
    "p_ABC123": "p_ABC123", "g_xyz": "gift", "ref_5": "ref", "grp_abc": "group", "w_family": "web", "v_love": "vk",
    "pdf": "pdf", "occ": "occ",
}
for i, (param, expected) in enumerate(cases.items()):
    chat = 1000 + i
    if param.startswith("g_") or param.startswith("grp_"):
        # эти параметры ведут в свои сценарии; проверяем только метку
        assert B.source_label(param) == expected
        continue
    B.cmd_start(msg(f"/start {param}", chat))
    assert B.get_client(chat)["source"] == expected, (param, B.get_client(chat)["source"])
B.cmd_start(msg("/start", 2000))
assert B.get_client(2000)["source"] == "direct"

# first touch: повторный старт с другой меткой источник не меняет
B.cmd_start(msg("/start ig_father", 2001))
B.cmd_start(msg("/start tg_love", 2001))
assert B.get_client(2001)["source"] == "ig_father"

# ── тема после префикса: известная открывает повод, неизвестная — просто меню, без ошибок ──
SENT.clear()
B.cmd_start(msg("/start ig_father", 2002))
assert any("occ:go:family" in str(s[2].get("reply_markup", "")) or
           "occ:go:family" in str([b.callback_data for row in s[2]["reply_markup"].keyboard for b in row])
           for s in SENT if s[0] == "send_message" and hasattr(s[2].get("reply_markup"), "keyboard")
           and any(getattr(b, "callback_data", "") for row in s[2]["reply_markup"].keyboard for b in row))
SENT.clear()
B.cmd_start(msg("/start yt_nepoimiche", 2003))
assert len(texts(2003)) == 1 and "Алиса Невская" in texts(2003)[0], "неизвестная тема — только меню"
B.cmd_start(msg("/start ad_", 2004))
B.cmd_start(msg("/start ig_", 2005))
B.cmd_start(msg("/start vk_ЧТО_ТО_странное", 2006))
assert B.get_client(2006)["source"] == "vk_что_то_странное"
SENT.clear()
B.cmd_start(msg("/start ad_kanal-1", 2007))
assert any("Письма с открыткой" in t for t in texts(2007)), "посев открывает каталог"
assert B.source_topic_key("tg_father") == "family" and B.source_topic_key("ig_den-otca") is None
assert B.source_topic_key("pin_birthday_2") == "birthday"

# ── источник попадает в заказ ──
oid_orders = {}
for chat, price, status in [(1000, 199, "done"), (1000, 199, "done"), (2001, 299, "done"), (2000, 100, "pending"),
                            (2002, 0, "done")]:
    o = {"order_id": B.new_order_id(), "chat_id": chat, "name": "Т", "pain": "x", "product": "family",
         "price_rub": price, "status": status, "created_at": B.now_msk().isoformat(),
         "paid_at": B.now_msk().isoformat() if status == "done" else None}
    B.save_order(o)
    oid_orders[o["order_id"]] = chat
saved = {o["order_id"]: o for o in B.all_orders()}
assert all(saved[i].get("source") == B.get_client(c)["source"] for i, c in oid_orders.items())
vk = {"order_id": B.new_order_id(), "chat_id": 1000, "name": "Т", "pain": "x", "product": "family",
      "price_rub": 50, "status": "done", "source": "vk_post", "created_at": B.now_msk().isoformat(),
      "paid_at": B.now_msk().isoformat()}
B.save_order(vk)
assert B.get_order(vk["order_id"])["source"] == "vk_post", "заранее заданный источник заказа не затирается"

# ── отчёт: источник → старты → превью → оплаты → выручка ──
cardbase.log_event(1000, "preview", "family", "family-001")
cardbase.log_event(2001, "preview", "family", "family-001")
cardbase.log_event(2001, "preview", "family", "family-001")      # один человек — одно превью
B.TEST_USERS.add(3000)
B.cmd_start(msg("/start ig_father", 3000))
B.save_order({"order_id": "T1", "chat_id": 3000, "name": "Т", "pain": "x", "product": "family", "price_rub": 500,
              "status": "done", "is_test": True, "created_at": B.now_msk().isoformat(),
              "paid_at": B.now_msk().isoformat()})
rows, total = S.report(7)
by = {r["source"]: r for r in rows}
ig = by["ig_father"]
assert (ig["starts"], ig["previews"], ig["paid"], ig["revenue"]) == (3, 2, 3, 697), ig
assert by["vk_post"]["paid"] == 1 and by["vk_post"]["revenue"] == 50
assert "p_ABC123" in by and by["p_ABC123"]["starts"] == 1
assert total["revenue"] == 747 and total["paid"] == 4, total
ch = {r["source"]: r for r in S.channels(rows)}
assert ch["ig"]["starts"] >= 3 and ch["ig"]["revenue"] == 697
# окно: старый заказ в 7 дней не попадает, в 30 — попадает
from datetime import timedelta  # noqa: E402
old = (B.now_msk() - timedelta(days=20)).isoformat()
B.save_order({"order_id": "OLD1", "chat_id": 1000, "name": "Т", "pain": "x", "product": "family", "price_rub": 100,
              "status": "done", "created_at": old, "paid_at": old})
assert S.report(7)[1]["revenue"] == 747 and S.report(30)[1]["revenue"] == 847
# команда админа и поле в /stats
SENT.clear()
S.cmd_sources(msg("/sources", 1))
out = texts(1)
assert len(out) == 2 and "ig_father" in out[0] and "Источники за 7 дн." in out[0] and "за 30 дн." in out[1]
SENT.clear()
S.cmd_sources(msg("/sources", 500))
assert not texts(500), "не админ — ничего"
rep = B.sources_report()
assert rep["7"]["total"]["revenue"] == 747 and rep["30"]["total"]["revenue"] == 847
print("OK: sources")
