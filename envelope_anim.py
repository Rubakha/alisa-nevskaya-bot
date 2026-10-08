"""Анимации конверта для получателя прямо в Telegram (без сайта): запечатанный конверт и «печать ломается».

closed_gif()  — конверт с сургучной печатью «А», печать мягко пульсирует, золотые искорки (зациклен);
opening_gif() — печать трескается и раскалывается, клапан открывается, из конверта поднимается открытка.
Рисуется PIL в удвоенном размере и уменьшается (гладкие края); результат кэшируется в памяти.
"""
import io
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

S = 2                      # суперсэмплинг
W = H = 480
CREAM_TOP, CREAM_BOT = (251, 246, 236), (239, 227, 208)
WAX, WAX2 = (143, 47, 42), (185, 84, 76)
GOLD = (233, 201, 138)
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "CormorantGaramond.ttf")
_cache = {}


def _bg():
    im = Image.new("RGB", (W * S, H * S))
    d = ImageDraw.Draw(im)
    for y in range(H * S):
        k = y / (H * S)
        d.line([(0, y), (W * S, y)], fill=tuple(round(a + (b - a) * k) for a, b in zip(CREAM_TOP, CREAM_BOT)))
    return im


def _font(size):
    f = ImageFont.truetype(FONT, size * S)
    try:
        f.set_variation_by_name("Bold")
    except Exception:
        pass
    return f


def _seal(d, cx, cy, r, alpha_scale=1.0):
    """Сургучная печать с ободком и буквой «А»."""
    cx, cy, r = cx * S, cy * S, r * S
    d.ellipse([cx - r + 5, cy - r + 9, cx + r + 5, cy + r + 9], fill=(110, 80, 60))  # тень
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=WAX)
    d.ellipse([cx - r * 0.78, cy - r * 0.78, cx + r * 0.78, cy + r * 0.78], outline=(245, 225, 210), width=max(2, int(r * 0.05)))
    d.ellipse([cx - r * 0.55, cy - r * 0.62, cx - r * 0.1, cy - r * 0.25], fill=WAX2)  # блик
    d.text((cx, cy - r * 0.04), "А", font=_font(int(r / S * 1.1)), fill=(245, 225, 210), anchor="mm")


def _envelope(d, flap_open=0.0):
    """Конверт по центру. flap_open: 0 — закрыт, 1 — клапан полностью открыт."""
    x0, y0, x1, y1 = 70 * S, 170 * S, 410 * S, 400 * S
    d.rounded_rectangle([x0 + 6, y0 + 10, x1 + 6, y1 + 10], 14, fill=(150, 125, 100))  # тень
    d.rounded_rectangle([x0, y0, x1, y1], 14, fill=(236, 220, 192))
    d.polygon([(x0, y1), ((x0 + x1) // 2, (y0 + y1) // 2 + 10 * S), (x0, y0 + 40 * S)], fill=(228, 210, 180))
    d.polygon([(x1, y1), ((x0 + x1) // 2, (y0 + y1) // 2 + 10 * S), (x1, y0 + 40 * S)], fill=(228, 210, 180))
    d.polygon([(x0, y1), (x1, y1), ((x0 + x1) // 2, (y0 + y1) // 2 - 10 * S)], fill=(241, 226, 200))
    mid_x = (x0 + x1) // 2
    full = (y1 - y0) * 0.62
    tip = y0 + full * (1 - flap_open * 1.35)           # кончик клапана «взлетает» вверх при открытии
    colour = (239, 224, 196) if flap_open < 0.5 else (225, 205, 172)
    d.polygon([(x0, y0), (x1, y0), (mid_x, tip)], fill=colour)
    return x0, y0, x1, y1, mid_x, tip


def _sparkles(d, t, n_frames, rnd_state, strength=1.0):
    for x, y, r, phase in rnd_state:
        k = (math.sin(2 * math.pi * (t / n_frames + phase)) + 1) / 2
        if k > 0.35:
            rr = r * (0.5 + k) * S
            c = tuple(min(255, int(v * (0.6 + 0.4 * k))) for v in GOLD)
            d.line([(x * S - rr, y * S), (x * S + rr, y * S)], fill=c, width=2 * S // 2 + 1)
            d.line([(x * S, y * S - rr), (x * S, y * S + rr)], fill=c, width=2 * S // 2 + 1)


def _finish(frames, duration, loop):
    out = io.BytesIO()
    small = [f.resize((W, H), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=96) for f in frames]
    small[0].save(out, "GIF", save_all=True, append_images=small[1:], duration=duration, loop=loop, disposal=2)
    return out.getvalue()


def closed_gif():
    """Запечатанный конверт: печать пульсирует. Зациклен."""
    if "closed" in _cache:
        return _cache["closed"]
    rnd = random.Random(11)
    sp = [(rnd.randint(40, W - 40), rnd.randint(40, 330), rnd.randint(5, 10), rnd.random()) for _ in range(14)]
    n = 16
    frames = []
    for t in range(n):
        im = _bg()
        d = ImageDraw.Draw(im)
        _sparkles(d, t, n, sp)
        x0, y0, x1, y1, mid_x, tip = _envelope(d)
        pulse = 1 + 0.07 * math.sin(2 * math.pi * t / n)
        _seal(d, mid_x // S, int(tip // S) - 4, 44 * pulse)
        frames.append(im)
    _cache["closed"] = _finish(frames, 90, 0)
    return _cache["closed"]


def opening_gif():
    """Печать трескается и раскалывается, клапан открывается, открытка поднимается. Играет один раз."""
    if "opening" in _cache:
        return _cache["opening"]
    rnd = random.Random(5)
    sp = [(rnd.randint(40, W - 40), rnd.randint(30, 300), rnd.randint(6, 12), rnd.random()) for _ in range(22)]
    n = 20
    frames = []
    for t in range(n):
        im = _bg()
        d = ImageDraw.Draw(im)
        _sparkles(d, t, n, sp)
        if t < 6:                                        # дрожь печати
            x0, y0, x1, y1, mid_x, tip = _envelope(d)
            shake = (1 if t % 2 else -1) * (t * 1.2)
            _seal(d, mid_x // S + shake, int(tip // S) - 4, 44 + t * 1.5)
        elif t < 10:                                     # печать раскалывается на две половины
            k = (t - 6) / 4
            x0, y0, x1, y1, mid_x, tip = _envelope(d, flap_open=k * 0.5)
            r = 53 * (1 - k * 0.2)
            cy = int(tip // S) - 4 + k * 30
            layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            _seal(ld, mid_x // S, cy, r)
            half = layer.copy()
            left = layer.crop((0, 0, int(mid_x), im.size[1]))
            right = layer.crop((int(mid_x), 0, im.size[0], im.size[1]))
            im.paste(left, (int(-k * 28 * S), int(k * 14 * S)), left)
            im.paste(right, (int(mid_x + k * 28 * S), int(k * 14 * S)), right)
        else:                                            # клапан открыт, открытка поднимается
            k = min(1.0, (t - 10) / 9)
            x0, y0, x1, y1, mid_x, tip = _envelope(d, flap_open=0.5 + 0.5 * min(1, k * 2))
            card_h, card_w = 190 * S, 250 * S
            top = y1 - 40 * S - (y1 - y0 + 20 * S) * k
            d.rounded_rectangle([mid_x - card_w // 2 + 5, top + 8, mid_x + card_w // 2 + 5, top + card_h + 8], 10, fill=(170, 150, 125))
            d.rounded_rectangle([mid_x - card_w // 2, top, mid_x + card_w // 2, top + card_h], 10, fill=(252, 248, 240))
            d.line([(mid_x - 80 * S, top + 70 * S), (mid_x + 80 * S, top + 70 * S)], fill=(214, 196, 165), width=2 * S)
            d.line([(mid_x - 60 * S, top + 105 * S), (mid_x + 60 * S, top + 105 * S)], fill=(225, 210, 185), width=2 * S)
            d.polygon([(x0, y1), (x1, y1), (mid_x, (y0 + y1) // 2 - 10 * S)], fill=(241, 226, 200))  # передний карман
        frames.append(im)
    frames += [frames[-1]] * 4
    _cache["opening"] = _finish(frames, 100, 1)
    return _cache["opening"]
