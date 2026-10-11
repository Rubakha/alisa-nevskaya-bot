"""B2B без Telegram: проекты на диске, разбор списка, логотип и открытки, пакетная генерация, ZIP и таблица.

Проект хранится в DATA_DIR/b2b/<id>/project.json, файлы рядом: logo.png, cards/NNN.jpg, results.json.
Telegram-слой — b2b.py; здесь всё проверяется тестами без сети.
"""
import csv
import io
import json
import os
import re
import secrets
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image, ImageDraw

import b2b_ai
import b2b_config as C
import postcards

_LOCK = threading.Lock()
ROOT = None  # DATA_DIR/b2b, задаётся configure()


def configure(data_dir):
    global ROOT
    ROOT = os.path.join(data_dir, "b2b")
    os.makedirs(ROOT, exist_ok=True)


# ── проекты ─────────────────────────────────────────────────────
def new_id():
    while True:
        pid = "B2B-" + secrets.token_hex(3).upper()
        if not os.path.exists(os.path.join(ROOT, pid)):
            return pid


def pdir(pid):
    return os.path.join(ROOT, pid)


def load(pid):
    if not re.fullmatch(r"B2B-[0-9A-F]{6}", pid or ""):
        return None
    try:
        with open(os.path.join(pdir(pid), "project.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(p):
    os.makedirs(pdir(p["id"]), exist_ok=True)
    path = os.path.join(pdir(p["id"]), "project.json")
    with _LOCK:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
        os.replace(path + ".tmp", path)


def all_projects():
    out = []
    for name in sorted(os.listdir(ROOT)) if ROOT and os.path.isdir(ROOT) else []:
        p = load(name)
        if p:
            out.append(p)
    return out


def create(chat_id, kind, **extra):
    p = {"id": new_id(), "chat_id": chat_id, "kind": kind, "status": "draft",
         "created_at": datetime.now().isoformat(timespec="seconds"), "people": [], "previews": [],
         "preview_rounds": 0, "results": {}, "order_id": None, "company": "", "tone": "warm",
         "sign": "", "has_logo": False, "dept_card": False, "wish": ""}
    p.update(extra)
    save(p)
    return p


# ── список людей ────────────────────────────────────────────────
HEADER_WORDS = {"имя", "фио", "name", "сотрудник", "клиент", "фамилия имя"}
MAX_ROWS = 2000


def _row(cells):
    cells = [re.sub(r"\s+", " ", str(c or "")).strip() for c in cells]
    if not cells or not cells[0]:
        return None
    return {"name": cells[0][:60], "role": (cells[1] if len(cells) > 1 else "")[:80],
            "detail": (cells[2] if len(cells) > 2 else "")[:200]}


def _rows(rows):
    people = []
    for i, cells in enumerate(rows):
        r = _row(cells)
        if not r:
            continue
        if i == 0 and r["name"].lower() in HEADER_WORDS:
            continue
        people.append(r)
        if len(people) >= MAX_ROWS:
            break
    return people


def parse_text(text):
    """Строки «Имя, роль, деталь»; разделитель — запятая, точка с запятой, табуляция или « | »."""
    rows = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = re.split(r"\t|;|\||,", line, maxsplit=2)
        rows.append(parts)
    return _rows(rows)


def parse_csv(data: bytes):
    text = data.decode("utf-8-sig", errors="replace")
    sample = text[:2000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return _rows(list(csv.reader(io.StringIO(text), dialect)))


def parse_xlsx(data: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for row in ws.iter_rows(values_only=True, max_row=MAX_ROWS + 1):
        rows.append(["" if c is None else c for c in row[:3]])
    wb.close()
    return _rows(rows)


def parse_file(filename, data: bytes):
    name = (filename or "").lower()
    if name.endswith(".xlsx"):
        return parse_xlsx(data)
    if name.endswith((".csv", ".txt", ".tsv")):
        return parse_csv(data) if not name.endswith(".txt") else parse_text(data.decode("utf-8-sig", "replace"))
    raise ValueError("format")


# ── логотип и открытки ──────────────────────────────────────────
def save_logo(pid, data: bytes):
    """Проверяет, что это картинка, и сохраняет перекодированным PNG (никаких исходных байт на диск)."""
    im = Image.open(io.BytesIO(data))
    im.load()
    if im.width * im.height > 25_000_000:
        raise ValueError("too big")
    im = im.convert("RGBA")
    im.thumbnail((600, 300))
    os.makedirs(pdir(pid), exist_ok=True)
    im.save(os.path.join(pdir(pid), "logo.png"), "PNG")


def logo_path(p):
    path = os.path.join(pdir(p["id"]), "logo.png")
    return path if p.get("has_logo") and os.path.exists(path) else None


def add_logo(jpeg: bytes, logo_file, quality=85) -> bytes:
    """Логотип в левом верхнем углу фото на светлой плашке. Без логотипа — только пережатие."""
    card = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if logo_file:
        logo = Image.open(logo_file).convert("RGBA")
        logo.thumbnail((260, 110))
        pad = 18
        plate = Image.new("RGBA", (logo.width + 2 * pad, logo.height + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(plate).rounded_rectangle([0, 0, plate.width - 1, plate.height - 1], radius=18,
                                                fill=(250, 246, 238, 225))
        plate.alpha_composite(logo, (pad, pad))
        base = card.convert("RGBA")
        base.alpha_composite(plate, (40, 40))
        card = base.convert("RGB")
    buf = io.BytesIO()
    card.save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def card_ref(i):
    """Фон открытки для i-го получателя: первый фон повода в разных композициях с реквизитом."""
    base = postcards.first_ref(C.CARD_PRODUCT)
    if not base:
        return None
    return f"{base}~{(i % C.CARD_SEED_VARIANTS) + 1}"


def render_card(p, i, letter, preview=False):
    jpeg = postcards.render(C.CARD_PRODUCT, letter["card_title"], letter["card_line"],
                            _plain_sign(p), preview=preview, card_ref=card_ref(i))
    return add_logo(jpeg, logo_path(p))


def _plain_sign(p):
    return (p.get("sign") or p.get("company") or "")[:60]


# ── пакетная генерация ──────────────────────────────────────────
def people_for_run(p):
    """Список к генерации: люди + (по желанию) общая открытка отдела. Индекс — стабильный ключ результата."""
    items = [dict(x, idx=i) for i, x in enumerate(p["people"])]
    if p["kind"] == "team" and p.get("dept_card"):
        items.append({"name": "Команда", "role": "", "detail": "", "dept": True, "idx": len(items)})
    return items


def _cards_dir(pid):
    d = os.path.join(pdir(pid), "cards")
    os.makedirs(d, exist_ok=True)
    return d


def _results_path(pid):
    return os.path.join(pdir(pid), "results.json")


def load_results(pid):
    try:
        with open(_results_path(pid), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_results(pid, results):
    with _LOCK:
        with open(_results_path(pid) + ".tmp", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False)
        os.replace(_results_path(pid) + ".tmp", _results_path(pid))


def run_batch(p, on_progress=None, sleep=time.sleep, generate=None, render=None):
    """Генерирует письма и открытки для тех, кого ещё нет в results (поэтому безопасно перезапускать).

    Параллельность ограничена C.PARALLEL, на каждого — до C.RETRIES попыток с паузой 2, 4 с…
    on_progress(done, total, failed) вызывается после каждого человека. Возвращает (done, failed).
    """
    generate = generate or b2b_ai.generate_letter
    render = render or render_card
    items = people_for_run(p)
    results = load_results(p["id"])
    todo = [it for it in items if not (results.get(str(it["idx"])) or {}).get("letter")]
    state = {"done": len(items) - len(todo), "failed": 0, "since_save": 0}
    lock = threading.Lock()
    cards = _cards_dir(p["id"])

    def work(it):
        err = ""
        for attempt in range(C.RETRIES):
            try:
                letter = generate(p, it, wish=p.get("wish", ""))
                jpeg = render(p, it["idx"], letter)
                with open(os.path.join(cards, f"{it['idx'] + 1:04d}.jpg"), "wb") as f:
                    f.write(jpeg)
                rec = {"letter": letter["letter"], "card_title": letter["card_title"],
                       "card_line": letter["card_line"]}
                break
            except Exception as exc:  # сеть, лимиты, отказ модели — пробуем ещё раз
                err = str(exc)[:200]
                rec = None
                if attempt + 1 < C.RETRIES:
                    sleep(C.RETRY_PAUSE * (attempt + 1))
        with lock:
            if rec:
                results[str(it["idx"])] = rec
                state["done"] += 1
            else:
                results[str(it["idx"])] = {"error": err or "unknown"}
                state["failed"] += 1
            state["since_save"] += 1
            if state["since_save"] >= 10:
                save_results(p["id"], results)
                state["since_save"] = 0
            snapshot = (state["done"], len(items), state["failed"])
        if on_progress:
            try:
                on_progress(*snapshot)
            except Exception:
                pass

    with ThreadPoolExecutor(max_workers=max(1, C.PARALLEL)) as pool:
        list(pool.map(work, todo))
    save_results(p["id"], results)
    return state["done"], state["failed"]


# ── выдача: таблица и ZIP ───────────────────────────────────────
def safe_cell(v):
    """Защита от формул в Excel/Sheets: ячейка не должна начинаться с = + - @."""
    v = "" if v is None else str(v)
    return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


def table_rows(p):
    results = load_results(p["id"])
    rows = []
    for it in people_for_run(p):
        r = results.get(str(it["idx"])) or {}
        rows.append([it["idx"] + 1, it["name"], it.get("role", ""), r.get("letter") or "",
                     f"{it['idx'] + 1:04d}.jpg" if r.get("letter") else "", r.get("error", "")])
    return rows


TABLE_HEAD = ["№", "Имя", "Роль", "Текст письма", "Файл открытки", "Ошибка (если письмо не получилось)"]


def table_xlsx(p) -> bytes:
    import openpyxl
    from openpyxl.styles import Alignment
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Письма"
    ws.append(TABLE_HEAD)
    for row in table_rows(p):
        ws.append([safe_cell(c) if isinstance(c, str) else c for c in row])
    ws.column_dimensions["B"].width = 24
    ws.column_dimensions["D"].width = 90
    for r in ws.iter_rows(min_row=2):
        r[3].alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def table_csv(p) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(TABLE_HEAD)
    for row in table_rows(p):
        w.writerow([safe_cell(c) if isinstance(c, str) else c for c in row])
    return ("﻿" + buf.getvalue()).encode("utf-8")


def file_stem(i, name):
    s = re.sub(r"[^\w\- ]", "", name, flags=re.UNICODE).strip().replace(" ", "_")[:40] or "card"
    return f"{i:04d}_{s}"


def build_zips(p, skip=(), tag=""):
    """Возвращает список (имя_файла, bytes) — ZIP-части до C.ZIP_PART_BYTES с открытками «NNNN_Имя.jpg»."""
    cards = _cards_dir(p["id"])
    results = load_results(p["id"])
    parts, cur, size = [], [], 0
    for it in people_for_run(p):
        if not (results.get(str(it["idx"])) or {}).get("letter") or it["idx"] in skip:
            continue
        path = os.path.join(cards, f"{it['idx'] + 1:04d}.jpg")
        if not os.path.exists(path):
            continue
        n = os.path.getsize(path)
        if cur and size + n > C.ZIP_PART_BYTES:
            parts.append(cur)
            cur, size = [], 0
        cur.append((file_stem(it["idx"] + 1, it["name"]) + ".jpg", path))
        size += n
    if cur:
        parts.append(cur)
    out = []
    for k, files in enumerate(parts, 1):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for arc, path in files:
                z.write(path, arc)
        suffix = tag + (f"_часть{k}" if len(parts) > 1 else "")
        out.append((f"{p['id']}_открытки{suffix}.zip", buf.getvalue()))
    return out
