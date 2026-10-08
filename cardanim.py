"""Живые открытки: GIF-анимация поверх готовой открытки (искры, лепестки, снег, боке). Без ИИ, 0 ₽.

animate(product_key, title, line, sign, card_ref, preview=False) -> bytes GIF. Кадры зациклены без шва,
расположение частиц задаётся ссылкой card_ref (воспроизводимо). Эффекты — только по фото-части открытки,
текст остаётся неподвижным и читаемым. GIF 640×800, 16 кадров, ~1–3 МБ.
"""
import io
import math
import random

from PIL import Image, ImageDraw, ImageFilter

import postcards as PC

FRAMES = 16
DURATION_MS = 110
OUT_W, OUT_H = 640, 800
ZONE_H = round(OUT_H * PC.PHOTO_H / PC.H)   # эффекты живут над границей фото/бумаги


def _star(d, cx, cy, r, alpha):
    c = (255, 244, 214, alpha)
    d.line([(cx - r, cy), (cx + r, cy)], fill=c, width=2)
    d.line([(cx, cy - r), (cx, cy + r)], fill=c, width=2)
    d.ellipse([cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3], fill=(255, 250, 235, min(255, alpha + 30)))


def _layer_sparkle(t, rnd_state):
    layer = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x, y, r, phase in rnd_state:
        k = (math.sin(2 * math.pi * (t / FRAMES + phase)) + 1) / 2
        if k > 0.15:
            _star(d, x, y, max(4, round(r * (0.4 + 0.6 * k))), round(60 + 195 * k))
    return layer


def _layer_petals(t, rnd_state):
    layer = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    for x0, y0, speed, amp, phase, size, color, rot in rnd_state:
        y = (y0 + t * ZONE_H * speed / FRAMES) % (ZONE_H + 60) - 30
        x = x0 + amp * math.sin(2 * math.pi * (t / FRAMES * speed + phase))
        petal = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(petal).ellipse([size // 2, 0, size * 3 // 2, size * 2 - 2], fill=color)
        petal = petal.rotate(rot + t * 360 / FRAMES * speed, resample=Image.BICUBIC)
        layer.paste(petal, (round(x - size), round(y - size)), petal)
    return layer


def _layer_snow(t, rnd_state):
    layer = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x0, y0, speed, amp, phase, r, alpha in rnd_state:
        y = (y0 + t * ZONE_H * speed / FRAMES) % (ZONE_H + 20) - 10
        x = x0 + amp * math.sin(2 * math.pi * (t / FRAMES * speed + phase))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, alpha))
    return layer


def _layer_bokeh(t, rnd_state):
    layer = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for x0, y0, r, phase, drift in rnd_state:
        k = (math.sin(2 * math.pi * (t / FRAMES + phase)) + 1) / 2
        x = x0 + drift * math.sin(2 * math.pi * (t / FRAMES + phase))
        y = y0 - drift * 0.6 * math.cos(2 * math.pi * (t / FRAMES + phase))
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 236, 190, round(25 + 70 * k)))
    return layer.filter(ImageFilter.GaussianBlur(5))


def _state(kind, rnd):
    if kind == "sparkle":
        return [(rnd.randint(30, OUT_W - 30), rnd.randint(20, ZONE_H - 20), rnd.randint(7, 16), rnd.random())
                for _ in range(30)]
    if kind == "petals":
        colors = [(240, 160, 184, 245), (250, 200, 212, 245), (232, 140, 168, 245), (255, 196, 150, 240)]
        return [(rnd.randint(0, OUT_W), rnd.randint(0, ZONE_H), rnd.choice([1, 1, 2]), rnd.randint(12, 36), rnd.random(),
                 rnd.randint(12, 19), rnd.choice(colors), rnd.randint(0, 360)) for _ in range(14)]
    if kind == "snow":
        return [(rnd.randint(0, OUT_W), rnd.randint(0, ZONE_H), rnd.choice([1, 1, 2, 3]), rnd.randint(4, 14),
                 rnd.random(), rnd.randint(2, 5), rnd.randint(150, 235)) for _ in range(70)]
    return [(rnd.randint(40, OUT_W - 40), rnd.randint(40, ZONE_H - 40), rnd.randint(14, 34), rnd.random(),
             rnd.randint(8, 22)) for _ in range(16)]


LAYERS = {"sparkle": _layer_sparkle, "petals": _layer_petals, "snow": _layer_snow, "bokeh": _layer_bokeh}


def animate(product_key, title, line, sign, card_ref, preview=False):
    spec = PC.parse_spec(card_ref)
    kind = (spec or {}).get("anim", "none")
    if kind not in LAYERS:
        raise ValueError("у этой открытки нет анимации")
    base = Image.open(io.BytesIO(PC.render(product_key, title, line, sign, preview=preview, card_ref=card_ref)))
    base = base.convert("RGB").resize((OUT_W, OUT_H), Image.LANCZOS)
    rnd = random.Random("alisa-anim-" + card_ref)
    state = _state(kind, rnd)
    frames = []
    for t in range(FRAMES):
        frame = base.convert("RGBA")
        frame.alpha_composite(LAYERS[kind](t, state))
        frames.append(frame.convert("RGB").convert("P", palette=Image.ADAPTIVE, colors=128))
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=DURATION_MS, loop=0, disposal=2)
    return buf.getvalue()
