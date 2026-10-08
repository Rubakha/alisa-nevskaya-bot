"""Премиум-формат «Голосовое письмо»: письмо, прочитанное живым голосом, с тихой музыкой.

Модуль не знает про Telegram: текст -> ElevenLabs (HTTP) -> mp3 -> (ffmpeg) подложка -> ogg/opus + mp3.
Всё настраивается переменными окружения; без ELEVENLABS_API_KEY и id голоса функция скрыта (enabled() == False).

  ELEVENLABS_API_KEY        ключ API (без него фича выключена)
  ELEVENLABS_VOICE_ID_F     id женского голоса
  ELEVENLABS_VOICE_ID_M     id мужского голоса (достаточно одного из двух)
  ELEVENLABS_MODEL          модель, по умолчанию eleven_v4
  ELEVENLABS_STABILITY      stability голоса (по умолчанию 0.35)
  ELEVENLABS_SIMILARITY     similarity_boost (по умолчанию 0.85); use_speaker_boost всегда true, style не передаётся (v4 его не принимает)
  ELEVENLABS_SEED           seed для повторяемости подачи (по умолчанию 42)
  VOICE_DIRECTION           указание подачи в начале текста (по умолчанию "[warmly, softly]"; пусто — без указания)
  VOICE_LETTER_PRICE        цена пункта каталога «Голосовое письмо», ₽ (по умолчанию 390)
  VOICE_UPSELL_PRICE        фиксированная доплата «Озвучить голосом» к обычному письму, ₽ (по умолчанию 100)
  VOICE_MUSIC_DB            громкость подложки относительно голоса, dB (по умолчанию -22)
  VOICE_PAUSE               пауза между абзацами многоточием (по умолчанию 1; 0 — просто пустая строка). v4 не понимает SSML <break>
"""
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

import requests

log = logging.getLogger("alisa.voice")

BASE = os.path.dirname(os.path.abspath(__file__))
MUSIC_DIR = os.path.join(BASE, "assets", "music")
MUSIC_EXT = (".mp3", ".ogg", ".wav", ".m4a", ".flac")

ADDON_KEY = "voice_addon"   # product доплаты за озвучку уже оплаченного письма
API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
MAX_CHUNK = 4500            # символов в одном запросе (лимит модели больше, режем по абзацам)
MAX_TOTAL = 9000            # письма длиннее не озвучиваем целиком


def _env(name, default=""):
    return (os.getenv(name) or default).strip()


def api_key():
    return _env("ELEVENLABS_API_KEY")


def voice_id(gender):
    return _env("ELEVENLABS_VOICE_ID_M" if gender == "m" else "ELEVENLABS_VOICE_ID_F")


def genders():
    """Доступные голоса: ['f', 'm'] — те, для которых задан id."""
    return [g for g in ("f", "m") if voice_id(g)] if api_key() else []


def enabled():
    return bool(genders())


def price():
    try:
        return max(int(_env("VOICE_LETTER_PRICE", "390")), 1)
    except ValueError:
        return 390


def upsell_price():
    """Фиксированная доплата «Озвучить голосом» к обычному письму (после оплаты и на превью), ₽."""
    try:
        return max(int(_env("VOICE_UPSELL_PRICE", "100")), 0)
    except ValueError:
        return 100


def _float_env(name, default):
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


# ── текст ────────────────────────────────────────────────────

_EMOJI = re.compile("[\U00010000-\U0010ffff☀-➿⬀-⯿️‍⃣]")


def clean_text(text):
    """Текст для озвучки: без эмодзи, разметки и квадратных скобок (v4 читает их как указания подачи),
    абзацы разделены пустой строкой."""
    text = _EMOJI.sub("", text or "")
    text = re.sub(r"[*_`~#>\[\]]+", "", text)
    text = text.replace("▒", "")
    paras = [re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", " ", p)).strip() for p in re.split(r"\n\s*\n", text)]
    return "\n\n".join(p for p in paras if p)


def with_pauses(text):
    """Пауза между абзацами: многоточие в конце абзаца + пустая строка (SSML <break> v4 не понимает)."""
    if _env("VOICE_PAUSE", "1") in ("0", "false", "no"):
        return text
    paras = text.split("\n\n")
    out = [p if p.endswith(("...", "…", "?", "!")) else p.rstrip(".") + "..." for p in paras[:-1]]
    return "\n\n".join(out + paras[-1:])


def direction():
    """Указание подачи для v4, например «[warmly, softly] » (в начало каждого запроса)."""
    d = os.getenv("VOICE_DIRECTION")
    d = "[warmly, softly]" if d is None else d.strip()
    return d + " " if d else ""


def chunks(text, limit=MAX_CHUNK):
    """Режет очищенный текст по абзацам на части не длиннее limit."""
    out, cur = [], ""
    for p in text.split("\n\n"):
        while len(p) > limit:  # абзац-гигант режем по предложениям/пробелу
            cut = max(p.rfind(". ", 0, limit), p.rfind(" ", 0, limit))
            cut = cut + 1 if cut > 0 else limit
            if cur:
                out.append(cur)
                cur = ""
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if cur and len(cur) + len(p) + 2 > limit:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


# ── ElevenLabs ───────────────────────────────────────────────

def tts(text, gender="f"):
    """Озвучка очищенного текста. Возвращает (mp3_bytes, символов_отправлено). Бросает RuntimeError."""
    vid = voice_id(gender)
    if not (api_key() and vid):
        raise RuntimeError("ElevenLabs не настроен")
    text = clean_text(text)
    if not text:
        raise RuntimeError("пустой текст")
    text = text[:MAX_TOTAL]
    audio, sent = [], 0
    for part in chunks(text):
        body = direction() + with_pauses(part)
        payload = {
            "text": body,
            "model_id": _env("ELEVENLABS_MODEL", "eleven_v4"),
            "seed": int(_float_env("ELEVENLABS_SEED", 42)),
            "voice_settings": {"stability": _float_env("ELEVENLABS_STABILITY", 0.35),
                               "similarity_boost": _float_env("ELEVENLABS_SIMILARITY", 0.85),
                               "use_speaker_boost": True},
        }
        r = requests.post(API.format(voice=vid), params={"output_format": "mp3_44100_128"},
                          headers={"xi-api-key": api_key(), "Accept": "audio/mpeg"},
                          json=payload, timeout=120)
        if r.status_code != 200 or not r.content:
            raise RuntimeError(f"ElevenLabs {r.status_code}: {(r.text or '')[:200]}")
        audio.append(r.content)
        sent += len(body)
    return b"".join(audio), sent


# ── музыка и ffmpeg ──────────────────────────────────────────

def ffmpeg_ok():
    return bool(shutil.which("ffmpeg"))


def pick_music(seed="", music_dir=None):
    """Трек из assets/music/ (детерминированно по seed). Нет папки/файлов — None (будет только голос)."""
    folder = music_dir or MUSIC_DIR
    try:
        files = sorted(f for f in os.listdir(folder) if f.lower().endswith(MUSIC_EXT))
    except OSError:
        return None
    if not files:
        return None
    idx = int(hashlib.md5(str(seed).encode()).hexdigest(), 16) % len(files)
    return os.path.join(folder, files[idx])


def _duration(path):
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _run(cmd):
    res = subprocess.run(cmd, capture_output=True, timeout=180)
    if res.returncode != 0:
        raise RuntimeError("ffmpeg: " + res.stderr.decode("utf-8", "ignore")[-300:])


def mix(voice_mp3, music_path=None):
    """Голос + тихая подложка. Возвращает (ogg_opus_bytes, mp3_bytes).

    Подложка по кругу, громкость VOICE_MUSIC_DB (−22 dB по умолчанию), плавный вход и затухание,
    после голоса ещё ~2.5 секунды музыки. Нет ffmpeg — (None, voice_mp3): уйдёт один mp3."""
    if not ffmpeg_ok():
        return None, voice_mp3
    db = min(max(_float_env("VOICE_MUSIC_DB", -22.0), -40.0), -10.0)
    with tempfile.TemporaryDirectory() as tmp:
        vin = os.path.join(tmp, "voice.mp3")
        with open(vin, "wb") as f:
            f.write(voice_mp3)
        out_mp3, out_ogg = os.path.join(tmp, "out.mp3"), os.path.join(tmp, "out.ogg")
        if music_path and os.path.exists(music_path):
            total = _duration(vin) + 2.5
            fade = f",afade=t=out:st={max(total - 2.5, 0):.2f}:d=2.5" if total > 3 else ""
            flt = (f"[1:a]volume={db}dB,afade=t=in:d=1.5[m];"
                   f"[0:a]apad=pad_dur=2.5[v];"
                   f"[v][m]amix=inputs=2:duration=first:dropout_transition=0,volume=2{fade}[a]")
            _run(["ffmpeg", "-y", "-v", "error", "-i", vin, "-stream_loop", "-1", "-i", music_path,
                  "-filter_complex", flt, "-map", "[a]", "-ar", "44100", "-ac", "2", "-b:a", "128k", out_mp3])
        else:
            shutil.copy(vin, out_mp3)
        _run(["ffmpeg", "-y", "-v", "error", "-i", out_mp3, "-c:a", "libopus", "-b:a", "48k",
              "-ar", "48000", "-ac", "1", out_ogg])
        with open(out_ogg, "rb") as f:
            ogg = f.read()
        with open(out_mp3, "rb") as f:
            mp3 = f.read()
    return ogg, mp3


def make(text, gender="f", seed=""):
    """Весь конвейер. Возвращает dict: ogg (или None), mp3, chars, music (bool)."""
    voice_mp3, chars = tts(text, gender)
    music = pick_music(seed)
    try:
        ogg, mp3 = mix(voice_mp3, music)
    except Exception as exc:  # микс не должен сорвать оплаченный заказ: отдаём чистый голос
        log.error("voice mix failed, plain voice: %s", exc)
        music = None
        try:
            ogg, mp3 = mix(voice_mp3, None)
        except Exception as exc2:
            log.error("voice convert failed, mp3 only: %s", exc2)
            ogg, mp3 = None, voice_mp3
    return {"ogg": ogg, "mp3": mp3, "chars": chars, "music": bool(music and ogg)}


# ── учёт расходов ────────────────────────────────────────────

def log_usage(data_dir, order_id, chars, gender, ok=True):
    """Символы ElevenLabs на заказ: строка в DATA_DIR/voice_usage.jsonl (отдельного учёта расходов в боте нет)."""
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "order_id": order_id, "chars": chars,
           "voice": gender, "ok": ok, "model": _env("ELEVENLABS_MODEL", "eleven_v4")}
    log.info("elevenlabs usage: %s", json.dumps(rec, ensure_ascii=False))
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "voice_usage.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        log.error("voice usage not written: %s", exc)


def usage_total(data_dir, days=None):
    """Сумма символов по журналу (для админа). Возвращает (символов, заказов)."""
    path = os.path.join(data_dir, "voice_usage.jsonl")
    chars = n = 0
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                chars += int(r.get("chars") or 0)
                n += 1
    except OSError:
        pass
    return chars, n
