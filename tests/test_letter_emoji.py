"""Смайлики в письмах: правило есть в промптах (и одиночного, и общего письма), подсказка по тону, заголовок и строка открытки
без лишних смайлов, обращение и подпись без смайлов. Платный API не вызывается. Запуск: python tests/test_letter_emoji.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ai_assistant as A  # noqa: E402

CALLS = []
NL = chr(10)
REPLY = NL.join(["===TITLE===", "С днём рождения, Катя! 🎂🎉", "===LINE===", "Ты смеёшься громче всех 🌷✨",
                 "===LETTER===",
                 "Катя, привет 🌷", "", "Помнишь черешню на крыше? Это было лучшее лето ☕", "",
                 "Спасибо, что ты есть 🤍", "Твой Серёжа 🤍"])


def fake_call(system, prompt, max_tokens=1500, model=None, temperature=None):
    CALLS.append({"system": system, "prompt": prompt})
    return REPLY


A._call = fake_call
QA = [("Как зовут?", "Катя"), ("Детали", "черешня")]

for tone, expect in (("solemn", "Смайликов 1–2"), ("tender", "Смайликов 2–4"), ("warm", "Смайликов 3–5"),
                     ("simple", "Смайликов 2–3")):
    out = A.generate_occasion("поздравление", "birthday", QA, tone, "Серёжа", "С днём рождения, Катя!")
    c = CALLS[-1]
    assert "2–5 уместных по теме смайликов" in c["system"], "правило про смайлики в системном промпте"
    assert "обращение в начале и подпись в конце — без смайликов" in c["system"]
    assert "без смайлов-лиц" in c["system"] and expect in c["prompt"], (tone, c["prompt"])
    assert "без смайликов" in c["prompt"], "для заголовка и строки открытки смайлики не просим"

# открытка: максимум один смайл; письмо: обращение и подпись чистые, середина сохранена
assert len(A.EMOJI_RE.findall(out["card_title"])) <= 1 and len(A.EMOJI_RE.findall(out["card_line"])) <= 1
lines = [ln for ln in out["letter"].split(NL) if ln.strip()]
assert not A.EMOJI_RE.search(lines[0]) and lines[0].startswith("Катя, привет"), lines[0]
assert not A.EMOJI_RE.search(lines[-1]) and "Серёжа" in lines[-1], lines[-1]
assert "☕" in out["letter"] and "🤍" in out["letter"], "смайлики внутри письма остаются"

# общее письмо: то же правило и очистка; «— Имя» без смайлов
GREPLY = NL.join(["===TITLE===", "Маме 🎂🥂", "===LINE===", "Мы любим тебя 🤍🤍", "===LETTER===",
                  "Дорогая мама 🌷", "", "Я помню твои блины ☕", "— Аня 🤍", "", "Общая подпись 🤍"])
A._call = lambda system, prompt, **k: (CALLS.append({"system": system, "prompt": prompt}), GREPLY)[1]
g = A.generate_group_letter("день рождения", "Мама", "мама", [{"sign": "Аня", "memory": "блины", "thanks": "спасибо"}],
                            "Ваши дети", "Маме")
assert "2–5 уместных по теме смайликов" in CALLS[-1]["system"] and "без смайликов" in CALLS[-1]["prompt"]
assert len(A.EMOJI_RE.findall(g["card_title"])) <= 1 and len(A.EMOJI_RE.findall(g["card_line"])) <= 1
glines = [ln for ln in g["letter"].split(NL) if ln.strip()]
assert not A.EMOJI_RE.search(glines[0]) and not A.EMOJI_RE.search(glines[-1])
assert [ln for ln in glines if ln.startswith("—")] == ["— Аня"], glines
assert "☕" in g["letter"]

assert A.cap_emoji("Привет 🌷 мир 🎂", 1) == "Привет 🌷 мир" and A.cap_emoji("Без смайлов") == "Без смайлов"
assert A.cap_emoji("❤️ и 🕊️", 1) == "❤️ и"
print("OK: letter emoji")
