"""Генератор «своей картинки» для открытки: клиент выбирает стиль, сюжет, палитру и добавки кнопками, ИИ рисует фон.

Безопасно по деньгам и по содержанию:
  * включается только если в окружении есть WAVESPEED_API_KEY (без него кнопки нет);
  * только после оплаты заказа, до 3 попыток на заказ, дневной лимит AI_CARD_DAILY_CAP (по умолчанию 60);
  * текст промпта клиент не вводит — только наши кнопки; на картинке запрещены буквы, лица и люди;
  * каждая генерация пишется в журнал DATA_DIR/ai_cards/ledger.jsonl (≈ 0,72 ₽ на flux-2-klein).
Готовый фон лежит в DATA_DIR/ai_cards/<заказ>_<n>.jpg, ссылка card_ref = «ai:<заказ>_<n>».
Подключается: ai_card.register(bot_best).
"""
import io
import json
import os
import re
import time
import urllib.request
from datetime import datetime

import requests
from PIL import Image

import postcards as PC

API = "https://api.wavespeed.ai/api/v3"
MODEL = "wavespeed-ai/flux-2-klein-4b/text-to-image"
COST_RUB = 0.72
MAX_TRIES = 3

STYLES = {
    "water": ("🎨 Акварель", "soft watercolor illustration, loose washes, visible paper texture"),
    "paper": ("✂️ Бумажная поделка", "whimsical 3D paper-craft quilling art, layered cut paper, tiny paper flowers, soft shadows"),
    "golden": ("🌅 Золотой свет", "photograph in rich warm golden-hour light, creamy bokeh background, highly detailed"),
    "retro": ("📖 Ретро-иллюстрация", "vintage 1960s storybook gouache illustration, textured paper"),
    "film": ("🎞 Уютное фото", "cozy analog film photograph, soft natural window light, shallow depth of field"),
}
PALETTES = {
    "warm": ("Тёплая", "warm cream, honey and gold palette"),
    "rose": ("Нежно-розовая", "blush pink, cream and sage green palette"),
    "rainbow": ("Радужная", "joyful rainbow pastel palette"),
    "sage": ("Зелёная", "sage green, olive and cream palette"),
    "deep": ("Глубокая", "deep burgundy, emerald and gold palette"),
}
EXTRAS = {
    "candles": ("🕯 Свечи", "glowing candles"),
    "butterflies": ("🦋 Бабочки", "delicate butterflies"),
    "ribbon": ("🎀 Лента", "a satin ribbon"),
    "dried": ("🌾 Сухоцветы", "dried flowers and wheat ears"),
    "sparkle": ("✨ Блики", "golden sparkles and bokeh lights"),
    "snow": ("❄️ Снег", "gently falling snow"),
}
MAX_EXTRAS = 2
SUBJECTS = {
    "birthday": {"cake": ("🎂 Торт со свечами", "a birthday cake with candles"),
                 "bouquet": ("💐 Пышный букет", "a lush bouquet of peonies and garden roses in a vase"),
                 "gift": ("🎁 Подарок", "a gift box with a ribbon bow"),
                 "balloons": ("🎈 Воздушные шары", "pastel balloons floating"),
                 "tea": ("☕ Чай и цветы", "a tea cup and flowers on a table")},
    "love": {"roses": ("🌹 Розы", "a romantic bouquet of roses"),
             "letter": ("💌 Письмо с печатью", "a vintage love letter with a wax seal"),
             "dinner": ("🕯 Ужин при свечах", "a candle-lit table set for two"),
             "cups": ("☕ Две чашки", "two cups on a cozy windowsill"),
             "heart": ("💞 Сердце из цветов", "a heart shape made of flowers")},
    "friend": {"picnic": ("🧺 Пикник", "a picnic basket with berries and pastries on a blanket"),
               "cups": ("☕ Две кружки", "two mugs and cookies on a windowsill"),
               "wild": ("🌼 Полевые цветы", "a bouquet of wildflowers"),
               "cake": ("🍰 Десерт", "slices of cake with berries"),
               "balloons": ("🎈 Шары", "pastel balloons")},
    "family": {"wheat": ("🌾 Букет с колосками", "a bouquet of daisies and wheat ears wrapped in craft paper"),
               "table": ("🍞 Домашний стол", "a family table with fresh bread, a teapot and flowers"),
               "armchair": ("🛋 Уютное кресло", "a cozy armchair with a knitted plaid and a cup of tea"),
               "frame": ("🖼 Рамка и цветы", "an empty vintage photo frame with flowers"),
               "garden": ("🌷 Сад", "a blooming garden with a wooden bench")},
    "thanks": {"bouquet": ("💐 Благодарный букет", "a lush bouquet of soft pink and white flowers with eucalyptus"),
               "tea": ("☕ Чай и сладости", "a tea cup with honey and cookies"),
               "tag": ("🏷 Подарочная бирка", "a kraft gift tag with twine and dried flowers"),
               "basket": ("🧺 Корзина", "a wicker basket of flowers and fruit"),
               "olive": ("🌿 Оливковая ветвь", "an olive branch and a white flower")},
    "sorry": {"rose": ("🤍 Белая роза", "a single white rose in a glass vase"),
              "rain": ("🌧 Дождь за окном", "a rainy window sill with a small flower"),
              "olive": ("🌿 Оливковая ветвь", "an olive branch"),
              "dove": ("🕊 Белый голубь", "a white dove silhouette in soft light"),
              "tea": ("☕ Тихий вечер", "a quiet evening cup of tea with a candle")},
    "support": {"blanket": ("🧶 Плед и чай", "a chunky knit blanket and a steaming cup of tea"),
                "candle": ("🕯 Свеча", "a lit candle in a calm room"),
                "window": ("🌧 Окно и дождь", "a rainy window with a warm lamp glow"),
                "lavender": ("💜 Лаванда", "a small bouquet of lavender"),
                "sunrise": ("🌅 Рассвет", "a gentle sunrise over a field")},
    "toast": {"glasses": ("🥂 Бокалы", "two crystal glasses of sparkling champagne"),
              "table": ("🍾 Праздничный стол", "a festive table with candles and flowers"),
              "fireworks": ("🎆 Огни", "festive golden lights and sparklers"),
              "citrus": ("🍊 Цитрусы", "sparkling wine with citrus slices and rosemary"),
              "deco": ("✨ Арт-деко", "champagne glasses on an art deco background of fan arches")},
    "newyear": {"tree": ("🎄 Ёлка", "a decorated christmas tree with warm string lights"),
                "window": ("🪟 Зимнее окно", "a frosty window with garland lights"),
                "table": ("🍊 Новогодний стол", "a cozy table with mandarins, candles and cinnamon"),
                "gifts": ("🎁 Подарки", "wrapped gifts with ribbons under a tree"),
                "cottage": ("🏡 Домик", "a snowy cottage with glowing windows")},
    "santa": {"cottage": ("🏡 Домик в снегу", "a cozy wooden cottage with glowing windows among snowy firs"),
              "letter": ("✉️ Письмо", "a letter with a red wax seal and pine branches"),
              "lantern": ("🏮 Фонарь", "a glowing lantern in the snow"),
              "sleigh": ("🛷 Следы саней", "a sleigh track in a snowy forest under a starry sky"),
              "mailbox": ("📮 Почтовый ящик", "a snowy mailbox with a red envelope")},
}
RULES = ("Beautiful greeting-card background, subject in the upper two thirds, calm soft area in the lower third. "
         "Absolutely no text, no letters, no numbers, no watermark, no people, no faces.")

B = None
WIZ = {}  # chat_id -> выбор клиента (на время мастера; потеря при рестарте не страшна)


# ── ключ, лимиты, журнал ─────────────────────────────────────────
def enabled():
    return bool(os.getenv("WAVESPEED_API_KEY", "").strip())


def _dir():
    d = os.path.join(B.DATA_DIR, "ai_cards")
    os.makedirs(d, exist_ok=True)
    return d


def _ledger_path():
    return os.path.join(_dir(), "ledger.jsonl")


def used_for_order(order_id):
    return len([f for f in os.listdir(_dir()) if f.startswith(f"{order_id}_") and f.endswith(".jpg")])


def today_count():
    day = datetime.now().date().isoformat()
    try:
        with open(_ledger_path(), encoding="utf-8") as f:
            return sum(1 for line in f if f'"ts": "{day}' in line)
    except OSError:
        return 0


def daily_cap():
    try:
        return int(os.getenv("AI_CARD_DAILY_CAP", "60"))
    except ValueError:
        return 60


def log_gen(order_id, rub=COST_RUB):
    with open(_ledger_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "order": order_id, "rub": rub},
                           ensure_ascii=False) + "\n")


def build_prompt(sel):
    subject = SUBJECTS[sel["occ"]][sel["subject"]][1]
    parts = [subject, STYLES[sel["style"]][1], PALETTES[sel["pal"]][1]]
    parts += [EXTRAS[k][1] for k in sel.get("extras", []) if k in EXTRAS]
    return ", ".join(parts) + ". " + RULES


# ── генерация (в тестах подменяется) ─────────────────────────────
def generate_image(prompt):
    """Возвращает JPEG 1080×806 (bytes). Ключ — из окружения, нигде не печатается."""
    key = os.environ["WAVESPEED_API_KEY"].strip()
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    r = requests.post(f"{API}/{MODEL}", headers=headers, timeout=60,
                      json={"prompt": prompt, "size": "1280*960", "output_format": "jpeg"})
    r.raise_for_status()
    task = r.json()["data"]["id"]
    deadline = time.time() + 150
    while time.time() < deadline:
        res = requests.get(f"{API}/predictions/{task}/result", headers=headers, timeout=60).json()["data"]
        if res["status"] == "completed":
            with urllib.request.urlopen(res["outputs"][0], timeout=120) as resp:
                raw = resp.read()
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = 1080, 806
            scale = max(w / im.width, h / im.height)
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
            x, y = (im.width - w) // 2, (im.height - h) // 2
            out = io.BytesIO()
            im.crop((x, y, x + w, y + h)).save(out, "JPEG", quality=88, optimize=True)
            return out.getvalue()
        if res["status"] in ("failed", "cancelled", "timeout", "deleted"):
            raise RuntimeError(f"генерация: {res['status']}")
        time.sleep(3)
    raise TimeoutError("генерация не уложилась в 150 с")


# ── интерфейс ────────────────────────────────────────────────────
def _kb():
    return B.types.InlineKeyboardMarkup(row_width=2)


def _btn(text, data):
    return B.types.InlineKeyboardButton(text, callback_data=data)


def offer(chat_id, order):
    """После оплаты: предложить нарисовать свою картинку (если функция включена и повод поддержан)."""
    if not enabled() or order.get("product") not in SUBJECTS:
        return
    kb = _kb()
    kb.add(_btn("🎨 Нарисовать свою картинку", f"ai:start:{order['order_id']}"))
    kb.add(_btn("Оставить эту", "ai:no"))
    B.bot.send_message(chat_id, f"🎨 Хочешь свою картинку для открытки? Выбери стиль и сюжет кнопками — я нарисую. "
                                f"До {MAX_TRIES} попыток, это бесплатно.", reply_markup=kb)


def _order(call, order_id):
    order = B.get_order(order_id)
    if not order or order.get("chat_id") != call.message.chat.id or order.get("status") != "done" \
            or order.get("product") not in SUBJECTS:
        B.bot.answer_callback_query(call.id, "Недоступно", show_alert=True)
        return None
    return order


def _sel(chat_id, order):
    sel = WIZ.get(chat_id)
    if not sel or sel["order"] != order["order_id"]:
        sel = {"order": order["order_id"], "occ": order["product"], "style": None, "subject": None, "pal": None,
               "extras": []}
        WIZ[chat_id] = sel
    return sel


def _edit(call, text, markup):
    try:
        B.bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup)
    except Exception:
        B.bot.send_message(call.message.chat.id, text, reply_markup=markup)


def style_menu(order_id):
    kb = _kb()
    for key, (label, _) in STYLES.items():
        kb.add(_btn(label, f"ai:st:{order_id}:{key}"))
    return kb


def subject_menu(order_id, occ):
    kb = _kb()
    for key, (label, _) in SUBJECTS[occ].items():
        kb.add(_btn(label, f"ai:sub:{order_id}:{key}"))
    kb.add(_btn("⬅️ Стиль", f"ai:start:{order_id}"))
    return kb


def pal_menu(order_id):
    kb = _kb()
    kb.add(*[_btn(label, f"ai:pal:{order_id}:{key}") for key, (label, _) in PALETTES.items()])
    return kb


def extras_menu(order_id, sel):
    kb = _kb()
    kb.add(*[_btn(("✓ " if key in sel["extras"] else "") + label, f"ai:ex:{order_id}:{key}")
             for key, (label, _) in EXTRAS.items()])
    kb.add(_btn(f"✨ Нарисовать ({MAX_TRIES - used_for_order(order_id)} попыт.)", f"ai:go:{order_id}"))
    return kb


def start_cb(call):
    order = _order(call, call.data.split(":")[2])
    if not order:
        return
    B.bot.answer_callback_query(call.id)
    WIZ.pop(call.message.chat.id, None)
    _sel(call.message.chat.id, order)
    _edit(call, "🎨 Шаг 1 из 4. Выбери стиль:", style_menu(order["order_id"]))


def pick_cb(call):
    _, kind, order_id, value = call.data.split(":")
    order = _order(call, order_id)
    if not order:
        return
    sel = _sel(call.message.chat.id, order)
    B.bot.answer_callback_query(call.id)
    if kind == "st" and value in STYLES:
        sel["style"] = value
        _edit(call, "🎨 Шаг 2 из 4. Что нарисовать?", subject_menu(order_id, sel["occ"]))
    elif kind == "sub" and value in SUBJECTS[sel["occ"]]:
        sel["subject"] = value
        _edit(call, "🎨 Шаг 3 из 4. Палитра:", pal_menu(order_id))
    elif kind == "pal" and value in PALETTES:
        sel["pal"] = value
        _edit(call, f"🎨 Шаг 4 из 4. Добавки (до {MAX_EXTRAS}) и можно рисовать:", extras_menu(order_id, sel))
    elif kind == "ex" and value in EXTRAS:
        if value in sel["extras"]:
            sel["extras"].remove(value)
        elif len(sel["extras"]) >= MAX_EXTRAS:
            B.bot.answer_callback_query(call.id, f"Не больше {MAX_EXTRAS} добавок — сними одну", show_alert=True)
            return
        else:
            sel["extras"].append(value)
        try:
            B.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id,
                                            reply_markup=extras_menu(order_id, sel))
        except Exception:
            pass


def result_markup(order_id, n):
    left = MAX_TRIES - used_for_order(order_id)
    kb = _kb()
    kb.add(_btn("✅ Эта подходит", f"ai:use:{order_id}:{n}"))
    if left > 0:
        kb.add(_btn(f"🔁 Ещё вариант (осталось {left})", f"ai:go:{order_id}"))
    kb.add(_btn("✏️ Изменить выбор", f"ai:start:{order_id}"))
    return kb


def go_cb(call):
    order = _order(call, call.data.split(":")[2])
    if not order:
        return
    chat_id, order_id = call.message.chat.id, order["order_id"]
    sel = _sel(chat_id, order)
    if not (sel["style"] and sel["subject"] and sel["pal"]):
        B.bot.answer_callback_query(call.id, "Сначала выбери стиль, сюжет и палитру", show_alert=True)
        return
    if used_for_order(order_id) >= MAX_TRIES:
        B.bot.answer_callback_query(call.id, "Попытки закончились — выбери одну из готовых", show_alert=True)
        return
    if today_count() >= daily_cap():
        B.bot.answer_callback_query(call.id, "Сегодня рисую слишком много — попробуй завтра 🤍", show_alert=True)
        return
    B.bot.answer_callback_query(call.id, "Рисую… около 20 секунд")
    B.bot.send_chat_action(chat_id, "upload_photo")
    try:
        img = generate_image(build_prompt(sel))
    except Exception as exc:
        B.log.error("ai card generate %s: %s", order_id, exc)
        B.bot.send_message(chat_id, "Не получилось нарисовать, попытка не потрачена. Попробуй ещё раз чуть позже.")
        return
    n = used_for_order(order_id) + 1
    with open(os.path.join(_dir(), f"{order_id}_{n}.jpg"), "wb") as f:
        f.write(img)
    log_gen(order_id)
    B.cb_event(chat_id, "ai_gen", order["product"], f"ai:{order_id}_{n}")
    ref = f"ai:{order_id}_{n}"
    card = PC.render(order["product"], order.get("card_title", ""), order.get("card_line", ""),
                     order.get("sign", ""), card_ref=ref)
    B.bot.send_photo(chat_id, card, caption=f"Вариант {n} из {MAX_TRIES}", reply_markup=result_markup(order_id, n))


def use_cb(call):
    _, _, order_id, n = call.data.split(":")
    order = _order(call, order_id)
    if not order or not n.isdigit() or not os.path.exists(os.path.join(_dir(), f"{order_id}_{n}.jpg")):
        return
    B.bot.answer_callback_query(call.id, "Готово ✅")
    ref = f"ai:{order_id}_{n}"
    order["card_ref"] = ref
    try:
        msg = B.bot.send_photo(call.message.chat.id, B.occ_postcard(order), caption="🖼 Твоя открытка — с твоей картинкой")
        order["card_file_id"] = msg.photo[-1].file_id  # inline-отправка получателю возьмёт эту
        order.pop("card_anim_file_id", None)
    except Exception as exc:
        B.log.error("ai card use %s: %s", order_id, exc)
    B.save_order(order)
    B.cb_event(call.message.chat.id, "ai_use", order["product"], ref)
    try:
        B.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass
    WIZ.pop(call.message.chat.id, None)


def no_cb(call):
    B.bot.answer_callback_query(call.id)
    try:
        B.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass


def report():
    """Для /cardstats: сколько картинок нарисовано и ≈ сколько это стоило."""
    n, rub = 0, 0.0
    try:
        with open(_ledger_path(), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                n += 1
                rub += e.get("rub", COST_RUB)
    except OSError:
        pass
    return {"enabled": enabled(), "generated": n, "rub": round(rub, 1)}


def register(bot_module):
    global B
    B = bot_module
    PC.AI_DIR = _dir()
    cb = B.bot.callback_query_handler
    cb(func=lambda c: c.data.startswith("ai:start:"))(start_cb)
    cb(func=lambda c: re.match(r"^ai:(st|sub|pal|ex):", c.data) is not None)(pick_cb)
    cb(func=lambda c: c.data.startswith("ai:go:"))(go_cb)
    cb(func=lambda c: c.data.startswith("ai:use:"))(use_cb)
    cb(func=lambda c: c.data == "ai:no")(no_cb)
