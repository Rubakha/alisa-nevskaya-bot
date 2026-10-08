"""Отправка получателю: inline даёт три способа (открытка, письмо текстом, ссылка), запасной путь — готовые сообщения.
Запуск: python tests/test_send_pack.py"""
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

SENT = []
MSG_ID = [100]


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        MSG_ID[0] += 1
        return pytypes.SimpleNamespace(message_id=MSG_ID[0], chat=pytypes.SimpleNamespace(id=500),
                                       photo=[pytypes.SimpleNamespace(file_id="PH")],
                                       animation=pytypes.SimpleNamespace(file_id="AN"))
    return f


for m in ["send_message", "send_photo", "send_animation", "answer_callback_query", "answer_inline_query",
          "send_chat_action"]:
    setattr(B.bot, m, rec(m))


def user(chat):
    return pytypes.SimpleNamespace(id=chat, first_name="Т", last_name="", username="u%d" % chat)


B.cmd_start(pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=500), text="/start", content_type="text",
                                    from_user=user(500)))
LONG = "Катя, ты для меня самый близкий человек. " * 20
order = {"order_id": B.new_order_id(), "chat_id": 500, "name": "Т", "pain": "family", "product": "family",
         "price_rub": 199, "status": "done", "gift_for": "Катя", "sign": "Серёжа", "letter_text": LONG,
         "card_file_id": "PH", "created_at": B.now_msk().isoformat()}
B.save_order(order)
code = B.occ_gift_code(order)


def inline(chat=500):
    SENT.clear()
    B.occ_inline_send(pytypes.SimpleNamespace(id="q", query="card_" + code, from_user=user(chat)))
    return [s for s in SENT if s[0] == "answer_inline_query"][-1][1][1]


res = inline()
kinds = [type(r).__name__ for r in res]
assert kinds == ["InlineQueryResultCachedPhoto", "InlineQueryResultArticle", "InlineQueryResultArticle"], kinds
link = B.occ_gift_link(order)
assert link in res[0].caption and "Катя, тебе письмо от Серёжи" in res[0].caption
assert res[1].title == "📝 Письмо текстом" and "ты для меня самый близкий" in res[1].input_message_content.message_text
assert link in res[2].input_message_content.message_text
assert len({r.id for r in res}) == 3, "id результатов уникальны"
assert all(r.reply_markup.keyboard[0][0].url == link for r in res)

# слишком длинное письмо — режем и оставляем ссылку
order["letter_text"] = "слово " * 1500
B.save_order(order)
text = inline()[1].input_message_content.message_text
assert len(text) <= 4096 and link in text

# чужой человек получает пустой ответ
assert inline(chat=999) == []

# живая открытка — первой идёт анимация
order["card_anim_file_id"] = "AN"
B.save_order(order)
assert type(inline()[0]).__name__ == "InlineQueryResultCachedGif"

# запасной путь: готовые сообщения для пересылки
order["letter_text"] = LONG
B.save_order(order)
SENT.clear()
B.occ_forward_pack(pytypes.SimpleNamespace(id="c", data="gift:fwd:" + order["order_id"], from_user=user(500),
                                           message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=500),
                                                                           message_id=5)))
anim = [s for s in SENT if s[0] == "send_animation"]
assert anim and link in anim[0][2]["caption"]
assert any("ты для меня самый близкий" in str(s[1]) for s in SENT if s[0] == "send_message"), "письмо текстом тоже"
SENT.clear()
B.occ_forward_pack(pytypes.SimpleNamespace(id="c", data="gift:fwd:" + order["order_id"], from_user=user(999),
                                           message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=999),
                                                                           message_id=5)))
assert not [s for s in SENT if s[0] in ("send_photo", "send_animation")], "чужой заказ не отдаём"

# простой путь: одна кнопка «Отправить» → выбор человека → в поле текст и ссылка с превью открытки
B.bot.send_message = rec("send_message")
order["card_anim_file_id"] = None
B.save_order(order)
B.occ_deliver(500, order)
kb = None
for s_ in SENT:
    if s_[0] == "send_message" and "Письмо готово" in str(s_[1]):
        kb = s_[2]["reply_markup"]
assert kb is not None
first = kb.keyboard[0][0]
assert first.text == "💌 Отправить Катя"
assert first.web_app.url == f"{B.site_pages.SITE_URL}/share/{code}", "главная кнопка — мини-приложение отправки"
share = B.occ_share_url(order)   # запасная ссылка «поделиться» живёт в «Других способах»
assert share.startswith("https://t.me/share/url?url=")
import urllib.parse as up  # noqa: E402
qs = up.parse_qs(up.urlparse(share).query)
assert qs["url"][0] == B.occ_web_envelope(order) and "/e/" in qs["url"][0]
assert "Катя, тебе письмо от Серёжи" in qs["text"][0]
assert "gift:more:" + order["order_id"] in [getattr(b, "callback_data", None) for row in kb.keyboard for b in row]

# превью ссылки: на странице конверта og:image именно этой открытки; для Telegram-получателя есть кнопка в бота
page = B.app.test_client().get(f"/e/{code}")
html = page.get_data(as_text=True)
assert page.status_code == 200
assert f'property="og:image" content="{B.site_pages.SITE_URL}/e/{code}.jpg"' in html
assert 'property="og:title" content="💌 Катя, тебе письмо от Серёжи"' in html
assert f"start=g_{code}" in html and "Вскрыть письмо от Серёжи" in html
img = B.app.test_client().get(f"/e/{code}.jpg")
assert img.status_code == 200 and img.mimetype == "image/jpeg" and len(img.data) > 10000
assert order.get("opened_at") is None and not B.get_order(order["order_id"]).get("opened_at"),     "превью ссылки не считается вскрытием"

# «Другие способы»
SENT.clear()
B.occ_more_ways(pytypes.SimpleNamespace(id="c", data="gift:more:" + order["order_id"], from_user=user(500),
                                        message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=500),
                                                                        message_id=5)))
more = [s_ for s_ in SENT if s_[0] == "send_message"][-1]
cbs = [getattr(b, "callback_data", None) for row in more[2]["reply_markup"].keyboard for b in row]
assert "gift:fwd:" + order["order_id"] in cbs
assert any(getattr(b, "switch_inline_query_chosen_chat", None) for row in more[2]["reply_markup"].keyboard for b in row)
assert B.occ_gift_link(order) in more[1][1]
SENT.clear()
B.occ_more_ways(pytypes.SimpleNamespace(id="c", data="gift:more:" + order["order_id"], from_user=user(999),
                                        message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=999),
                                                                        message_id=5)))
assert not [s_ for s_ in SENT if s_[0] == "send_message"]
print("OK: send pack")
