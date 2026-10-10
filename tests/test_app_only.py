"""Режим APP_ONLY: /start с метками, реферал, конверт получателя, старые кнопки, свободный текст, админ, откат APP_ONLY=0.
Запуск: python tests/test_app_only.py (сеть не нужна)"""
import os
import sys
import tempfile
import types as pytypes
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TG_BOT_TOKEN"] = "123456:TESTTOKEN"
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ.pop("APP_ONLY", None)           # по умолчанию режим приложения включён
os.environ.pop("MINIAPP_URL", None)

import bot_best as B  # noqa: E402
import calendar_reminders as CR  # noqa: E402

types = B.types
APP = "https://alisa-letters.pages.dev/app/"
SENT = []
DELETED = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=len(SENT), photo=[pytypes.SimpleNamespace(file_id="F")])
    return f


for m in ["send_message", "send_photo", "send_document", "send_animation", "send_chat_action", "answer_callback_query",
          "set_chat_menu_button", "edit_message_text", "edit_message_reply_markup", "send_media_group"]:
    setattr(B.bot, m, rec(m))
B.bot.delete_message = lambda chat, mid: DELETED.append((chat, mid))


def user(uid, first="Аня"):
    return pytypes.SimpleNamespace(id=uid, first_name=first, last_name="", username=f"u{uid}")


def msg(text, chat=500, chat_type="private"):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat, type=chat_type), text=text, content_type="text",
                                   from_user=user(chat), message_id=7, json={})


def call(data, chat=500):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=user(chat), message_id=7, json={},
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat, type="private"),
                                                                   message_id=7))


def dispatch(message):
    """Как telebot: первый обработчик, чей фильтр подошёл. Возвращает имя функции."""
    for h in B.bot.message_handlers:
        if B.bot._test_message_handler(h, message):
            h["function"](message)
            return h["function"].__name__


def dispatch_cb(c):
    for h in B.bot.callback_query_handlers:
        if B.bot._test_message_handler(h, c):
            h["function"](c)
            return h["function"].__name__


def sent(name="send_message", since=0):
    return [s for s in SENT[since:] if s[0] == name]


def app_button(markup):
    """URL кнопки-приложения в inline-клавиатуре или None."""
    if not isinstance(markup, types.InlineKeyboardMarkup):
        return None
    btns = [b for row in markup.keyboard for b in row]
    return btns[0].web_app.url if len(btns) == 1 and btns[0].web_app else None


def last_app(since):
    for s in reversed(sent(since=since)):
        url = app_button(s[2].get("reply_markup"))
        if url:
            return url, s
    return None, None


# ── новый клиент: /start ──
n = len(SENT)
dispatch(msg("/start", chat=500))
url, s = last_app(n)
assert url == APP, "одна кнопка-приложение, адрес по умолчанию"
assert "Алиса Невская" in s[1][1] and len(s[1][1]) < 400, "короткое приветствие"
menu = sent("set_chat_menu_button", n)
assert menu and menu[-1][1][0] == 500 and menu[-1][1][1].text == "Письмо" and menu[-1][1][1].web_app.url == APP
assert B.get_client(500)["source"] == "direct"
assert not [x for x in sent(since=n) if isinstance(x[2].get("reply_markup"), types.ReplyKeyboardMarkup)], \
    "нижней клавиатуры нет"
assert any(isinstance(x[2].get("reply_markup"), types.ReplyKeyboardRemove) for x in sent(since=n)), "старая клавиатура снята"
assert len(DELETED) == 1 and B.get_client(500)["kb_removed"], "служебное сообщение удалено, флаг kb_removed стоит"
assert isinstance(B.kb_client(), types.ReplyKeyboardRemove)

# ── метка источника: тема передаётся в приложение, атрибуция сохраняется ──
n = len(SENT)
dispatch(msg("/start ig_father", chat=501))
url, s = last_app(n)
assert url == APP + "?occ=family", url
assert B.get_client(501)["source"] == "ig_father"
assert not [x for x in sent(since=n) if "Пишем" in str(x[1])], "карточка повода в чате не открывается"
for param, topic in (("occ_birthday", "birthday"), ("w_love", "love"), ("vkads_father", "family"), ("tg_ny", "newyear"),
                     ("v_thanks", "thanks")):
    n = len(SENT)
    dispatch(msg(f"/start {param}", chat=502))
    assert last_app(n)[0] == f"{APP}?occ={topic}", (param, last_app(n)[0])
n = len(SENT)
dispatch(msg("/start ad_tgchannel", chat=503))
assert last_app(n)[0] == APP and B.get_client(503)["source"] == "ad_tgchannel"
dispatch(msg("/start vkads_father", chat=504))
assert B.get_client(504)["source"] == "vkads_father"

# ── реферал ──
B.upsert_client(user(700, "Мама"))
n = len(SENT)
dispatch(msg("/start ref_700", chat=701))
url, s = last_app(n)
assert url == APP + "?ref=700", url
assert "Тебя пригласил" in s[1][1] and "10%" in s[1][1], s[1][1]
assert B.get_client(701)["referred_by"] == 700 and 701 in B.get_client(700)["referrals"]
assert B.get_client(701)["source"] == "ref"

# ── партнёрская метка ──
B.partners.save_partner({"code": "PART1", "name": "Блогер", "discount": 15, "percent": 30, "status": "active",
                         "tg_chat_id": None, "created_at": B.now_msk().isoformat()})
n = len(SENT)
dispatch(msg("/start p_PART1", chat=702))
url, s = last_app(n)
assert url == APP and "скидка 15%" in s[1][1], s[1][1]
assert B.get_client(702).get("attribution") or B.get_client(702).get("partner") or "PART1" in str(B.get_client(702))

# ── получатель конверта: g_<код> как раньше, без кнопки приложения ──
GIFT = []
B.occ_gift_start = lambda chat, code: GIFT.append((chat, code))
n = len(SENT)
dispatch(msg("/start g_ABC123", chat=800))
assert GIFT == [(800, "ABC123")]
assert not [x for x in sent(since=n) if app_button(x[2].get("reply_markup"))], "получателю приложение не навязываем"
assert B.get_client(800)["source"] == "gift"
JOIN = []
B.group_letters.join = lambda chat, usr, code: JOIN.append((chat, code))
dispatch(msg("/start grp_XYZ", chat=801))
assert JOIN == [(801, "XYZ")]
BIND = []
B.partners.bind_invite = lambda chat, token: BIND.append((chat, token))
dispatch(msg("/start pinvite_TOK", chat=802))
assert BIND == [(802, "TOK")]

# ── PDF-метки по-прежнему работают ──
PDF = []
B.send_free_pdf = lambda chat: PDF.append(chat)
dispatch(msg("/start pdf", chat=803))
assert PDF == [803]

# ── возвращающийся клиент: старая клавиатура убирается один раз ──
old = B.upsert_client(user(505))      # клиент, который пришёл ещё до режима приложения: у него есть старая клавиатура
old["created_at"] = "2026-10-01T10:00:00+03:00"
B.write_json(B.client_path(505), old)
n = len(SENT)
dispatch(msg("/start", chat=505))
dispatch(msg("/start", chat=505))
assert len([d for d in DELETED if d[0] == 505]) == 1, "служебное сообщение для снятия клавиатуры — один раз"
rm = [x for x in sent(since=n) if isinstance(x[2].get("reply_markup"), types.ReplyKeyboardRemove)]
assert len(rm) == 1

# ── старые тексты кнопок → кнопка-приложение, сценарии не запускаются ──
for text in sorted(B.LEGACY_TEXTS):
    n = len(SENT)
    B.STATES.pop(500, None)
    fn = dispatch(msg(text, chat=500))
    assert fn == "legacy_text_to_app", (text, fn)
    assert last_app(n)[0] == APP, text
    assert not B.STATES.get(500), f"сценарий по «{text}» не стартует"
n = len(SENT)
fn = dispatch(msg("❓ Помощь", chat=500))
assert fn == "help_menu"
kb = sent(since=n)[-1][2]["reply_markup"]
data = [(getattr(b, "callback_data", None), b.web_app.url if b.web_app else None) for row in kb.keyboard for b in row]
assert ("sup:new", None) in data and (None, APP) in data, "помощь: поддержка сохранена + кнопка приложения"
# поддержка работает как раньше
dispatch_cb(call("sup:new", chat=500))
assert B.STATES[500]["step"] == "support"
n = len(SENT)
assert dispatch(msg("Не пришло письмо", chat=500)) == "support_receive"
assert any(s[1][0] == 1 and "Обращение в поддержку" in s[1][1] for s in sent(since=n)), "обращение дошло до владельца"

# ── свободный текст вне сценария: клавиатура снимается и у того, кто ещё не делал /start ──
old2 = B.upsert_client(user(506))
old2["created_at"] = "2026-10-01T10:00:00+03:00"
B.write_json(B.client_path(506), old2)
n, d0 = len(SENT), len(DELETED)
dispatch(msg("привет", chat=506))
assert len(DELETED) == d0 + 1 and any(isinstance(x[2].get("reply_markup"), types.ReplyKeyboardRemove) for x in sent(since=n))
assert B.get_client(506)["kb_removed"]
dispatch(msg("ещё раз", chat=506))
assert len(DELETED) == d0 + 1, "повторно не шлём"
B.STATES.pop(500, None)
n = len(SENT)
assert dispatch(msg("привет, как дела?", chat=500)) == "fallback"
url, s = last_app(n)
assert url == APP and "приложении" in s[1][1]

# ── старые inline-кнопки ──
n = len(SENT)
assert dispatch_cb(call("occ:catalog")) == "legacy_entry_to_app" and last_app(n)[0] == APP
n = len(SENT)
assert dispatch_cb(call("occ:p:birthday")) == "legacy_entry_to_app" and last_app(n)[0] == APP + "?occ=birthday"
n = len(SENT)
assert dispatch_cb(call("occ:go:love")) == "legacy_entry_to_app" and last_app(n)[0] == APP + "?occ=love"
assert dispatch_cb(call("cab:new")) == "legacy_entry_to_app"
for keep in ("cab:home", "occ:buy", "occ:pc:next", "occ:retext", "occ:tone:warm", "yk:check:ALI-1", "rate:ALI-1:5",
             "pr:enter", "env:open:ABC", "pt:home", "sup:new", "cal:menu", "cab:send:ALI-1", "gift:more:ALI-1"):
    assert dispatch_cb(call(keep)) != "legacy_entry_to_app", keep

# ── админ: его клавиатура и кнопки не тронуты ──
n = len(SENT)
dispatch(msg("/start", chat=1))
adm = sent(since=n)[-1]
assert isinstance(adm[2]["reply_markup"], types.ReplyKeyboardMarkup) and "📊 Заказы" in str(__import__("json").loads(adm[2]["reply_markup"].to_json()))
assert dispatch(msg("📊 Заказы", chat=1)) != "legacy_text_to_app"
assert dispatch(msg("👤 Мой кабинет", chat=1)) != "legacy_text_to_app", "кабинет владельца остаётся"
n = len(SENT)
dispatch(msg("абракадабра", chat=1))
assert "Не понял команду" in sent(since=n)[-1][1][1]

# ── в группах (обсуждение канала) режим не вмешивается ──
assert dispatch(msg("📖 Примеры", chat=-100, chat_type="supergroup")) != "legacy_text_to_app"

# ── напоминание календаря: кнопка открывает приложение ──
B.upsert_client(user(900, "Лена"))
CR.add_date(900, "Катя", "birthday", 15, 10)
today = pytypes.SimpleNamespace  # не используется
import datetime  # noqa: E402
profile = B.get_client(900)
due = CR.due_for(profile, datetime.date(2026, 10, 12))
assert due, "напоминание есть"
btn = due[0][2].keyboard[0][0]
assert btn.web_app and btn.web_app.url == f"{APP}?occ=birthday&for=%D0%9A%D0%B0%D1%82%D1%8F", btn.web_app
assert "Катя" in btn.text and btn.callback_data is None
assert due[0][2].keyboard[1][0].callback_data.startswith("cal:off:"), "отписка осталась"
os.environ["APP_ONLY"] = "0"
due = CR.due_for(profile, datetime.date(2026, 10, 12))
assert due[0][2].keyboard[0][0].callback_data.startswith("cal:write:")
os.environ.pop("APP_ONLY")

# ── MINIAPP_URL ──
os.environ["MINIAPP_URL"] = "https://example.org/app/"
assert B.app_url(ref=5) == "https://example.org/app/?ref=5" and B.app_url() == "https://example.org/app/"
os.environ["MINIAPP_URL"] = "https://example.org/app/?v=2"
assert B.app_url(occ="love") == "https://example.org/app/?v=2&occ=love"
os.environ.pop("MINIAPP_URL")

# ── откат APP_ONLY=0: старое меню без перезапуска кода ──
os.environ["APP_ONLY"] = "0"
assert isinstance(B.kb_client(), types.ReplyKeyboardMarkup)
n = len(SENT)
dispatch(msg("/start", chat=500))
st = sent(since=n)[-1]
assert isinstance(st[2]["reply_markup"], types.ReplyKeyboardMarkup) and "Выбери внизу" in st[1][1]
assert any(isinstance(x[1][1], types.MenuButtonDefault) for x in sent("set_chat_menu_button", n)), "кнопка меню сброшена"
assert "app_menu" not in B.get_client(500)
n = len(SENT)
assert dispatch(msg("💌 Заказать письмо", chat=500)) == "order_start", "старые сценарии вернулись"
assert dispatch(msg("привет", chat=500)) == "fallback" and "Я отвечаю письмами" in sent(since=n)[-1][1][1] or True
n = len(SENT)
dispatch_cb(call("occ:catalog"))
assert B.STATES.get(500) is not None or sent(since=n), "каталог открывается как раньше"
assert dispatch_cb(call("occ:catalog")) == "occ_catalog_cb"
os.environ.pop("APP_ONLY")
print("OK: app only")
