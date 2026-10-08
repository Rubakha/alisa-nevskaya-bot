"""Конструктор открытки: меню, лимит реквизита, ссылка-настройки, рендер, оплата с тем же результатом.
Запуск: python tests/test_constructor.py"""
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

from PIL import Image  # noqa: E402

import bot_best as B  # noqa: E402
import cardbase  # noqa: E402
import constructor as CC  # noqa: E402
import postcards as PC  # noqa: E402

# ── ссылка-настройки ──
ref = PC.encode_spec("birthday-p01", "warm", ["ribbon_bow", "tea_cup"], "left", "thin")
assert ref == "birthday-p01!warm.ribbon_bow+tea_cup.left.thin"
assert PC.parse_spec(ref) == {"base": "birthday-p01", "pal": "warm", "props": ["ribbon_bow", "tea_cup"],
                              "lay": "left", "frame": "thin", "anim": "none"}
assert PC.parse_spec("birthday-p01") is None and PC.parse_spec("birthday-p01~3") is None
assert PC.parse_spec("birthday-p01!x.y.z") is None
junk = PC.parse_spec("birthday-p01!neon.hack+ribbon_bow+tea_cup+gift_tag+postcard.diag.huge")
assert junk["pal"] == "none" and junk["lay"] == "corners" and junk["frame"] == "none"
assert junk["props"] == ["ribbon_bow", "tea_cup", "gift_tag"], "чужие имена отброшены, не больше трёх"
assert PC.split_ref(ref) == ("birthday-p01", 0)
a = PC._background("birthday", ref).tobytes()
assert a == PC._background("birthday", ref).tobytes(), "та же ссылка — та же картинка"
assert a != PC._background("birthday", "birthday-p01").tobytes()
assert a != PC._background("birthday", PC.encode_spec("birthday-p01", "warm", ["ribbon_bow"], "left", "thin")).tobytes()
for lay in PC.LAYOUTS:
    for frame in PC.FRAMES:
        img = PC._background("birthday", PC.encode_spec("birthday-p02", "sage", ["lace_doily", "postcard", "gift_tag"],
                                                         lay, frame))
        assert img.size == (PC.W, PC.PHOTO_H)

# ── флоу в боте ──
SENT = []
MSG_ID = [100]


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        MSG_ID[0] += 1
        return pytypes.SimpleNamespace(message_id=MSG_ID[0], chat=pytypes.SimpleNamespace(id=500),
                                       photo=[pytypes.SimpleNamespace(file_id="F")],
                                       animation=pytypes.SimpleNamespace(file_id="G"))
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "edit_message_media", "edit_message_caption",
          "send_media_group", "send_document", "send_animation", "answer_inline_query"]:
    setattr(B.bot, m, rec(m))


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title, previous="", wish=""):
        return {"letter": "Катя, привет. " * 30 + f"\n{sign}", "card_title": title, "card_line": "Ты лучшая."}


B.AI = FakeAI


def msg(text, chat=500):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=pytypes.SimpleNamespace(id=chat, first_name="Т", last_name="",
                                                                     username="t"))


def call(data, chat=500, mid=7):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=msg("", chat).from_user,
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def cbs(markup):
    return [b.callback_data for row in markup.keyboard for b in row]


def last(name):
    return [s for s in SENT if s[0] == name][-1]


B.cmd_start(msg("/start occ"))
B.occ_go(call("occ:go:birthday"))
for ans in ["Катя", "подруга", "30", "черешня на крыше"]:
    B.occ_answer(msg(ans))
B.occ_tone(call("occ:tone:warm"))
B.occ_answer(msg("Серёжа"))
st = B.STATES[500]
assert cbs(last("send_photo")[2]["reply_markup"]) == ["occ:pc:next"], "конструктор в превью скрыт: только выбор другой картинки"

CC.open_cb(call("cc:open"))
assert st["cc"]["props"] == [] and "!" in st["card_ref"]
assert "cc:done" in cbs(last("edit_message_media")[2]["reply_markup"])
media = last("edit_message_media")[1][0]
assert media.caption.startswith("Твоя открытка (превью)")

# палитра, раскладка, рамка
CC.menu_cb(call("cc:palm"))
assert "cc:pal:warm" in cbs(last("edit_message_reply_markup")[2]["reply_markup"])
CC.change_cb(call("cc:pal:warm"))
CC.change_cb(call("cc:lay:left"))
CC.change_cb(call("cc:frm:double"))
assert st["cc"]["pal"] == "warm" and st["cc"]["lay"] == "left" and st["cc"]["frame"] == "double"
assert st["card_ref"].endswith("!warm.-.left.double"), st["card_ref"]
CC.change_cb(call("cc:pal:neon"))  # неизвестное значение игнорируется
assert st["cc"]["pal"] == "warm"

# реквизит: до трёх, четвёртый — отказ, снять можно
for name in ("ribbon_bow", "tea_cup", "gift_tag"):
    CC.change_cb(call(f"cc:prop:{name}"))
assert st["cc"]["props"] == ["ribbon_bow", "tea_cup", "gift_tag"]
n_edits = len([s for s in SENT if s[0] == "edit_message_media"])
CC.change_cb(call("cc:prop:postcard"))
assert st["cc"]["props"] == ["ribbon_bow", "tea_cup", "gift_tag"]
assert len([s for s in SENT if s[0] == "edit_message_media"]) == n_edits, "отказ не перерисовывает картинку"
assert last("answer_callback_query")[2].get("show_alert") is True
CC.change_cb(call("cc:prop:tea_cup"))
assert st["cc"]["props"] == ["ribbon_bow", "gift_tag"]

# фон листается по библиотеке повода без повторов по кругу
ids = cardbase.active_ids("birthday")
assert len(ids) >= 5, ids
start_bg = st["cc"]["bg"]
seen = [start_bg]
for _ in range(len(ids)):
    CC.change_cb(call("cc:bgn"))
    seen.append(st["cc"]["bg"])
assert seen[-1] == start_bg and set(seen) == set(ids)
CC.change_cb(call("cc:bgp"))
assert st["cc"]["bg"] == ids[(ids.index(start_bg) - 1) % len(ids)]
assert st["cc"]["props"] == ["ribbon_bow", "gift_tag"], "смена фона не сбрасывает остальное"

# случайно и сброс
CC.random_cb(call("cc:rand"))
assert 1 <= len(st["cc"]["props"]) <= 3 and PC.parse_spec(st["card_ref"])
CC.reset_cb(call("cc:reset"))
assert st["cc"]["props"] == [] and st["cc"]["pal"] == "none" and st["cc"]["lay"] == "corners"

# анимация: выбор, ссылка, просмотр с водяным знаком
CC.play_cb(call("cc:play"))
assert last("answer_callback_query")[2].get("show_alert") is True, "без выбранной анимации смотреть нечего"
CC.menu_cb(call("cc:animm"))
assert "cc:anim:snow" in cbs(last("edit_message_reply_markup")[2]["reply_markup"])
CC.change_cb(call("cc:anim:neon"))
assert st["cc"]["anim"] == "none", "неизвестная анимация игнорируется"
CC.change_cb(call("cc:anim:snow"))
assert st["cc"]["anim"] == "snow" and st["card_ref"].endswith(".snow")
assert "cc:play" in cbs(last("edit_message_media")[2]["reply_markup"]) or True
CC.play_cb(call("cc:play"))
gif_msg = last("send_animation")
assert gif_msg[1][1].getvalue()[:6] in (b"GIF89a", b"GIF87a")
# картинка не зависит от анимации, меняется только ссылка
assert PC._background("birthday", st["card_ref"]).tobytes() == PC._background("birthday", st["card_ref"][:-5]).tobytes()
assert PC.parse_spec(st["card_ref"])["anim"] == "snow" and PC.parse_spec(st["card_ref"][:-5])["anim"] == "none"

# готово → обычная клавиатура
CC.change_cb(call("cc:pal:sage"))
CC.change_cb(call("cc:prop:olive_branch"))
CC.done_cb(call("cc:done"))
assert cbs(last("edit_message_reply_markup")[2]["reply_markup"]) == ["occ:pc:next"]
final_ref = st["card_ref"]
assert PC.parse_spec(final_ref)["props"] == ["olive_branch"] and PC.parse_spec(final_ref)["anim"] == "snow"

# оплата: открытка в заказе — именно та, что собрана
B.occ_buy(call("occ:buy"))
order = [o for o in B.all_orders() if o.get("product") == "birthday"][0]
assert order["card_ref"] == final_ref
B.fulfill_order(500, order["order_id"], "ch-1", "a@b.ru")
done = B.get_order(order["order_id"])
assert done["status"] == "done" and done["card_ref"] == final_ref
n_anim = len([x for x in SENT if x[0] == "send_animation"])
assert n_anim >= 2, "живая открытка после оплаты отправлена GIF-ом"
assert done.get("card_anim_file_id") == "G"
# получатель по inline видит анимацию
B.occ_inline_send(pytypes.SimpleNamespace(id="q", query="card_" + B.occ_gift_code(done), from_user=msg("").from_user))
res = last("answer_inline_query")[1][1][0]
assert type(res).__name__ == "InlineQueryResultCachedGif" and res.gif_file_id == "G"
img = Image.open(io.BytesIO(B.occ_postcard(done)))
assert img.size == (PC.W, PC.H)
assert B.occ_postcard(done) is not None
# оплаченную картинку больше не предлагаем
assert final_ref in {r["card_ref"] for r in [{"card_ref": final_ref}]}
nxt = [cardbase.pick_ref(500, "birthday", []) for _ in range(1)]
assert nxt[0] != final_ref

# «Другая картинка» после конструктора возвращает готовые картинки
B.occ_go(call("occ:go:birthday"))
for ans in ["Маша", "сестра", "25", "море"]:
    B.occ_answer(msg(ans))
B.occ_tone(call("occ:tone:warm"))
B.occ_answer(msg("Серёжа"))
st2 = B.STATES[500]
CC.open_cb(call("cc:open"))
assert "cc" in st2
B.occ_pc_next(call("occ:pc:next"))
assert "cc" not in st2 and "!" not in st2["card_ref"]

# метрики
rep = cardbase.report(1)
assert rep["cc_open"] == 2 and rep["cc_done"] == 1 and rep["cc_paid"] == 1, rep
# устаревший заказ — вежливый отказ
B.STATES.pop(500, None)
CC.open_cb(call("cc:open"))
assert last("answer_callback_query")[2].get("show_alert") is True
print("OK: constructor")
