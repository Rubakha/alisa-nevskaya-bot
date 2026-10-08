"""Отправка письма получателю в один тап через мини-приложение Telegram.

Кнопка «Отправить» в боте открывает крошечную страницу (Web App). Страница просит сервер подготовить сообщение
(Bot API savePreparedInlineMessage) и вызывает Telegram.WebApp.shareMessage: Telegram сам показывает выбор
человека, а получателю уходит ТОЛЬКО открытка (или GIF) и кнопка «Вскрыть письмо от <имя>» — без текста и ссылок.
Если клиент Telegram старый и shareMessage недоступен, на странице есть обычная кнопка «поделиться ссылкой».

Безопасность: initData из Telegram проверяется подписью на токене бота, пользователь должен быть автором заказа.
"""
import hashlib
import hmac
import html
import json
import time
import urllib.parse

import requests
from flask import request

import envelope_pages

B = None
MAX_AGE = 24 * 3600


def validate_init_data(init_data, token, now=None, max_age=MAX_AGE):
    """Проверяет подпись Telegram Web App. Возвращает id пользователя или None."""
    try:
        pairs = dict(urllib.parse.parse_qsl(init_data or "", keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    got = pairs.pop("hash", None)
    if not got:
        return None
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    want = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, got):
        return None
    try:
        if (now or time.time()) - int(pairs.get("auth_date", "0")) > max_age:
            return None
        return int(json.loads(pairs.get("user", "{}"))["id"])
    except (ValueError, KeyError, TypeError):
        return None


def button_label(order):
    return f"💌 Вскрыть письмо от {envelope_pages.from_whom(order)}"[:60]


def prepared_result(order, code, site):
    """Что получит адресат: открытка/GIF и одна кнопка со ссылкой на закрытый конверт."""
    markup = {"inline_keyboard": [[{"text": button_label(order), "url": f"{site}/e/{code}"}]]}
    if order.get("card_anim_file_id"):
        return {"type": "gif", "id": code, "gif_file_id": order["card_anim_file_id"], "reply_markup": markup}
    if order.get("card_file_id"):
        return {"type": "photo", "id": code, "photo_file_id": order["card_file_id"], "reply_markup": markup}
    return {"type": "article", "id": code, "title": "Письмо", "reply_markup": markup,
            "input_message_content": {"message_text": "💌 Тебе письмо"}}


def save_prepared(token, user_id, result):
    """Bot API savePreparedInlineMessage → id подготовленного сообщения."""
    r = requests.post(f"https://api.telegram.org/bot{token}/savePreparedInlineMessage", timeout=30, json={
        "user_id": user_id, "result": result, "allow_user_chats": True, "allow_group_chats": True,
        "allow_bot_chats": False, "allow_channel_chats": False})
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "savePreparedInlineMessage failed"))
    return data["result"]["id"]


PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1,maximum-scale=1"><title>Отправить письмо</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>body{margin:0;background:#f6f0e6;color:#3b2f2a;font:17px/1.5 Georgia,serif;display:flex;min-height:100vh;
align-items:center;justify-content:center;text-align:center;padding:24px}a,button{display:none;margin:14px auto 0;padding:13px 24px;
border:0;border-radius:999px;background:#8f2f2a;color:#fff;font:600 16px -apple-system,Segoe UI,sans-serif;text-decoration:none}
</style></head><body><div><div style="font-size:42px">💌</div><p id="s">Открываю выбор получателя…</p>
<a id="fb" href="%(fallback)s">Отправить обычной ссылкой</a><button id="again">Выбрать ещё раз</button></div>
<script>
var tg=window.Telegram&&Telegram.WebApp,s=document.getElementById('s'),fb=document.getElementById('fb'),again=document.getElementById('again'),CODE=%(code)s;
function fail(m){s.textContent=m;fb.style.display='inline-block'}
fb.onclick=function(e){if(tg&&tg.openTelegramLink){e.preventDefault();tg.openTelegramLink(fb.href);setTimeout(function(){tg.close()},300)}};
function run(){
  s.textContent='Открываю выбор получателя…';again.style.display='none';
  fetch('/share/'+CODE+'/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({initData:tg.initData})})
  .then(function(r){return r.json()}).then(function(j){
    if(!j.id){fail(j.error||'Не получилось подготовить сообщение.');return}
    if(!tg.shareMessage){fail('В этой версии Telegram нет быстрой отправки. Можно отправить обычной ссылкой.');return}
    tg.shareMessage(j.id,function(sent){if(sent){tg.close()}else{s.textContent='Отправка отменена.';again.style.display='inline-block'}});
  }).catch(function(){fail('Нет связи. Попробуй ещё раз или отправь ссылкой.')});
}
if(!tg||!tg.initData){fail('Открой эту страницу кнопкой в боте Telegram.')}else{tg.ready();if(tg.expand)tg.expand();again.onclick=run;run()}
</script></body></html>"""


def register(app, bot_module):
    global B
    B = bot_module

    @app.route("/share/<code>", methods=["GET"])
    def share_page(code):
        order = B.envelope_order(code)
        if not order:
            return "", 404
        page = PAGE % {"fallback": html.escape(B.occ_share_url(order), quote=True), "code": json.dumps(code)}
        return page, 200, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}

    @app.route("/share/<code>/prepare", methods=["POST"])
    def share_prepare(code):
        order = B.envelope_order(code)
        body = request.get_json(silent=True) or {}
        user_id = validate_init_data(body.get("initData"), B.TG_BOT_TOKEN)
        if not order or not user_id or user_id != order.get("chat_id"):
            return {"error": "Это письмо можно отправить только из чата с ботом, где ты его заказывала."}, 403
        try:
            prepared = save_prepared(B.TG_BOT_TOKEN, user_id, prepared_result(order, code, B.site_pages.SITE_URL))
        except Exception as exc:
            B.log.error("share prepare %s: %s", order["order_id"], exc)
            return {"error": "Telegram не принял сообщение. Отправь письмо обычной ссылкой."}, 502
        return {"id": prepared}, 200
