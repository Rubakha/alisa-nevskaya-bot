"""Календарь дат близких: человек сам просит напомнить — бот пишет ему за 3 дня до даты.

Только по согласию: даты сохраняются после явного «Да, напомни» (после заказа или из «🔔 Напоминания»),
праздничные напоминания — по подписке. Правила: тихие часы (10–20 МСК), не чаще одного напоминания
в 7 дней на человека, отписка одной кнопкой прямо под напоминанием, заблокировавшим бота не пишем.
Напоминание ведёт в обычный сценарий с уже заполненным именем получателя.

Хранение: в профиле клиента profile["cal"] = {dates, subs, sent, last_at, src_until}.
Подключается из bot_best: calendar_reminders.register(bot_best).
"""
import calendar as pycal
import json
import os
import re
import time
from datetime import date, datetime, timedelta

REMIND_DAYS = 3                 # за сколько дней напоминаем
WEEKLY_GAP = timedelta(days=7)
QUIET_FROM, QUIET_TO = 10, 20   # МСК: пишем только в это окно
SRC_WINDOW = timedelta(hours=3) # заказ в течение 3 часов после нажатия кнопки напоминания — «из напоминания»

MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6, "июля": 7, "августа": 8,
          "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12}
KINDS = {"birthday": ("🎂", "день рождения", "birthday"), "anniversary": ("💞", "годовщина", "love"),
         "other": ("📅", "важная дата", "friend")}


def _nth_weekday(year, month, weekday, n):
    """n-й (1..) или последний (n=-1) день недели месяца (пн=0)."""
    days = [d for d in range(1, pycal.monthrange(year, month)[1] + 1)
            if date(year, month, d).weekday() == weekday]
    return date(year, month, days[n - 1] if n > 0 else days[-1])


HOLIDAYS = {
    "mother": {"title": "День матери", "icon": "🤍", "product": "family",
               "date": lambda y: _nth_weekday(y, 11, 6, -1)},
    "father": {"title": "День отца", "icon": "🤍", "product": "family",
               "date": lambda y: _nth_weekday(y, 10, 6, 3)},
    "newyear": {"title": "Новый год", "icon": "🎄", "product": "newyear", "date": lambda y: date(y, 12, 31)},
    "feb14": {"title": "14 февраля", "icon": "💞", "product": "love", "date": lambda y: date(y, 2, 14)},
    "feb23": {"title": "23 февраля", "icon": "🌿", "product": "thanks", "date": lambda y: date(y, 2, 23)},
    "mar8": {"title": "8 Марта", "icon": "🌷", "product": "thanks", "date": lambda y: date(y, 3, 8)},
}

B = None


# ── даты ────────────────────────────────────────────────────────
def parse_date(text):
    """'14.03', '14.03.1990', '14 марта' -> (day, month) или None."""
    t = (text or "").strip().lower()
    m = re.match(r"^(\d{1,2})\s+([а-я]+)", t)
    if m and m.group(2) in MONTHS:
        day, month = int(m.group(1)), MONTHS[m.group(2)]
    else:
        m = re.match(r"^(\d{1,2})[./\-\s](\d{1,2})(?:[./\-\s]\d{2,4})?$", t)
        if not m:
            return None
        day, month = int(m.group(1)), int(m.group(2))
    try:
        date(2024, month, day)  # високосный год: 29.02 допустим
    except ValueError:
        return None
    return day, month


def next_occurrence(day, month, today):
    for year in (today.year, today.year + 1):
        d = day
        if month == 2 and day == 29 and not pycal.isleap(year):
            d = 28
        cand = date(year, month, d)
        if cand >= today:
            return cand
    return None


def fmt(day, month):
    return f"{day:02d}.{month:02d}"


# ── профиль ─────────────────────────────────────────────────────
def get_cal(chat_id):
    profile = B.get_client(chat_id)
    if profile is None:
        return None, None
    cal = profile.setdefault("cal", {})
    cal.setdefault("dates", [])
    cal.setdefault("subs", [])
    cal.setdefault("sent", {})
    return profile, cal


def _save(chat_id, profile):
    B.write_json(B.client_path(chat_id), profile)


def log_event(chat_id, name, **extra):
    """Журнал для метрик; тестовые аккаунты не считаем."""
    if B.is_test_user(chat_id):
        return
    try:
        path = os.path.join(B.DATA_DIR, "calendar_events.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": B.now_msk().isoformat(timespec="seconds"), "chat_id": chat_id,
                                "name": name, **extra}, ensure_ascii=False) + "\n")
    except OSError as exc:
        B.log.error("calendar log: %s", exc)


def add_date(chat_id, name, kind, day, month):
    profile, cal = get_cal(chat_id)
    if profile is None:
        return None
    for d in cal["dates"]:
        if d["name"].lower() == name.lower() and d["kind"] == kind:
            d.update(day=day, month=month)
            _save(chat_id, profile)
            return d
    entry = {"id": f"d{int(time.time() * 1000) % 10 ** 9}", "name": name, "kind": kind, "day": day, "month": month}
    cal["dates"].append(entry)
    _save(chat_id, profile)
    log_event(chat_id, "date_added", kind=kind)
    return entry


def is_from_reminder(chat_id):
    _, cal = get_cal(chat_id)
    until = (cal or {}).get("src_until")
    try:
        return bool(until) and datetime.fromisoformat(until) > B.now_msk()
    except ValueError:
        return False


def mark_source(chat_id):
    profile, cal = get_cal(chat_id)
    if profile is not None:
        cal["src_until"] = (B.now_msk() + SRC_WINDOW).isoformat()
        _save(chat_id, profile)


# ── после заказа: «напомнить?» ──────────────────────────────────
def offer_after_order(chat_id, order):
    """Один вопрос после доставки письма: напомнить о дате этого человека + (раз) о праздниках."""
    product = order.get("product")
    name = (order.get("gift_for") or "").strip()
    if not name or order.get("group_id") or product in ("santa", "newyear", "toast", "sorry", "support"):
        return
    profile, cal = get_cal(chat_id)
    if profile is None:
        return
    kind = "anniversary" if product == "love" else "birthday"
    if any(d["name"].lower() == name.lower() and d["kind"] == kind for d in cal["dates"]):
        return
    icon, title, _ = KINDS[kind]
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    kb.add(B.types.InlineKeyboardButton(f"{icon} Да, напомни за 3 дня", callback_data=f"cal:add:{order['order_id']}"))
    if not cal["subs"]:
        kb.add(B.types.InlineKeyboardButton("🔔 Напоминать про праздники", callback_data="cal:menu"))
    kb.add(B.types.InlineKeyboardButton("Нет, спасибо", callback_data="cal:no"))
    short = name if len(name) <= 30 else name[:30]
    B.bot.send_message(chat_id, f"🤍 Хочешь, я напомню за 3 дня до следующего повода у {B.esc(short)} "
                                f"({title})? Тогда письмо успеешь написать без спешки.\n"
                                "Напоминаю только тем, кто сам попросил, отписаться можно одной кнопкой.",
                       parse_mode="HTML", reply_markup=kb)


def add_cb(call):
    chat_id = call.message.chat.id
    order = B.get_order(call.data.split(":", 2)[2])
    B.bot.answer_callback_query(call.id)
    if not order or order.get("chat_id") != chat_id:
        return
    kind = "anniversary" if order.get("product") == "love" else "birthday"
    B.STATES[chat_id] = {"step": "cal_date", "name": (order.get("gift_for") or "").strip()[:40], "kind": kind}
    _drop_buttons(call)
    B.bot.send_message(chat_id, f"Напиши дату одним сообщением — например, 14.03 или «14 марта». "
                                "Год не нужен.")


def no_cb(call):
    B.bot.answer_callback_query(call.id, "Хорошо, не буду")
    _drop_buttons(call)


def _drop_buttons(call):
    try:
        B.bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass


# ── меню «🔔 Напоминания» ───────────────────────────────────────
def menu_text_markup(chat_id):
    profile, cal = get_cal(chat_id)
    lines = ["🔔 <b>Напоминания</b>\n",
             f"Я напишу за {REMIND_DAYS} дня до даты, не чаще раза в неделю и только днём. "
             "Отписаться можно здесь или под любым напоминанием.\n"]
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    if cal and cal["dates"]:
        lines.append("<b>Даты близких</b>")
        for d in cal["dates"]:
            icon, title, _ = KINDS.get(d["kind"], KINDS["other"])
            lines.append(f"{icon} {B.esc(d['name'])} — {title}, {fmt(d['day'], d['month'])}")
            kb.add(B.types.InlineKeyboardButton(f"🗑 Убрать: {d['name'][:20]}", callback_data=f"cal:del:{d['id']}"))
    else:
        lines.append("Дат близких пока нет.")
    kb.add(B.types.InlineKeyboardButton("➕ Добавить дату", callback_data="cal:new"))
    lines.append("\n<b>Праздники</b> (нажми, чтобы включить или выключить)")
    subs = (cal or {}).get("subs", [])
    for key, h in HOLIDAYS.items():
        on = key in subs
        kb.add(B.types.InlineKeyboardButton(f"{'✅' if on else '▫️'} {h['title']}", callback_data=f"cal:tog:{key}"))
    return "\n".join(lines), kb


def show_menu(chat_id, call=None):
    profile, _ = get_cal(chat_id)
    if profile is None:
        B.bot.send_message(chat_id, "Нажми /start, чтобы я тебя запомнила 🤍")
        return
    text, kb = menu_text_markup(chat_id)
    if call:
        B.safe_edit(call, text, kb)
    else:
        B.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def menu_cb(call):
    B.bot.answer_callback_query(call.id)
    show_menu(call.message.chat.id, call)


def toggle_cb(call):
    chat_id = call.message.chat.id
    key = call.data.split(":", 2)[2]
    profile, cal = get_cal(chat_id)
    B.bot.answer_callback_query(call.id)
    if profile is None or key not in HOLIDAYS:
        return
    if key in cal["subs"]:
        cal["subs"].remove(key)
        log_event(chat_id, "unsub", what=key)
    else:
        cal["subs"].append(key)
        log_event(chat_id, "sub", what=key)
    _save(chat_id, profile)
    show_menu(chat_id, call)


def delete_cb(call):
    chat_id = call.message.chat.id
    did = call.data.split(":", 2)[2]
    profile, cal = get_cal(chat_id)
    B.bot.answer_callback_query(call.id, "Убрала")
    if profile is None:
        return
    cal["dates"] = [d for d in cal["dates"] if d["id"] != did]
    _save(chat_id, profile)
    log_event(chat_id, "unsub", what="date")
    show_menu(chat_id, call)


def new_cb(call):
    chat_id = call.message.chat.id
    B.bot.answer_callback_query(call.id)
    B.STATES[chat_id] = {"step": "cal_name"}
    B.bot.send_message(chat_id, "О ком напомнить? Напиши имя так, как ты к нему обращаешься "
                                "(«мама», «Катя», «Андрей Петрович»).")


def kind_cb(call):
    chat_id = call.message.chat.id
    st = B.STATES.get(chat_id) or {}
    B.bot.answer_callback_query(call.id)
    if st.get("step") != "cal_kind":
        return
    save_new(chat_id, st["name"], call.data.split(":", 2)[2], st["day"], st["month"])
    B.STATES.pop(chat_id, None)
    _drop_buttons(call)


def text_cb(message):
    chat_id = message.chat.id
    st = B.STATES[chat_id]
    text = (message.text or "").strip()
    if st["step"] == "cal_name":
        if not 1 <= len(text) <= 40:
            B.bot.send_message(chat_id, "Имя — коротко, до 40 знаков.")
            return
        st.update(step="cal_date", name=text, kind=None)
        B.bot.send_message(chat_id, f"Когда у {B.esc(text)} важная дата? Например, 14.03 или «14 марта».",
                           parse_mode="HTML")
        return
    parsed = parse_date(text)
    if not parsed:
        B.bot.send_message(chat_id, "Не поняла дату 🙈 Напиши так: 14.03 или «14 марта».")
        return
    day, month = parsed
    if st.get("kind"):
        B.STATES.pop(chat_id, None)
        save_new(chat_id, st["name"], st["kind"], day, month)
        return
    st.update(step="cal_kind", day=day, month=month)
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    for key, (icon, title, _) in KINDS.items():
        kb.add(B.types.InlineKeyboardButton(f"{icon} {title.capitalize()}", callback_data=f"cal:kind:{key}"))
    B.bot.send_message(chat_id, "Что это за дата?", reply_markup=kb)


def save_new(chat_id, name, kind, day, month):
    entry = add_date(chat_id, name, kind, day, month)
    if not entry:
        B.bot.send_message(chat_id, "Нажми /start, чтобы я тебя запомнила 🤍")
        return
    today = B.now_msk().date()
    when = next_occurrence(day, month, today)
    remind = (when - timedelta(days=REMIND_DAYS)) if when else None
    extra = f" Напомню {fmt(remind.day, remind.month)}." if remind and remind > today else ""
    kb = B.types.InlineKeyboardMarkup()
    kb.add(B.types.InlineKeyboardButton("🔔 Все напоминания", callback_data="cal:menu"))
    B.bot.send_message(chat_id, f"Готово 🤍 Запомнила: {B.esc(name)}, {fmt(day, month)}.{extra} "
                                "Отписаться можно в любой момент — кнопка будет под напоминанием.",
                       parse_mode="HTML", reply_markup=kb)


# ── сами напоминания ────────────────────────────────────────────
def reminder_markup(write_cb, off_cb, app_params=None):
    """Кнопка напоминания. В режиме APP_ONLY («Написать письмо <имени>») открывает приложение, иначе — сценарий бота."""
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    if app_params is not None and B.app_only():
        kb.add(B.types.InlineKeyboardButton(write_cb[0], web_app=B.types.WebAppInfo(url=B.app_url(**app_params))))
    else:
        kb.add(B.types.InlineKeyboardButton(write_cb[0], callback_data=write_cb[1]))
    kb.add(B.types.InlineKeyboardButton("🔕 Больше не напоминать об этом", callback_data=off_cb))
    return kb


def due_for(profile, today):
    """Список (ключ события, текст, markup) для человека на сегодня, самый близкий первым."""
    cal = profile.get("cal") or {}
    found = []
    for d in cal.get("dates", []):
        when = next_occurrence(d["day"], d["month"], today)
        if when is None:
            continue
        left = (when - today).days
        key = f"{d['id']}:{when.year}"
        if 1 <= left <= REMIND_DAYS and key not in cal.get("sent", {}):
            icon, title, _ = KINDS.get(d["kind"], KINDS["other"])
            text = (f"{icon} Через {left} дн. — {title} у {B.esc(d['name'])} ({fmt(d['day'], d['month'])}). "
                    f"Хочешь, Алиса соберёт письмо с открыткой для {B.esc(d['name'])}? "
                    "Начало письма — бесплатно.")
            found.append((left, key, text, reminder_markup(
                (f"💌 Написать письмо: {d['name'][:20]}", f"cal:write:{d['id']}"), f"cal:off:d:{d['id']}",
                {"occ": KINDS.get(d["kind"], KINDS["other"])[2], "for": d["name"][:40]})))
    for hkey in cal.get("subs", []):
        h = HOLIDAYS.get(hkey)
        if not h:
            continue
        for year in (today.year, today.year + 1):
            when = h["date"](year)
            if when >= today:
                break
        left = (when - today).days
        key = f"h:{hkey}:{when.year}"
        if 1 <= left <= REMIND_DAYS and key not in cal.get("sent", {}):
            text = (f"{h['icon']} Скоро {h['title']} — {when.day:02d}.{when.month:02d}. "
                    "Если захочется сказать близкому важное словами, Алиса поможет: письмо с открыткой, "
                    "начало бесплатно.")
            found.append((left, key, text, reminder_markup(
                ("💌 Выбрать письмо", f"cal:h:{hkey}"), f"cal:off:h:{hkey}", {"occ": h["product"]})))
    found.sort(key=lambda x: x[0])
    return [(k, t, m) for _, k, t, m in found]


def run_once(now=None, send=True):
    """Один проход по клиентам. Возвращает число отправленных напоминаний."""
    now = now or B.now_msk()
    if not QUIET_FROM <= now.hour < QUIET_TO:
        return 0
    today = now.date()
    sent_total = 0
    for fname in os.listdir(B.CLIENTS_DIR):
        if not fname.endswith(".json") or not fname[:-5].isdigit():
            continue
        chat_id = int(fname[:-5])
        profile = B.get_client(chat_id)
        cal = (profile or {}).get("cal")
        if not cal or cal.get("inactive") or (not cal.get("dates") and not cal.get("subs")):
            continue
        last = cal.get("last_at")
        if last:
            try:
                if now - datetime.fromisoformat(last) < WEEKLY_GAP:
                    continue
            except ValueError:
                pass
        items = due_for(profile, today)
        if not items:
            continue
        key, text, markup = items[0]  # одно напоминание за проход; остальное — позже по окну
        if not send:
            sent_total += 1
            continue
        try:
            B.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
        except Exception as exc:
            if "403" in str(exc) or "blocked" in str(exc).lower() or "deactivated" in str(exc).lower():
                cal["inactive"] = True
                _save(chat_id, profile)
            else:
                B.log.error("calendar remind %s: %s", chat_id, exc)
            continue
        cal.setdefault("sent", {})[key] = now.isoformat(timespec="seconds")
        cal["last_at"] = now.isoformat(timespec="seconds")
        _save(chat_id, profile)
        log_event(chat_id, "reminded", what=key.split(":")[0])
        sent_total += 1
        time.sleep(0.05)
    return sent_total


def loop():
    mark = os.path.join(B.DATA_DIR, "calendar_last_run.txt")
    while True:
        try:
            now = B.now_msk()
            last = ""
            try:
                with open(mark, encoding="utf-8") as f:
                    last = f.read().strip()
            except OSError:
                pass
            if QUIET_FROM + 1 <= now.hour < QUIET_TO and last != now.date().isoformat():
                run_once(now)
                with open(mark, "w", encoding="utf-8") as f:
                    f.write(now.date().isoformat())
        except Exception as exc:
            B.log.error("calendar loop: %s", exc)
        time.sleep(1200)


def write_cb(call):
    """Из напоминания — сразу в сценарий с предзаполненным именем."""
    chat_id = call.message.chat.id
    did = call.data.split(":", 2)[2]
    profile, cal = get_cal(chat_id)
    B.bot.answer_callback_query(call.id)
    entry = next((d for d in (cal or {}).get("dates", []) if d["id"] == did), None)
    if not entry:
        return
    mark_source(chat_id)
    log_event(chat_id, "clicked", what="date")
    key = KINDS.get(entry["kind"], KINDS["other"])[2]
    if B.pending_count(chat_id) >= B.MAX_PENDING:
        pending = B.pending_orders(chat_id)[0]
        B.bot.send_message(chat_id, "⏳ Сначала оплати или отмени неоплаченное письмо.",
                           reply_markup=B.pending_markup(pending["order_id"]))
        return
    p = B.OCC.PRODUCTS[key]
    qs = B.occ_questions(key)
    first = qs[0]  # вопрос про имя — отвечен заранее
    B.STATES[chat_id] = {"step": "occ_q", "product": key, "idx": 1, "qa": [(first[1], entry["name"])],
                         "data": {first[0]: entry["name"]}}
    B.bot.send_message(chat_id, f"{p['icon']} Пишем для {B.esc(entry['name'])}. Поехали 🤍", parse_mode="HTML")
    B.occ_ask(chat_id)


def holiday_cb(call):
    chat_id = call.message.chat.id
    h = HOLIDAYS.get(call.data.split(":", 2)[2])
    B.bot.answer_callback_query(call.id)
    if not h:
        return
    mark_source(chat_id)
    log_event(chat_id, "clicked", what="holiday")
    B.occ_open_product(chat_id, h["product"])


def off_cb(call):
    chat_id = call.message.chat.id
    _, _, kind, ident = call.data.split(":", 3)
    profile, cal = get_cal(chat_id)
    B.bot.answer_callback_query(call.id, "Больше не напоминаю")
    if profile is None:
        return
    if kind == "d":
        cal["dates"] = [d for d in cal["dates"] if d["id"] != ident]
    else:
        cal["subs"] = [s for s in cal["subs"] if s != ident]
    _save(chat_id, profile)
    log_event(chat_id, "unsub", what=kind)
    _drop_buttons(call)


def cmd_remind(message):
    show_menu(message.chat.id)


# ── метрики ─────────────────────────────────────────────────────
def report(days=30):
    since = (B.now_msk() - timedelta(days=days)).isoformat()
    ev = {}
    try:
        with open(os.path.join(B.DATA_DIR, "calendar_events.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("ts", "") >= since:
                    ev[e["name"]] = ev.get(e["name"], 0) + 1
    except OSError:
        pass
    dates = subs = people = 0
    for fname in B.client_files():
        c = B.read_json(os.path.join(B.CLIENTS_DIR, fname), None) or {}
        cal = c.get("cal") or {}
        if cal.get("dates") or cal.get("subs"):
            people += 1
            dates += len(cal.get("dates", []))
            subs += len(cal.get("subs", []))
    orders = [o for o in B.all_orders() if o.get("from_reminder") and o.get("status") == "done"
              and o.get("created_at", "") >= since]
    return {"days": days, "people": people, "dates": dates, "subs": subs,
            "date_added": ev.get("date_added", 0), "sub_events": ev.get("sub", 0),
            "reminded": ev.get("reminded", 0), "clicked": ev.get("clicked", 0), "unsub": ev.get("unsub", 0),
            "orders": len(orders), "revenue": sum(o.get("price_rub", 0) for o in orders)}


def cmd_stats(message):
    if not B.admin_only(message):
        return
    r = report(30)
    B.bot.send_message(
        message.chat.id,
        "🔔 <b>Календарь дат за 30 дней</b>\n\n"
        f"Подписано людей: {r['people']} (дат: {r['dates']}, праздников: {r['subs']})\n"
        f"Добавлено дат: {r['date_added']}, включено праздников: {r['sub_events']}\n"
        f"Отправлено напоминаний: {r['reminded']}, нажали кнопку: {r['clicked']}\n"
        f"Заказов из напоминаний: {r['orders']} на {r['revenue']}₽\nОтписок: {r['unsub']}",
        parse_mode="HTML")


def register(bot_module):
    global B
    B = bot_module
    bot = B.bot
    cb = bot.callback_query_handler
    cb(func=lambda c: c.data.startswith("cal:add:"))(add_cb)
    cb(func=lambda c: c.data == "cal:no")(no_cb)
    cb(func=lambda c: c.data == "cal:menu")(menu_cb)
    cb(func=lambda c: c.data.startswith("cal:tog:"))(toggle_cb)
    cb(func=lambda c: c.data.startswith("cal:del:"))(delete_cb)
    cb(func=lambda c: c.data == "cal:new")(new_cb)
    cb(func=lambda c: c.data.startswith("cal:kind:"))(kind_cb)
    cb(func=lambda c: c.data.startswith("cal:write:"))(write_cb)
    cb(func=lambda c: c.data.startswith("cal:h:"))(holiday_cb)
    cb(func=lambda c: c.data.startswith("cal:off:"))(off_cb)
    bot.message_handler(
        func=lambda m: B.STATES.get(m.chat.id, {}).get("step") in ("cal_name", "cal_date")
        and m.content_type == "text" and not (m.text or "").startswith("/"))(text_cb)
    bot.message_handler(commands=["remind"])(cmd_remind)
    bot.message_handler(commands=["calstats"])(cmd_stats)
