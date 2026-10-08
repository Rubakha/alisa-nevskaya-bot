"""«Живая» база открыток: выбор без повторов, превью с маской, другая картинка, перегенерации, метрики.
Запуск: python tests/test_cardbase.py"""
import json
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

import cardbase  # noqa: E402

# ── 1. база на временных файлах ──
bgs = tempfile.mkdtemp()
for occ, stems in {"birthday": ["001", "002"], "love": ["003", "004"]}.items():
    os.makedirs(os.path.join(bgs, occ))
    for st in stems:
        Image.new("RGB", (40, 30), (200, 100, 100)).save(os.path.join(bgs, occ, st + ".jpg"))
with open(os.path.join(bgs, "catalog.json"), "w", encoding="utf-8") as f:
    json.dump({"cards": [{"id": "love-004", "status": "review", "style": "x", "season": "autumn"}]}, f)
cardbase.init(tempfile.mkdtemp(), bgs)
assert cardbase.active_ids("birthday") == ["birthday-001", "birthday-002"]
assert cardbase.active_ids("love") == ["love-003"], "новый фон из каталога ждёт проверки"
assert [c["id"] for c in cardbase.review_queue()] == ["love-004"]
assert cardbase.set_status("love-004", "active") and len(cardbase.active_ids("love")) == 2
assert cardbase.pick_ref(1, "nothing") is None

# фон на проверке, файл которого удалили из репозитория, бот сам переводит в «убрано»
tmp_file = os.path.join(bgs, "birthday", "009.jpg")
Image.new("RGB", (40, 30)).save(tmp_file)
with open(os.path.join(bgs, "catalog.json"), "w", encoding="utf-8") as f:
    json.dump({"cards": [{"id": "birthday-009", "status": "review"}]}, f)
cardbase.sync_catalog()
assert cardbase.get_card("birthday-009")["status"] == "review"
os.remove(tmp_file)
cardbase.sync_catalog()
assert cardbase.get_card("birthday-009")["status"] == "retired"
assert "birthday-009" not in [c["id"] for c in cardbase.review_queue()]
assert "birthday-009" not in cardbase.active_ids("birthday")

seen = []
for _ in range(2 + 2 * cardbase.VARIANTS):  # все варианты повода без повторов
    r = cardbase.pick_ref(7, "birthday", seen)
    assert r not in seen, r
    seen.append(r)
expected = set(["birthday-001", "birthday-002"]
               + [f"birthday-00{i}~{n}" for i in (1, 2) for n in range(1, cardbase.VARIANTS + 1)])
assert set(seen) == expected
assert set(seen[:2]) == {"birthday-001", "birthday-002"}, "настоящие фоны раньше композиций"
assert cardbase.pick_ref(7, "birthday", seen) in seen, "всё просмотрено — идём по кругу"
assert cardbase.pick_ref(7, "birthday", []) == seen[0], "порядок для человека стабилен"

# уже взятое — в самом конце, но не запрещено
cardbase.mark_taken(7, seen[0])
s2 = []
for _ in range(len(seen)):
    s2.append(cardbase.pick_ref(7, "birthday", s2))
assert s2[-1] == seen[0] and len(set(s2)) == len(seen), "взятая картинка предлагается последней"

# ── 2. рендер: композиция воспроизводима и отличается от фона ──
import postcards  # noqa: E402

bg = lambda ref: postcards._background("birthday", ref).tobytes()  # noqa: E731
assert bg("birthday-001~2") == bg("birthday-001~2") != bg("birthday-001"), "композиция воспроизводима по seed"
assert bg("birthday-001~2") != bg("birthday-001~3")
assert bg(None) == bg("birthday-001"), "без card_ref - первый фон повода"
assert len(postcards.render("birthday", "T", "L", "S", card_ref="birthday-001~2")) > 10000

# ── 3. флоу в боте ──
import bot_best as B  # noqa: E402

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
          "edit_message_text", "edit_message_reply_markup", "edit_message_media", "edit_message_caption",
          "send_media_group", "send_document"]:
    setattr(B.bot, m, rec(m))
CALLS = []


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title, previous="", wish=""):
        CALLS.append((previous, wish))
        n = len(CALLS)
        return {"letter": f"Катя, вариант {n}. " + "Первая половина письма про черешню. " * 6
                          + "ВТОРАЯ_ПОЛОВИНА_СЕКРЕТ " * 6 + f"\n{sign}",
                "card_title": title, "card_line": f"Строка {n}"}


B.AI = FakeAI


def msg(text, chat=500):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=pytypes.SimpleNamespace(id=chat, first_name="Т", last_name="",
                                                                     username="t"))


def call(data, chat=500, mid=1):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=msg("", chat).from_user,
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=mid))


def kinds(name):
    return [s for s in SENT if s[0] == name]


def cbs(markup):
    return [b.callback_data for row in markup.keyboard for b in row]


B.cmd_start(msg("/start occ"))
B.occ_go(call("occ:go:birthday"))
for ans in ["Катя", "подруга", "30", "черешня на крыше"]:
    B.occ_answer(msg(ans))
B.occ_tone(call("occ:tone:warm"))
B.occ_answer(msg("Серёжа"))

st = B.STATES[500]
assert st["step"] == "occ_paywall" and st["card_ref"] and st["card_seen"] == [st["card_ref"]]
assert cbs(kinds("send_photo")[-1][2]["reply_markup"]) == ["occ:pc:next"]
text_msg = kinds("send_message")[-1]
shown = text_msg[1][1]
assert "▒" in shown and "ВТОРАЯ_ПОЛОВИНА_СЕКРЕТ" not in shown and "Первая половина" in shown, shown
assert cbs(text_msg[2]["reply_markup"]) == ["occ:buy", "pr:enter", "occ:retext", "letter:cancel"]
assert cardbase.report(1)["previews"] == 1

# другая картинка — бесплатно и без лимита, без повторов
first = st["card_ref"]
for _ in range(5):
    B.occ_pc_next(call("occ:pc:next"))
assert len(set(st["card_seen"])) == 6 and st["card_ref"] != first
assert len(kinds("edit_message_media")) == 5
assert cardbase.report(1)["pc_next"] == 5

# две бесплатные перегенерации текста, затем пожелание, затем конец
B.occ_retext(call("occ:retext"))
B.occ_retext(call("occ:retext"))
assert st["retexts"] == 2 and "вариант 3" in st["letter"] and CALLS[-1][0].startswith("Катя, вариант 2")
B.occ_retext(call("occ:retext"))
assert st["step"] == "occ_wish" and len(CALLS) == 3
B.occ_pc_next(call("occ:pc:next"))  # картинки листаются и в этом шаге
B.occ_wish_receive(msg("сделай короче и теплее"))
assert st["step"] == "occ_paywall" and st["wish_used"] and CALLS[-1][1] == "сделай короче и теплее"
n = len(CALLS)
B.occ_retext(call("occ:retext"))
assert len(CALLS) == n and st["step"] == "occ_paywall", "после пожелания правок больше нет"
rep = cardbase.report(1)
assert rep["retext"] == 2 and rep["retext_wish"] == 1

# оплата: картинка уходит в заказ, потом не предлагается снова
ref = st["card_ref"]
B.occ_buy(call("occ:buy"))
oid = [o for o in B.all_orders() if o.get("product") == "birthday"][0]["order_id"]
assert B.get_order(oid)["card_ref"] == ref
B.fulfill_order(500, oid, "ch-1", "a@b.ru")
assert B.get_order(oid)["status"] == "done"
rep = cardbase.report(1)
assert rep["buy"] == 1 and rep["paid"] == 1 and rep["conversion"] == 100.0, rep
later = []
_n = len(cardbase.active_ids("birthday"))
for _ in range(_n * (1 + cardbase.VARIANTS)):
    later.append(cardbase.pick_ref(500, "birthday", later))
assert later[-1] == ref and ref not in later[:-1], "оплаченная картинка — последняя в очереди"

# тестовый аккаунт не попадает в метрики
B.TEST_USERS.add(900)
before = cardbase.report(1)["previews"]
B.cb_event(900, "preview", "birthday", "birthday-001")
assert cardbase.report(1)["previews"] == before

# проверка новых фонов владельцем
newf = os.path.join(postcards.BGS, "love", "zz_test.jpg")
Image.new("RGB", (40, 30)).save(newf)
try:
    cardbase.sync_catalog()
    B.send_card_review(limit=1000)  # сначала разбираем уже лежащие в репозитории фоны на проверку
    cardbase.set_status("love-zz_test", "review")
    cardbase.sync_catalog()
    with cardbase._connect() as _c:
        _c.execute("UPDATE cards SET review_sent=0 WHERE id='love-zz_test'")
    n = len(kinds("send_photo"))
    assert B.send_card_review() == 1 and len(kinds("send_photo")) == n + 1
    assert B.send_card_review() == 0, "повторно не присылаем"
    B.card_review_cb(call("cbr:no:love-zz_test", chat=500))  # не владелец — ничего не меняет
    assert cardbase.get_card("love-zz_test")["status"] == "review"
    B.card_review_cb(call("cbr:ok:love-zz_test", chat=1))
    assert cardbase.get_card("love-zz_test")["status"] == "active"
finally:
    os.remove(newf)
print("OK: cardbase flow")
