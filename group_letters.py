"""Групповое письмо «от всех нас»: организатор создаёт общее письмо и ссылку-приглашение, каждый
участник по ссылке отвечает на пару вопросов (без оплаты), Алиса собирает всё в одно письмо с открыткой,
дальше — обычный путь: превью → оплата организатором → отправка получателю в один тап.

Хранение: DATA_DIR/groups/<код>.json. Подключается из bot_best: group_letters.register(bot_best).
Архитектурно kind="family" — на будущее сюда же ляжет B2B-вариант («спасибо от команды»): другой набор
поводов и подписей, тот же поток участников и сборки.
"""
import os
import re
import secrets
import urllib.parse

GROUP_PRICE = 399       # выше одиночного письма (199–249 ₽): внутри работа нескольких людей
MAX_PARTICIPANTS = 12
MIN_TO_BUILD = 2
GROUP_OCCASIONS = ["family", "birthday", "thanks", "friend", "toast"]

B = None  # модуль bot_best (внедряется в register)


def _dir():
    d = os.path.join(B.DATA_DIR, "groups")
    os.makedirs(d, exist_ok=True)
    return d


def group_path(code):
    return os.path.join(_dir(), f"{code}.json")


def get_group(code):
    return B.read_json(group_path(code), None) if re.fullmatch(r"[A-Za-z0-9]{4,16}", code or "") else None


def save_group(g):
    B.write_json(group_path(g["code"]), g)


def all_groups():
    out = []
    for f in os.listdir(_dir()):
        if f.endswith(".json"):
            g = B.read_json(os.path.join(_dir(), f), None)
            if g and not g.get("is_test"):
                out.append(g)
    return out


def invite_link(code):
    return f"https://t.me/{B.BOT_USERNAME}?start=grp_{code}"


def questions(g):
    name = g["name"]
    return [
        ("memory", f"Одно воспоминание о {name}, которое тебя греет. Пара предложений — чем конкретнее, "
                   "тем живее получится письмо."),
        ("thanks", f"Что хочешь сказать {name}? За что благодарен(а), чего желаешь — своими словами."),
    ]


# ── организатор: создание ───────────────────────────────────────
def kb_occasions():
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    for key in GROUP_OCCASIONS:
        p = B.OCC.PRODUCTS[key]
        kb.add(B.types.InlineKeyboardButton(f"{p['icon']} {p['title']}", callback_data=f"grp:occ:{key}"))
    kb.add(B.types.InlineKeyboardButton("↩️ Назад", callback_data="occ:catalog"))
    return kb


MENU_TEXT = ("👥 <b>Письмо от всех нас</b>\n\n"
             "Ты собираешь тёплые слова у родных, друзей или коллег — Алиса складывает их в одно письмо "
             "с открыткой, где подписан каждый. Участникам платить не нужно: им достаточно ответить "
             "на два вопроса по ссылке.\n\nДля кого письмо?")


def new_group_cb(call):
    B.bot.answer_callback_query(call.id)
    B.safe_edit(call, MENU_TEXT, kb_occasions())


def open_menu(chat_id):
    """Вход по ссылке с сайта (?start=w_group): сразу выбор повода группового письма."""
    B.bot.send_message(chat_id, MENU_TEXT, parse_mode="HTML", reply_markup=kb_occasions())


def occasion_cb(call):
    chat_id = call.message.chat.id
    key = call.data.split(":", 2)[2]
    B.bot.answer_callback_query(call.id)
    if key not in GROUP_OCCASIONS:
        return
    B.STATES[chat_id] = {"step": "grp_name", "occasion": key}
    B.bot.send_message(chat_id, "Как зовут того, кому письмо? Напиши так, как вы к нему обращаетесь "
                                "(«мама», «Андрей Петрович», «Катя»).")


def organizer_text(message):
    chat_id = message.chat.id
    st = B.STATES[chat_id]
    text = (message.text or "").strip()
    if not 1 <= len(text) <= 60:
        B.bot.send_message(chat_id, "Напиши коротко, до 60 знаков.")
        return
    if st["step"] == "grp_name":
        st.update(step="grp_who", name=text)
        B.bot.send_message(chat_id, "Кто он(а) для вас всех? Одним-двумя словами: «наша мама», "
                                    "«коллега по отделу», «лучшая подруга».")
    elif st["step"] == "grp_who":
        st.update(step="grp_sign", who=text)
        B.bot.send_message(chat_id, "Как подписать письмо от всех? Например: «Твои дети», «Команда отдела», "
                                    "«Все твои». Имена каждого участника добавятся отдельно.")
    else:
        create_group(chat_id, message.from_user, st, text)


def create_group(chat_id, user, st, sign):
    B.STATES.pop(chat_id, None)
    profile = B.upsert_client(user)
    code = secrets.token_urlsafe(5).replace("-", "x").replace("_", "y")
    g = {"code": code, "kind": "family", "organizer": chat_id, "organizer_name": profile["name"],
         "occasion": st["occasion"], "name": st["name"], "who": st["who"], "sign": sign,
         "created_at": B.now_msk().isoformat(), "status": "open", "participants": [], "opened": [],
         "built": False, "order_id": None, "is_test": B.is_test_user(chat_id)}
    save_group(g)
    panel(chat_id, g)


def panel_text(g):
    n = len(g["participants"])
    names = ", ".join(p["sign"] for p in g["participants"]) or "пока никого"
    return (f"👥 <b>Общее письмо для {B.esc(g['name'])}</b>\n\n"
            f"Написали: <b>{n}</b> из {MAX_PARTICIPANTS} — {B.esc(names)}\n"
            f"Открыли ссылку: {len(g['opened'])}\n\n"
            "Отправь приглашение близким — каждому нужно ответить на два вопроса, это без оплаты. "
            f"⚠️ Не отправляй в чат, где есть {B.esc(g['name'])}: письмо задумано как сюрприз.\n\n"
            + ("Когда соберётся достаточно голосов — нажми «Собрать письмо»." if n >= MIN_TO_BUILD
               else f"Для сборки нужно хотя бы {MIN_TO_BUILD} части (можно добавить и свою)."))


def panel_markup(g):
    code = g["code"]
    share = ("https://t.me/share/url?url=" + urllib.parse.quote(invite_link(code))
             + "&text=" + urllib.parse.quote(f"Собираем общее письмо для {g['name']}. Напиши пару слов 🤍"))
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    kb.add(B.types.InlineKeyboardButton(
        "💌 Отправить приглашение",
        switch_inline_query_chosen_chat=B.types.SwitchInlineQueryChosenChat(
            query=f"grp_{code}", allow_user_chats=True, allow_group_chats=True)))
    kb.add(B.types.InlineKeyboardButton("📤 Ссылкой (WhatsApp, другие чаты)", url=share))
    kb.add(B.types.InlineKeyboardButton("✍️ Добавить мою часть", callback_data=f"grp:self:{code}"))
    if g["participants"]:
        kb.add(B.types.InlineKeyboardButton(
            "🔔 Напомнить остальным",
            switch_inline_query_chosen_chat=B.types.SwitchInlineQueryChosenChat(
                query=f"grpr_{code}", allow_user_chats=True, allow_group_chats=True)))
    kb.add(B.types.InlineKeyboardButton("🔄 Обновить", callback_data=f"grp:status:{code}"))
    if len(g["participants"]) >= MIN_TO_BUILD:
        kb.add(B.types.InlineKeyboardButton("✨ Собрать письмо", callback_data=f"grp:build:{code}"))
    return kb


def panel(chat_id, g):
    B.bot.send_message(chat_id, panel_text(g), parse_mode="HTML", reply_markup=panel_markup(g))


def _own_group(call):
    code = call.data.split(":", 2)[2]
    g = get_group(code)
    if not g or g["organizer"] != call.message.chat.id:
        B.bot.answer_callback_query(call.id, "Недоступно")
        return None
    return g


def status_cb(call):
    g = _own_group(call)
    if not g:
        return
    B.bot.answer_callback_query(call.id, "Обновлено")
    try:
        B.bot.edit_message_text(panel_text(g), call.message.chat.id, call.message.message_id,
                                parse_mode="HTML", reply_markup=panel_markup(g))
    except Exception:
        pass  # без изменений


def self_cb(call):
    g = _own_group(call)
    if not g:
        return
    B.bot.answer_callback_query(call.id)
    start_participant(call.message.chat.id, call.from_user, g)


# ── участник ────────────────────────────────────────────────────
def join(chat_id, user, code):
    """Точка входа по ссылке t.me/<бот>?start=grp_<код>."""
    g = get_group(code)
    if not g:
        B.bot.send_message(chat_id, "Эта ссылка не работает 😔 Попроси того, кто её прислал, отправить новую.",
                           reply_markup=B.kb_client())
        return
    if g["status"] != "open":
        B.bot.send_message(chat_id, "Письмо уже собрано и отправлено — спасибо, что хотели присоединиться 🤍",
                           reply_markup=B.kb_client())
        return
    if chat_id not in g["opened"]:
        g["opened"].append(chat_id)
        save_group(g)
    if len(g["participants"]) >= MAX_PARTICIPANTS and not any(p["chat_id"] == chat_id for p in g["participants"]):
        B.bot.send_message(chat_id, "Все места в этом письме уже заняты 🤍", reply_markup=B.kb_client())
        return
    start_participant(chat_id, user, g)


def start_participant(chat_id, user, g):
    profile = B.upsert_client(user)
    B.STATES[chat_id] = {"step": "grp_p", "code": g["code"], "idx": 0, "answers": {},
                         "default_sign": (profile["name"].split() or ["Я"])[0]}
    who = "Ты" if chat_id == g["organizer"] else f"{B.esc(g['organizer_name'])} собирает общее письмо"
    intro = (f"💌 {who} — для <b>{B.esc(g['name'])}</b> ({B.esc(g['who'])}).\n\n"
             "От тебя — два коротких ответа, это бесплатно и займёт пару минут. "
             "Алиса соберёт слова всех в одно письмо с открыткой." if chat_id != g["organizer"] else
             f"💌 Твоя часть письма для <b>{B.esc(g['name'])}</b> — два коротких ответа.")
    B.bot.send_message(chat_id, intro, parse_mode="HTML")
    ask(chat_id)


def ask(chat_id):
    st = B.STATES[chat_id]
    g = get_group(st["code"])
    qs = questions(g)
    if st["idx"] < len(qs):
        st["qkey"] = qs[st["idx"]][0]
        B.bot.send_message(chat_id, qs[st["idx"]][1])
        return
    st["qkey"] = "sign"
    kb = B.types.InlineKeyboardMarkup()
    kb.add(B.types.InlineKeyboardButton(f"Подписать «{st['default_sign']}»", callback_data="grp:sign:default"))
    B.bot.send_message(chat_id, f"Как подписать твою часть? Напиши имя так, как тебя зовёт {B.esc(g['name'])}.",
                       reply_markup=kb)


def participant_text(message):
    chat_id = message.chat.id
    st = B.STATES[chat_id]
    text = (message.text or "").strip()
    if st["qkey"] == "sign":
        if not 1 <= len(text) <= 40:
            B.bot.send_message(chat_id, "Подпись — коротко, до 40 знаков.")
            return
        finish_participant(chat_id, message.from_user, text)
        return
    if len(text) < 10:
        B.bot.send_message(chat_id, "Напиши чуть подробнее — хотя бы пару слов от себя 🙂")
        return
    if len(text) > 800:
        B.bot.send_message(chat_id, "Слишком длинно — сократи до 800 знаков, пожалуйста.")
        return
    st["answers"][st["qkey"]] = text
    st["idx"] += 1
    ask(chat_id)


def sign_default_cb(call):
    chat_id = call.message.chat.id
    st = B.STATES.get(chat_id) or {}
    B.bot.answer_callback_query(call.id)
    if st.get("step") != "grp_p" or st.get("qkey") != "sign":
        return
    finish_participant(chat_id, call.from_user, st["default_sign"])


def finish_participant(chat_id, user, sign):
    st = B.STATES.pop(chat_id)
    g = get_group(st["code"])
    if not g or g["status"] != "open":
        B.bot.send_message(chat_id, "Письмо уже собрано — спасибо тебе 🤍", reply_markup=B.kb_client())
        return
    part = {"chat_id": chat_id, "sign": sign, "memory": st["answers"]["memory"],
            "thanks": st["answers"]["thanks"], "at": B.now_msk().isoformat()}
    g["participants"] = [p for p in g["participants"] if p["chat_id"] != chat_id] + [part]
    save_group(g)
    n = len(g["participants"])
    if chat_id == g["organizer"]:
        B.bot.send_message(chat_id, "Твоя часть сохранена 🤍")
        panel(chat_id, g)
        return
    kb = B.types.InlineKeyboardMarkup()
    kb.add(B.types.InlineKeyboardButton("💌 Написать своё письмо", callback_data="occ:catalog"))
    B.bot.send_message(chat_id, f"Спасибо, {B.esc(sign)} 🤍 Твои слова войдут в письмо для {B.esc(g['name'])}. "
                                "Когда оно будет готово, организатор отправит его сам.\n\n"
                                "А если захочется написать своё письмо кому-то близкому — я рядом.",
                       parse_mode="HTML", reply_markup=kb)
    try:
        B.bot.send_message(g["organizer"], f"✍️ {B.esc(sign)} написал(а) свою часть. Уже: {n}.",
                           parse_mode="HTML")
    except Exception as exc:
        B.log.error("group notify organizer: %s", exc)


# ── сборка письма ───────────────────────────────────────────────
def contributions(g):
    return [{"sign": p["sign"], "memory": p["memory"], "thanks": p["thanks"]} for p in g["participants"]]


def generate(code, previous="", wish=""):
    g = get_group(code)
    p = B.OCC.PRODUCTS[g["occasion"]]
    default_title = p["card_title"].format(name=g["name"])[:40]
    return B.AI.generate_group_letter(p["brief"], g["name"], g["who"], contributions(g), g["sign"],
                                      default_title, previous=previous, wish=wish)


def build_cb(call):
    g = _own_group(call)
    if not g:
        return
    chat_id = call.message.chat.id
    if len(g["participants"]) < MIN_TO_BUILD:
        B.bot.answer_callback_query(call.id, f"Нужно хотя бы {MIN_TO_BUILD} части", show_alert=True)
        return
    if g["status"] != "open":
        B.bot.answer_callback_query(call.id, "Письмо уже оформлено", show_alert=True)
        return
    if not (B.AI and B.AI.available() and hasattr(B.AI, "generate_group_letter")):
        B.bot.answer_callback_query(call.id, "Помощник сейчас недоступен", show_alert=True)
        return
    B.bot.answer_callback_query(call.id, "Собираю…")
    B.bot.send_message(chat_id, "✍️ Складываю все слова в одно письмо… это займёт около минуты.")
    B.bot.send_chat_action(chat_id, "typing")
    res = generate(g["code"])
    if res["letter"].startswith("[ai]"):
        B.bot.send_message(chat_id, "Не получилось собрать письмо прямо сейчас 😔 Попробуй через пару минут.")
        B.log.error("group letter failed: %s", res["letter"])
        return
    g["built"] = True
    save_group(g)
    p = B.OCC.PRODUCTS[g["occasion"]]
    ref = B.cardbase_pick(chat_id, g["occasion"], [])
    state = {"step": "occ_paywall", "product": g["occasion"], "qa": [], "data": {}, "group": g["code"],
             "price": GROUP_PRICE, "letter": res["letter"], "card_title": res["card_title"],
             "card_line": res["card_line"], "sign": g["sign"], "tone": p.get("tone") or "tender",
             "name": g["name"], "paywall_at": B.now_msk(), "card_ref": ref, "card_seen": [ref] if ref else [],
             "retexts": 0, "wish_used": False,
             "default_title": p["card_title"].format(name=g["name"])[:40]}
    B.STATES[chat_id] = state
    B.occ_send_preview(chat_id, state)


# ── inline: приглашение и напоминание в один тап ────────────────
def inline_handler(query):
    q = query.query or ""
    reminder = q.startswith("grpr_")
    code = q.split("_", 1)[1].strip()
    g = get_group(code)
    if not g or g["organizer"] != query.from_user.id or g["status"] != "open":
        B.bot.answer_inline_query(query.id, [], cache_time=0, is_personal=True)
        return
    name = B.esc(g["name"])
    if reminder:
        text = (f"🤍 Напоминаю про общее письмо для {name} — пока не хватает твоей части. "
                "Два коротких ответа, это бесплатно:")
        title = "Напомнить о письме"
    else:
        text = (f"💌 Собираем общее письмо для {name} ({B.esc(g['who'])}) — каждому нужно написать пару тёплых "
                "слов. Два коротких ответа по ссылке, бесплатно. Только пока не показывай это "
                f"{name} — это сюрприз 🤍")
        title = f"Пригласить написать для {g['name']}"
    kb = B.types.InlineKeyboardMarkup()
    kb.add(B.types.InlineKeyboardButton("✍️ Написать свою часть", url=invite_link(g["code"])))
    result = B.types.InlineQueryResultArticle(
        id=f"{q}"[:60], title=title, description="Отправится от твоего имени",
        input_message_content=B.types.InputTextMessageContent(text, parse_mode="HTML"),
        reply_markup=kb)
    B.bot.answer_inline_query(query.id, [result], cache_time=0, is_personal=True)


# ── метрики ─────────────────────────────────────────────────────
def report():
    gs = all_groups()
    paid = {o.get("group_id") for o in B.all_orders() if o.get("group_id") and o.get("status") == "done"}
    return {
        "created": len(gs),
        "invited_opened": sum(len(g["opened"]) for g in gs),
        "written": sum(len(g["participants"]) for g in gs),
        "built": sum(1 for g in gs if g.get("built")),
        "ordered": sum(1 for g in gs if g.get("order_id")),
        "paid": sum(1 for g in gs if g["code"] in paid),
    }


def cmd_stats(message):
    if not B.admin_only(message):
        return
    r = report()
    B.bot.send_message(
        message.chat.id,
        "👥 <b>Групповые письма</b>\n\n"
        f"Создано: {r['created']}\nОткрыли ссылку (участники): {r['invited_opened']}\n"
        f"Написали свою часть: {r['written']}\nСобрано писем: {r['built']}\n"
        f"Дошли до оплаты: {r['ordered']}\nОплачено: {r['paid']}", parse_mode="HTML")


def reopen(code, order_id):
    """Заказ отменён до оплаты — группа снова принимает части."""
    g = get_group(code)
    if g and g.get("order_id") == order_id:
        g["status"] = "open"
        g["order_id"] = None
        save_group(g)


def mark_ordered(code, order_id):
    g = get_group(code)
    if g:
        g["status"] = "ordered"
        g["order_id"] = order_id
        save_group(g)


# ── подключение к боту ──────────────────────────────────────────
def register(bot_module):
    global B
    B = bot_module
    bot = B.bot
    _dir()
    bot.callback_query_handler(func=lambda c: c.data == "grp:new")(new_group_cb)
    bot.callback_query_handler(func=lambda c: c.data.startswith("grp:occ:"))(occasion_cb)
    bot.callback_query_handler(func=lambda c: c.data.startswith("grp:self:"))(self_cb)
    bot.callback_query_handler(func=lambda c: c.data.startswith("grp:status:"))(status_cb)
    bot.callback_query_handler(func=lambda c: c.data.startswith("grp:build:"))(build_cb)
    bot.callback_query_handler(func=lambda c: c.data == "grp:sign:default")(sign_default_cb)
    bot.message_handler(
        func=lambda m: B.STATES.get(m.chat.id, {}).get("step") in ("grp_name", "grp_who", "grp_sign")
        and m.content_type == "text" and not (m.text or "").startswith("/"))(organizer_text)
    bot.message_handler(
        func=lambda m: B.STATES.get(m.chat.id, {}).get("step") == "grp_p"
        and m.content_type == "text" and not (m.text or "").startswith("/"))(participant_text)
    bot.inline_handler(func=lambda q: (q.query or "").startswith(("grp_", "grpr_")))(inline_handler)
    bot.message_handler(commands=["groupstats"])(cmd_stats)
