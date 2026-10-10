"""Конструктор открытки в превью: клиент сам собирает картинку из наших заготовок и видит результат сразу.

Меню живёт на клавиатуре под фото превью («🎨 Настроить открытку»): фон из библиотеки повода,
палитра, до трёх предметов реквизита Алисы, раскладка, рамка. Ничего не стоит: всё складывается из готовых
файлов (postcards.compose_custom). Итог кодируется ссылкой card_ref вида «<фон>!<палитра>.<реквизит>.<раскладка>.<рамка>»,
поэтому оплаченная открытка получается точно такой же, как в превью. Подключается: constructor.register(bot_best).
"""
import io
import random

import cardanim
import cardbase
import postcards as PC

PROP_LABELS = {
    "dried_bouquet": "💐 Сухоцветы", "olive_branch": "🌿 Оливковая ветка", "ribbon_bow": "🎀 Бант",
    "envelope_cream": "✉️ Конверт", "envelope_kraft": "📜 Крафт-конверт", "gift_tag": "🏷 Бирка",
    "lace_doily": "🕸 Кружево", "stamps_key": "🔑 Марки и ключ", "tea_cup": "☕ Чашка", "postcard": "💌 Открытка",
    # вторая партия реквизита (10.10.2026)
    "quill_pen": "🪶 Перо", "candle_brass": "🕯 Свеча", "pressed_daisies": "🌼 Ромашки", "eucalyptus_sprig": "🌿 Эвкалипт",
    "wheat_ears": "🌾 Колосья", "lavender_sprig": "💜 Лаванда", "dried_rose": "🌹 Сухая роза", "heart_locket": "🤍 Медальон",
    "pine_branch": "🌲 Хвоя", "ornament_ball": "🎄 Ёлочный шар", "snowflake_lace": "❄️ Кружевная снежинка",
    "cinnamon_orange": "🍊 Корица и апельсин", "cake_slice": "🍰 Кусочек торта", "teapot_cream": "🫖 Чайник",
    "pocket_watch": "⏱ Карманные часы", "old_book": "📖 Старая книга", "wax_seal_stamp": "🔴 Сургучная печать",
    "ivy_sprig": "🍃 Плющ", "pearl_string": "⚪ Жемчуг", "gift_box_small": "🎁 Подарок",
}


def prop_label(name):
    """Подпись предмета: из словаря, иначе автоподпись из имени файла (tea_cup → «🖼 Tea cup»)."""
    return PROP_LABELS.get(name) or "🖼 " + name.replace("_", " ").capitalize()


def prop_labels():
    """Все предметы из assets/props/*.png (новые файлы подхватываются без правки кода)."""
    return {name: prop_label(name) for name in PC.available_props()}


PAL_LABELS = {"none": "Как есть", "warm": "Тёплая", "soft": "Прохладная", "sepia": "Сепия", "rose": "Розовая",
              "sage": "Шалфей"}
LAY_LABELS = {"corners": "По углам", "left": "Слева", "right": "Справа", "top": "Сверху", "bottom": "Снизу"}
FRAME_LABELS = {"none": "Без рамки", "thin": "Тонкая", "double": "Двойная"}
ANIM_LABELS = {"none": "Без анимации", "sparkle": "✨ Искры", "petals": "🌸 Лепестки", "snow": "❄️ Снег",
               "bokeh": "💫 Боке"}
STEPS = ("occ_paywall", "occ_wish", "promo_enter")

B = None


def _kb():
    return B.types.InlineKeyboardMarkup(row_width=2)


def _btn(text, data):
    return B.types.InlineKeyboardButton(text, callback_data=data)


def default_cc(state):
    base = PC.split_ref(state.get("card_ref") or PC.first_ref(state["product"]))[0]
    return {"bg": base, "pal": "none", "props": [], "lay": "corners", "frame": "none", "anim": "none"}


def apply(state):
    cc = state["cc"]
    state["card_ref"] = PC.encode_spec(cc["bg"], cc["pal"], cc["props"], cc["lay"], cc["frame"], cc.get("anim", "none"))


def main_menu(state):
    cc = state["cc"]
    kb = _kb()
    kb.add(_btn(f"🖼 Фон", "cc:bg"), _btn(f"🎨 Палитра: {PAL_LABELS[cc['pal']]}", "cc:palm"))
    n = len(cc["props"])
    kb.add(_btn(f"🌿 Реквизит ({n}/{PC.MAX_PROPS})", "cc:propm"), _btn(f"📐 {LAY_LABELS[cc['lay']]}", "cc:laym"))
    kb.add(_btn(f"🪞 {FRAME_LABELS[cc['frame']]}", "cc:frmm"), _btn("🎲 Случайно", "cc:rand"))
    kb.add(_btn(f"✨ {ANIM_LABELS[cc.get('anim', 'none')]}" if cc.get("anim", "none") != "none" else "✨ Анимация", "cc:animm"),
           _btn("↩️ Сбросить", "cc:reset"))
    if cc.get("anim", "none") != "none":
        kb.add(_btn("▶️ Посмотреть анимацию", "cc:play"))
    kb.add(_btn("✅ Готово", "cc:done"))
    return kb


def bg_menu(state):
    cc = state["cc"]
    ids = cardbase.active_ids(state["product"]) or [cc["bg"]]
    i = ids.index(cc["bg"]) + 1 if cc["bg"] in ids else 1
    kb = _kb()
    kb.add(_btn("◀", "cc:bgp"), _btn(f"Фон {i} из {len(ids)}", "cc:noop"), _btn("▶", "cc:bgn"))
    kb.add(_btn("⬅️ Назад", "cc:menu"))
    return kb


def options_menu(current, labels, prefix):
    kb = _kb()
    kb.add(*[_btn(("✓ " if key == current else "") + text, f"cc:{prefix}:{key}") for key, text in labels.items()])
    kb.add(_btn("⬅️ Назад", "cc:menu"))
    return kb


def props_menu(state):
    chosen = state["cc"]["props"]
    kb = _kb()
    kb.add(*[_btn(("✓ " if key in chosen else "") + text, f"cc:prop:{key}") for key, text in prop_labels().items()])
    kb.add(_btn("⬅️ Назад", "cc:menu"))
    return kb


def show(call, state, markup):
    """Перерисовывает картинку и клавиатуру под ней одним действием."""
    chat_id = call.message.chat.id
    state["photo_msg"] = call.message.message_id
    try:
        B.bot.edit_message_media(
            B.types.InputMediaPhoto(B.occ_preview_image(state), caption=B.PHOTO_CAPTION),
            chat_id, call.message.message_id, reply_markup=markup)
    except Exception as exc:
        B.log.debug("constructor refresh: %s", exc)  # без изменений или Telegram отклонил повтор


def _state(call):
    state = B.STATES.get(call.message.chat.id) or {}
    if state.get("step") not in STEPS or "product" not in state:
        B.bot.answer_callback_query(call.id, "Заказ устарел — начни заново", show_alert=True)
        return None
    return state


def open_cb(call):
    state = _state(call)
    if not state:
        return
    state["cc"] = default_cc(state)
    apply(state)
    B.bot.answer_callback_query(call.id, "Собери открытку по-своему 🎨")
    B.cb_event(call.message.chat.id, "cc_open", state["product"], state["card_ref"])
    show(call, state, main_menu(state))


def menu_cb(call):
    state = _state(call)
    if not state or "cc" not in state:
        return
    B.bot.answer_callback_query(call.id)
    kind = call.data.split(":", 1)[1]
    cc = state["cc"]
    if kind == "menu":
        markup = main_menu(state)
    elif kind == "bg":
        markup = bg_menu(state)
    elif kind == "palm":
        markup = options_menu(cc["pal"], PAL_LABELS, "pal")
    elif kind == "propm":
        markup = props_menu(state)
    elif kind == "laym":
        markup = options_menu(cc["lay"], LAY_LABELS, "lay")
    elif kind == "frmm":
        markup = options_menu(cc["frame"], FRAME_LABELS, "frm")
    elif kind == "animm":
        markup = options_menu(cc.get("anim", "none"), ANIM_LABELS, "anim")
    else:
        return
    try:
        B.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=markup)
    except Exception as exc:
        B.log.debug("constructor menu: %s", exc)


def change_cb(call):
    state = _state(call)
    if not state or "cc" not in state:
        return
    cc = state["cc"]
    parts = call.data.split(":")
    kind, value = parts[1], parts[2] if len(parts) > 2 else None
    markup = None
    if kind in ("bgp", "bgn"):
        ids = cardbase.active_ids(state["product"]) or [cc["bg"]]
        i = ids.index(cc["bg"]) if cc["bg"] in ids else 0
        cc["bg"] = ids[(i + (1 if kind == "bgn" else -1)) % len(ids)]
        markup = bg_menu(state)
    elif kind == "pal" and value in PAL_LABELS:
        cc["pal"] = value
        markup = options_menu(cc["pal"], PAL_LABELS, "pal")
    elif kind == "lay" and value in LAY_LABELS:
        cc["lay"] = value
        markup = options_menu(cc["lay"], LAY_LABELS, "lay")
    elif kind == "frm" and value in FRAME_LABELS:
        cc["frame"] = value
        markup = options_menu(cc["frame"], FRAME_LABELS, "frm")
    elif kind == "anim" and value in ANIM_LABELS:
        cc["anim"] = value
        markup = options_menu(cc["anim"], ANIM_LABELS, "anim")
    elif kind == "prop" and value in prop_labels():
        if value in cc["props"]:
            cc["props"].remove(value)
        elif len(cc["props"]) >= PC.MAX_PROPS:
            B.bot.answer_callback_query(call.id, f"Не больше {PC.MAX_PROPS} предметов — сними один", show_alert=True)
            return
        else:
            cc["props"].append(value)
        markup = props_menu(state)
    else:
        B.bot.answer_callback_query(call.id)
        return
    B.bot.answer_callback_query(call.id)
    apply(state)
    show(call, state, markup)


def random_cb(call):
    state = _state(call)
    if not state or "cc" not in state:
        return
    rnd = random.Random()
    cc = state["cc"]
    cc["pal"] = rnd.choice(list(PAL_LABELS))
    labels = list(prop_labels())
    cc["props"] = rnd.sample(labels, min(len(labels), rnd.choice([1, 2, 3])))
    cc["lay"] = rnd.choice(list(LAY_LABELS))
    cc["frame"] = rnd.choice(list(FRAME_LABELS))
    cc["anim"] = rnd.choice(list(ANIM_LABELS))
    ids = cardbase.active_ids(state["product"])
    if ids:
        cc["bg"] = rnd.choice(ids)
    B.bot.answer_callback_query(call.id, "🎲")
    apply(state)
    show(call, state, main_menu(state))


def reset_cb(call):
    state = _state(call)
    if not state or "cc" not in state:
        return
    state["cc"] = {**default_cc(state), "bg": state["cc"]["bg"]}
    B.bot.answer_callback_query(call.id, "Сбросила")
    apply(state)
    show(call, state, main_menu(state))


def done_cb(call):
    state = _state(call)
    if not state:
        return
    B.bot.answer_callback_query(call.id, "Готово ✅")
    B.cb_event(call.message.chat.id, "cc_done", state["product"], state.get("card_ref"))
    try:
        B.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                        reply_markup=B.occ_photo_markup())
    except Exception as exc:
        B.log.debug("constructor done: %s", exc)


def play_cb(call):
    """Показывает, как будет двигаться открытка: GIF с водяным знаком «превью» отдельным сообщением."""
    state = _state(call)
    if not state or "cc" not in state or state["cc"].get("anim", "none") == "none":
        B.bot.answer_callback_query(call.id, "Выбери анимацию", show_alert=True)
        return
    B.bot.answer_callback_query(call.id, "Готовлю анимацию…")
    B.bot.send_chat_action(call.message.chat.id, "upload_video")
    try:
        gif = io.BytesIO(cardanim.animate(state["product"], state["card_title"], state["card_line"],
                                          state["sign"], state["card_ref"], preview=True))
        gif.name = "preview.gif"
        B.bot.send_animation(call.message.chat.id, gif,
                             caption="Так будет двигаться твоя открытка ✨ После оплаты — без надписи «превью».")
    except Exception as exc:
        B.log.error("constructor play: %s", exc)
        B.bot.send_message(call.message.chat.id, "Не получилось показать анимацию, попробуй ещё раз.")


def noop_cb(call):
    B.bot.answer_callback_query(call.id)


def register(bot_module):
    global B
    B = bot_module
    cb = B.bot.callback_query_handler
    cb(func=lambda c: c.data == "cc:open")(open_cb)
    cb(func=lambda c: c.data in ("cc:menu", "cc:bg", "cc:palm", "cc:propm", "cc:laym", "cc:frmm", "cc:animm"))(menu_cb)
    cb(func=lambda c: c.data in ("cc:bgp", "cc:bgn") or c.data.split(":")[1:2] in (["pal"], ["lay"], ["frm"], ["prop"], ["anim"])
       and c.data.startswith("cc:"))(change_cb)
    cb(func=lambda c: c.data == "cc:rand")(random_cb)
    cb(func=lambda c: c.data == "cc:reset")(reset_cb)
    cb(func=lambda c: c.data == "cc:done")(done_cb)
    cb(func=lambda c: c.data == "cc:play")(play_cb)
    cb(func=lambda c: c.data == "cc:noop")(noop_cb)
