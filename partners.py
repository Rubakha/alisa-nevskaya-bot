"""Партнёры, промокоды и скидки «Алисы».

Партнёр (блогер, канал, психолог) получает ссылку t.me/<бот>?start=p_<КОД> и промокод с тем же кодом.
Приглашённые закрепляются за ним (первый заход; навсегда, если первая оплата в течение 30 дней),
партнёр получает процент с каждого фактически оплаченного заказа, включая повторные.
Реквизиты карт не храним — только текстовая пометка способа выплаты.

Скидки не складываются: из промокода, скидки партнёра для его аудитории и скидки «Подари подруге» (−10%)
берётся наибольшая; поверх неё списывается накопленный баланс (50 ₽ за каждого оплатившего друга).

Хранение (DATA_DIR): partners/<КОД>.json, promos/<КОД>.json, partner_events.jsonl; привязка клиента —
profile["partner"] = {code, at, locked}. Подключается из bot_best: partners.register(bot_best).
"""
import csv
import io
import json
import os
import re
import secrets
import urllib.parse
from datetime import datetime, timedelta

DEFAULT_PERCENT = 30
DEFAULT_DISCOUNT = 15
FRIEND_DISCOUNT = 10           # «Подари подруге»: −10% другу на первый заказ
FRIEND_BONUS_RUB = 50          # клиенту — за каждого оплатившего друга, на баланс
LOCK_DAYS = 30                 # первая оплата в течение 30 дней закрепляет партнёра навсегда
AVG_CHECK_FALLBACK = 249       # для калькулятора, пока мало реальных заказов
MIN_PAYOUT_RUB = 500           # выплаты — по понедельникам по СБП, от этой суммы
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

B = None


# ── хранилище ───────────────────────────────────────────────────
def _dir(name):
    d = os.path.join(B.DATA_DIR, name)
    os.makedirs(d, exist_ok=True)
    return d


def _norm(code):
    return re.sub(r"[^A-Za-z0-9]", "", code or "").upper()


def partner_path(code):
    return os.path.join(_dir("partners"), f"{_norm(code)}.json")


def promo_path(code):
    return os.path.join(_dir("promos"), f"{_norm(code)}.json")


def get_partner(code):
    code = _norm(code)
    return B.read_json(partner_path(code), None) if code else None


def save_partner(p):
    B.write_json(partner_path(p["code"]), p)


def all_partners():
    out = []
    for f in sorted(os.listdir(_dir("partners"))):
        if f.endswith(".json"):
            p = B.read_json(os.path.join(_dir("partners"), f), None)
            if p:
                out.append(p)
    return out


def partner_by_chat(chat_id):
    return next((p for p in all_partners() if p.get("tg_chat_id") == chat_id), None)


def get_promo(code):
    code = _norm(code)
    return B.read_json(promo_path(code), None) if code else None


def save_promo(p):
    B.write_json(promo_path(p["code"]), p)


def all_promos():
    out = []
    for f in sorted(os.listdir(_dir("promos"))):
        if f.endswith(".json"):
            p = B.read_json(os.path.join(_dir("promos"), f), None)
            if p:
                out.append(p)
    return out


def log_event(chat_id, code, kind, **extra):
    if B.is_test_user(chat_id):
        return
    try:
        with open(os.path.join(B.DATA_DIR, "partner_events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": B.now_msk().isoformat(timespec="seconds"), "chat_id": chat_id,
                                "code": code, "kind": kind, **extra}, ensure_ascii=False) + "\n")
    except OSError as exc:
        B.log.error("partner log: %s", exc)


def read_events():
    out = []
    try:
        with open(os.path.join(B.DATA_DIR, "partner_events.jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def new_code():
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not get_partner(code) and not get_promo(code):
            return code


def link(code):
    return f"https://t.me/{B.BOT_USERNAME}?start=p_{code}"


def invite_link(token):
    return f"https://t.me/{B.BOT_USERNAME}?start=pinvite_{token}"


# ── заказы и первый заказ ───────────────────────────────────────
def paid_orders(chat_id=None):
    return [o for o in B.all_orders() if o.get("status") == "done" and not o.get("is_test")
            and (chat_id is None or o.get("chat_id") == chat_id)]


def is_first_order(chat_id, exclude_order_id=None):
    return not any(o.get("order_id") != exclude_order_id for o in paid_orders(chat_id))


# ── привязка клиента к партнёру ─────────────────────────────────
def touch(chat_id, code, is_new=False):
    """Партнёрский заход. Возвращает партнёра, если заход засчитан, иначе None."""
    p = get_partner(code)
    if not p or p.get("status") != "active":
        return None
    if p.get("tg_chat_id") == chat_id or chat_id == B.ADMIN_ID:
        return None  # свои ссылки не считаем
    profile = B.get_client(chat_id)
    if profile is None:
        return None
    log_event(chat_id, p["code"], "click")
    if is_new and not any(e["chat_id"] == chat_id and e["kind"] == "new" for e in read_events()):
        log_event(chat_id, p["code"], "new")  # «новый» — один раз на человека, даже если /start повторили за минуту
    now = B.now_msk()
    cur = profile.get("partner")
    if cur and cur.get("locked"):
        return p
    if cur:
        try:
            age = now - datetime.fromisoformat(cur["at"])
        except (KeyError, ValueError):
            age = timedelta(days=LOCK_DAYS + 1)
        if age <= timedelta(days=LOCK_DAYS):
            return p  # первый заход сильнее, пока не истёк срок
    elif paid_orders(chat_id):
        return p  # уже платил без партнёра — не перепривязываем
    profile["partner"] = {"code": p["code"], "at": now.isoformat(), "locked": False}
    B.write_json(B.client_path(chat_id), profile)
    return p


def active_attribution(profile, now=None):
    """Действующая привязка: (partner, locked) или (None, False)."""
    cur = (profile or {}).get("partner")
    if not cur:
        return None, False
    p = get_partner(cur["code"])
    if not p or p.get("status") != "active":
        return None, False
    if cur.get("locked"):
        return p, True
    now = now or B.now_msk()
    try:
        if now - datetime.fromisoformat(cur["at"]) > timedelta(days=LOCK_DAYS):
            return None, False
    except (KeyError, ValueError):
        return None, False
    return p, False


# ── промокоды ───────────────────────────────────────────────────
def check_promo(chat_id, code, now=None):
    """(ok, promo/partner-словарь или текст ошибки). Код партнёра работает как промокод со скидкой партнёра."""
    code = _norm(code)
    now = now or B.now_msk()
    promo = get_promo(code)
    if promo:
        if not promo.get("active", True):
            return False, "Этот промокод больше не действует."
        if promo.get("expires") and now.date().isoformat() > promo["expires"]:
            return False, "Срок действия промокода истёк."
        used = promo.get("used", [])
        if promo.get("limit") and len(used) >= promo["limit"]:
            return False, "Промокод уже использовали максимальное число раз."
        if promo.get("once_per_user", True) and any(u["chat_id"] == chat_id for u in used):
            return False, "Ты уже использовал(а) этот промокод."
        if promo.get("partner"):
            pp = get_partner(promo["partner"])
            if not pp or pp.get("status") != "active":
                return False, "Этот промокод больше не действует."
        return True, promo
    p = get_partner(code)
    if p and p.get("status") == "active":
        if not is_first_order(chat_id):  # код партнёра — скидка аудитории только на первый заказ
            return False, "Ты уже использовал(а) этот промокод."
        return True, {"code": p["code"], "kind": "percent", "value": p["discount"], "partner": p["code"],
                      "is_partner_code": True}
    return False, "Не нашла такой промокод. Проверь написание."


def apply_promo_code(chat_id, text):
    """Вызывается при вводе кода клиентом. Возвращает (ok, сообщение). Сохраняет код в профиль на время заказа."""
    ok, res = check_promo(chat_id, text)
    if not ok:
        return False, res
    profile = B.get_client(chat_id)
    if profile is None:
        return False, "Нажми /start и попробуй ещё раз."
    if res.get("partner"):
        touch(chat_id, res["partner"])  # код партнёра/промокод партнёра — заход, как по ссылке
    return True, res


def promo_discount(promo, base):
    if promo["kind"] == "percent":
        return min(base, round(base * promo["value"] / 100))
    return min(base, int(promo["value"]))


def promo_label(promo):
    return f"{promo['value']}%" if promo["kind"] == "percent" else f"{int(promo['value'])}₽"


# ── цена: скидки не складываются + баланс ───────────────────────
def quote(chat_id, base, promo_code=None, now=None):
    """Считает цену для клиента. Ничего не списывает."""
    profile = B.get_client(chat_id) or {}
    first = is_first_order(chat_id)
    cands = []  # (скидка ₽, приоритет, источник, подпись)
    promo = None
    if promo_code:
        ok, res = check_promo(chat_id, promo_code, now)
        if ok:
            promo = res
            cands.append((promo_discount(promo, base), 3, f"promo:{promo['code']}",
                          f"промокод {promo['code']} −{promo_label(promo)}"))
    if first:
        partner, _ = active_attribution(profile, now)
        if partner:
            cands.append((round(base * partner["discount"] / 100), 2, f"partner:{partner['code']}",
                          f"скидка {partner['discount']}% от {partner['name']}"))
        if profile.get("referred_by"):
            cands.append((round(base * FRIEND_DISCOUNT / 100), 1, "friend",
                          f"подарок от друга −{FRIEND_DISCOUNT}%"))
    best = max(cands, key=lambda c: (c[0], c[1]), default=None)
    discount = best[0] if best and best[0] > 0 else 0
    after = base - discount
    balance = int(profile.get("bonus_rub", 0) or 0)
    used = min(balance, after)
    return {"base": base, "discount": discount, "source": best[2] if discount else None,
            "label": best[3] if discount else "", "after_discount": after, "balance_used": used,
            "payable": after - used, "promo_code": promo["code"] if promo and best and best[2].startswith("promo:") else None}


def price_lines(q):
    lines = []
    if q["discount"]:
        lines.append(f"🎟 {q['label']}: −{q['discount']}₽")
    if q["balance_used"]:
        lines.append(f"💰 С баланса: −{q['balance_used']}₽")
    return "\n".join(lines)


def button_price(q):
    if q["payable"] != q["base"]:
        return f"{q['payable']}₽ (было {q['base']}₽)" if q["payable"] else "бесплатно"
    return f"{q['payable']}₽"


def apply_to_order(order, chat_id, promo_code=None):
    """Записывает итоговую цену и основание в заказ. price_rub — то, что реально платит клиент."""
    q = quote(chat_id, order["price_rub"], promo_code)
    order["base_price_rub"] = q["base"]
    order["discount_rub"] = q["discount"]
    order["discount_source"] = q["source"]
    order["promo_code"] = q["promo_code"]
    order["balance_used_rub"] = q["balance_used"]
    order["price_rub"] = q["payable"]
    return q


# ── оплата: закрепление, начисления, промокод, баланс, бонус другу ──
def commission_for(amount, percent):
    return round(amount * percent / 100)


def on_paid(order):
    """Вызывать после того, как заказ отмечен оплаченным. Идемпотентно по заказу."""
    if order.get("is_test") or order.get("partners_done"):
        return
    chat_id = order["chat_id"]
    profile = B.get_client(chat_id)
    if profile is None:
        return
    amount = int(order.get("price_rub") or 0)
    first = is_first_order(chat_id, exclude_order_id=order["order_id"])
    now = B.now_msk()

    # баланс клиента
    used = int(order.get("balance_used_rub") or 0)
    if used:
        profile["bonus_rub"] = max(0, int(profile.get("bonus_rub", 0)) - used)

    # промокод
    if order.get("promo_code"):
        promo = get_promo(order["promo_code"])
        if promo:
            promo.setdefault("used", []).append({"chat_id": chat_id, "order_id": order["order_id"],
                                                 "at": now.isoformat(timespec="seconds"), "amount": amount})
            save_promo(promo)

    # партнёр: закрепление и начисление
    cur = profile.get("partner")
    if cur:
        p = get_partner(cur["code"])
        valid = False
        if p and p.get("status") == "active":
            if cur.get("locked"):
                valid = True
            else:
                try:
                    in_time = now - datetime.fromisoformat(cur["at"]) <= timedelta(days=LOCK_DAYS)
                except (KeyError, ValueError):
                    in_time = False
                if in_time:
                    cur["locked"] = True
                    cur["locked_at"] = now.isoformat(timespec="seconds")
                    valid = True
                else:
                    profile.pop("partner", None)  # пропустил 30 дней — привязка сгорает до следующего захода
        if valid and amount > 0:
            order["partner_code"] = p["code"]
            order["partner_percent"] = p["percent"]
            order["partner_commission"] = commission_for(amount, p["percent"])
            log_event(chat_id, p["code"], "paid", amount=amount, commission=order["partner_commission"])
        elif valid:
            order["partner_code"] = p["code"]
            order["partner_percent"] = p["percent"]
            order["partner_commission"] = 0

    # бонус пригласившему: 50 ₽ за каждого оплатившего друга — один раз, за первый платный заказ
    inviter_id = profile.get("referred_by")
    if inviter_id and first and amount > 0 and inviter_id != chat_id:
        inviter = B.get_client(inviter_id)
        if inviter:
            inviter["bonus_rub"] = int(inviter.get("bonus_rub", 0)) + FRIEND_BONUS_RUB
            B.write_json(B.client_path(inviter_id), inviter)
            order["friend_bonus_to"] = inviter_id
            try:
                B.bot.send_message(
                    inviter_id,
                    f"🎁 {B.esc(profile['name'])} оплатил(а) первое письмо по твоей ссылке.\n"
                    f"Тебе — {FRIEND_BONUS_RUB}₽ на баланс (сейчас {inviter['bonus_rub']}₽): "
                    "они спишутся в твоём следующем заказе.", parse_mode="HTML")
            except Exception:
                pass

    order["partners_done"] = True
    B.write_json(B.client_path(chat_id), profile)
    B.save_order(order)


def refund_order(order_id):
    """Возврат: начисление партнёру сторнируется. Возвращает (ok, текст)."""
    order = B.get_order(order_id)
    if not order:
        return False, "Заказ не найден."
    if order.get("refunded"):
        return False, "Уже отмечен как возврат."
    order["refunded"] = True
    order["refunded_at"] = B.now_msk().isoformat(timespec="seconds")
    order["partner_commission_reversed"] = order.get("partner_commission", 0)
    order["partner_commission"] = 0
    B.save_order(order)
    return True, f"Возврат отмечен. Начисление партнёру сторнировано: {order['partner_commission_reversed']}₽."


# ── статистика ──────────────────────────────────────────────────
def partner_stats(p, events=None, orders=None):
    events = events if events is not None else read_events()
    orders = orders if orders is not None else paid_orders()
    code = p["code"]
    clicks = [e for e in events if e["code"] == code and e["kind"] == "click"]
    mine = [o for o in orders if o.get("partner_code") == code and not o.get("refunded")]
    earned = sum(o.get("partner_commission", 0) for o in mine)
    paid = sum(x["amount"] for x in p.get("payouts", []))
    return {"clicks": len(clicks), "unique": len({e["chat_id"] for e in clicks}),
            "new_users": len([e for e in events if e["code"] == code and e["kind"] == "new"]),
            "orders": len(mine), "turnover": sum(o.get("price_rub", 0) for o in mine),
            "earned": earned, "paid": paid, "due": earned - paid,
            "buyers": len({o["chat_id"] for o in mine})}


def promo_stats(promo):
    used = promo.get("used", [])
    return {"uses": len(used), "turnover": sum(u.get("amount", 0) for u in used)}


def report():
    events, orders = read_events(), paid_orders()
    rows = []
    for p in all_partners():
        st = partner_stats(p, events, orders)
        rows.append({"code": p["code"], "name": p["name"], "percent": p["percent"], "discount": p["discount"],
                     "status": p["status"], **st})
    return {"partners": rows, "promos": [{"code": x["code"], "active": x.get("active", True), **promo_stats(x)}
                                         for x in all_promos()]}


def csv_export():
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["код", "имя", "контакт", "процент", "скидка", "статус", "переходы", "уникальных", "новых",
                "заказов", "оборот", "начислено", "выплачено", "к выплате", "способ выплаты"])
    events, orders = read_events(), paid_orders()
    for p in all_partners():
        s = partner_stats(p, events, orders)
        w.writerow([p["code"], p["name"], p["contact"], p["percent"], p["discount"], p["status"], s["clicks"],
                    s["unique"], s["new_users"], s["orders"], s["turnover"], s["earned"], s["paid"], s["due"],
                    p.get("payout_note", "")])
    return ("﻿" + buf.getvalue()).encode("utf-8")


# ══ интерфейс ═══════════════════════════════════════════════════
def money(n):
    return f"{int(n):,}".replace(",", " ") + " ₽"


def partner_line(p, s):
    flag = "" if p["status"] == "active" else " ⏸"
    return (f"<b>{B.esc(p['name'])}</b>{flag} · {p['percent']}% / скидка {p['discount']}% · <code>{p['code']}</code>\n"
            f"   переходы {s['clicks']} · новые {s['new_users']} · заказы {s['orders']} · оборот {money(s['turnover'])}\n"
            f"   начислено {money(s['earned'])} · выплачено {money(s['paid'])} · <b>к выплате {money(s['due'])}</b>"
            + (" ✅ можно платить" if s["due"] >= MIN_PAYOUT_RUB else
               (f" (ещё копится до {MIN_PAYOUT_RUB} ₽)" if s["due"] > 0 else "")))


def kb_admin_list():
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    for p in all_partners():
        kb.add(B.types.InlineKeyboardButton(f"⚙️ {p['name'][:30]} ({p['code']})", callback_data=f"pa:open:{p['code']}"))
    kb.add(B.types.InlineKeyboardButton("➕ Добавить партнёра", callback_data="pa:add"))
    kb.add(B.types.InlineKeyboardButton("📥 Экспорт CSV", callback_data="pa:csv"))
    return kb


def show_list(chat_id):
    ps = all_partners()
    events, orders = read_events(), paid_orders()
    text = "🤝 <b>Партнёры</b>\n\n" + (
        "\n\n".join(partner_line(p, partner_stats(p, events, orders)) for p in ps) if ps
        else "Партнёров пока нет. Нажми «Добавить партнёра» — бот выдаст ссылку и текст приглашения.")
    B.send_long(chat_id, text, "", kb_admin_list()) if len(text) > 3500 else \
        B.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb_admin_list())


def invite_text(p):
    return (f"Здравствуйте, {p['name']}! Приглашаем вас в партнёрскую программу «Алисы Невской» — личные письма "
            "с открыткой в Telegram.\n\n"
            f"• Вам — {p['percent']}% с каждой оплаты тех, кто пришёл по вашей ссылке, включая повторные заказы.\n"
            f"• Вашей аудитории — скидка {p['discount']}% на первое письмо (ссылка или промокод {p['code']}).\n"
            f"• Человек закрепляется за вами навсегда, если оплатит в течение {LOCK_DAYS} дней после перехода.\n"
            "• В личном кабинете в боте — переходы, заказы, начисления и выплаты в реальном времени.\n"
            f"• Выплата по понедельникам по СБП, от {MIN_PAYOUT_RUB} ₽; каждое начисление видно в кабинете.\n"
            f"• Процент считается от суммы, которую клиент реально заплатил (после скидки).\n\n"
            f"Ваша ссылка: {link(p['code'])}\nПромокод: {p['code']}\n\n"
            f"Чтобы открыть кабинет, нажмите: {invite_link(p['invite_token'])}\n\n"
            "Важно: реклама в вашем канале маркируется (erid) — это ответственность публикующего, "
            "мы можем подсказать, как это сделать.")


def show_partner(chat_id, code, message_id=None):
    p = get_partner(code)
    if not p:
        return
    s = partner_stats(p)
    text = (partner_line(p, s) + f"\n\nКонтакт: {B.esc(p['contact'])}\nВыплата: {B.esc(p.get('payout_note') or '—')}\n"
            + (f"Кабинет привязан ✅" if p.get("tg_chat_id") else "Кабинет не привязан (приглашение не открыто)"))
    kb = B.types.InlineKeyboardMarkup(row_width=2)
    c = p["code"]
    kb.add(B.types.InlineKeyboardButton("✏️ Процент", callback_data=f"pa:pct:{c}"),
           B.types.InlineKeyboardButton("✏️ Скидка", callback_data=f"pa:disc:{c}"))
    kb.add(B.types.InlineKeyboardButton("💸 Отметить выплату", callback_data=f"pa:pay:{c}"),
           B.types.InlineKeyboardButton("📨 Приглашение", callback_data=f"pa:link:{c}"))
    kb.add(B.types.InlineKeyboardButton("▶️ Включить" if p["status"] != "active" else "⏸ Приостановить",
                                        callback_data=f"pa:toggle:{c}"),
           B.types.InlineKeyboardButton("↩️ Список", callback_data="pa:list"))
    if p.get("payouts"):
        text += "\n\n<b>Выплаты</b>\n" + "\n".join(
            f"• {x['date']} — {money(x['amount'])} {B.esc(x.get('comment') or '')}" for x in p["payouts"][-8:])
    B.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)


def admin_only_call(call):
    if call.message.chat.id != B.ADMIN_ID:
        B.bot.answer_callback_query(call.id, "Недоступно")
        return False
    return True


def admin_text_start(message):
    if not B.admin_only(message):
        return
    B.STATES.pop(message.chat.id, None)
    show_list(message.chat.id)


def admin_cb(call):
    if not admin_only_call(call):
        return
    chat_id = call.message.chat.id
    parts = call.data.split(":")
    action = parts[1]
    code = parts[2] if len(parts) > 2 else None
    B.bot.answer_callback_query(call.id)
    if action == "list":
        B.STATES.pop(chat_id, None)
        show_list(chat_id)
    elif action == "csv":
        f = io.BytesIO(csv_export())
        f.name = f"partners_{B.now_msk().date().isoformat()}.csv"
        B.bot.send_document(chat_id, f, caption="Партнёры: цифры на сегодня")
    elif action == "add":
        B.STATES[chat_id] = {"step": "pa_add_name"}
        B.bot.send_message(chat_id, "➕ Новый партнёр. Как его зовут или как называется канал?")
    elif action == "open":
        show_partner(chat_id, code)
    elif action in ("pct", "disc"):
        B.STATES[chat_id] = {"step": f"pa_set_{action}", "code": code}
        B.bot.send_message(chat_id, "Новый процент партнёру (число 1–90)?" if action == "pct"
                           else "Новая скидка для аудитории партнёра (число 0–50)?")
    elif action == "pay":
        p = get_partner(code)
        s = partner_stats(p)
        B.STATES[chat_id] = {"step": "pa_pay_amount", "code": code}
        B.bot.send_message(chat_id, f"К выплате сейчас: {money(s['due'])}. Сколько выплатил(а), ₽?")
    elif action == "toggle":
        p = get_partner(code)
        p["status"] = "paused" if p["status"] == "active" else "active"
        save_partner(p)
        show_partner(chat_id, code)
    elif action == "link":
        p = get_partner(code)
        B.bot.send_message(chat_id, "Текст приглашения — перешли или скопируй партнёру:")
        B.bot.send_message(chat_id, invite_text(p))


def _num(text, lo, hi):
    try:
        v = int(re.sub(r"[^\d]", "", text or ""))
    except ValueError:
        return None
    return v if lo <= v <= hi else None


def admin_steps(message):
    chat_id = message.chat.id
    st = B.STATES[chat_id]
    text = (message.text or "").strip()
    step = st["step"]
    if text == "/cancel":
        B.STATES.pop(chat_id, None)
        B.bot.send_message(chat_id, "Отменено.")
        return
    if step == "pa_add_name":
        st.update(step="pa_add_contact", name=text[:60])
        B.bot.send_message(chat_id, "Контакт партнёра: @username, телефон или ссылка (только для связи).")
    elif step == "pa_add_contact":
        st.update(step="pa_add_pct", contact=text[:100])
        B.bot.send_message(chat_id, f"Процент партнёру? (по умолчанию {DEFAULT_PERCENT}) — число или «-».")
    elif step == "pa_add_pct":
        v = DEFAULT_PERCENT if text == "-" else _num(text, 1, 90)
        if v is None:
            B.bot.send_message(chat_id, "Нужно число от 1 до 90.")
            return
        st.update(step="pa_add_disc", percent=v)
        B.bot.send_message(chat_id, f"Скидка для его аудитории, %? (по умолчанию {DEFAULT_DISCOUNT}) — число или «-».")
    elif step == "pa_add_disc":
        v = DEFAULT_DISCOUNT if text == "-" else _num(text, 0, 50)
        if v is None:
            B.bot.send_message(chat_id, "Нужно число от 0 до 50.")
            return
        st.update(step="pa_add_note", discount=v)
        B.bot.send_message(chat_id, "Способ выплаты — только пометка словами, например «СБП, знает владелец». "
                                    "Номера карт и телефоны сюда не пиши. Или «-».")
    elif step == "pa_add_note":
        if re.search(r"\d{9,}", text.replace(" ", "")):
            B.bot.send_message(chat_id, "Похоже на номер карты или телефона — реквизиты не храним. "
                                        "Напиши пометку словами или «-».")
            return
        code = new_code()
        p = {"code": code, "name": st["name"], "contact": st["contact"], "percent": st["percent"],
             "discount": st["discount"], "status": "active", "payout_note": "" if text == "-" else text[:100],
             "created_at": B.now_msk().isoformat(), "invite_token": secrets.token_urlsafe(8),
             "tg_chat_id": None, "payouts": []}
        save_partner(p)
        B.STATES.pop(chat_id, None)
        B.bot.send_message(chat_id, f"✅ Партнёр добавлен: {B.esc(p['name'])}\nСсылка: {link(code)}\nПромокод: "
                                    f"<code>{code}</code>\n\nТекст приглашения для пересылки 👇", parse_mode="HTML")
        B.bot.send_message(chat_id, invite_text(p))
    elif step in ("pa_set_pct", "pa_set_disc"):
        lo, hi = (1, 90) if step == "pa_set_pct" else (0, 50)
        v = _num(text, lo, hi)
        if v is None:
            B.bot.send_message(chat_id, f"Нужно число от {lo} до {hi}.")
            return
        p = get_partner(st["code"])
        p["percent" if step == "pa_set_pct" else "discount"] = v
        save_partner(p)
        B.STATES.pop(chat_id, None)
        B.bot.send_message(chat_id, "Готово. Новый процент действует на будущие заказы, прошлые начисления не меняются."
                           if step == "pa_set_pct" else "Готово.")
        show_partner(chat_id, p["code"])
    elif step == "pa_pay_amount":
        v = _num(text, 1, 10 ** 7)
        p = get_partner(st["code"])
        due = partner_stats(p)["due"]
        if v is None:
            B.bot.send_message(chat_id, "Нужна сумма в рублях.")
            return
        if due < MIN_PAYOUT_RUB:
            B.STATES.pop(chat_id, None)
            B.bot.send_message(chat_id, f"К выплате {money(due)} — меньше минимума {MIN_PAYOUT_RUB} ₽. "
                                        "Выплата по понедельникам от этой суммы, пока копится.")
            return
        if v > due:
            B.bot.send_message(chat_id, f"Это больше, чем к выплате ({money(due)}). Введи сумму не больше.")
            return
        st.update(step="pa_pay_comment", amount=v)
        B.bot.send_message(chat_id, "Комментарий к выплате (например «СБП за октябрь») или «-».")
    elif step == "pa_pay_comment":
        p = get_partner(st["code"])
        p.setdefault("payouts", []).append({"amount": st["amount"], "date": B.now_msk().strftime("%d.%m.%Y"),
                                            "comment": "" if text == "-" else text[:100],
                                            "at": B.now_msk().isoformat(timespec="seconds")})
        save_partner(p)
        B.STATES.pop(chat_id, None)
        B.bot.send_message(chat_id, f"💸 Выплата отмечена: {money(st['amount'])}.")
        if p.get("tg_chat_id"):
            try:
                B.bot.send_message(p["tg_chat_id"], f"💸 Тебе отметили выплату: {money(st['amount'])}"
                                   + (f" — {B.esc(p['payouts'][-1]['comment'])}" if p["payouts"][-1]["comment"] else ""),
                                   parse_mode="HTML")
            except Exception:
                pass
        show_partner(chat_id, p["code"])


# ── промокоды: админ-команды ────────────────────────────────────
PROMO_HELP = ("🎟 <b>Промокоды</b>\n\n"
              "Создать: <code>/promo_add КОД 20% до 31.12.2026 лимит 50 один партнёр КОД</code>\n"
              "• скидка: <code>20%</code> или фикс <code>100р</code>\n"
              "• необязательно: <code>до ДД.ММ.ГГГГ</code>, <code>лимит N</code>, <code>один</code> "
              "(один раз на человека — по умолчанию), <code>многоразовый</code>, <code>партнёр КОД</code>\n"
              "Отключить: <code>/promo_off КОД</code>. Возврат по заказу: <code>/refund ALI-…</code>")


def parse_promo_args(text):
    toks = (text or "").split()[1:]
    if len(toks) < 2:
        return None, "Нужны код и размер скидки."
    code = _norm(toks[0])
    if not code or len(code) < 3:
        return None, "Код — минимум 3 буквы или цифры."
    promo = {"code": code, "kind": None, "value": 0, "expires": None, "limit": None, "once_per_user": True,
             "partner": None, "active": True, "used": [], "created_at": B.now_msk().isoformat(timespec="seconds")}
    i = 1
    while i < len(toks):
        t = toks[i].lower()
        if t.endswith("%") and t[:-1].isdigit():
            promo.update(kind="percent", value=int(t[:-1]))
        elif re.fullmatch(r"\d+(р|₽|руб)?", t):
            promo.update(kind="fixed", value=int(re.sub(r"\D", "", t)))
        elif t == "до" and i + 1 < len(toks):
            try:
                promo["expires"] = datetime.strptime(toks[i + 1], "%d.%m.%Y").date().isoformat()
            except ValueError:
                return None, "Срок — в формате ДД.ММ.ГГГГ."
            i += 1
        elif t == "лимит" and i + 1 < len(toks) and toks[i + 1].isdigit():
            promo["limit"] = int(toks[i + 1])
            i += 1
        elif t in ("один", "once"):
            promo["once_per_user"] = True
        elif t in ("многоразовый", "multi"):
            promo["once_per_user"] = False
        elif t in ("партнёр", "партнер") and i + 1 < len(toks):
            if not get_partner(toks[i + 1]):
                return None, "Такого партнёра нет."
            promo["partner"] = _norm(toks[i + 1])
            i += 1
        else:
            return None, f"Не понял: «{toks[i]}»."
        i += 1
    if not promo["kind"] or promo["value"] <= 0:
        return None, "Укажи скидку: 20% или 100р."
    if promo["kind"] == "percent" and promo["value"] > 90:
        return None, "Скидка не больше 90%."
    return promo, None


def cmd_promo_add(message):
    if not B.admin_only(message):
        return
    promo, err = parse_promo_args(message.text)
    if err:
        B.bot.send_message(message.chat.id, f"{err}\n\n{PROMO_HELP}", parse_mode="HTML")
        return
    if get_promo(promo["code"]) or get_partner(promo["code"]):
        B.bot.send_message(message.chat.id, "Такой код уже занят.")
        return
    save_promo(promo)
    B.bot.send_message(message.chat.id, f"✅ Промокод <code>{promo['code']}</code> создан: −{promo_label(promo)}"
                       + (f", до {promo['expires']}" if promo["expires"] else "")
                       + (f", лимит {promo['limit']}" if promo["limit"] else "")
                       + (f", партнёр {promo['partner']}" if promo["partner"] else ""), parse_mode="HTML")


def cmd_promo_off(message):
    if not B.admin_only(message):
        return
    parts = (message.text or "").split()
    promo = get_promo(parts[1]) if len(parts) > 1 else None
    if not promo:
        B.bot.send_message(message.chat.id, "Укажи существующий код: /promo_off КОД")
        return
    promo["active"] = False
    save_promo(promo)
    B.bot.send_message(message.chat.id, f"Промокод {promo['code']} отключён.")


def promos_text():
    lines = [PROMO_HELP, ""]
    for p in all_promos():
        s = promo_stats(p)
        lines.append(f"{'✅' if p.get('active', True) else '⏸'} <code>{p['code']}</code> −{promo_label(p)} · "
                     f"использован {s['uses']}" + (f"/{p['limit']}" if p.get("limit") else "")
                     + f" · оборот {money(s['turnover'])}" + (f" · до {p['expires']}" if p.get("expires") else "")
                     + (f" · партнёр {p['partner']}" if p.get("partner") else ""))
    return "\n".join(lines)


def promos_menu(message):
    if B.admin_only(message):
        B.bot.send_message(message.chat.id, promos_text(), parse_mode="HTML")


def cmd_refund(message):
    if not B.admin_only(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        B.bot.send_message(message.chat.id, "Формат: /refund ALI-… (после возврата денег клиенту)")
        return
    ok, text = refund_order(parts[1])
    B.bot.send_message(message.chat.id, text)


# ── клиент: ввод промокода ──────────────────────────────────────
def promo_enter_cb(call):
    chat_id = call.message.chat.id
    st = B.STATES.get(chat_id) or {}
    B.bot.answer_callback_query(call.id)
    if st.get("step") not in ("occ_paywall", "paywall", "occ_wish"):
        B.bot.send_message(chat_id, "Сначала дойди до письма — промокод вводится перед оплатой 🤍")
        return
    st["promo_back"] = st["step"]
    st["step"] = "promo_enter"
    B.bot.send_message(chat_id, "Напиши промокод одним сообщением. /cancel — вернуться к письму.")


def promo_text(message):
    chat_id = message.chat.id
    st = B.STATES[chat_id]
    text = (message.text or "").strip()
    st["step"] = st.pop("promo_back", "occ_paywall" if st.get("product") else "paywall")
    if text.lower() in ("/cancel", "отмена"):
        B.bot.send_message(chat_id, "Хорошо, оставляю как есть.")
        return
    ok, res = apply_promo_code(chat_id, text)
    if not ok:
        B.bot.send_message(chat_id, f"{res}")
        return
    st["promo"] = res["code"]
    base = B.price_base(st)
    q = quote(chat_id, base, st["promo"])
    if q["promo_code"] != res["code"]:
        B.bot.send_message(chat_id, "Промокод принят, но у тебя уже есть скидка побольше — скидки не складываются, "
                                    "я применила лучшую 🤍")
    else:
        B.bot.send_message(chat_id, f"Промокод принят 🤍 Скидка: −{q['discount']}₽")
    B.refresh_paywall(chat_id, st)


# ── партнёр: приглашение и кабинет ──────────────────────────────
def bind_invite(chat_id, token):
    p = next((x for x in all_partners() if x.get("invite_token") == token), None)
    if not p:
        B.bot.send_message(chat_id, "Эта ссылка-приглашение не работает. Напиши тому, кто её прислал.")
        return
    if p.get("tg_chat_id") and p["tg_chat_id"] != chat_id:
        B.bot.send_message(chat_id, "Это приглашение уже использовано другим аккаунтом.")
        return
    first = not p.get("tg_chat_id")
    p["tg_chat_id"] = chat_id
    save_partner(p)
    if first and B.ADMIN_ID:
        try:
            B.bot.send_message(B.ADMIN_ID, f"🤝 Партнёр {B.esc(p['name'])} открыл кабинет.", parse_mode="HTML")
        except Exception:
            pass
    B.bot.send_message(chat_id, f"🤝 Привет, {B.esc(p['name'])}! Кабинет партнёра открыт.", parse_mode="HTML")
    show_cabinet(chat_id, p)


def cabinet_markup():
    kb = B.types.InlineKeyboardMarkup(row_width=2)
    kb.add(B.types.InlineKeyboardButton("🔄 Обновить", callback_data="pt:home"),
           B.types.InlineKeyboardButton("🧮 Калькулятор", callback_data="pt:calc"))
    kb.add(B.types.InlineKeyboardButton("📝 Тексты постов", callback_data="pt:posts"),
           B.types.InlineKeyboardButton("💸 Выплаты", callback_data="pt:pay"))
    kb.add(B.types.InlineKeyboardButton("📦 Набор партнёра (посты и сторис)", callback_data="pt:kit"))
    return kb


def kit_text(p):
    """Набор партнёра из assets/partner_kit.md: ссылка, код и скидка подставлены; erid и рекламодателя
    партнёр вписывает сам."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "partner_kit.md")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return (text.replace("{КОД}", p["code"]).replace("{ссылка}", link(p["code"]))
            .replace("−15%", f"−{p['discount']}%"))


def cabinet_text(p):
    s = partner_stats(p)
    return (f"🤝 <b>Кабинет партнёра · {B.esc(p['name'])}</b>\n\n"
            f"Твоя ссылка:\n<code>{link(p['code'])}</code>\nПромокод: <code>{p['code']}</code>\n\n"
            f"Тебе — <b>{p['percent']}%</b> с каждой оплаты приглашённых, включая повторные. Твоей аудитории — "
            f"скидка {p['discount']}% на первое письмо.\n\n"
            f"Переходы: {s['clicks']} (уникальных {s['unique']})\nНовых пользователей: {s['new_users']}\n"
            f"Заказов: {s['orders']} · покупателей: {s['buyers']}\nОборот: {money(s['turnover'])}\n\n"
            f"Начислено: {money(s['earned'])}\nВыплачено: {money(s['paid'])}\n<b>К выплате: {money(s['due'])}</b>\n\n"
            f"Выплата по понедельникам по СБП, от {MIN_PAYOUT_RUB} ₽. Процент считается от суммы, которую "
            "клиент реально заплатил (после скидки).\n"
            f"Закрепление: если человек оплатит в течение {LOCK_DAYS} дней после перехода — он за тобой навсегда.")


def show_cabinet(chat_id, p, call=None):
    text = cabinet_text(p)
    if call:
        B.safe_edit(call, text, cabinet_markup())
    else:
        B.bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=cabinet_markup())


def avg_check():
    orders = [o for o in paid_orders() if o.get("price_rub", 0) > 0]
    if len(orders) >= 5:
        return round(sum(o["price_rub"] for o in orders) / len(orders))
    return AVG_CHECK_FALLBACK


def calc_text(p):
    check = avg_check()
    rows = []
    for n in (5, 15, 30):
        rows.append(f"• {n} заказов в месяц → <b>{money(n * check * p['percent'] / 100)}</b> в месяц")
    return ("🧮 <b>Сколько можно заработать</b>\n\n"
            f"Средний чек сейчас около {money(check)}, твой процент — {p['percent']}%.\n" + "\n".join(rows) +
            "\n\nЗаказы повторяются: приглашённый закреплён за тобой, и каждое его следующее письмо — "
            "снова твой процент. Это оценка, а не обещание: реальные цифры — в кабинете.")


def posts_text(p):
    lk = link(p["code"])
    return [
        f"Иногда нужные слова не находятся. «Алиса Невская» помогает: отвечаешь на пару вопросов — "
        f"и получаешь личное письмо с открыткой, которое можно отправить близкому человеку. Начало письма бесплатно, "
        f"для моих подписчиков скидка {p['discount']}% на первое: {lk}",
        f"Хочешь сказать маме, подруге или любимому что-то важное, но не знаешь как? Попробуй: бот пишет письмо "
        f"по твоим деталям, а не шаблон. Первое письмо со скидкой {p['discount']}% по моей ссылке: {lk}",
        f"Подарок без магазина: письмо и открытка для человека, которого любишь. Занимает 3 минуты. "
        f"Мой промокод {p['code']} даёт {p['discount']}% на первое письмо: {lk}",
    ]


def cabinet_cb(call):
    chat_id = call.message.chat.id
    p = partner_by_chat(chat_id)
    B.bot.answer_callback_query(call.id)
    if not p:
        return
    action = call.data.split(":")[1]
    if action == "home":
        show_cabinet(chat_id, p, call)
    elif action == "calc":
        B.bot.send_message(chat_id, calc_text(p), parse_mode="HTML")
    elif action == "posts":
        B.bot.send_message(chat_id, "📝 Готовые тексты — копируй любой. Реклама маркируется (erid) — "
                                    "добавь свой erid, как требует закон о рекламе.")
        for t in posts_text(p):
            B.bot.send_message(chat_id, t, disable_web_page_preview=True)
    elif action == "kit":
        f = io.BytesIO(kit_text(p).encode("utf-8"))
        f.name = "partner_kit.txt"
        B.bot.send_document(chat_id, f, caption="Набор партнёра: ссылка и код уже подставлены. Реклама маркируется — "
                                                "рекламодателя и erid впиши перед публикацией.")
    elif action == "pay":
        hist = p.get("payouts", [])
        s = partner_stats(p)
        text = (f"💸 <b>Выплаты</b>\nВыплата по понедельникам по СБП, от {MIN_PAYOUT_RUB} ₽.\n"
                f"К выплате сейчас: <b>{money(s['due'])}</b>"
                + ("" if s["due"] >= MIN_PAYOUT_RUB else f" (копится до {MIN_PAYOUT_RUB} ₽)") + "\n\n") + (
            "\n".join(f"• {x['date']} — {money(x['amount'])} {B.esc(x.get('comment') or '')}" for x in hist[-15:])
            if hist else "Выплат пока не было.")
        B.bot.send_message(chat_id, text, parse_mode="HTML")


def cmd_partner(message):
    p = partner_by_chat(message.chat.id)
    if p:
        show_cabinet(message.chat.id, p)


def offer_cabinet(chat_id):
    """Партнёру при /start — кнопка кабинета."""
    p = partner_by_chat(chat_id)
    if p:
        kb = B.types.InlineKeyboardMarkup()
        kb.add(B.types.InlineKeyboardButton("🤝 Кабинет партнёра", callback_data="pt:home"))
        B.bot.send_message(chat_id, "Ты в партнёрской программе 🤍", reply_markup=kb)


# ── «Подари подруге» ────────────────────────────────────────────
def friend_link(chat_id):
    return f"https://t.me/{B.BOT_USERNAME}?start=ref_{chat_id}"


def friend_gift_markup(chat_id):
    text = f"Дарю тебе −{FRIEND_DISCOUNT}% на первое письмо от Алисы Невской 🤍"
    url = "https://t.me/share/url?url=" + urllib.parse.quote(friend_link(chat_id)) + "&text=" + urllib.parse.quote(text)
    return B.types.InlineKeyboardButton(f"🎁 Подари подруге −{FRIEND_DISCOUNT}%", url=url)


def friend_gift_cb(call):
    chat_id = call.message.chat.id
    B.bot.answer_callback_query(call.id)
    kb = B.types.InlineKeyboardMarkup(row_width=1)
    kb.add(friend_gift_markup(chat_id))
    B.bot.send_message(
        chat_id, f"🎁 <b>Подари подруге −{FRIEND_DISCOUNT}%</b>\n\nОтправь ей свою ссылку — на первое письмо у неё "
                 f"будет скидка {FRIEND_DISCOUNT}%. А тебе — {FRIEND_BONUS_RUB}₽ на баланс за каждую подругу, "
                 "которая оплатит. Баланс спишется в твоём следующем заказе (деньгами не выводится).\n\n"
                 f"<code>{friend_link(chat_id)}</code>", parse_mode="HTML", reply_markup=kb)


# ── подключение к боту ──────────────────────────────────────────
def register(bot_module):
    global B
    B = bot_module
    bot = B.bot
    _dir("partners")
    _dir("promos")
    cb = bot.callback_query_handler
    cb(func=lambda c: c.data.startswith("pa:"))(admin_cb)
    cb(func=lambda c: c.data.startswith("pt:"))(cabinet_cb)
    cb(func=lambda c: c.data == "pr:enter")(promo_enter_cb)
    cb(func=lambda c: c.data == "ref:gift")(friend_gift_cb)
    bot.message_handler(func=lambda m: B.admin_only(m) and m.text == "🤝 Партнёры")(admin_text_start)
    bot.message_handler(func=lambda m: B.admin_only(m) and m.text == "🎟 Промокоды")(promos_menu)
    bot.message_handler(
        func=lambda m: B.admin_only(m) and B.STATES.get(m.chat.id, {}).get("step", "").startswith("pa_")
        and m.content_type == "text")(admin_steps)
    bot.message_handler(
        func=lambda m: B.STATES.get(m.chat.id, {}).get("step") == "promo_enter"
        and m.content_type == "text")(promo_text)
    bot.message_handler(commands=["promo_add"])(cmd_promo_add)
    bot.message_handler(commands=["promo_off"])(cmd_promo_off)
    bot.message_handler(commands=["refund"])(cmd_refund)
    bot.message_handler(commands=["partner"])(cmd_partner)
