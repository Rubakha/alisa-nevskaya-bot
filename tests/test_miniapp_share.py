"""Отправка в один тап (мини-приложение) и страницы конверта: подпись initData, подготовленное сообщение,
имя отправителя в родительном падеже, вскрытие с анимацией, GIF живой открытки.
Запуск: python tests/test_miniapp_share.py (сеть не нужна)"""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import types as pytypes
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TG_BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""

import bot_best as B  # noqa: E402
import envelope_pages as E  # noqa: E402
import miniapp_share as M  # noqa: E402

TOKEN = os.environ["TG_BOT_TOKEN"]
for _m in ["send_message", "send_photo", "send_animation", "send_chat_action", "answer_callback_query"]:
    setattr(B.bot, _m, lambda *a, **k: pytypes.SimpleNamespace(message_id=1))

# ── имя отправителя ──
cases = {"Серёжа": "Серёжи", "Твой Серёжа": "Серёжи", "Твоя мама": "мамы", "Андрей": "Андрея", "Мария": "Марии",
         "Катя": "Кати", "Саша": "Саши", "Бабушка": "Бабушки", "Иван": "Ивана", "Игорь": "Игоря", "Дочь": "Дочери"}
for sign, want in cases.items():
    assert E.from_whom({"sign": sign}) == want, (sign, E.from_whom({"sign": sign}))
assert E.from_whom({"sign": "", "name": "Сергей Рубаха"}) == "Сергея", "без подписи — имя из профиля"
assert E.from_whom({"sign": "Паша и Маша"}) == "Паша и Маша", "составные подписи не склоняем"
assert E.from_whom({}) == "близкого человека"


def init_data(user_id, token=TOKEN, age=0, tamper=False):
    user = json.dumps({"id": user_id, "first_name": "T"}, ensure_ascii=False)
    pairs = {"auth_date": str(int(time.time()) - age), "query_id": "AAA", "user": user}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        pairs["user"] = json.dumps({"id": user_id + 1})
    return urllib.parse.urlencode({**pairs, "hash": h})


assert M.validate_init_data(init_data(500), TOKEN) == 500
assert M.validate_init_data(init_data(500, token="999:OTHER"), TOKEN) is None, "подпись чужим токеном"
assert M.validate_init_data(init_data(500, tamper=True), TOKEN) is None, "подмена пользователя"
assert M.validate_init_data(init_data(500, age=3 * 24 * 3600), TOKEN) is None, "протухшие данные"
assert M.validate_init_data("", TOKEN) is None and M.validate_init_data("hash=abc", TOKEN) is None

# ── заказ ──
def user(chat):
    return pytypes.SimpleNamespace(id=chat, first_name="Сергей", last_name="", username="u%d" % chat)


B.cmd_start(pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=500), text="/start", content_type="text",
                                    from_user=user(500)))
order = {"order_id": B.new_order_id(), "chat_id": 500, "name": "Сергей", "pain": "love", "product": "love",
         "price_rub": 249, "status": "done", "gift_for": "Любимая", "sign": "Твой Серёжа",
         "card_title": "С Новым годом, Любимая!", "card_line": "Ты самое важное.", "letter_text":
         "Первый абзац письма.\n\nВторой абзац про нас.\n\nТретий — самый тёплый.", "card_file_id": "PHOTO1",
         "card_ref": "love-001", "created_at": B.now_msk().isoformat()}
B.save_order(order)
code = B.occ_gift_code(order)
SITE = B.site_pages.SITE_URL

# ── подготовленное сообщение: только открытка и кнопка ──
res = M.prepared_result(order, code, SITE)
assert res["type"] == "photo" and res["photo_file_id"] == "PHOTO1" and "caption" not in res
btn = res["reply_markup"]["inline_keyboard"][0][0]
assert btn == {"text": "💌 Вскрыть письмо от Серёжи", "url": f"{SITE}/e/{code}"}, btn
order["card_anim_file_id"] = "GIF1"
assert M.prepared_result(order, code, SITE)["type"] == "gif"
assert M.prepared_result({**order, "card_file_id": None, "card_anim_file_id": None}, code, SITE)["type"] == "article"
order["card_anim_file_id"] = None

# ── эндпоинты ──
client = B.app.test_client()
page = client.get(f"/share/{code}")
html = page.get_data(as_text=True)
assert page.status_code == 200 and "telegram-web-app.js" in html and "shareMessage" in html
assert f'"{code}"' in html and "t.me/share/url" in html, "запасная ссылка на странице"
assert client.get("/share/nope").status_code == 404

CALLS = []


def fake_post(url, json=None, timeout=None):
    CALLS.append((url, json))
    return pytypes.SimpleNamespace(json=lambda: {"ok": True, "result": {"id": "PREP123", "expiration_date": 1}})


M.requests.post = fake_post
r = client.post(f"/share/{code}/prepare", json={"initData": init_data(500)})
assert r.status_code == 200 and r.get_json() == {"id": "PREP123"}
url, body = CALLS[-1]
assert url.endswith("/savePreparedInlineMessage") and body["user_id"] == 500
assert body["result"]["reply_markup"]["inline_keyboard"][0][0]["text"] == "💌 Вскрыть письмо от Серёжи"
assert body["allow_user_chats"] and body["allow_group_chats"] and not body["allow_channel_chats"]

n = len(CALLS)
assert client.post(f"/share/{code}/prepare", json={"initData": init_data(777)}).status_code == 403, "чужой человек"
assert client.post(f"/share/{code}/prepare", json={"initData": init_data(500, tamper=True)}).status_code == 403
assert client.post(f"/share/{code}/prepare", json={}).status_code == 403
assert client.post("/share/nope/prepare", json={"initData": init_data(500)}).status_code == 403
assert len(CALLS) == n, "без проверки Telegram не вызываем"

M.requests.post = lambda url, json=None, timeout=None: pytypes.SimpleNamespace(
    json=lambda: {"ok": False, "description": "bad"})
r = client.post(f"/share/{code}/prepare", json={"initData": init_data(500)})
assert r.status_code == 502 and "ссылкой" in r.get_json()["error"]

# ── страницы конверта ──
closed = client.get(f"/e/{code}").get_data(as_text=True)
assert "Вскрыть письмо от Серёжи" in closed and 'class="seal"' in closed
assert f'og:image" content="{SITE}/e/{code}.jpg"' in closed
assert 'og:title" content="💌 Любимая, тебе письмо от Серёжи"' in closed
assert f"start=g_{code}" in closed

opened = client.get(f"/e/{code}/open", headers={"User-Agent": "TelegramBot (like TwitterBot)"})
assert opened.status_code == 200
o = opened.get_data(as_text=True)
assert o.count("<p style=\"animation-delay") == 3, "письмо появляется по абзацам"
assert "Первый абзац письма." in o and "Второй абзац про нас." in o
assert f'src="/e/{code}.jpg"' in o and ".gif" not in o, "без анимации — обычная открытка"
assert 'class="stage"' in o and "canvas" in o and "keyframes crack" in o, "печать ломается, искры"
assert not B.get_order(order["order_id"]).get("opened_at"), "ссылка-превью (бот) не считается вскрытием"

# ── живая открытка: GIF на странице и по адресу /e/<код>.gif ──
assert client.get(f"/e/{code}.gif").status_code == 404, "без анимации GIF-а нет"
order["card_ref"] = "love-001!none.-.corners.none.sparkle"
B.save_order(order)
o = client.get(f"/e/{code}/open", headers={"User-Agent": "TelegramBot"}).get_data(as_text=True)
assert f'src="/e/{code}.gif"' in o
g = client.get(f"/e/{code}.gif")
assert g.status_code == 200 and g.mimetype == "image/gif" and g.data[:3] == b"GIF"
g2 = client.get(f"/e/{code}.gif")
assert g2.data == g.data, "GIF кэшируется"
assert os.path.exists(os.path.join(os.environ["DATA_DIR"], "anim_cache", f"{order['order_id']}.gif"))
print("OK: miniapp share")
