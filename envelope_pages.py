"""Страницы конверта для получателя: закрытый конверт с сургучной печатью и вскрытие с анимацией.

closed_html — конверт и кнопка «Вскрыть письмо от …» (+ og-метки для превью ссылки);
open_html — печать «ломается», открытка (GIF, если выбрана анимация) и письмо появляются по абзацам, золотые искры.
Чистые функции без обращения к боту: HTML собирается из заказа, поэтому легко проверяются тестами.
"""
import html
import re

import webfonts

CSS = """
:root{--cream:#f6f0e6;--ink:#3b2f2a;--wax:#8f2f2a;--wax2:#b9544c;--gold:#e9c98a}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:radial-gradient(circle at 50% 0,#fbf6ec,#efe3d0 70%);color:var(--ink);
font:18px/1.65 'Manrope',system-ui,sans-serif;overflow-x:hidden}
.w{max-width:620px;margin:0 auto;padding:28px 18px 56px;text-align:center;position:relative;z-index:2}
h1{font-family:'Playfair Display',Georgia,serif;font-size:26px;line-height:1.3;margin:18px 0 6px;font-weight:600}
.sub{color:#7a6a60;font-size:16px;margin:0 0 22px}
.btn{display:inline-block;margin-top:22px;padding:15px 30px;background:var(--wax);color:#fff;border-radius:999px;
text-decoration:none;font:600 17px/1 -apple-system,Segoe UI,sans-serif;box-shadow:0 8px 20px rgba(143,47,42,.35)}
.btn:active{transform:translateY(1px)}
.f{font:14px/1.5 -apple-system,Segoe UI,sans-serif;color:#7a6a60;margin-top:34px}
.f a{color:#8f2f2a}
canvas#fx{position:fixed;inset:0;width:100%;height:100%;pointer-events:none;z-index:1}
/* конверт */
.env{position:relative;width:min(320px,82vw);height:210px;margin:26px auto 8px;background:linear-gradient(#f3e6d0,#e9d6b8);
border-radius:10px;box-shadow:0 14px 34px rgba(80,50,30,.25);animation:float 4s ease-in-out infinite}
.env:before,.env:after{content:"";position:absolute;left:0;right:0;bottom:0;height:60%;
background:linear-gradient(135deg,transparent 49%,rgba(120,80,40,.10) 50%) left/50% 100% no-repeat,
linear-gradient(225deg,transparent 49%,rgba(120,80,40,.10) 50%) right/50% 100% no-repeat}
.flap{position:absolute;left:0;right:0;top:0;height:62%;background:linear-gradient(#efdfc4,#e4cfae);
clip-path:polygon(0 0,100% 0,50% 100%);border-radius:10px 10px 0 0;transform-origin:top}
.seal{position:absolute;left:50%;top:48%;width:74px;height:74px;margin:-37px 0 0 -37px;border-radius:50%;
background:radial-gradient(circle at 35% 30%,var(--wax2),var(--wax) 65%);box-shadow:0 4px 10px rgba(0,0,0,.35),inset 0 0 0 5px rgba(255,255,255,.08);
display:flex;align-items:center;justify-content:center;color:#f5e1d2;font:700 36px/1 'Playfair Display',Georgia,serif;
animation:pulse 2.4s ease-in-out infinite}
.seal:after{content:"";position:absolute;inset:8px;border:2px solid rgba(245,225,210,.45);border-radius:50%}
@keyframes float{50%{transform:translateY(-6px)}}
@keyframes pulse{50%{transform:scale(1.07)}}
/* вскрытие */
.stage{position:relative;width:min(320px,82vw);margin:6px auto 0;height:150px;overflow:hidden;animation:collapse .7s ease 2.5s forwards}
@keyframes collapse{to{height:0;margin:0}}
.stage .seal{animation:crack 1.6s ease-in forwards}
.stage .half{position:absolute;top:50%;left:50%;width:74px;height:37px;margin-left:-37px;background:var(--wax);
border-radius:74px 74px 0 0;opacity:0}
.stage .h1{animation:halfL 1.6s ease-in .9s forwards}.stage .h2{margin-top:0;transform:rotate(180deg);animation:halfR 1.6s ease-in .9s forwards}
@keyframes crack{0%{transform:scale(1)}35%{transform:scale(1.18) rotate(-4deg)}55%{transform:scale(1.18) rotate(4deg)}60%{opacity:1}100%{opacity:0;transform:scale(1.3)}}
@keyframes halfL{0%{opacity:1;transform:translate(0,0)}100%{opacity:0;transform:translate(-90px,120px) rotate(-70deg)}}
@keyframes halfR{0%{opacity:1;transform:rotate(180deg) translate(0,0)}100%{opacity:0;transform:rotate(180deg) translate(-90px,120px) rotate(-70deg)}}
.reveal{opacity:0;transform:translateY(16px);animation:rise .9s ease-out forwards}
@keyframes rise{to{opacity:1;transform:none}}
.card{width:100%;border-radius:12px;box-shadow:0 12px 34px rgba(60,40,25,.28);display:block}
.l p{margin:0 0 1.15em;text-align:left;opacity:0;animation:rise .9s ease-out forwards;font-family:'Playfair Display',Georgia,serif;font-size:18px;line-height:1.75}
.l p.sign{font-family:'Bad Script',cursive;font-size:26px;line-height:1.4;font-style:normal;text-align:right}
"""

FX = """
(function(){var c=document.getElementById('fx');if(!c||!c.getContext)return;var x=c.getContext('2d'),W,H,P=[];
function size(){W=c.width=innerWidth;H=c.height=innerHeight}size();addEventListener('resize',size);
for(var i=0;i<46;i++)P.push({x:Math.random()*W,y:Math.random()*H,r:1+Math.random()*2.6,s:.25+Math.random()*.7,p:Math.random()*6.3});
var t=0,end=Date.now()+%(ms)d;
(function f(){x.clearRect(0,0,W,H);t+=.016;var k=Math.min(1,(end-Date.now())/1500);
P.forEach(function(q){q.y-=q.s;q.x+=Math.sin(t*1.4+q.p)*.4;if(q.y<-8){q.y=H+8;q.x=Math.random()*W}
var a=(.35+.65*Math.abs(Math.sin(t*2+q.p)))*Math.max(0,k);x.fillStyle='rgba(233,201,138,'+a+')';
x.beginPath();x.arc(q.x,q.y,q.r,0,6.3);x.fill()});
if(Date.now()<end)requestAnimationFrame(f);else x.clearRect(0,0,W,H)})()})();
"""


def _page(title, head, body, script=""):
    return ('<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" '
            'content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow">'
            f'{head}<title>{html.escape(title)}</title>{webfonts.head()}<style>{CSS}</style></head><body>'
            f'<canvas id="fx"></canvas><div class="w">{body}</div>{script}</body></html>')


_POSSESSIVE = re.compile(r"^(твой|твоя|твои|твоё|ваш|ваша|ваши|ваше|любящий|любящая|с любовью)\s+", re.I)
_GEN_EXCEPT = {"дочь": "дочери", "мать": "матери", "любовь": "любови"}


def genitive(word):
    """Родительный падеж для подписи «от …»: Серёжа → Серёжи, мама → мамы, Андрей → Андрея, Мария → Марии.
    Фразы из нескольких слов и неизвестные формы возвращаются как есть."""
    if not word or not re.fullmatch(r"[А-Яа-яЁё-]+", word):
        return word
    low = word.lower()
    if low in _GEN_EXCEPT:
        new = _GEN_EXCEPT[low]
        return new.capitalize() if word[0].isupper() else new
    if low.endswith("ия"):
        return word[:-1] + "и"
    if low.endswith(("й", "ь")):
        return word[:-1] + "я"
    if low.endswith("а"):
        return word[:-1] + ("и" if len(low) > 1 and low[-2] in "гкхжчшщ" else "ы")
    if low.endswith("я"):
        return word[:-1] + "и"
    if low[-1] in "еоиуыэю":
        return word
    return word + "а"


def sender_name(order):
    """Имя отправителя для «от …»: подпись без «Твой/Твоя…», иначе имя из профиля Telegram."""
    sign = (order.get("sign") or "").strip()
    while _POSSESSIVE.match(sign):
        sign = _POSSESSIVE.sub("", sign, count=1).strip()
    if not sign:
        sign = ((order.get("name") or "").split() or [""])[0]
    return sign


def from_whom(order):
    """«Серёжи» / «мамы» — готово к подстановке после «от»."""
    return genitive(sender_name(order)) or "близкого человека"


def _names(order):
    return (order.get("gift_for") or "").strip(), from_whom(order)


def closed_html(order, code, site, bot_username, image_site=None):
    """Закрытый конверт: печать, кнопка «Вскрыть письмо от …», og-метки для превью."""
    name, who = _names(order)
    title = f"💌 {name}, тебе письмо от {who}" if name else f"💌 Тебе письмо от {who}"
    desc = "Нажми — и вскроешь конверт: внутри открытка и слова, написанные специально для тебя."
    esc = html.escape
    head = (f'<meta property="og:type" content="website"><meta property="og:title" content="{esc(title)}">'
            f'<meta property="og:description" content="{esc(desc)}">'
            f'<meta property="og:image" content="{image_site or site}/e/{esc(code)}.jpg">'
            f'<meta property="og:url" content="{site}/e/{esc(code)}"><meta name="twitter:card" content="summary_large_image">')
    body = (f'<div class="env"><div class="flap"></div><div class="seal">А</div></div>'
            f'<h1>💌 {esc(name) + ", тебе" if name else "Тебе"} письмо</h1>'
            f'<p class="sub">От <b>{esc(who)}</b>. {esc(desc)}</p>'
            f'<a class="btn" href="/e/{esc(code)}/open">✉️ Вскрыть письмо от {esc(who)}</a>')
    script = f"<script>{FX % {'ms': 6000}}</script>"
    return _page("Тебе письмо 💌", head, body, script)


def open_html(order, code, animated, bot_username):
    """Вскрытие: печать ломается, открытка (GIF, если выбрана анимация) и письмо появляются по абзацам."""
    name, who = _names(order)
    esc = html.escape
    img = f"/e/{esc(code)}.gif" if animated else f"/e/{esc(code)}.jpg"
    paragraphs = [p.strip() for p in (order.get("letter_text") or "").replace("\r\n", "\n").split("\n\n") if p.strip()]
    last = len(paragraphs) - 1
    letter = "".join(f'<p style="animation-delay:{3.4 + i * 0.7:.1f}s"'
                     f'{" class=" + chr(34) + "sign" + chr(34) if i == last and last > 0 and len(p) <= 60 else ""}>'
                     f'{esc(p).replace(chr(10), "<br>")}</p>'
                     for i, p in enumerate(paragraphs))   # короткая последняя строка — подпись: Bad Script
    body = (f'<div class="stage"><div class="seal">А</div><div class="half h1"></div><div class="half h2"></div></div>'
            f'<img class="card reveal" style="animation-delay:1.7s" src="{img}" alt="Открытка">'
            f'<div class="l" style="margin-top:28px">{letter}</div>'
            f'<p class="f reveal" style="animation-delay:{4 + len(paragraphs) * 0.7:.1f}s">Это письмо собрала Алиса Невская — '
            'цифровая героиня проекта «Когда трудно сказать важное — здесь находятся слова». Захочется ответить так же — '
            f'<a href="https://vk.me/alisanevskaya_diary">напиши Алисе во ВКонтакте</a> или '
            f'<a href="https://t.me/{esc(bot_username)}?start=occ">в Telegram</a>.</p>')
    script = f"<script>{FX % {'ms': 9000}}</script>"
    return _page("Письмо 💌", "", body, script)
