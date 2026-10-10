#!/usr/bin/env python3
"""gen_cards_pinterest.py — фоны открыток в стиле топа Pinterest (разбор: ai-content-pipeline/references/CARD_STYLE_PINTEREST.md).
Использует gen_cards (ключ, бюджет, журнал расходов, каталог со статусом review). Платная генерация только с --confirm-spend.
  python tools/gen_cards_pinterest.py --count 6 --confirm-spend [--occasion love] [--model flux2]"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_cards as G  # noqa: E402

BASE = ("Top-down flat lay on textured cream handmade paper, lots of calm empty space for text in the lower third and center, "
        "soft diffused natural light, muted warm cream, sand and sage palette{accent}, natural analog film grain, slightly imperfect, "
        "elegant editorial still life, no text, no letters, no numbers, no people.")
ACC = {"love": ", with deep burgundy accents", "newyear": ", with muted red and pine green accents",
       "santa": ", with muted red and pine green accents", "toast": ", with soft gold accents"}
SC = {
    "family": ["a vintage brass pocket watch, a folded tweed flat cap, a small kraft tag with twine and a few dried gypsophila stems",
               "a leather notebook with a fountain pen and a sprig of dried lavender",
               "a folded wool sweater, a ceramic mug of tea and a few wheat ears",
               "a single soft cream peony beside a kraft envelope tied with twine",
               "reading glasses on an open old book with pressed dried flowers",
               "two ceramic cups, a blank instant photo frame and a dried flower sprig"],
    "birthday": ["a garden bouquet of cream roses and daisies wrapped in kraft paper with a ribbon, scattered petals",
                 "a small cream cake with one thin candle on a linen napkin, scattered petals",
                 "a gift box wrapped in kraft paper with a satin ribbon and dried flowers",
                 "a vintage blank postcard, a small brass key and ranunculus flowers",
                 "pastel macarons beside a blush peony",
                 "a ceramic teacup, a slice of cake and sprigs of lavender"],
    "love": ["deep burgundy roses, a red wax seal and a feather quill over an aged blank parchment letter",
             "an antique brass key, lace ribbon and dried rose petals on aged paper",
             "two candles and a vintage envelope with a red wax seal",
             "a pearl necklace and a dried dusty-rose on parchment",
             "a pressed red flower and an open antique locket",
             "torn-edge parchment with dried flowers and a dusty-rose satin ribbon"],
    "friend": ["two ceramic mugs, a croissant and wildflowers on a linen cloth",
               "blank instant photos, a eucalyptus sprig and washi tape",
               "two tea cups, cookies and a few daisies",
               "an open book, a mug and a corner of a knitted blanket",
               "a small potted plant, a blank note card and a pencil",
               "a wildflower bouquet in a glass jar beside a blank postcard"],
    "thanks": ["a single soft cream peony beside a kraft paper envelope tied with natural twine, a dried flower tucked under the twine",
               "a dried flower tucked into a blank kraft tag with a gold paper clip",
               "white ranunculus wrapped in cream paper tied with satin ribbon",
               "a cup of tea, a small daisy bouquet and a folded blank note",
               "an olive branch and a cream envelope with an olive-green wax seal",
               "a small jar of honey with a linen cover, dried flowers and a blank tag"],
    "sorry": ["pale dried pampas grass and gypsophila framing a blank torn-edge cream paper sheet, an open envelope",
              "an open envelope with a sprig of dried flowers and an olive branch",
              "a single white flower on a blank folded card, soft shadow",
              "two cups of tea and a blank hand-torn note",
              "dried lavender and a blank torn-edge note",
              "a pale feather and an olive branch on cream paper"],
    "support": ["soft cream linen folds, a burning candle, a cup of tea and a blank card",
                "an open book, a mug, dried daisies and a corner of a knitted blanket",
                "linen fabric, dried gypsophila, a smooth stone and a small blank card",
                "a cup of hot tea, honey, a lemon slice and a blank note",
                "a wool blanket corner, a candle and lavender sprigs",
                "a sunlit windowsill with a small plant and a blank card"],
    "toast": ["two champagne coupe glasses with golden confetti on cream linen and a blank menu card",
              "a sparkling wine bottle with gold foil, two glasses and an olive branch",
              "gold ribbon, small confetti and a blank place card",
              "two glasses of rose wine and a few flowers",
              "gold cutlery on a linen napkin with a blank card",
              "a lit sparkler candle and a festive table setting with a blank card"],
    "newyear": ["matte cream glass ornament balls and pine sprigs with red berries",
                "cinnamon sticks, dried orange slices and star anise tied with twine",
                "a small vintage lantern, a spruce branch and cotton snow",
                "gingerbread cookies with white icing and a pine sprig",
                "knitted mittens, a mug of cocoa and pine cones",
                "gold garland, a crochet snowflake and a red ribbon"],
    "santa": ["a vintage red envelope with a wax seal, a fir branch and a candy cane on blank parchment",
              "a small vintage lantern, fir sprigs and a red wool mitten",
              "a wooden toy, a red ribbon, a fir branch and a kraft tag",
              "a mug of cocoa with marshmallows, a fir branch and a blank letter",
              "a snowy pine branch, a small brass bell and a red ribbon",
              "a star-shaped cookie, gold tinsel and a sealed letter"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--occasion")
    ap.add_argument("--model", default="flux2")
    ap.add_argument("--confirm-spend", action="store_true")
    a = ap.parse_args()
    occs = [a.occasion] if a.occasion else list(SC)
    n = sum(min(a.count, len(SC[o])) for o in occs)
    G.guards(a.confirm_spend, n * G.MODELS[a.model]["usd"] * G.USD_RUB)
    key = G.load_key()
    m = G.MODELS[a.model]
    total = 0.0
    for occ in occs:
        for i in range(min(a.count, len(SC[occ]))):
            if G.remaining_rub() < m["usd"] * G.USD_RUB:
                print("бюджет исчерпан")
                return
            stem = G.next_stem(occ)
            prompt = SC[occ][i] + ". " + BASE.format(accent=ACC.get(occ, ""))
            img, spent = G.generate(key, a.model, prompt)
            usd = spent if spent is not None else m["usd"]
            rub = round(usd * G.USD_RUB, 2)
            path = G.BGS / occ / f"{stem}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(G.to_card_jpeg(img))
            cat = G.load_catalog()
            cat["cards"].append({"id": f"{occ}-{stem}", "occasion": occ, "season": "all", "style": "pinterest-flat",
                                 "status": "review", "prompt": prompt, "model": a.model, "cost_rub": rub,
                                 "created_at": datetime.now().isoformat(timespec="seconds")})
            G.CATALOG.write_text(json.dumps(cat, ensure_ascii=False, indent=1), encoding="utf-8")
            G.add_ledger({"ts": datetime.now().isoformat(timespec="seconds"), "model": a.model, "occasion": occ,
                          "card_id": f"{occ}-{stem}", "usd": usd, "rub": rub})
            total += rub
            print(f"{occ}-{stem}: {rub} руб", flush=True)
    print(f"Итого {total:.1f} руб. Осталось бюджета {G.remaining_rub():.0f} руб.")


if __name__ == "__main__":
    main()
