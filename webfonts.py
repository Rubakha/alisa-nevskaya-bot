"""Шрифты веб-страниц бота (сайт, конверт получателя) — свои, без Google Fonts и других CDN.

Playfair Display — заголовки и текст письма, Manrope — интерфейс, Bad Script — имена и подписи. Файлы WOFF (кириллица +
латиница, собраны tools/make_webfonts.py) лежат в assets/fonts/web и отдаются маршрутом /fonts/<имя> с кэшем на год.
Адрес в @font-face абсолютный (адрес сайта бота): страница конверта может открываться и через прокси короткого домена
(ENVELOPE_BASE), где своих /fonts нет; для кросс-доменной загрузки маршрут отдаёт Access-Control-Allow-Origin: *.
"""
import os

from flask import Response, abort

SITE_URL = os.getenv("SITE_URL", "https://alisabot-w-sergeyrubakha1s.mia0.amvera.tech").rstrip("/")
DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts", "web")
FILES = {"playfair.woff": "Playfair Display", "manrope.woff": "Manrope", "badscript.woff": "Bad Script"}
RANGES = {"playfair.woff": "400 900", "manrope.woff": "200 800", "badscript.woff": "400"}


def font_face_css():
    rules = []
    for fname, family in FILES.items():
        rules.append("@font-face{font-family:'%s';src:url(%s/fonts/%s) format('woff');font-weight:%s;font-style:normal;"
                     "font-display:swap}" % (family, SITE_URL, fname, RANGES[fname]))
    return "".join(rules)


def head():
    """Блок для <head>: подгрузка основного шрифта заранее и сами @font-face."""
    return (f'<link rel="preload" href="{SITE_URL}/fonts/playfair.woff" as="font" type="font/woff" crossorigin>'
            f"<style>{font_face_css()}</style>")


def register(app):
    @app.route("/fonts/<name>", methods=["GET"], endpoint="webfont")
    def webfont(name):
        if name not in FILES or not os.path.exists(os.path.join(DIR, name)):
            abort(404)
        with open(os.path.join(DIR, name), "rb") as f:
            data = f.read()
        return Response(data, 200, mimetype="font/woff", headers={
            "Cache-Control": "public, max-age=31536000, immutable", "Access-Control-Allow-Origin": "*"})
