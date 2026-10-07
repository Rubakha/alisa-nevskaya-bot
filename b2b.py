"""Корпоративная линейка к Новому году (B2B): Telegram-слой.

Путь заказчика: ?start=b2b или кнопка «🏢 Для компаний» → пакет → анкета (компания, тон, подпись, логотип)
→ список людей (CSV/XLSX или текстом) → 2–3 превью → оплата (карта/СБП для малых сумм или счёт для юрлица
с подтверждением админа командой /b2b_paid) → пакетная генерация (b2b_core.run_batch) → ZIP с открытками
и таблица «имя → текст». Бот никому, кроме заказчика, ничего не отправляет.

Подключается из bot_best: b2b.register(bot_best) и два крючка — /start b2b и fulfill_order → on_paid.
Хранение и генерация без Telegram — в b2b_core, промпты — в b2b_ai, цены — в b2b_config.
"""
import io
import os
import threading
import time
from datetime import datetime, timedelta

import b2b_ai
import b2b_config as C
import b2b_core as K

B = None  # модуль bot_best (внедряется в register)
RUNNING = set()
_RUN_LOCK = threading.Lock()

ANKETA_STEPS = ("b2b_logo", "b2b_company", "b2b_sign", "b2b_list", "b2b_wish", "b2b_req", "b2b_bd_contact")
KIND_TITLE = {k: f"{v['icon']} {v['title']}" for k, v in C.PACKAGES.items()}


def rub(n):
    return f"{n:,}".replace(",", " ") + " ₽"


# ── общее ───────────────────────────────────────────────────────
def is_open(chat_id):
    """Продажи идут с C.SALES_START; админ и тестовые аккаунты могут заходить раньше."""
    if chat_id == B.ADMIN_ID or B.is_test_user(chat_id):
        return True
    return B.now_msk().strftime("%Y-%m-%d") >= C.SALES_START


def _ru_date(iso):
    y, m, d = iso.split("-")
    months = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
    return f"{int(d)} {months[int(m) - 1]}"


def _kb(*rows):
    kb = B.types.InlineKeyboardMarkup()
    for row in rows:
        kb.row(*[B.types.InlineKeyboardButton(t, callback_data=d) for t, d in row])
    return kb


def _proj(call, pid=None):
    """Проект своего заказчика или None (чужой/несуществующий — «Недоступно»)."""
    pid = pid or call.data.split(":")[2]
    p = K.load(pid)
    if not p or p["chat_id"] != call.message.chat.id:
        B.bot.answer_callback_query(call.id, "Недоступно")
        return None
    return p


def _state(chat_id, step, p):
    B.STATES[chat_id] = {"step": step, "pid": p["id"]}


def _price(p):
    if p["kind"] == "team":
        base = C.team_price(len(p["people"])) or 0
        return base + (C.TEAM_DEPT_CARD_PRICE if p.get("dept_card") else 0)
    if p["kind"] == "cli":
        pack = C.client_pack(len(p["people"]), p.get("pack"))
        return pack[1] if pack else 0
    return 0


def _recent_count(chat_id):
    since = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    return len([p for p in K.all_projects() if p["chat_id"] == chat_id and p["created_at"] >= since])


def _new_project(chat_id, kind, **extra):
    for old in K.all_projects():  # брошенные черновики не копим
        if old["chat_id"] == chat_id and old["status"] in ("draft", "previews"):
            old["status"] = "abandoned"
            K.save(old)
    p = K.create(chat_id, kind, **extra)
    return p


def _admin(text, **kw):
    if B.ADMIN_ID:
        try:
            B.safe_send(B.ADMIN_ID, text, **kw)
        except Exception as exc:
            B.log.error("b2b admin notify: %s", exc)


# ── витрина ─────────────────────────────────────────────────────
def menu_text():
    t1 = ", ".join(f"до {n} — {rub(p)}" for n, p in C.TEAM_TIERS)
    return (
        "🏢 <b>Новогодние письма для компаний</b>\n\n"
        "Каждое письмо — подарок самому человеку: одна точная мысль о нём, а не реклама компании. "
        "Вы получаете готовые открытки с вашим логотипом и подписью и вручаете их сами 🎄\n\n"
        f"🎄 <b>Новый год коллективу</b>\nПерсональные письма и открытки сотрудникам, по желанию — "
        f"общая открытка отдела. {t1}.\n\n"
        "💌 <b>Клиентам компании</b>\nГотовые файлы для ваших клиентов и партнёров — "
        "отправляете вы сами, мы никому не пишем. "
        + ", ".join(f"{n} шт — {rub(p)}" for n, p in C.CLIENT_PACKS) + ".\n\n"
        "🎂 <b>Дни рождения команды</b>\nПоздравления каждому сотруднику в его день. "
        + ", ".join(f"до {n} человек — {rub(p)}/мес" for n, p in C.BD_TIERS) + ".\n\n"
        "Выберите, с чего начнём 🤍"
    )


def menu_markup():
    return _kb([("🎄 Коллективу", "b2b:pk:team")], [("💌 Клиентам компании", "b2b:pk:cli")],
               [("🎂 Дни рождения команды", "b2b:pk:bd")])


def open_menu(chat_id):
    B.STATES.pop(chat_id, None)
    if not is_open(chat_id):
        B.bot.send_message(
            chat_id, menu_text() + f"\n\n🕯 Заказы откроются {_ru_date(C.SALES_START)}. "
            "Нажмите кнопку — напомним в первый день.", parse_mode="HTML",
            reply_markup=_kb([("🔔 Напомните мне", "b2b:notify")]))
        return
    B.bot.send_message(chat_id, menu_text(), parse_mode="HTML", reply_markup=menu_markup())


def menu_cb(call):
    B.bot.answer_callback_query(call.id)
    open_menu(call.message.chat.id)


def notify_cb(call):
    chat_id = call.message.chat.id
    path = os.path.join(K.ROOT, "interest.json")
    ids = B.read_json(path, [])
    if chat_id not in ids:
        ids.append(chat_id)
        B.write_json(path, ids)
    B.bot.answer_callback_query(call.id, "Напомним 🤍")


def pk_cb(call):
    chat_id = call.message.chat.id
    kind = call.data.split(":")[2]
    B.bot.answer_callback_query(call.id)
    if kind not in C.PACKAGES:
        return
    if not is_open(chat_id):
        open_menu(chat_id)
        return
    if _recent_count(chat_id) >= C.MAX_PROJECTS_PER_DAY:
        B.bot.send_message(chat_id, "Сегодня вы уже оформили несколько заказов 🕯 Напишите в «❓ Помощь» — "
                                    "поможем вручную.")
        return
    if kind == "cli":
        rows = [[(f"{n} шт · {rub(p)}", f"b2b:cl:{n}")] for n, p in C.CLIENT_PACKS]
        B.safe_edit(call, "💌 <b>Клиентам компании</b>\n\nСколько писем нужно? Если в списке окажется больше, "
                          "подберём подходящий пакет сами.\n\nВажно: мы не рассылаем письма вашим клиентам — "
                          "вы получите файлы и отправите их сами, от своего имени.", _kb(*rows))
        return
    if kind == "bd":
        rows = [[(f"до {n} человек · {rub(p)}/мес", f"b2b:bd:{n}")] for n, p in C.BD_TIERS]
        B.safe_edit(call, "🎂 <b>Дни рождения команды</b>\n\nМы поздравляем сотрудников в их день рождения "
                          "письмом и открыткой — от лица компании. Пока подключаем вручную: оставьте заявку, "
                          "и мы договоримся о деталях.\n\nСколько человек в команде?", _kb(*rows))
        return
    p = _new_project(chat_id, "team")
    B.safe_edit(call, "🎄 <b>Новый год коллективу</b>\n\nОтвечать будем коротко, всё занимает пару минут.")
    ask_company(chat_id, p)


def cl_cb(call):
    chat_id = call.message.chat.id
    B.bot.answer_callback_query(call.id)
    n = int(call.data.split(":")[2])
    if n not in dict(C.CLIENT_PACKS) or not is_open(chat_id):
        return
    p = _new_project(chat_id, "cli", pack=n)
    ask_company(chat_id, p)


# ── анкета ──────────────────────────────────────────────────────
def ask_company(chat_id, p):
    _state(chat_id, "b2b_company", p)
    B.bot.send_message(chat_id, "Как называется ваша компания? Так, как её знают ваши люди "
                                "(«Студия Линия», «Кофейня на Садовой»).")


def anketa_text(message):
    chat_id = message.chat.id
    st = B.STATES[chat_id]
    step = st["step"]
    p = K.load(st["pid"])
    text = (message.text or "").strip()
    if not p or p["chat_id"] != chat_id:
        B.STATES.pop(chat_id, None)
        return
    if step == "b2b_logo":
        B.bot.send_message(chat_id, "Пришлите логотип картинкой или нажмите «Без логотипа» 🤍")
        return
    if step == "b2b_company":
        if not 2 <= len(text) <= 80:
            B.bot.send_message(chat_id, "Напишите название коротко, до 80 знаков.")
            return
        p["company"] = text
        if p["kind"] == "bd":
            ask_bd_contact(chat_id, p)
            return
        K.save(p)
        B.bot.send_message(chat_id, "Каким должен быть тон писем?", reply_markup=_kb(
            [(label, f"b2b:tone:{p['id']}:{k}") for k, label in C.TONES.items()]))
        B.STATES.pop(chat_id, None)
    elif step == "b2b_sign":
        if not 2 <= len(text) <= 60:
            B.bot.send_message(chat_id, "Подпись — коротко, до 60 знаков.")
            return
        p["sign"] = text
        K.save(p)
        ask_logo(chat_id, p)
    elif step == "b2b_list":
        people = K.parse_text(text)
        take_people(chat_id, p, people)
    elif step == "b2b_wish":
        if not 3 <= len(text) <= 300:
            B.bot.send_message(chat_id, "Напишите пожелание коротко, до 300 знаков.")
            return
        p["wish"] = text
        K.save(p)
        B.STATES.pop(chat_id, None)
        send_previews(chat_id, p, redo=True)
    elif step == "b2b_req":
        if not 10 <= len(text) <= 800:
            B.bot.send_message(chat_id, "Напишите реквизиты одним сообщением: название юрлица, ИНН, КПП "
                                        "(если есть) и e-mail для документов.")
            return
        invoice_request(chat_id, p, text)
    elif step == "b2b_bd_contact":
        if not 5 <= len(text) <= 500:
            B.bot.send_message(chat_id, "Напишите коротко, до 500 знаков.")
            return
        bd_lead(chat_id, p, text)


def tone_cb(call):
    p = _proj(call)
    if not p:
        return
    chat_id = call.message.chat.id
    p["tone"] = call.data.split(":")[3] if call.data.split(":")[3] in C.TONES else "warm"
    K.save(p)
    B.bot.answer_callback_query(call.id)
    B.safe_edit(call, f"Тон: <b>{C.TONES[p['tone']]}</b>")
    _state(chat_id, "b2b_sign", p)
    kb = _kb([(f"Подписать «{p['company'][:30]}»", f"b2b:sign:{p['id']}")])
    B.bot.send_message(chat_id, "Как подписать письма? Например: «Команда Студии Линия» или «Анна Петрова, "
                                "директор». Подпись будет и в тексте, и на открытке.", reply_markup=kb)


def sign_cb(call):
    p = _proj(call)
    if not p:
        return
    B.bot.answer_callback_query(call.id)
    p["sign"] = p["company"][:60]
    K.save(p)
    ask_logo(call.message.chat.id, p)


def ask_logo(chat_id, p):
    _state(chat_id, "b2b_logo", p)
    B.bot.send_message(chat_id, "Пришлите логотип картинкой (PNG или JPG, лучше на прозрачном или светлом фоне) — "
                                "он будет в углу открытки. Или пропустите 🤍",
                       reply_markup=_kb([("Без логотипа", f"b2b:nologo:{p['id']}")]))


def logo_message(message):
    chat_id = message.chat.id
    p = K.load(B.STATES[chat_id]["pid"])
    if not p or p["chat_id"] != chat_id:
        return
    if message.content_type == "photo":
        fid, size = message.photo[-1].file_id, getattr(message.photo[-1], "file_size", 0) or 0
    else:
        doc = message.document
        if not (doc.mime_type or "").startswith("image/"):
            B.bot.send_message(chat_id, "Нужна картинка: PNG или JPG. Или нажмите «Без логотипа».")
            return
        fid, size = doc.file_id, doc.file_size or 0
    if size > C.MAX_UPLOAD_BYTES:
        B.bot.send_message(chat_id, "Файл тяжёлый — пришлите логотип до 2 МБ.")
        return
    try:
        data = B.bot.download_file(B.bot.get_file(fid).file_path)
        K.save_logo(p["id"], data)
    except Exception as exc:
        B.log.warning("b2b logo: %s", exc)
        B.bot.send_message(chat_id, "Не получилось открыть картинку 😔 Пришлите PNG или JPG ещё раз "
                                    "или нажмите «Без логотипа».")
        return
    p["has_logo"] = True
    K.save(p)
    B.bot.send_message(chat_id, "Логотип сохранён 🤍")
    after_logo(chat_id, p)


def nologo_cb(call):
    p = _proj(call)
    if not p:
        return
    B.bot.answer_callback_query(call.id)
    p["has_logo"] = False
    K.save(p)
    after_logo(call.message.chat.id, p)


def after_logo(chat_id, p):
    if p["kind"] == "team":
        B.bot.send_message(
            chat_id, "Сделать ещё и общую открытку отдела — от компании всей команде?"
            + (f" (+{rub(C.TEAM_DEPT_CARD_PRICE)})" if C.TEAM_DEPT_CARD_PRICE else " Она входит в цену."),
            reply_markup=_kb([("🎄 Да, добавить", f"b2b:dept:{p['id']}:1"),
                              ("Не нужно", f"b2b:dept:{p['id']}:0")]))
        return
    ask_list(chat_id, p)


def dept_cb(call):
    p = _proj(call)
    if not p:
        return
    B.bot.answer_callback_query(call.id)
    p["dept_card"] = call.data.endswith(":1")
    K.save(p)
    ask_list(call.message.chat.id, p)


def ask_list(chat_id, p):
    _state(chat_id, "b2b_list", p)
    if p["kind"] == "team":
        text = ("Теперь список сотрудников. Пришлите файл CSV или XLSX либо просто текст — по человеку в строке:\n\n"
                "<code>Имя, роль, одна деталь</code>\n\n"
                "Нужно только имя; роль и деталь — по желанию, но с ними письмо точнее. Например:\n"
                "<code>Мария Орлова, бухгалтер, всегда сдаёт отчёты за день до срока</code>\n\n"
                f"Максимум для этого пакета — {C.TEAM_MAX} чел.")
    else:
        text = ("Теперь список клиентов. Пришлите файл CSV или XLSX либо просто текст — по человеку в строке:\n\n"
                "<code>Имя, роль, одна деталь</code>\n\n"
                "Нужно только имя; роль и деталь — по желанию. Например:\n"
                "<code>Игорь Соколов, директор «Вектора», с вами с 2019 года</code>")
    B.bot.send_message(chat_id, text, parse_mode="HTML")


def list_document(message):
    chat_id = message.chat.id
    p = K.load(B.STATES[chat_id]["pid"])
    if not p or p["chat_id"] != chat_id:
        return
    doc = message.document
    if (doc.file_size or 0) > C.MAX_UPLOAD_BYTES:
        B.bot.send_message(chat_id, "Файл тяжёлый — до 2 МБ. Сократите список или пришлите текстом.")
        return
    try:
        data = B.bot.download_file(B.bot.get_file(doc.file_id).file_path)
        people = K.parse_file(doc.file_name, data)
    except ValueError:
        B.bot.send_message(chat_id, "Принимаю CSV, XLSX или текст. Пришлите список в одном из этих видов 🤍")
        return
    except Exception as exc:
        B.log.warning("b2b list: %s", exc)
        B.bot.send_message(chat_id, "Не получилось прочитать файл 😔 Проверьте, что в первой колонке имена, "
                                    "или пришлите список текстом.")
        return
    take_people(chat_id, p, people)


def take_people(chat_id, p, people):
    if not people:
        B.bot.send_message(chat_id, "Не нашла в списке ни одного имени. Формат: «Имя, роль, деталь» — "
                                    "по человеку в строке.")
        return
    n = len(people)
    if p["kind"] == "team" and n > C.TEAM_MAX:
        B.bot.send_message(chat_id, f"В списке {n} человек — для пакета «коллективу» максимум {C.TEAM_MAX}. "
                                    "Для большей команды напишите в «❓ Помощь» — посчитаем отдельно. "
                                    "Можно прислать список короче.")
        return
    if p["kind"] == "cli":
        pack = C.client_pack(n, p.get("pack"))
        if not pack:
            B.bot.send_message(chat_id, f"В списке {n} — это больше нашего максимума ({C.CLIENT_MAX}). "
                                        "Разделите список на два заказа или напишите в «❓ Помощь».")
            return
        if pack[0] != p.get("pack"):
            B.bot.send_message(chat_id, f"В списке {n} — подойдёт пакет на {pack[0]} писем.")
            p["pack"] = pack[0]
    p["people"] = people
    p["status"] = "previews"
    p["previews"], p["preview_rounds"] = [], 0
    K.save(p)
    B.STATES.pop(chat_id, None)
    B.bot.send_message(chat_id, f"Получила список: <b>{n}</b> чел. 🤍 Сейчас напишу пару примеров — посмотрите, "
                                "подходит ли голос.", parse_mode="HTML")
    send_previews(chat_id, p)


# ── превью ──────────────────────────────────────────────────────
def send_previews(chat_id, p, redo=False):
    if p["preview_rounds"] >= C.PREVIEW_ROUNDS:
        B.bot.send_message(chat_id, "Примеров уже достаточно 🤍 Если голос подходит — переходим к оплате.",
                           reply_markup=_kb([("✅ К оплате", f"b2b:pay:{p['id']}")]))
        return
    B.bot.send_chat_action(chat_id, "typing")
    items = K.people_for_run(p)
    step = max(1, len(items) // C.PREVIEW_COUNT)
    picks = [items[i * step] for i in range(min(C.PREVIEW_COUNT, len(items)))]
    shown, new = 0, []
    for k, it in enumerate(picks):
        prev = p["previews"][k]["letter"] if redo and k < len(p["previews"]) else ""
        try:
            letter = b2b_ai.generate_letter(p, it, previous=prev, wish=p.get("wish", ""))
            card = K.render_card(p, it["idx"], letter, preview=True)
        except Exception as exc:
            B.log.error("b2b preview: %s", exc)
            continue
        new.append({"idx": it["idx"], "letter": letter["letter"]})
        shown += 1
        B.bot.send_photo(chat_id, card, caption=f"🖼 Открытка · {B.esc(it['name'])}", parse_mode="HTML")
        B.bot.send_message(chat_id, f"💌 <b>{B.esc(it['name'])}</b>\n\n{B.esc(letter['letter'])}",
                           parse_mode="HTML")
    if not shown:
        B.bot.send_message(chat_id, "Помощник сейчас недоступен 😔 Попробуйте через пару минут.",
                           reply_markup=_kb([("🔄 Повторить", f"b2b:regen:{p['id']}")]))
        return
    p["previews"] = new
    p["preview_rounds"] += 1
    K.save(p)
    rows = [[("✅ Подходит, к оплате", f"b2b:pay:{p['id']}")]]
    if p["preview_rounds"] < C.PREVIEW_ROUNDS:
        rows.append([("🔄 Другие варианты", f"b2b:regen:{p['id']}"), ("✏️ Пожелание", f"b2b:wish:{p['id']}")])
    B.bot.send_message(chat_id, "Так будут звучать все письма — каждое о своём человеке 🤍 "
                                "Открытки в готовом файле — без надписи «превью».", reply_markup=_kb(*rows))


def regen_cb(call):
    p = _proj(call)
    if not p or p["status"] != "previews":
        return
    B.bot.answer_callback_query(call.id, "Пишу заново…")
    send_previews(call.message.chat.id, p, redo=True)


def wish_cb(call):
    p = _proj(call)
    if not p or p["status"] != "previews":
        return
    B.bot.answer_callback_query(call.id)
    _state(call.message.chat.id, "b2b_wish", p)
    B.bot.send_message(call.message.chat.id, "Что поменять? Например: «чуть теплее», «короче», «без шуток», "
                                             "«упомянуть, что мы вместе шесть лет».")


# ── оплата ──────────────────────────────────────────────────────
def pay_cb(call):
    p = _proj(call)
    if not p or p["status"] not in ("previews", "invoice_requested", "awaiting_payment"):
        if p:
            B.bot.answer_callback_query(call.id, "Заказ уже оформлен")
        return
    B.bot.answer_callback_query(call.id)
    price, n = _price(p), len(p["people"])
    items = f"{n} чел." + (" + открытка отдела" if p.get("dept_card") else "")
    rows = []
    if price <= C.SMALL_PAY_LIMIT:
        rows.append([("💳 Картой или СБП", f"b2b:card:{p['id']}")])
    rows.append([("🧾 Счёт для юрлица", f"b2b:inv:{p['id']}")])
    B.bot.send_message(
        call.message.chat.id,
        f"{KIND_TITLE[p['kind']]}\n{items} · <b>{rub(price)}</b>\n\n"
        "После оплаты я напишу все письма и соберу открытки — пришлю архив и таблицу «имя → текст». "
        "Это занимает от нескольких минут до часа." + ("" if price <= C.SMALL_PAY_LIMIT else
                                                       "\n\nДля такой суммы — оплата по счёту."),
        parse_mode="HTML", reply_markup=_kb(*rows))


def card_cb(call):
    p = _proj(call)
    if not p or p["status"] not in ("previews", "awaiting_payment") or _price(p) > C.SMALL_PAY_LIMIT:
        if p:
            B.bot.answer_callback_query(call.id, "Недоступно")
        return
    chat_id = call.message.chat.id
    B.bot.answer_callback_query(call.id)
    order = B.get_order(p["order_id"]) if p.get("order_id") else None
    if not order or order.get("status") != "pending":
        profile = B.upsert_client(call.from_user)
        order = {
            "order_id": B.new_order_id(), "chat_id": chat_id, "name": profile["name"],
            "username": profile.get("username", ""), "pain": "b2b", "product": "b2b", "b2b_id": p["id"],
            "answers": [], "letter_text": "", "is_gift": False, "status": "pending",
            "created_at": B.now_msk().isoformat(), "paid_at": None, "delivered_at": None,
            "email": None, "rating": None,
        }
    order["price_rub"] = _price(p)
    B.save_order(order)
    p["order_id"], p["status"] = order["order_id"], "awaiting_payment"
    K.save(p)
    if B.send_order_invoice(chat_id, order):
        B.notify_admin_new_order(order)


def inv_cb(call):
    p = _proj(call)
    if not p or p["status"] not in ("previews", "awaiting_payment", "invoice_requested"):
        if p:
            B.bot.answer_callback_query(call.id, "Заказ уже оформлен")
        return
    B.bot.answer_callback_query(call.id)
    _state(call.message.chat.id, "b2b_req", p)
    ours = f"\n\nНаши реквизиты:\n<code>{B.esc(C.REQUISITES)}</code>" if C.REQUISITES else ""
    B.bot.send_message(
        call.message.chat.id,
        f"🧾 Счёт на <b>{rub(_price(p))}</b>. Пришлите одним сообщением реквизиты вашей компании: название "
        "юрлица, ИНН, КПП (если есть) и e-mail для документов. Передам заявку — после поступления оплаты "
        "сразу начну писать письма." + ours, parse_mode="HTML")


def invoice_request(chat_id, p, requisites):
    B.STATES.pop(chat_id, None)
    p["requisites"], p["status"] = requisites, "invoice_requested"
    K.save(p)
    B.bot.send_message(chat_id, "Заявка принята 🤍 Счёт пришлём в течение рабочего дня (сюда или на указанный "
                                "e-mail). Как только оплата поступит, начну работу — вы получите архив здесь.")
    _admin(admin_card(p, "🧾 <b>Заявка на счёт</b>") + f"\n\nРеквизиты:\n{B.esc(requisites)}\n\n"
           f"Когда оплата придёт: <code>/b2b_paid {p['id']}</code>")


def bd_lead(chat_id, p, contact):
    B.STATES.pop(chat_id, None)
    p["contact"], p["status"] = contact, "lead"
    K.save(p)
    B.bot.send_message(chat_id, "Заявка принята 🤍 Свяжемся с вами в течение рабочего дня и обсудим подключение.")
    _admin(admin_card(p, "🎂 <b>Заявка: дни рождения команды</b>") + f"\nКонтакт: {B.esc(contact)}")


def ask_bd_contact(chat_id, p):
    K.save(p)
    _state(chat_id, "b2b_bd_contact", p)
    B.bot.send_message(chat_id, "Как с вами связаться и когда удобно? Напишите контакт и, если хотите, "
                                "пару слов о команде.")


def bd_cb(call):
    chat_id = call.message.chat.id
    B.bot.answer_callback_query(call.id)
    size = int(call.data.split(":")[2])
    if size not in dict(C.BD_TIERS) or not is_open(chat_id):
        return
    p = _new_project(chat_id, "bd", pack=size)
    ask_company(chat_id, p)


# ── после оплаты ────────────────────────────────────────────────
def on_paid(order):
    """Вызывается из fulfill_order для заказов product='b2b' (оплата картой/СБП)."""
    p = K.load(order.get("b2b_id"))
    if not p:
        _admin(f"⚠️ Оплачен заказ <code>{order['order_id']}</code>, но проект B2B не найден.")
        return
    p["is_test"] = bool(order.get("is_test"))  # тестовая оплата 0 ₽ — вне выручки
    mark_paid(p, "card", order["order_id"])


def mark_paid(p, how, ref=""):
    if p.get("paid"):
        return False
    p["paid"] = {"how": how, "ref": ref, "at": datetime.now().isoformat(timespec="seconds"),
                 "sum": 0 if p.get("is_test") else _price(p)}
    p["status"] = "paid"
    K.save(p)
    B.bot.send_message(p["chat_id"], "✅ Оплата получена. Пишу письма и собираю открытки — покажу, как продвигается 🤍")
    _admin(admin_card(p, "💰 <b>Оплачен B2B-заказ</b>") + f"\nОплата: {how}")
    start_generation(p["id"])
    return True


def start_generation(pid):
    with _RUN_LOCK:
        if pid in RUNNING:
            return False
        RUNNING.add(pid)
    threading.Thread(target=_job, args=(pid,), name=f"b2b-{pid}", daemon=True).start()
    return True


def _job(pid):
    try:
        run_and_deliver(pid)
    except Exception as exc:
        B.log.error("b2b job %s: %s", pid, exc)
        _admin(f"⚠️ B2B {pid}: генерация упала: {B.esc(str(exc)[:200])}\nПовтор: <code>/b2b_retry {pid}</code>")
    finally:
        with _RUN_LOCK:
            RUNNING.discard(pid)


def run_and_deliver(pid, sleep=time.sleep, generate=None, render=None):
    p = K.load(pid)
    chat_id = p["chat_id"]
    p["status"] = "generating"
    K.save(p)
    total = len(K.people_for_run(p))
    msg = B.bot.send_message(chat_id, f"✍️ Готово 0 из {total}")
    last = {"t": 0.0, "n": -1}

    def progress(done, tot, failed):
        now = time.time()
        if done == last["n"] or (now - last["t"] < 4 and done < tot):
            return
        last.update(t=now, n=done)
        try:
            B.bot.edit_message_text(f"✍️ Готово {done} из {tot}", chat_id, msg.message_id)
        except Exception:
            pass

    done, failed = K.run_batch(p, on_progress=progress, sleep=sleep, generate=generate, render=render)
    p = K.load(pid)
    if failed:
        p["status"] = "partial"
        K.save(p)
        _admin(f"⚠️ B2B {pid}: {failed} из {total} писем не получились. Повтор: <code>/b2b_retry {pid}</code>")
    if done:
        deliver(p, partial=bool(failed))
    else:
        B.bot.send_message(chat_id, "Задерживаемся 🕯 Письма пока не получились — мы уже знаем и всё доделаем, "
                                    "напишем сразу, как будет готово.")
    if not failed:
        p["status"] = "done"
        p["done_at"] = datetime.now().isoformat(timespec="seconds")
        K.save(p)
        _admin(admin_card(p, "✅ <b>B2B готов и отправлен заказчику</b>"))


def deliver(p, partial=False):
    chat_id = p["chat_id"]
    skip = set(p.get("delivered", []))
    zips = K.build_zips(p, skip=skip, tag="_дополнение" if skip else "")
    for name, data in zips:
        B.bot.send_document(chat_id, B.types.InputFile(io.BytesIO(data), file_name=name))
    p["delivered"] = sorted(int(k) for k, r in K.load_results(p["id"]).items() if r.get("letter"))
    K.save(p)
    B.bot.send_document(chat_id, B.types.InputFile(io.BytesIO(K.table_xlsx(p)), file_name="Письма_имя_текст.xlsx"),
                        caption="📋 Таблица «имя → текст»: у каждого письма указан файл открытки")
    how = ("Раздайте открытки своим сотрудникам лично, в рассылке или на корпоративе — как вам удобнее"
           if p["kind"] == "team" else
           "Отправьте письма клиентам сами, от своего имени — мы никому не пишем. Текст каждого — в таблице")
    extra = ("\n\n⏳ Несколько писем не получились с первого раза — дошлю их отдельным сообщением, ничего "
             "делать не нужно.") if partial else ""
    B.bot.send_message(chat_id, f"🎄 Всё готово 🤍 {how}. Открытки — в архиве "
                                f"({len(zips)} {'файл' if len(zips) == 1 else 'файла'}).{extra}\n\n"
                                "Если захочется что-то поправить — напишите в «❓ Помощь».")


# ── админ ───────────────────────────────────────────────────────
def admin_card(p, title):
    return (f"{title}\n<code>{p['id']}</code> · {KIND_TITLE.get(p['kind'], p['kind'])}\n"
            f"{B.esc(p['company'])} · {len(p['people'])} чел. · {rub(_price(p))}")


def cmd_b2b(message):
    if not B.admin_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) > 1:
        p = K.load(parts[1])
        if not p:
            B.bot.send_message(message.chat.id, "Проект не найден.")
            return
        res = K.load_results(p["id"])
        ok = len([1 for r in res.values() if r.get("letter")])
        B.bot.send_message(
            message.chat.id,
            admin_card(p, "🏢 <b>Проект</b>") + f"\nСтатус: {p['status']}\nТон: {p['tone']} · подпись: "
            f"{B.esc(p['sign'])} · логотип: {'да' if p['has_logo'] else 'нет'}\nПисем готово: {ok}\n"
            + (f"Оплата: {p['paid']['how']} {p['paid']['at']}\n" if p.get("paid") else "")
            + (f"Реквизиты: {B.esc(p['requisites'])}\n" if p.get("requisites") else "")
            + (f"Контакт: {B.esc(p['contact'])}\n" if p.get("contact") else ""), parse_mode="HTML")
        return
    projects = [p for p in K.all_projects() if p["status"] not in ("draft", "abandoned")]
    projects.sort(key=lambda p: p["created_at"], reverse=True)
    paid = [p for p in projects if p.get("paid")]
    revenue = sum(p["paid"]["sum"] for p in paid)
    interest = len(B.read_json(os.path.join(K.ROOT, "interest.json"), []))
    lines = [f"{p['id']} · {C.PACKAGES[p['kind']]['icon']} {B.esc(p['company'][:24])} · {len(p['people'])} чел. "
             f"· {rub(_price(p))} · {p['status']}" for p in projects[:30]]
    B.bot.send_message(
        message.chat.id,
        f"🏢 <b>B2B</b>\nЗаказов: {len(projects)} · оплачено: {len(paid)} на {rub(revenue)}\n"
        f"Ждут напоминания об открытии: {interest}\n\n" + ("\n".join(lines) or "Пока пусто.")
        + "\n\n/b2b ID — детали · /b2b_paid ID — подтвердить оплату по счёту · /b2b_retry ID — дописать письма",
        parse_mode="HTML")


def cmd_paid(message):
    if not B.admin_only(message):
        return
    parts = (message.text or "").split()
    p = K.load(parts[1]) if len(parts) > 1 else None
    if not p or p["kind"] == "bd":
        B.bot.send_message(message.chat.id, "Формат: /b2b_paid B2B-XXXXXX")
    elif p.get("paid"):
        B.bot.send_message(message.chat.id, "Этот заказ уже оплачен.")
    elif not p["people"]:
        B.bot.send_message(message.chat.id, "В заказе нет списка людей — подтверждать нечего.")
    else:
        mark_paid(p, "счёт")
        B.bot.send_message(message.chat.id, f"Оплата {p['id']} подтверждена, запускаю генерацию.")


def cmd_retry(message):
    if not B.admin_only(message):
        return
    parts = (message.text or "").split()
    p = K.load(parts[1]) if len(parts) > 1 else None
    if not p or not p.get("paid"):
        B.bot.send_message(message.chat.id, "Формат: /b2b_retry B2B-XXXXXX (заказ должен быть оплачен).")
        return
    res = K.load_results(p["id"])
    for k in [k for k, r in res.items() if r.get("error")]:
        del res[k]  # ошибочные записи переделываем
    K.save_results(p["id"], res)
    ok = start_generation(p["id"])
    B.bot.send_message(message.chat.id, "Запустила." if ok else "Уже идёт.")


def resume_running():
    """После перезапуска бота дописывает заказы, которые застряли на генерации."""
    for p in K.all_projects():
        if p["status"] == "generating" and p.get("paid"):
            start_generation(p["id"])


# ── подключение ─────────────────────────────────────────────────
def register(bot_module):
    global B
    B = bot_module
    bot = B.bot
    K.configure(B.DATA_DIR)

    def cb(prefix, fn):
        bot.callback_query_handler(func=lambda c: c.data.startswith(prefix))(fn)

    bot.callback_query_handler(func=lambda c: c.data == "b2b:menu")(menu_cb)
    bot.callback_query_handler(func=lambda c: c.data == "b2b:notify")(notify_cb)
    for prefix, fn in (("b2b:pk:", pk_cb), ("b2b:cl:", cl_cb), ("b2b:bd:", bd_cb), ("b2b:tone:", tone_cb),
                       ("b2b:sign:", sign_cb), ("b2b:nologo:", nologo_cb), ("b2b:dept:", dept_cb),
                       ("b2b:regen:", regen_cb), ("b2b:wish:", wish_cb), ("b2b:pay:", pay_cb),
                       ("b2b:card:", card_cb), ("b2b:inv:", inv_cb)):
        cb(prefix, fn)

    def step(m):
        return B.STATES.get(m.chat.id, {}).get("step")

    bot.message_handler(func=lambda m: m.content_type == "text" and (m.text or "") == "🏢 Для компаний")(
        lambda m: open_menu(m.chat.id))
    bot.message_handler(func=lambda m: step(m) in ANKETA_STEPS and m.content_type == "text"
                        and not (m.text or "").startswith("/"))(anketa_text)
    bot.message_handler(func=lambda m: step(m) == "b2b_logo", content_types=["photo", "document"])(logo_message)
    bot.message_handler(func=lambda m: step(m) == "b2b_list", content_types=["document"])(list_document)
    bot.message_handler(commands=["b2b"])(cmd_b2b)
    bot.message_handler(commands=["b2b_paid"])(cmd_paid)
    bot.message_handler(commands=["b2b_retry"])(cmd_retry)
    t = threading.Timer(30, resume_running)
    t.daemon = True
    t.start()
