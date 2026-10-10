"""«Живая» база открыток: фоны по поводам, статусы, без повторов, метрики.

Файлы фонов лежат в assets/postcards/<повод>/<файл>.jpg (в git). Метаданные,
статусы и статистика — SQLite в DATA_DIR (переживает деплой). Новые фоны из
assets/postcards/catalog.json попадают в базу со статусом review и ждут ✅/❌ владельца;
файлы без записи в каталоге считаются утверждёнными (active).

Ссылка на картинку (card_ref): "<повод>-<файл>" — фон как есть,
"<повод>-<файл>~<n>" — тот же фон в композиции с реквизитом (см. postcards.compose).
"""
import json
import time
import os
import random
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta

VARIANTS = 6  # композиций с реквизитом на каждый активный фон

_db_path = None
_bgs_dir = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY, occasion TEXT NOT NULL, season TEXT DEFAULT 'all', style TEXT DEFAULT 'alisa',
    status TEXT NOT NULL DEFAULT 'active', prompt TEXT DEFAULT '', cost_rub REAL DEFAULT 0,
    created_at TEXT, review_sent INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS user_cards (
    chat_id INTEGER NOT NULL, card_ref TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY (chat_id, card_ref));
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, chat_id INTEGER, name TEXT NOT NULL,
    occasion TEXT, card_ref TEXT);
CREATE INDEX IF NOT EXISTS ev_name_ts ON events (name, ts);
"""


def _connect():
    conn = sqlite3.connect(_db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init(data_dir, bgs_dir):
    global _db_path, _bgs_dir
    os.makedirs(data_dir, exist_ok=True)
    _db_path = os.path.join(data_dir, "cardbase.db")
    _bgs_dir = bgs_dir
    with closing(_connect()) as c:
        c.executescript(SCHEMA)
        c.commit()
    sync_catalog()
    _SYNC.update(sig=_assets_signature(), at=time.time())


def _now():
    return datetime.now().isoformat(timespec="seconds")


def sync_catalog():
    """Заносит в базу новые файлы фонов; существующие записи (и их статусы) не трогает."""
    catalog = {}
    try:
        with open(os.path.join(_bgs_dir, "catalog.json"), encoding="utf-8") as f:
            catalog = {c["id"]: c for c in json.load(f).get("cards", [])}
    except (OSError, ValueError):
        pass
    added = 0
    with closing(_connect()) as c:
        known = {r["id"] for r in c.execute("SELECT id FROM cards")}
        for occ in sorted(os.listdir(_bgs_dir)):
            folder = os.path.join(_bgs_dir, occ)
            if not os.path.isdir(folder):
                continue
            for fname in sorted(os.listdir(folder)):
                if not fname.endswith(".jpg"):
                    continue
                cid = f"{occ}-{fname[:-4]}"
                if cid in known:
                    continue
                meta = catalog.get(cid)
                c.execute(
                    "INSERT INTO cards (id, occasion, season, style, status, prompt, cost_rub, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?)",
                    (cid, occ, (meta or {}).get("season", "all"), (meta or {}).get("style", "alisa"),
                     (meta or {}).get("status", "review") if meta else "active",
                     (meta or {}).get("prompt", ""), (meta or {}).get("cost_rub", 0),
                     (meta or {}).get("created_at", _now())))
                added += 1
        # файл фона удалён из репозитория, а он ещё ждал проверки — больше не предлагаем владельцу
        for r in c.execute("SELECT id FROM cards WHERE status='review'").fetchall():
            if not file_exists(r["id"]):
                c.execute("UPDATE cards SET status='retired' WHERE id=?", (r["id"],))
        c.commit()
    return added


def file_exists(card_id):
    occ, _, stem = card_id.partition("-")
    return os.path.exists(os.path.join(_bgs_dir, occ, f"{stem}.jpg"))


_SYNC = {"sig": None, "at": 0.0}


def _assets_signature():
    """Отпечаток папки фонов: файлы и catalog.json. Меняется, когда штаб кладёт новые картинки."""
    sig = []
    try:
        sig.append(os.path.getmtime(os.path.join(_bgs_dir, "catalog.json")))
    except OSError:
        sig.append(0)
    try:
        for occ in sorted(os.listdir(_bgs_dir)):
            folder = os.path.join(_bgs_dir, occ)
            if os.path.isdir(folder):
                sig.append((occ, len([f for f in os.listdir(folder) if f.endswith(".jpg")])))
    except OSError:
        pass
    return sig


def refresh_if_changed(max_age=30):
    """Новые фоны (assets/postcards/<повод>/*.jpg + catalog.json) появляются без перезапуска: не чаще раза в max_age секунд."""
    now = time.time()
    if now - _SYNC["at"] < max_age:
        return 0
    _SYNC["at"] = now
    sig = _assets_signature()
    if sig == _SYNC["sig"]:
        return 0
    _SYNC["sig"] = sig
    return sync_catalog()


def active_ids(occasion):
    refresh_if_changed()
    with closing(_connect()) as c:
        rows = c.execute("SELECT id FROM cards WHERE occasion=? AND status='active' ORDER BY (cost_rub >= 10) DESC, id", (occasion,))
        return [r["id"] for r in rows if file_exists(r["id"])]


def premium_ids(occasion):
    """Фоны высшего качества (nano-pro, ≥10 ₽ за картинку): в выдаче и в сетке идут первыми."""
    with closing(_connect()) as c:
        rows = c.execute("SELECT id FROM cards WHERE occasion=? AND status='active' AND cost_rub >= 10", (occasion,))
        return {r["id"] for r in rows}


def pick_ref(chat_id, occasion, seen=()):
    """Следующая картинка для превью: новые для человека — первыми, уже взятые — последними."""
    bases = active_ids(occasion)
    if not bases:
        return None
    with closing(_connect()) as c:
        taken = {r["card_ref"]: r["at"] for r in
                 c.execute("SELECT card_ref, at FROM user_cards WHERE chat_id=?", (chat_id,))}
    prem = premium_ids(occasion)
    rnd = random.Random(f"{chat_id}:{occasion}")
    # композиции с реквизитом поверх фото (чашка, веточка, конверт) выглядят «висящими» — по умолчанию выдаём чистые фоны;
    # реквизит появляется, только если клиент сам выбрал его в конструкторе (CARD_PROPS_VARIANTS=1 вернёт старое поведение)
    comps = ([f"{b}~{n}" for b in bases for n in range(1, VARIANTS + 1)]
             if os.getenv("CARD_PROPS_VARIANTS", "").lower() in ("1", "true", "yes") else [])
    ordered = []
    for group in (bases, comps):  # настоящие фоны раньше композиций
        group = sorted(group)
        rnd.shuffle(group)
        group.sort(key=lambda b: 0 if b.split("~")[0] in prem else 1)  # дорогие по виду — вперёд, внутри групп порядок случайный
        ordered += group
    ranked = [r for r in ordered if r not in taken]
    ranked += sorted((r for r in ordered if r in taken), key=lambda r: taken[r])
    for r in ranked:
        if r not in seen:
            return r
    return ranked[0]  # всё уже видели в этой сессии — идём по кругу


def taken_refs(chat_id):
    """Картинки, которые человек уже забрал (оплатил): в сетке выбора они идут последними."""
    with closing(_connect()) as c:
        return {r["card_ref"] for r in c.execute("SELECT card_ref FROM user_cards WHERE chat_id=?", (chat_id,))}


def mark_taken(chat_id, card_ref):
    if not card_ref:
        return
    with closing(_connect()) as c:
        c.execute("INSERT OR REPLACE INTO user_cards (chat_id, card_ref, at) VALUES (?,?,?)",
                  (chat_id, card_ref, _now()))
        c.commit()


def log_event(chat_id, name, occasion=None, card_ref=None):
    with closing(_connect()) as c:
        c.execute("INSERT INTO events (ts, chat_id, name, occasion, card_ref) VALUES (?,?,?,?,?)",
                  (_now(), chat_id, name, occasion, card_ref))
        c.commit()


# ── проверка новых фонов владельцем ─────────────────────────────
def review_queue(only_unsent=True):
    with closing(_connect()) as c:
        q = "SELECT * FROM cards WHERE status='review'" + (" AND review_sent=0" if only_unsent else "")
        return [dict(r) for r in c.execute(q + " ORDER BY occasion, id")]


def mark_review_sent(card_id):
    with closing(_connect()) as c:
        c.execute("UPDATE cards SET review_sent=1 WHERE id=?", (card_id,))
        c.commit()


def set_status(card_id, status):
    assert status in ("active", "review", "retired")
    with closing(_connect()) as c:
        cur = c.execute("UPDATE cards SET status=? WHERE id=?", (status, card_id))
        c.commit()
        return cur.rowcount > 0


def get_card(card_id):
    with closing(_connect()) as c:
        r = c.execute("SELECT * FROM cards WHERE id=?", (card_id,)).fetchone()
        return dict(r) if r else None


# ── метрики ─────────────────────────────────────────────────────
def report(days=30):
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with closing(_connect()) as c:
        counts = {r["name"]: r["n"] for r in c.execute(
            "SELECT name, COUNT(*) n FROM events WHERE ts>=? GROUP BY name", (since,))}
        previews = counts.get("preview", 0)
        per_card = {}
        for r in c.execute("SELECT name, card_ref FROM events WHERE ts>=? AND card_ref IS NOT NULL "
                           "AND name IN ('preview','buy','paid')", (since,)):
            base = r["card_ref"].split("~")[0].split("!")[0]
            d = per_card.setdefault(base, {"preview": 0, "buy": 0, "paid": 0})
            d[r["name"]] += 1
        statuses = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM cards GROUP BY status")}
        custom_paid = c.execute("SELECT COUNT(*) n FROM events WHERE name='paid' AND ts>=? AND card_ref LIKE '%!%'",
                                (since,)).fetchone()["n"]
    top = sorted(per_card.items(), key=lambda kv: (-kv[1]["paid"], -kv[1]["buy"], -kv[1]["preview"]))[:5]
    return {
        "days": days, "previews": previews, "paid": counts.get("paid", 0), "buy": counts.get("buy", 0),
        "conversion": round(100 * counts.get("paid", 0) / previews, 1) if previews else 0.0,
        "pc_next": counts.get("pc_next", 0), "retext": counts.get("retext", 0),
        "retext_wish": counts.get("retext_wish", 0), "statuses": statuses, "top": top,
        "cc_open": counts.get("cc_open", 0), "cc_done": counts.get("cc_done", 0), "cc_paid": custom_paid,
    }


def preview_chats(days):
    """chat_id всех, кому за период показали превью письма с открыткой (тестовые аккаунты в события не пишутся)."""
    since = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    with closing(_connect()) as c:
        return {r["chat_id"] for r in c.execute(
            "SELECT DISTINCT chat_id FROM events WHERE name='preview' AND ts>=?", (since,))}
