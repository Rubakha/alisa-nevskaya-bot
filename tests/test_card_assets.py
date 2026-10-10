"""Новые картинки подхватываются без правки кода: реквизит assets/props/*.png и фоны assets/postcards/<повод>/*.jpg.
Широкий реквизит не уходит на центр кадра. Запуск: python tests/test_card_assets.py"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["TG_BOT_TOKEN"] = "1:T"

from PIL import Image  # noqa: E402

import cardbase  # noqa: E402
import constructor as CC  # noqa: E402
import postcards as PC  # noqa: E402

# ── реквизит ──
tmp_props = tempfile.mkdtemp()
for f in os.listdir(PC.PROPS):
    shutil.copy(os.path.join(PC.PROPS, f), tmp_props)
PC.PROPS = tmp_props
base = PC.available_props()
assert base[:10] == PC.PROP_FILES, "прежний набор — первым и в прежнем порядке"
wide = Image.new("RGBA", (900, 200), (200, 50, 50, 255))
wide.save(os.path.join(tmp_props, "new_wide_scarf.png"))
Image.new("RGBA", (100, 100), (0, 0, 0, 255)).save(os.path.join(tmp_props, "Bad-Name.png"))   # недопустимое имя
Image.new("RGBA", (100, 100), (0, 0, 0, 255)).save(os.path.join(tmp_props, "a.b.png"))
now = PC.available_props()
assert now[:10] == PC.PROP_FILES and set(now) == set(base) | {"new_wide_scarf"} and now == base[:10] + sorted(set(now) - set(base[:10])), now
assert CC.prop_labels()["new_wide_scarf"] == "🖼 New wide scarf" and CC.prop_labels()["tea_cup"] == "☕ Чашка"
ref = PC.encode_spec("birthday-001", "none", ["new_wide_scarf", "tea_cup", "nope"], "corners", "none")
assert PC.parse_spec(ref)["props"] == ["new_wide_scarf", "tea_cup"], "новый предмет принимается, чужое имя — нет"
# ширина предмета ограничена, чтобы не накрывать центр кадра
fit = PC._fit_prop(wide, 250)
assert fit.width <= PC.MAX_PROP_W and fit.height < 250
im = Image.new("RGB", (PC.W, PC.PHOTO_H), (240, 240, 240))
for lay in PC.LAYOUTS:
    for n in (1, 2, 3):
        spec = {"base": "birthday-001", "pal": "none", "props": (["new_wide_scarf", "tea_cup", "gift_tag"])[:n],
                "lay": lay, "frame": "none", "anim": "none"}
        out = PC.compose_custom(im.copy(), spec)
        assert out.size == im.size
        # центр кадра (лица) остаётся нетронутым: предметы только по краям
        centre = out.crop((PC.W // 2 - 100, PC.PHOTO_H // 2 - 100, PC.W // 2 + 100, PC.PHOTO_H // 2 + 100))
        assert centre.getextrema() == ((240, 240), (240, 240), (240, 240)), (lay, n)
print("props ok")

# русские подписи у всех предметов, которые лежат в assets/props сейчас (иначе в конструкторе появятся «Quill pen»)
real = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "props")
for f in sorted(os.listdir(real)):
    if f.endswith(".png"):
        name = f[:-4]
        assert name in CC.PROP_LABELS and not CC.PROP_LABELS[name].startswith("🖼"), f"нет русской подписи: {name}"
for name in ("quill_pen", "candle_brass", "pressed_daisies", "eucalyptus_sprig", "wheat_ears", "lavender_sprig",
             "dried_rose", "heart_locket", "pine_branch", "ornament_ball", "snowflake_lace", "cinnamon_orange",
             "cake_slice", "teapot_cream", "pocket_watch", "old_book", "wax_seal_stamp", "ivy_sprig", "pearl_string",
             "gift_box_small"):
    assert name in CC.PROP_LABELS, name
assert CC.PROP_LABELS["quill_pen"] == "🪶 Перо" and CC.PROP_LABELS["snowflake_lace"] == "❄️ Кружевная снежинка"

# ── фоны ──
bgs, data = tempfile.mkdtemp(), tempfile.mkdtemp()
os.makedirs(os.path.join(bgs, "birthday"))
Image.new("RGB", (100, 80), (1, 2, 3)).save(os.path.join(bgs, "birthday", "001.jpg"))
cardbase.init(data, bgs)
assert cardbase.active_ids("birthday") == ["birthday-001"]
Image.new("RGB", (100, 80), (1, 2, 3)).save(os.path.join(bgs, "birthday", "002.jpg"))      # без записи в каталоге — сразу активна
os.makedirs(os.path.join(bgs, "thanks"))
Image.new("RGB", (100, 80), (1, 2, 3)).save(os.path.join(bgs, "thanks", "p01.jpg"))        # новый повод
import json  # noqa: E402
with open(os.path.join(bgs, "catalog.json"), "w", encoding="utf-8") as f:
    json.dump({"cards": [{"id": "birthday-003", "status": "review"}]}, f)
Image.new("RGB", (100, 80), (1, 2, 3)).save(os.path.join(bgs, "birthday", "003.jpg"))      # в каталоге на проверку — скрыта
cardbase._SYNC["at"] = 0
assert cardbase.active_ids("birthday") == ["birthday-001", "birthday-002"], cardbase.active_ids("birthday")
assert cardbase.active_ids("thanks") == ["thanks-p01"]
assert cardbase.get_card("birthday-003")["status"] == "review"
assert cardbase.refresh_if_changed(max_age=0) == 0, "без изменений повторно не синхронизируем"
print("OK: card assets")
