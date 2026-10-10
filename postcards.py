"""Открытка к письму: фото-фон сверху, «бумага» снизу, заголовок с именем,
рукописная строка и подпись. PIL, без внешних сервисов.

render(product_key, title, line, sign, preview=False, card_ref=None) -> bytes (JPEG 1080×1350)

card_ref — ссылка на картинку из базы (cardbase): "<повод>-<файл>" — фон как есть,
"<повод>-<файл>~<seed>" — тот же фон в композиции с реквизитом Алисы (цветокоррекция
и раскладка выбираются детерминированно по seed, поэтому вариант воспроизводится).
Без card_ref берётся первый фон повода.
"""
import io
import os
import random
import re

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
FONTS = os.path.join(BASE, "assets", "fonts")
BGS = os.path.join(BASE, "assets", "postcards")
PROPS = os.path.join(BASE, "assets", "props")
AI_DIR = os.path.join(BASE, "data", "ai_cards")  # переопределяется ботом: DATA_DIR/ai_cards (ссылки «ai:<заказ>_<n>»)

W, H = 1080, 1350
PHOTO_H = 800
PAPER = (247, 241, 231)
INK = (52, 42, 34)
INK_SOFT = (96, 74, 58)
ACCENT = (150, 58, 52)  # сургучно-красный


# Шрифты открытки: заголовки — Playfair Display, имя и подпись — Bad Script (рукописный), мелкий текст — Manrope
PLAYFAIR = "PlayfairDisplay-Variable.ttf"
PLAYFAIR_ITALIC = "PlayfairDisplay-Italic-Variable.ttf"
SCRIPT = "BadScript-Regular.ttf"
MANROPE = "Manrope-Variable.ttf"
FONT_FILES = (PLAYFAIR, PLAYFAIR_ITALIC, SCRIPT, MANROPE)


def _font(name, size, variation=None):
    """Шрифт нужного размера. variation: число — вес переменного шрифта (set_variation_by_axes), строка — имя начертания.
    Если вариация недоступна, остаётся начертание по умолчанию."""
    f = ImageFont.truetype(os.path.join(FONTS, name), size)
    if variation:
        try:
            if isinstance(variation, (int, float)):
                f.set_variation_by_axes([variation])
            else:
                f.set_variation_by_name(variation)
        except Exception:
            pass
    return f


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, fname, variation, start, minimum, max_w, max_lines):
    size = start
    while size >= minimum:
        f = _font(fname, size, variation)
        lines = _wrap(draw, text, f, max_w)
        if len(lines) <= max_lines:
            return f, lines
        size -= 2
    f = _font(fname, minimum, variation)
    return f, _wrap(draw, text, f, max_w)[:max_lines]


def split_ref(card_ref):
    """'birthday-001~3' -> ('birthday-001', 3); без ~ seed = 0. Ссылка конструктора 'base!spec' -> ('base', 0)."""
    base, _, seed = (card_ref or "").partition("~")
    base = base.partition("!")[0]
    return base, int(seed) if seed.isdigit() else 0


# ── конструктор: фон + палитра + реквизит + раскладка + рамка ───────────────
LAYOUTS = ["corners", "left", "right", "top", "bottom"]
FRAMES = ["none", "thin", "double"]
MAX_PROPS = 3


ANIMS = ["none", "sparkle", "petals", "snow", "bokeh"]


def encode_spec(base, pal="none", props=(), lay="corners", frame="none", anim="none"):
    """Ссылка на картинку, собранную в конструкторе: '<фон>!<палитра>.<реквизит+реквизит>.<раскладка>.<рамка>[.<анимация>]'.
    Пятое поле есть только у анимированных открыток — прежние ссылки остаются прежними."""
    ref = f"{base}!{pal}.{'+'.join(props) or '-'}.{lay}.{frame}"
    return ref + f".{anim}" if anim and anim != "none" else ref


def parse_spec(card_ref):
    """'birthday-001!warm.ribbon_bow+tea_cup.left.thin.snow' -> dict либо None, если это не ссылка конструктора."""
    base, bang, spec = (card_ref or "").partition("!")
    if not bang:
        return None
    base = base.partition("~")[0]
    parts = spec.split(".")
    if len(parts) not in (4, 5):
        return None
    pal, props, lay, frame = parts[:4]
    anim = parts[4] if len(parts) == 5 else "none"
    available = available_props()
    props = [x for x in props.split("+") if x in available][:MAX_PROPS]
    return {"base": base, "pal": pal if pal in GRADES else "none", "props": props,
            "lay": lay if lay in LAYOUTS else "corners", "frame": frame if frame in FRAMES else "none",
            "anim": anim if anim in ANIMS else "none"}


def bg_path(card_id):
    """Путь к файлу фона по id карточки '<повод>-<файл>'."""
    occ, _, stem = card_id.partition("-")
    return os.path.join(BGS, occ, f"{stem}.jpg")


def first_ref(product_key):
    folder = os.path.join(BGS, product_key)
    files = sorted(f for f in os.listdir(folder) if f.endswith(".jpg")) if os.path.isdir(folder) else []
    return f"{product_key}-{files[0][:-4]}" if files else None


GRADES = {
    "none": None,
    "warm": ((255, 226, 190), 0.16),
    "soft": ((236, 238, 250), 0.14),
    "sepia": ((214, 188, 150), 0.24),
    "rose": ((244, 208, 208), 0.18),
    "sage": ((206, 222, 200), 0.18),
}
PROP_FILES = ["dried_bouquet", "olive_branch", "ribbon_bow", "envelope_cream", "envelope_kraft",
              "gift_tag", "lace_doily", "stamps_key", "tea_cup", "postcard"]   # прежний набор: от него зависят старые композиции «~n»
_PROP_NAME = re.compile(r"^[a-z0-9_]{1,40}$")   # имя файла идёт в ссылку открытки: без точек, плюсов и дефисов


def available_props():
    """Реквизит из assets/props/*.png: новые файлы подхватываются без правки кода. Прежние — первыми и в прежнем порядке."""
    try:
        names = {f[:-4] for f in os.listdir(PROPS) if f.endswith(".png") and _PROP_NAME.match(f[:-4])}
    except OSError:
        names = set()
    return [n for n in PROP_FILES if n in names] + sorted(names - set(PROP_FILES))
CORNERS = ["bl", "br", "tl", "tr"]


def _grade(im, name):
    spec = GRADES.get(name)
    if not spec:
        return im
    tint, k = spec
    return Image.blend(im, Image.new("RGB", im.size, tint), k)


MAX_PROP_W = 300      # предмет не шире этого: в углу он не заходит на центр кадра, где лица
TILT = 12             # наклон в конструкторе — лёгкий, предмет лежит «по-человечески»


def _fit_prop(prop, h):
    """Высота h, но не шире MAX_PROP_W (широкие конверты и кружево уменьшаются)."""
    w = round(prop.width * h / prop.height)
    if w > MAX_PROP_W:
        h, w = round(h * MAX_PROP_W / w), MAX_PROP_W
    return prop.resize((w, h), Image.LANCZOS)


def _place_prop(photo, prop_name, corner, rnd, tame=False):
    path = os.path.join(PROPS, prop_name + ".png")
    if not os.path.exists(path):
        return
    prop = Image.open(path).convert("RGBA")
    h = rnd.randint(200, 290)
    if tame:
        prop = _fit_prop(prop, h).rotate(rnd.randint(-TILT, TILT), resample=Image.BICUBIC, expand=True)
    else:
        prop = prop.resize((round(prop.width * h / prop.height), h), Image.LANCZOS)
        prop = prop.rotate(rnd.randint(-38, 38), resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", prop.size, (0, 0, 0, 0))
    shadow.putalpha(prop.getchannel("A").point(lambda a: int(a * 0.35)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    inset_x, inset_y = rnd.randint(-70, 30), rnd.randint(-40, 40)
    x = inset_x if corner[1] == "l" else W - prop.width - inset_x
    y = inset_y if corner[0] == "t" else PHOTO_H - 120 - prop.height - inset_y  # выше зоны перехода в бумагу
    y = max(-60, min(y, PHOTO_H - 120 - prop.height + 40))
    photo.paste(shadow, (x + 8, y + 12), shadow)
    photo.paste(prop, (x, y), prop)


def compose(photo, seed):
    """Фон + реквизит Алисы: цветокоррекция и раскладка по seed (воспроизводимо)."""
    rnd = random.Random(f"alisa-card-{seed}")
    photo = _grade(photo, rnd.choice(list(GRADES)))
    names = rnd.sample(PROP_FILES, rnd.choice([1, 2, 2, 3]))
    for name, corner in zip(names, rnd.sample(CORNERS, len(names))):
        _place_prop(photo, name, corner, rnd)
    if rnd.random() < 0.5:  # лёгкое затемнение по краям
        vig = Image.new("L", photo.size, 0)
        ImageDraw.Draw(vig).ellipse([-W // 4, -PHOTO_H // 4, W + W // 4, PHOTO_H + PHOTO_H // 4], fill=255)
        vig = vig.filter(ImageFilter.GaussianBlur(120))
        dark = ImageEnhance.Brightness(photo).enhance(0.82)
        photo = Image.composite(photo, dark, vig)
    return photo


def _paste_prop(photo, name, cx, cy, h, rot):
    path = os.path.join(PROPS, name + ".png")
    if not os.path.exists(path):
        return
    prop = _fit_prop(Image.open(path).convert("RGBA"), h)
    prop = prop.rotate(rot, resample=Image.BICUBIC, expand=True)
    shadow = Image.new("RGBA", prop.size, (0, 0, 0, 0))
    shadow.putalpha(prop.getchannel("A").point(lambda a: int(a * 0.35)))
    shadow = shadow.filter(ImageFilter.GaussianBlur(10))
    x, y = round(cx - prop.width / 2), round(cy - prop.height / 2)
    photo.paste(shadow, (x + 8, y + 12), shadow)
    photo.paste(prop, (x, y), prop)


def _spread(n, lo, hi):
    if n == 1:
        return [(lo + hi) // 2]
    return [round(lo + (hi - lo) * i / (n - 1)) for i in range(n)]


def compose_custom(photo, spec):
    """Фон, собранный клиентом: палитра, до трёх предметов реквизита, раскладка, рамка. Детерминированно по spec."""
    rnd = random.Random("alisa-custom-" + encode_spec(spec["base"], spec["pal"], spec["props"], spec["lay"], spec["frame"]))  # анимация картинку не меняет
    photo = _grade(photo, spec["pal"])
    props, lay, n = spec["props"], spec["lay"], len(spec["props"])
    if n and lay == "corners":
        for name, corner in zip(props, ["bl", "br", "tl", "tr"]):   # сначала нижние углы — «на столе», верхние только для третьего предмета
            _place_prop(photo, name, corner, rnd, tame=True)
    elif n:
        h = 230
        if lay in ("left", "right"):
            x = 140 if lay == "left" else W - 140
            for name, y in zip(props, _spread(n, 330, 580)):   # нижняя половина кадра: предметы лежат «на столе»
                _paste_prop(photo, name, x + rnd.randint(-15, 15), y, h, rnd.randint(-TILT, TILT))
        else:   # вдоль верхнего или нижнего края; один предмет — у правого края, а не по центру кадра
            y = 150 if lay == "top" else PHOTO_H - 190
            xs = [W - 230] if n == 1 else _spread(n, 220, W - 220)
            for name, x in zip(props, xs):
                _paste_prop(photo, name, x, y + rnd.randint(-15, 15), h - 40, rnd.randint(-TILT, TILT))
    if spec["frame"] != "none":
        d = ImageDraw.Draw(photo)
        d.rectangle([22, 22, W - 23, PHOTO_H - 1], outline=PAPER, width=10)
        if spec["frame"] == "double":
            d.rectangle([48, 48, W - 49, PHOTO_H - 1], outline=(217, 195, 160), width=2)
    return photo


def _background(product_key, card_ref=None):
    ref = card_ref or first_ref(product_key)
    base, seed = split_ref(ref)
    if base.startswith("ai:"):  # картинка, нарисованная клиентом в генераторе
        path = os.path.join(AI_DIR, re.sub(r"[^A-Za-z0-9_-]", "", base[3:]) + ".jpg")
    else:
        path = bg_path(base) if base else ""
    if not os.path.exists(path):
        return Image.new("RGB", (W, PHOTO_H), (200, 180, 160))
    im = Image.open(path).convert("RGB")
    scale = max(W / im.width, PHOTO_H / im.height)
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    x = (im.width - W) // 2
    y = (im.height - PHOTO_H) // 2
    im = im.crop((x, y, x + W, y + PHOTO_H))
    spec = parse_spec(ref)
    if spec:
        return compose_custom(im, spec)
    return compose(im, f"{base}~{seed}") if seed else im


def _paper_texture():
    rnd = random.Random(7)
    paper = Image.new("RGB", (W, H), PAPER)
    noise = Image.effect_noise((W, H), 18).convert("L").filter(ImageFilter.GaussianBlur(0.6))
    paper = Image.blend(paper, Image.merge("RGB", (noise,) * 3), 0.035)
    d = ImageDraw.Draw(paper)
    for _ in range(40):  # редкие волокна бумаги
        x, y = rnd.randint(0, W), rnd.randint(PHOTO_H, H)
        d.line([(x, y), (x + rnd.randint(-30, 30), y + rnd.randint(-4, 4))], fill=(232, 224, 212), width=1)
    return paper


def _plain(text):
    """Шрифты открытки не умеют эмодзи — в тексте на картинке их быть не должно."""
    return re.sub(r"[𐀀-􏿿☀-➿️‍]", "", text or "").strip()


def render(product_key, title, line, sign, preview=False, card_ref=None):
    title, line, sign = _plain(title), _plain(line), _plain(sign)
    card = _paper_texture()
    photo = _background(product_key, card_ref)
    # фото плавно переходит в бумагу
    mask = Image.new("L", (W, PHOTO_H), 255)
    md = ImageDraw.Draw(mask)
    fade = 110
    for i in range(fade):
        md.line([(0, PHOTO_H - fade + i), (W, PHOTO_H - fade + i)], fill=int(255 * (1 - i / fade) ** 1.6))
    card.paste(photo, (0, 0), mask)

    d = ImageDraw.Draw(card)
    margin = 110
    max_w = W - 2 * margin

    # сургучная печать на стыке
    cx, cy, r = W // 2, PHOTO_H - 40, 38
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ACCENT)
    d.ellipse([cx - r + 7, cy - r + 7, cx + r - 7, cy + r - 7], outline=(185, 96, 88), width=2)
    seal_f = _font(PLAYFAIR, 40, 700)
    d.text((cx, cy - 2), "А", font=seal_f, fill=(245, 225, 210), anchor="mm")

    y = PHOTO_H + 30
    t_font, t_lines = _fit(d, title, PLAYFAIR, 600, 62, 40, max_w, 2)
    for ln in t_lines:
        d.text((W // 2, y), ln, font=t_font, fill=INK, anchor="ma")
        y += int(t_font.size * 1.2)

    y += 16
    l_font, l_lines = _fit(d, line, SCRIPT, None, 50, 34, max_w, 3)
    for ln in l_lines:
        d.text((W // 2, y), ln, font=l_font, fill=INK_SOFT, anchor="ma")
        y += int(l_font.size * 1.3)

    if sign:
        s_font = _font(SCRIPT, 42)
        sy = max(y + 18, H - 175)
        d.text((W - margin, sy), f"— {sign}", font=s_font, fill=INK, anchor="ra")

    foot = _font(MANROPE, 20, 600)
    label = "ПИСЬМО ОТ АЛИСЫ НЕВСКОЙ"
    spaced = " ".join(label)
    d.text((W // 2, H - 62), spaced, font=foot, fill=(170, 150, 132), anchor="ma")

    if preview:
        layer = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        wf = _font(MANROPE, 52, 600)
        for yy in range(0, H * 2, 230):
            ld.text((0, yy), ("превью   " * 12), font=wf, fill=(255, 255, 255, 115))
        layer = layer.rotate(28, resample=Image.BICUBIC)
        layer = layer.crop((W // 2, H // 2, W // 2 + W, H // 2 + H))
        card = card.convert("RGBA")
        card.alpha_composite(layer)
        card = card.convert("RGB")

    buf = io.BytesIO()
    card.save(buf, "JPEG", quality=90)
    return buf.getvalue()
