"""Собирает лёгкие веб-шрифты (WOFF) из TTF в assets/fonts: только кириллица, латиница и нужная пунктуация.

Запуск: python tools/make_webfonts.py (нужен fonttools). Результат — assets/fonts/web/*.woff, их отдаёт маршрут /fonts/<имя>
(webfonts.py); внешние CDN страницам не нужны. Оси переменных шрифтов сохраняются (вес меняется в CSS).
"""
import os

from fontTools import subset
from fontTools.ttLib import TTFont

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTS = os.path.join(BASE, "assets", "fonts")
OUT = os.path.join(FONTS, "web")
SOURCES = {
    "playfair.woff": "PlayfairDisplay-Variable.ttf",
    "manrope.woff": "Manrope-Variable.ttf",
    "badscript.woff": "BadScript-Regular.ttf",
}
# латиница, Latin-1, кириллица, общая пунктуация (тире, кавычки, многоточие), №, ₽, €, стрелка
UNICODES = (list(range(0x20, 0x7F)) + list(range(0xA0, 0x100)) + list(range(0x400, 0x530))
            + list(range(0x2010, 0x2028)) + [0x2030, 0x2039, 0x203A, 0x2116, 0x20AC, 0x20BD, 0x2192, 0x2022, 0x2122])


def build():
    os.makedirs(OUT, exist_ok=True)
    opts = subset.Options()
    opts.flavor = "woff"
    opts.layout_features = ["*"]
    opts.name_IDs = [1, 2, 3, 4, 6]
    opts.notdef_outline = True
    for out_name, src in SOURCES.items():
        font = TTFont(os.path.join(FONTS, src))
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=UNICODES)
        sub.subset(font)
        path = os.path.join(OUT, out_name)
        font.flavor = "woff"
        font.save(path)
        print(out_name, os.path.getsize(path), "байт")


if __name__ == "__main__":
    build()
