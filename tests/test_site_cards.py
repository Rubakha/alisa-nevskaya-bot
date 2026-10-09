"""Страницы «Открытки по поводам»: 200, sitemap, JSON-LD FAQPage, кнопка в бота, перелинковка, фид.
Запуск: python tests/test_site_cards.py"""
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("TG_BOT_TOKEN", "123:TEST")
os.environ["DATA_DIR"] = tempfile.mkdtemp()
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["YOOKASSA_PROVIDER_TOKEN"] = "x"
os.environ["YOOKASSA_SHOP_ID"] = ""

import bot_best as B  # noqa: E402
import occasions as OCC  # noqa: E402
import site_pages as SP  # noqa: E402
from site_cards import CARDS  # noqa: E402

c = B.app.test_client()
assert len(CARDS) == 16
sm = c.get("/sitemap.xml").get_data(as_text=True)
locs = {e.text for e in ET.fromstring(sm).iter("{http://www.sitemaps.org/schemas/sitemap/0.9}loc")}
for slug, m in CARDS.items():
    r = c.get(f"/{slug}")
    assert r.status_code == 200, slug
    h = r.get_data(as_text=True)
    assert SP.SITE_URL + f"/{slug}" in locs, slug
    assert m["key"] in OCC.PRODUCTS and f"?start=w_{m.get('bot', m['key'])}" in h, slug
    ld = json.loads(re.search(r'application/ld\+json">(.*?)</script>', h, re.S).group(1))
    assert ld["@type"] == "FAQPage" and 3 <= len(ld["mainEntity"]) <= 5, slug
    assert 8 <= len(m["phrases"]) <= 15, slug
    assert "<h1>" in h and 'name="description"' in h
    assert "!" not in json.dumps(m, ensure_ascii=False), slug  # тон Алисы
    assert 3 <= len(m["links"]) <= 4
    for s in m["links"]:
        assert c.get(SP.card_url(s)).status_code == 200, (slug, s)
        assert f'href="{SP.card_url(s)}"' in h
    assert len(set(m["phrases"])) == len(m["phrases"])
home = c.get("/").get_data(as_text=True)
assert "Открытки по поводам" in home and all(f'href="/{s}"' in home for s in CARDS)
# старые адреса целы
for u in ("/den-otca", "/den-materi", "/pismo/pismo-pape-i-mame", "/robots.txt"):
    assert c.get(u).status_code == 200, u
# Рождество: перелинковка с новогодними страницами
for slug in ("pozdravlenie-s-rozhdestvom-svoimi-slovami", "rozhdestvenskoe-pismo"):
    assert "/pismo/novogodnee-pozdravlenie-svoimi-slovami" in c.get(f"/{slug}").get_data(as_text=True)
assert "/pozdravlenie-s-rozhdestvom-svoimi-slovami" in c.get("/otkrytka-kollege-na-novyj-god").get_data(as_text=True)
for u in ("/pismo/novogodnee-pozdravlenie-svoimi-slovami", "/pozdravlenie-kollegam-s-novym-godom"):
    assert "/rozhdestvenskoe-pismo" in c.get(u).get_data(as_text=True), u
# фид
r = c.get("/feed.yml")
assert r.status_code == 200
root = ET.fromstring(r.get_data())
offers = root.findall(".//offer")
assert [o.find("price").text for o in offers] == ["199", "249", "399", "249", "249"]
assert all(o.find("url").text.startswith(SP.SITE_URL) for o in offers)
assert root.find(".//category").text == "Исполнители"
print("OK:", len(CARDS), "страниц")

g = c.get("/gruppovoe-pismo-ot-vseh").get_data(as_text=True)
assert "399 ₽" in g and "w_group" in g and "/gruppovoe-pismo-ot-vseh" in SP.SITE_URL + "/gruppovoe-pismo-ot-vseh"
assert dict((o.get("id"), o.find("url").text) for o in offers)["letter-group"].endswith("/gruppovoe-pismo-ot-vseh")
