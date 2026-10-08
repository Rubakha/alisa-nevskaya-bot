"""«Голосовое письмо» без сети: апселл, оплата, выдача, фича без ключа. ElevenLabs подменён.
Запуск: python tests/test_voice_letter.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types as pytypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["ADMIN_ID"] = "1"
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "test"
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
for k in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID_F", "ELEVENLABS_VOICE_ID_M", "VOICE_LETTER_PRICE", "VOICE_UPSELL_PRICE"):
    os.environ.pop(k, None)

import bot_best as B  # noqa: E402
import voice_flow as VF  # noqa: E402
import voice_letter as VL  # noqa: E402

VF.ASYNC = False
SENT = []


def rec(name):
    def f(*a, **k):
        SENT.append((name, a, k))
        return pytypes.SimpleNamespace(message_id=len(SENT), chat=pytypes.SimpleNamespace(id=500),
                                       voice=pytypes.SimpleNamespace(file_id="VOICE-FID"),
                                       photo=[pytypes.SimpleNamespace(file_id="PHOTO-FID")])
    return f


for m in ["send_message", "send_photo", "send_invoice", "send_chat_action", "answer_callback_query",
          "edit_message_text", "edit_message_reply_markup", "send_media_group", "send_document",
          "send_voice", "send_audio", "answer_inline_query"]:
    setattr(B.bot, m, rec(m))


class FakeAI:
    @staticmethod
    def available():
        return True

    @staticmethod
    def generate_occasion(brief, key, qa, tone, sign, title):
        return {"letter": "Катя, помнишь, как мы ели черешню на крыше? 🌿\n\nТы смеёшься громче всех ✉️\n" * 4
                          + f"\n{sign}",
                "card_title": title, "card_line": "Ты смеёшься громче всех — и мир теплее."}


B.AI = FakeAI
TTS_CALLS = []


class FakeResp:
    status_code = 200
    content = b"ID3fake-mp3"
    text = ""


def fake_post(url, params=None, headers=None, json=None, timeout=None):
    TTS_CALLS.append({"url": url, "headers": headers, "json": json})
    return FakeResp()


VL.requests.post = fake_post
REAL_MIX = VL.mix
VL.mix = lambda voice_mp3, music=None: (b"OggS-fake", voice_mp3)  # ffmpeg в этом тесте не нужен


def msg(text, chat=500):
    return pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat), text=text, content_type="text",
                                   from_user=pytypes.SimpleNamespace(id=chat, first_name="Тест", last_name="",
                                                                     username="t"))


def call(data, chat=500):
    return pytypes.SimpleNamespace(id="c", data=data, from_user=msg("", chat).from_user,
                                   message=pytypes.SimpleNamespace(chat=pytypes.SimpleNamespace(id=chat),
                                                                   message_id=1))


def calls(name):
    return [s for s in SENT if s[0] == name]


def buttons(markup):
    return [b for row in markup.keyboard for b in row]


def pay(order_id):
    m = msg("")
    m.successful_payment = pytypes.SimpleNamespace(invoice_payload=order_id, telegram_payment_charge_id="x",
                                                   order_info=None)
    B.on_paid(m)


def write_letter():
    B.occ_go(call("occ:go:birthday"))
    for a in ["Катя", "подруга", "30", "смеётся громче всех"]:
        B.occ_answer(msg(a))
    B.occ_tone(call("occ:tone:warm"))
    B.occ_answer(msg("Серёжа"))
    assert B.STATES[500]["step"] == "occ_paywall"


# ── 1. без ключа фича скрыта ─────────────────────────────────
assert not VL.enabled() and VL.genders() == []
assert not any("vo:" in (b.callback_data or "") for b in buttons(B.occ_catalog_markup()))
write_letter()
B.occ_buy(call("occ:buy"))
oid = calls("send_invoice")[-1][2]["invoice_payload"]
pay(oid)
assert not any("vo:up" in (b.callback_data or "") for s in SENT if s[0] == "send_message"
               for b in (buttons(s[2]["reply_markup"]) if s[2].get("reply_markup") else [])
               if hasattr(b, "callback_data")), "апселл виден без ключа"
B.bot.answer_callback_query = rec("answer_callback_query")
VF._upsell(call(f"vo:up:{oid}"))
assert not calls("send_invoice")[1:], "доплата выставлена без ключа"
assert not TTS_CALLS and not calls("send_voice")
print("OK: без ключа фича скрыта")

# ── 2. очистка текста, паузы, чанки ──────────────────────────
t = VL.clean_text("Привет, Катя 🌿\n\nТы *лучшая* ✉️🤍\nправда\n\n\n▒▒▒")
assert t == "Привет, Катя\n\nТы лучшая правда", repr(t)
assert VL.with_pauses("а\n\nб") == 'а <break time="0.8s" /> б'
os.environ["VOICE_PAUSE_SEC"] = "0"
assert VL.with_pauses("а\n\nб") == "а\n\nб"
os.environ.pop("VOICE_PAUSE_SEC")
big = "\n\n".join(["Слово. " * 300] * 4)
assert all(len(c) <= VL.MAX_CHUNK for c in VL.chunks(big)) and len(VL.chunks(big)) > 1
print("OK: текст")

# ── 3. включаем ElevenLabs (ключи фиктивные) ─────────────────
os.environ["ELEVENLABS_API_KEY"] = "test-key"
os.environ["ELEVENLABS_VOICE_ID_F"] = "voice-f"
os.environ["ELEVENLABS_VOICE_ID_M"] = "voice-m"
assert VL.enabled() and VL.genders() == ["f", "m"] and VL.price() == 390
assert VL.upsell_price() == 100
os.environ["VOICE_UPSELL_PRICE"] = "150"
assert VL.upsell_price() == 150
os.environ.pop("VOICE_UPSELL_PRICE")
labels = [b.text for b in buttons(B.occ_catalog_markup())]
assert "🎙 Голосовое письмо · 390₽" in labels, labels

# ── 4. апселл: доплата разницы → оплата → выдача ─────────────
order = B.get_order(oid)
kb = B.types.InlineKeyboardMarkup()
VF.add_upsell(kb, order)
assert [b.text for b in buttons(kb)] == ["🎙 Озвучить голосом — +100₽"], buttons(kb)
SENT.clear()
VF._upsell(call(f"vo:up:{oid}"))                    # два голоса — сначала выбор
assert "голосом" in calls("send_message")[-1][1][1] and not calls("send_invoice")
VF._upsell(call(f"vo:up:{oid}:m"))
inv = calls("send_invoice")[-1][2]
assert inv["prices"][0].amount == 100 * 100, inv
addon_id = inv["invoice_payload"]
addon = B.get_order(addon_id)
assert addon["product"] == "voice_addon" and addon["voice_for"] == oid and addon["voice_gender"] == "m"
pay(addon_id)
assert len(TTS_CALLS) == 1 and TTS_CALLS[0]["url"].endswith("/voice-m"), TTS_CALLS
assert TTS_CALLS[0]["headers"]["xi-api-key"] == "test-key"
sent_text = TTS_CALLS[0]["json"]["text"]
assert "🌿" not in sent_text and "✉" not in sent_text and "<break" in sent_text, sent_text
assert TTS_CALLS[0]["json"]["model_id"] == "eleven_multilingual_v2"
assert calls("send_voice") and calls("send_audio"), "нет выдачи"
parent, addon = B.get_order(oid), B.get_order(addon_id)
assert parent["voice_file_id"] == "VOICE-FID" and parent["voice_addon_order"] == addon_id
assert addon["voice_status"] == "done" and addon["voice_chars"] > 100
share = [b for s in calls("send_message") if s[2].get("reply_markup") for b in buttons(s[2]["reply_markup"])
         if getattr(b, "switch_inline_query_chosen_chat", None)]
assert share and share[-1].switch_inline_query_chosen_chat.query == "voice_" + parent["gift_code"], share
usage = [json.loads(x) for x in open(os.path.join(B.DATA_DIR, "voice_usage.jsonl"), encoding="utf-8")]
assert usage[-1]["order_id"] == addon_id and usage[-1]["chars"] == addon["voice_chars"] and usage[-1]["ok"]
# повторно озвучивать нельзя: апселл исчез
kb = B.types.InlineKeyboardMarkup()
VF.add_upsell(kb, B.get_order(oid))
assert not buttons(kb)
print("OK: апселл и выдача")

# ── 5. inline-отправка голосового адресату ───────────────────
SENT.clear()
q = pytypes.SimpleNamespace(id="q1", query="voice_" + parent["gift_code"], from_user=msg("").from_user)
VF.inline_send(q)
res = calls("answer_inline_query")[-1][1][1]
assert len(res) == 1 and res[0].voice_file_id == "VOICE-FID"
q.from_user = msg("", 999).from_user   # чужой аккаунт — пусто
VF.inline_send(q)
assert calls("answer_inline_query")[-1][1][1] == []
print("OK: inline")

# ── 6. пункт каталога: цена 390, голос выбран, выдача после оплаты ──
SENT.clear()
B.STATES.pop(500, None)
VF._go(call("vo:go:birthday"))                       # выбор голоса
assert "голосом" in calls("send_message")[-1][1][1] and 500 not in B.STATES
VF._go(call("vo:go:birthday:f"))
assert B.STATES[500]["voice"] and B.STATES[500]["price"] == 390 and B.STATES[500]["voice_gender"] == "f"
for a in ["Катя", "подруга", "30", "смеётся"]:
    B.occ_answer(msg(a))
B.occ_tone(call("occ:tone:warm"))
B.occ_answer(msg("Серёжа"))
SENT.clear()
n_tts = len(TTS_CALLS)
B.occ_buy(call("occ:buy"))
inv = calls("send_invoice")[-1][2]
assert inv["prices"][0].amount == 390 * 100, inv
vid = inv["invoice_payload"]
assert B.get_order(vid)["voice"] is True
pay(vid)
assert len(TTS_CALLS) == n_tts + 1 and TTS_CALLS[-1]["url"].endswith("/voice-f")
assert calls("send_voice") and calls("send_audio") and calls("send_photo")
vo = B.get_order(vid)
assert vo["voice_status"] == "done" and vo["voice_file_id"] == "VOICE-FID"
assert not any("vo:up" in (getattr(b, "callback_data", "") or "") for s in calls("send_message")
               if s[2].get("reply_markup") for b in buttons(s[2]["reply_markup"])), "апселл у уже озвученного"
print("OK: каталог → оплата → выдача")

# ── 7. сбой ElevenLabs: письмо на месте, клиент и админ предупреждены ──
FakeResp.status_code = 500
FakeResp.text = "boom"
SENT.clear()
VF._go(call("vo:go:friend:f"))
for a in ["Оля", "5 лет", "общая привычка", ]:
    B.occ_answer(msg(a))
B.occ_tone(call("occ:tone:simple"))
B.occ_answer(msg("Серёжа"))
B.occ_buy(call("occ:buy"))
fid = calls("send_invoice")[-1][2]["invoice_payload"]
pay(fid)
fo = B.get_order(fid)
assert fo["status"] == "done" and fo["voice_status"] == "failed", fo
assert any(s[1][0] == 1 and "voice_retry" in s[1][1] for s in calls("send_message")), "админ не уведомлён"
FakeResp.status_code = 200
assert VF.deliver(fo["chat_id"], fid, force=True) and B.get_order(fid)["voice_status"] == "done"
print("OK: сбой и повтор")

# ── 8. музыка: нет файла → только голос; есть → микс ─────────
VL.mix = REAL_MIX
empty = tempfile.mkdtemp()
assert VL.pick_music("x", music_dir=empty) is None and VL.pick_music("x", music_dir=empty + "/нет") is None
if shutil.which("ffmpeg"):
    work = tempfile.mkdtemp()
    tone = lambda f, hz, sec: subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                                              f"sine=frequency={hz}:duration={sec}", f], check=True)
    voice, music = os.path.join(work, "v.mp3"), os.path.join(work, "m.mp3")
    tone(voice, 440, 3)
    tone(music, 220, 1)
    assert VL.pick_music("x", music_dir=work) in (voice, music)
    vb = open(voice, "rb").read()
    ogg, mp3 = VL.mix(vb, music)
    assert ogg[:4] == b"OggS" and mp3[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3"), (ogg[:4], mp3[:3])
    ogg0, _ = VL.mix(vb, None)
    assert ogg0[:4] == b"OggS"
    open(os.path.join(work, "o.ogg"), "wb").write(ogg)
    assert VL._duration(os.path.join(work, "o.ogg")) > 5, "подложка не продлила звучание хвостом"
    print("OK: ffmpeg-микс")
else:
    print("SKIP: ffmpeg не установлен")

# ── 9. переключатель озвучки на превью ───────────────────────
VL.mix = lambda voice_mp3, music=None: (b"OggS-fake", voice_mp3)
B.STATES.pop(500, None)
write_letter()
st = B.STATES[500]
kb = B.occ_preview_markup(st)
assert "🎙 Озвучить голосом — +100₽" in [b.text for b in buttons(kb)], [b.text for b in buttons(kb)]
SENT.clear()
VF._preview_toggle(call("vo:pv:on"))                 # два голоса — спрашивает
assert "голосом" in calls("send_message")[-1][1][1] and not st.get("voice")
VF._preview_toggle(call("vo:pv:on:m"))
assert st["voice"] and st["price"] == 199 + 100 and st["voice_gender"] == "m"
assert calls("edit_message_text"), "превью не перерисовано"
kb = B.occ_preview_markup(st)
texts = [b.text for b in buttons(kb)]
assert any("299" in t for t in texts) and "🎙 Озвучка включена · убрать" in texts, texts
VF._preview_toggle(call("vo:pv:off"))
assert not st.get("voice") and not st.get("price")
VF._preview_toggle(call("vo:pv:on:f"))
B.occ_buy(call("occ:buy"))
inv = calls("send_invoice")[-1][2]
assert inv["prices"][0].amount == 299 * 100
pid = inv["invoice_payload"]
n = len(TTS_CALLS)
pay(pid)
assert len(TTS_CALLS) == n + 1 and TTS_CALLS[-1]["url"].endswith("/voice-f") and B.get_order(pid)["voice_status"] == "done"
# при включённой озвучке «забрать по набору» не показывается
B.STATES.pop(500, None)
write_letter()
c = B.get_client(500); c["credits"] = 2; B.write_json(B.client_path(500), c)
VF._preview_toggle(call("vo:pv:on:f"))
assert not any(b.callback_data == "occ:credit" for b in buttons(B.occ_preview_markup(B.STATES[500])))
SENT.clear(); B.occ_credit(call("occ:credit"))
assert B.get_client(500)["credits"] == 2, "набор списан под озвучку"
os.environ.pop("ELEVENLABS_API_KEY")                 # без ключа кнопки нет
assert not any("vo:pv" in (b.callback_data or "") for b in buttons(B.occ_preview_markup(B.STATES[500])))
os.environ["ELEVENLABS_API_KEY"] = "test-key"
print("OK: озвучка на превью")
print("OK: all voice letter checks passed")
