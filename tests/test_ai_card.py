"""Генератор «своей картинки» (ИИ): включение по ключу, мастер кнопок, лимиты, использование в заказе.
Запуск: python tests/test_ai_card.py (сеть не нужна: генерация подменена)"""
import io
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
os.environ.pop("WAVESPEED_API_KEY", None)

from PIL import Image  # noqa: E402

import ai_card as AC  # noqa: E402
import bot_best as B  # noqa: E402
import postcards as PC  # noqa: E402

SENT = []
MSG_ID = [100]


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        MSG_ID[0] += 1
        return pytypes.SimpleNamespace(message_id=MSG_ID[0], chat=pytypes.SimpleNamespace(id=500),
                                       photo=[pytypes.SimpleNamespace(file_id=f"F{MSG_ID[0]}")],
                                       animation=pytypes.SimpleNamespace(file_id="G"))
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "edit_message_media", "send_media_group",
          "send_document", "send_animation"]:
    setattr(B.bot, m, rec(m))

CALLS = []


def fake_generate(prompt):
    CALLS.append(prompt)
    buf = io.BytesIO()
    Image.new("RGB", (1080, 806), (120 + 30 * len(CALLS), 160, 140)).save(buf, "JPEG")
    return buf.getvalue()


AC.generate_image = fake_generate


def user(chat):
    return pytypes.SimpleNamespace(id=chat, first_name="Т", last_name="", username="u%d" % chat)


def call(data, chat=500, mid=9):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=user(chat),
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def cbs(markup):
    return [b.callback_data for row in markup.keyboard for b in row]


def last(name):
    return [s for s in SENT if s[0] == name][-1]


def make_order(chat=500, product="birthday"):
    B.cmd_start(pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text="/start", content_type="text",
                                        from_user=user(chat)))
    o = {"order_id": B.new_order_id(), "chat_id": chat, "name": "Т", "pain": product, "product": product,
         "price_rub": 199, "status": "done", "gift_for": "Катя", "card_title": "С днём рождения, Катя!",
         "card_line": "Ты лучшая.", "sign": "Серёжа", "letter_text": "Письмо.",
         "created_at": B.now_msk().isoformat()}
    B.save_order(o)
    return o


# ── без ключа функции нет ──
o = make_order()
assert not AC.enabled()
SENT.clear()
AC.offer(500, o)
assert not SENT, "без ключа WaveSpeed кнопка не показывается"

# ── с ключом: предложение, мастер ──
os.environ["WAVESPEED_API_KEY"] = "test-key-not-real"
assert AC.enabled()
SENT.clear()
AC.offer(500, o)
assert "ai:start:" + o["order_id"] in cbs(last("send_message")[2]["reply_markup"])
other = {**o, "product": "pack3"}
SENT.clear()
AC.offer(500, other)
assert not SENT, "для набора писем и неподдержанных продуктов не предлагаем"

oid = o["order_id"]
AC.start_cb(call(f"ai:start:{oid}"))
assert f"ai:st:{oid}:paper" in cbs(last("edit_message_text")[2]["reply_markup"])
AC.pick_cb(call(f"ai:st:{oid}:paper"))
assert f"ai:sub:{oid}:cake" in cbs(last("edit_message_text")[2]["reply_markup"])
AC.go_cb(call(f"ai:go:{oid}"))
assert last("answer_callback_query")[2].get("show_alert") is True and not CALLS, "без полного выбора не рисуем"
AC.pick_cb(call(f"ai:sub:{oid}:cake"))
AC.pick_cb(call(f"ai:sub:{oid}:nonexistent"))   # чужой ключ игнорируется
AC.pick_cb(call(f"ai:pal:{oid}:rainbow"))
for ex in ("candles", "butterflies", "ribbon"):
    AC.pick_cb(call(f"ai:ex:{oid}:{ex}"))
sel = AC.WIZ[500]
assert sel["extras"] == ["candles", "butterflies"], "не больше двух добавок"
assert last("answer_callback_query")[2].get("show_alert") is True

# ── генерация: 3 попытки, промпт собран только из наших кнопок ──
AC.go_cb(call(f"ai:go:{oid}"))
assert len(CALLS) == 1
p = CALLS[0]
assert "birthday cake" in p and "paper-craft" in p and "rainbow" in p and "candles" in p and "butterflies" in p
assert "no text" in p.lower() and "no people" in p.lower()
assert os.path.exists(os.path.join(os.environ["DATA_DIR"], "ai_cards", f"{oid}_1.jpg"))
photo = last("send_photo")
assert photo[2]["caption"] == "Вариант 1 из 3"
assert cbs(photo[2]["reply_markup"]) == [f"ai:use:{oid}:1", f"ai:go:{oid}", f"ai:start:{oid}"]
AC.go_cb(call(f"ai:go:{oid}"))
AC.go_cb(call(f"ai:go:{oid}"))
assert len(CALLS) == 3 and AC.used_for_order(oid) == 3
AC.go_cb(call(f"ai:go:{oid}"))
assert len(CALLS) == 3, "четвёртой попытки нет"
assert last("answer_callback_query")[2].get("show_alert") is True
assert cbs(last("send_photo")[2]["reply_markup"]) == [f"ai:use:{oid}:3", f"ai:start:{oid}"]

# чужой человек не может пользоваться чужим заказом
n_before = len(CALLS)
AC.go_cb(call(f"ai:go:{oid}", chat=999))
assert len(CALLS) == n_before

# ── выбор картинки: заказ получает её, открытка отрисована, inline берёт новую ──
AC.use_cb(call(f"ai:use:{oid}:2"))
done = B.get_order(oid)
assert done["card_ref"] == f"ai:{oid}_2"
assert done["card_file_id"].startswith("F")
img = Image.open(io.BytesIO(B.occ_postcard(done)))
assert img.size == (PC.W, PC.H)
bg_a = PC._background("birthday", f"ai:{oid}_2").tobytes()
bg_b = PC._background("birthday", f"ai:{oid}_3").tobytes()
assert bg_a != bg_b, "ссылка ai: ведёт на нарисованный файл"
AC.use_cb(call(f"ai:use:{oid}:7"))  # несуществующей попытки нет
assert B.get_order(oid)["card_ref"] == f"ai:{oid}_2"

# ── дневной лимит и журнал расходов ──
o2 = make_order(501, "love")
AC.start_cb(call(f"ai:start:{o2['order_id']}", chat=501))
AC.pick_cb(call(f"ai:st:{o2['order_id']}:film", chat=501))
AC.pick_cb(call(f"ai:sub:{o2['order_id']}:roses", chat=501))
AC.pick_cb(call(f"ai:pal:{o2['order_id']}:deep", chat=501))
os.environ["AI_CARD_DAILY_CAP"] = "3"
n_before = len(CALLS)
AC.go_cb(call(f"ai:go:{o2['order_id']}", chat=501))
assert len(CALLS) == n_before and AC.today_count() == 3
assert last("answer_callback_query")[2].get("show_alert") is True
os.environ["AI_CARD_DAILY_CAP"] = "60"
AC.go_cb(call(f"ai:go:{o2['order_id']}", chat=501))
assert len(CALLS) == n_before + 1
rep = AC.report()
assert rep["generated"] == 4 and abs(rep["rub"] - 4 * AC.COST_RUB) < 0.1 and rep["enabled"]

# сбой генерации не тратит попытку
def boom(prompt):
    raise RuntimeError("API down")


AC.generate_image = boom
used = AC.used_for_order(o2["order_id"])
AC.go_cb(call(f"ai:go:{o2['order_id']}", chat=501))
assert AC.used_for_order(o2["order_id"]) == used and "попытка не потрачена" in last("send_message")[1][1]

# все наборы описаны для всех поводов
import occasions as OCC  # noqa: E402
for key in OCC.CATALOG_ORDER:
    assert key in AC.SUBJECTS and len(AC.SUBJECTS[key]) >= 4, key
print("OK: ai card")
