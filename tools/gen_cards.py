#!/usr/bin/env python3
"""gen_cards.py — пополнение базы фонов открыток через WaveSpeed. Без Claude, по расписанию или вручную.

Деньги: генерация платная, поэтому скрипт
  * не запускался до 13.10.2026; владелец снял замок 07.10.2026 (START_DATE);
  * не запускается при флаге ai-content-pipeline/.claude/GENERATION_PAUSED;
  * тратит только с флагом --confirm-spend и не выходит за бюджет:
    старт — 500 ₽ до 13.11.2026, дальше до 300 ₽ в месяц (tools/gen_cards_ledger.json);
  * ключ берёт из WAVESPEED_API_KEY или из .claude/generation.local.json пайплайна и нигде не печатает.

Команды:
  python tools/gen_cards.py plan    --occasion birthday --count 3        # промпты и оценка цены, 0 ₽
  python tools/gen_cards.py compare --confirm-spend                      # одна картинка у 3 моделей, выбрать дешёвую
  python tools/gen_cards.py run     --model z-image --occasion birthday --count 3 --confirm-spend [--report]

Новые файлы ложатся в assets/postcards/<повод>/<NNN>.jpg, а записи со статусом review — в
assets/postcards/catalog.json. Бот сам подхватит их, пришлёт владельцу вечером подборку с ✅/❌
(после деплоя: файлы лежат в git). Расход пишется в ledger и в отчёт через ops/done.py.
"""
import argparse
import io
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BGS = ROOT / "assets" / "postcards"
CATALOG = BGS / "catalog.json"
LEDGER = Path(__file__).resolve().parent / "gen_cards_ledger.json"
PIPELINE = Path(os.getenv("ALISA_PIPELINE", r"D:\Рубаха\Боты\Алиса\Claud\ai-content-pipeline"))
DONE_PY = Path(r"D:\Рубаха\Рубаха проект\ops\done.py")
DONE_PYTHON = Path(r"D:\Рубаха\Рубаха проект\venv\Scripts\python.exe")

API = "https://api.wavespeed.ai/api/v3"
START_DATE = date(2026, 10, 7)   # снят замок 13.10 по прямому указанию владельца 07.10
START_BUDGET_UNTIL = date(2026, 11, 13)
START_BUDGET_RUB = 500
MONTH_BUDGET_RUB = 300
USD_RUB = float(os.getenv("USD_RUB", "90"))

# Кандидаты для сравнения. Цены — ориентир с публичных прайсов, реальную цену скрипт меряет по балансу.
# ID моделей сверить на wavespeed.ai/models: неверный ID API отклонит до списания денег.
MODELS = {
    "z-image": {"id": "wavespeed-ai/z-image/turbo", "usd": 0.005,
                "body": lambda p: {"prompt": p, "size": "1280*960", "output_format": "jpeg"}},
    "flux2": {"id": "wavespeed-ai/flux-2-klein-4b/text-to-image", "usd": 0.008,
              "body": lambda p: {"prompt": p, "size": "1280*960", "output_format": "jpeg"}},
    "seedream": {"id": "bytedance/seedream-v4.5", "usd": 0.04,
                 "body": lambda p: {"prompt": p, "size": "2048*1536"}},
    "nano-pro": {"id": "google/nano-banana-pro/text-to-image", "usd": 0.14,
                 "body": lambda p: {"prompt": p, "aspect_ratio": "4:3", "resolution": "1k",
                                    "output_format": "png"}},
}
COMPARE = ["z-image", "flux2", "seedream"]

# Фирменный стиль: references/BRAND_ALISA.md и new_reel_format/DESIGN.md — тёплая бумага, оливка,
# сухоцветы, мягкий свет; без людей и текста, внизу кадра спокойная «пустая» поверхность под плашку открытки.
STYLE = ("Soft natural window light, warm cream and sand palette with muted olive green accents, "
         "analog film look, shallow depth of field, calm editorial still life, elegant and homely, "
         "lower third of the frame is a quiet empty linen surface. No people, no faces, no text, "
         "no letters, no logos, no watermark, no neon, no pure white or pure black, 4:3 landscape.")
SEASONS = {
    "spring": "early spring light, first green branches, pale daffodils",
    "summer": "bright summer morning, wildflowers, open window with light curtains",
    "autumn": "golden autumn light, dried leaves, amber tones",
    "winter": "quiet winter evening, candle glow, evergreen sprigs, soft snow outside the window",
    "all": "timeless cozy interior",
}
SCENES = {
    "birthday": ["a small cream cake with two thin candles beside a peony bouquet in a glass vase",
                 "a wrapped gift tied with olive satin ribbon next to a lit candle and a linen napkin",
                 "a table set for two with tea cups, a slice of cake and dried flowers"],
    "love": ["two tea cups and a folded handwritten letter with a dried rose on linen",
             "a vintage envelope sealed with olive wax beside two candles",
             "a single stem of gypsophila lying across a handwritten love letter"],
    "friend": ["two mismatched ceramic mugs and a plate of cookies on a sunny windowsill",
               "a worn book, two cups and a soft blanket on a sofa",
               "a picnic basket, a thermos and wildflowers on a linen cloth"],
    "family": ["a family table with a teapot, a jar of jam and a vase of daisies",
               "an armchair with a knitted plaid, a cup of tea and a stack of old books",
               "a wooden kitchen table with fresh bread, a linen towel and wheat ears"],
    "thanks": ["a bouquet of dried flowers tied with twine next to a handwritten note card",
               "a cup of tea, a small jar of honey and a gift tag on linen",
               "a warm still life with an olive branch, a ceramic bowl and a folded napkin"],
    "sorry": ["an olive branch laid across an open empty notebook and a quiet cup of tea",
              "a single white flower in a small vase on a windowsill after rain",
              "a folded letter beside a candle, soft muted light, calm mood"],
    "support": ["a lit candle, a woolen blanket and a steaming cup on a calm evening table",
                "a window with raindrops, a warm lamp and an open book beside a cup of tea",
                "a pair of knitted socks, a candle and a small bouquet of lavender"],
    "toast": ["two crystal glasses and a bottle of sparkling wine with a sprig of rosemary on linen",
              "a festive table with candles, glasses and dried citrus slices",
              "two champagne glasses, ribbons and warm golden light"],
    "newyear": ["a fir branch with warm string lights, a candle and mandarins on linen",
                "a window with garland lights, a cup of cocoa and a wrapped gift",
                "a quiet winter table with pine cones, candles and cinnamon sticks"],
    "santa": ["a red-ribbon wrapped gift, a candle, fir sprigs and a handwritten letter",
              "a snowy window sill with a lantern, a letter and pine branches",
              "a wooden table with cookies, milk glass, a letter and tiny lights"],
}


def die(msg):
    sys.exit(f"gen_cards: {msg}")


def load_key():
    key = os.getenv("WAVESPEED_API_KEY", "").strip()
    if not key:
        cfg = PIPELINE / ".claude" / "generation.local.json"
        try:
            key = json.loads(cfg.read_text(encoding="utf-8")).get("wavespeed_api_key", "").strip()
        except (OSError, ValueError):
            key = ""
    if not key:
        die("нет ключа WaveSpeed: задай WAVESPEED_API_KEY или wavespeed_api_key в generation.local.json")
    return key


def request(key, url, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        die(f"WaveSpeed API {e.code}: {e.read().decode('utf-8', errors='replace')[:300]}")


def balance_usd(key):
    try:
        return float(request(key, f"{API}/balance")["data"]["balance"])
    except (KeyError, TypeError, ValueError):
        return None


# ── бюджет ──────────────────────────────────────────────────────
def ledger():
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"entries": []}


def remaining_rub(today=None):
    today = today or date.today()
    entries = ledger()["entries"]
    if today < START_BUDGET_UNTIL:
        spent = sum(e["rub"] for e in entries)
        return START_BUDGET_RUB - spent
    month = today.strftime("%Y-%m")
    spent = sum(e["rub"] for e in entries if e["ts"].startswith(month))
    return MONTH_BUDGET_RUB - spent


def add_ledger(entry):
    data = ledger()
    data["entries"].append(entry)
    LEDGER.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def guards(confirm_spend, estimate_rub):
    if date.today() < START_DATE:
        die(f"платная генерация разрешена с {START_DATE:%d.%m.%Y}")
    flag = PIPELINE / ".claude" / "GENERATION_PAUSED"
    if flag.exists():
        die("генерация остановлена владельцем (GENERATION_PAUSED). Не обходить.")
    if not confirm_spend:
        die(f"оценка ≈ {estimate_rub:.0f} ₽. Добавь --confirm-spend, чтобы потратить.")
    left = remaining_rub()
    if estimate_rub > left:
        die(f"не хватает бюджета: нужно ≈ {estimate_rub:.0f} ₽, осталось {left:.0f} ₽")


# ── промпты ─────────────────────────────────────────────────────
def build_prompt(occasion, index, season):
    scenes = SCENES[occasion]
    return f"{scenes[index % len(scenes)]}, {SEASONS[season]}. {STYLE}"


def next_stem(occasion):
    folder = BGS / occasion
    nums = [int(p.stem) for p in folder.glob("*.jpg") if p.stem.isdigit()] if folder.exists() else []
    return f"{max(nums, default=0) + 1:03d}"


def load_catalog():
    try:
        return json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cards": []}


# ── генерация ───────────────────────────────────────────────────
def generate(key, model_name, prompt, extra=None):
    """Возвращает (bytes картинки, потрачено USD по балансу или None)."""
    m = MODELS[model_name]
    body = m["body"](prompt)
    body.update(extra or {})
    before = balance_usd(key)
    task = request(key, f"{API}/{m['id']}", "POST", body)["data"]["id"]
    deadline = time.time() + 300
    while time.time() < deadline:
        res = request(key, f"{API}/predictions/{task}/result")["data"]
        if res["status"] == "completed":
            with urllib.request.urlopen(res["outputs"][0], timeout=120) as r:
                img = r.read()
            after = balance_usd(key)
            spent = round(before - after, 4) if before is not None and after is not None else None
            return img, spent
        if res["status"] in ("failed", "cancelled", "timeout", "deleted"):
            die(f"генерация не удалась: {res['status']}")
        time.sleep(3)
    die(f"таймаут задачи {task}")


def to_card_jpeg(raw):
    from PIL import Image
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = 1080, 806
    scale = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 2
    im = im.crop((x, y, x + w, y + h))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88, optimize=True)
    return out.getvalue()


def report_done(what, fail=None):
    if not DONE_PY.exists():
        print("done.py не найден — отчёт пропущен")
        return
    py = str(DONE_PYTHON) if DONE_PYTHON.exists() else sys.executable
    cmd = [py, str(DONE_PY), "--project", "Алиса", "--what", what]
    if fail:
        cmd += ["--fail", fail]
    subprocess.run(cmd, check=False)


# ── команды ─────────────────────────────────────────────────────
def cmd_plan(a):
    m = MODELS[a.model]
    est = a.count * m["usd"] * USD_RUB
    print(f"модель {a.model} ({m['id']}), ≈ {m['usd']} $/шт → {a.count} шт ≈ {est:.0f} ₽ (курс {USD_RUB}); "
          f"бюджет осталось {remaining_rub():.0f} ₽")
    for i in range(a.count):
        print(f"\n[{a.occasion} #{i + 1}] {build_prompt(a.occasion, i, a.season)}")


def cmd_compare(a):
    est = sum(MODELS[n]["usd"] for n in COMPARE) * USD_RUB
    guards(a.confirm_spend, est)
    key = load_key()
    out_dir = Path(__file__).resolve().parent / "_compare"
    out_dir.mkdir(exist_ok=True)
    prompt = build_prompt("birthday", 0, "autumn")
    total = 0.0
    for name in COMPARE:
        img, spent = generate(key, name, prompt)
        (out_dir / f"{name}.jpg").write_bytes(to_card_jpeg(img))
        usd = spent if spent is not None else MODELS[name]["usd"]
        total += usd
        add_ledger({"ts": datetime.now().isoformat(timespec="seconds"), "model": name, "occasion": "compare",
                    "card_id": "-", "usd": usd, "rub": round(usd * USD_RUB, 2)})
        print(f"{name}: {usd} $ ({usd * USD_RUB:.1f} ₽) → {out_dir / (name + '.jpg')}")
    print(f"\nИтого {total:.3f} $ ≈ {total * USD_RUB:.1f} ₽. Посмотри файлы, выбери модель и запиши цену в LESSONS.md.")
    report_done(f"Сравнение моделей для фонов открыток: {total * USD_RUB:.0f} ₽ расход")


def cmd_run(a):
    m = MODELS[a.model]
    est = a.count * m["usd"] * USD_RUB
    guards(a.confirm_spend, est)
    key = load_key()
    catalog = load_catalog()
    spent_rub = 0.0
    made = 0
    for i in range(a.count):
        if remaining_rub() < m["usd"] * USD_RUB:
            print("бюджет исчерпан — останавливаюсь")
            break
        stem = next_stem(a.occasion)
        prompt = build_prompt(a.occasion, i + int(stem), a.season)
        img, spent = generate(key, a.model, prompt)
        usd = spent if spent is not None else m["usd"]
        rub = round(usd * USD_RUB, 2)
        path = BGS / a.occasion / f"{stem}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(to_card_jpeg(img))
        card_id = f"{a.occasion}-{stem}"
        catalog["cards"].append({"id": card_id, "occasion": a.occasion, "season": a.season, "style": "alisa",
                                 "status": "review", "prompt": prompt, "model": a.model, "cost_rub": rub,
                                 "created_at": datetime.now().isoformat(timespec="seconds")})
        CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8")
        add_ledger({"ts": datetime.now().isoformat(timespec="seconds"), "model": a.model,
                    "occasion": a.occasion, "card_id": card_id, "usd": usd, "rub": rub})
        spent_rub += rub
        made += 1
        print(f"{card_id}: {rub} ₽ → {path}")
    print(f"\nГотово: {made} шт, {spent_rub:.1f} ₽. Осталось бюджета {remaining_rub():.0f} ₽. "
          "Дальше: git add/commit/push (деплой), бот пришлёт подборку владельцу вечером.")
    if a.report and made:
        report_done(f"Фоны открыток ({a.occasion}): {made} шт, расход {spent_rub:.0f} ₽ (WaveSpeed, {a.model})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("plan", cmd_plan), ("compare", cmd_compare), ("run", cmd_run)):
        p = sub.add_parser(name)
        p.add_argument("--model", default="z-image", choices=list(MODELS))
        p.add_argument("--occasion", default="birthday", choices=list(SCENES))
        p.add_argument("--season", default="all", choices=list(SEASONS))
        p.add_argument("--count", type=int, default=1)
        p.add_argument("--confirm-spend", action="store_true")
        p.add_argument("--report", action="store_true", help="записать итог в отчёт через done.py")
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
