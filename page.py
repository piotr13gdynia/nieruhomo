"""Generuje docs/index.html z zapisanych ofert."""
import json
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template.html"
OUTPUT = ROOT / "docs" / "index.html"

DETAIL_KEYS = ("floor", "floors_total", "build_year", "market", "advertiser", "kind", "subdistrict")
DEAL_KEYS = ("deal", "deviation", "median_ppm", "group", "group_size", "suspicious")


def build_items(config, offers, status):
    run_at = datetime.fromisoformat(status["run_at"])
    hide_after = timedelta(days=float(config.get("ukryj_po_dniach", 3)))
    active = [o for o in offers.values() if run_at - datetime.fromisoformat(o["last_seen"]) <= hide_after]
    active.sort(key=lambda o: o["first_seen"])

    # to samo mieszkanie wystawione na kilku portalach: ta sama cena i metraż
    groups = {}
    items = []
    for o in active:
        key = None
        if o.get("price") and o.get("area"):
            key = f"{o['search']}|{int(o['price'])}|{round(o['area'])}"
        if key and key in groups:
            item = groups[key]
            item["others"].append({"source": o["source"], "url": o["url"]})
            for k in DETAIL_KEYS:  # szczegóły z innego portalu uzupełniają brakujące
                if item.get(k) in (None, "") and o.get(k) not in (None, ""):
                    item[k] = o[k]
            continue
        prices = [h["price"] for h in o.get("price_history", []) if h.get("price")]
        item = {
            "title": o.get("title") or "Ogłoszenie bez tytułu",
            "url": o["url"],
            "source": o["source"],
            "search": o["search"],
            "price": o.get("price"),
            "first_price": prices[0] if prices else None,
            "area": o.get("area"),
            "rooms": o.get("rooms"),
            "location": o.get("location") or "",
            "image": o.get("image"),
            "first_seen": o["first_seen"],
            "last_seen": o["last_seen"],
            "others": [],
        }
        for k in DETAIL_KEYS + DEAL_KEYS:
            if o.get(k) not in (None, ""):
                item[k] = o[k]
        items.append(item)
        if key:
            groups[key] = item
    return items


def render(config, offers, status, auctions=None, health=None):
    deals_cfg = (config.get("okazje") or {})
    payload = {
        "run_at": status["run_at"],
        "searches": [s.get("nazwa", "Wyszukiwanie") for s in config.get("wyszukiwania", [])],
        "sources": [s for s in status["sources"]],
        "items": build_items(config, offers, status),
        "deal_threshold": deals_cfg.get("prog_procent", 10),
        "market_size": status.get("market_size", 0),
        "groups_rated": status.get("groups_rated", 0),
        "auctions": auctions or [],
        "health": health or [],
    }
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", data)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html, encoding="utf-8")
    print(f"Strona zapisana: {OUTPUT.relative_to(ROOT)} ({len(payload['items'])} ofert)")
