"""TYMCZASOWE: zapytania do API serwisu licytacji (uruchamiane w GitHub Actions)."""
import json
import time
from pathlib import Path

from curl_cffi import requests as creq

out = Path("tmp_probe")
out.mkdir(exist_ok=True)
s = creq.Session(impersonate="chrome")
BASE = "https://licytacje.komornik.pl/services/item-back/rest/item/"
H = {"Accept-Language": "pl-PL,pl;q=0.9", "Accept": "application/json", "Content-Type": "application/json",
     "Origin": "https://licytacje.komornik.pl", "Referer": "https://licytacje.komornik.pl/wyszukiwarka-licytacji"}
RE = {"field": "mainCategory", "value": ["REAL_ESTATE"]}
MAZ = {"field": "province", "value": ["mazowieckie"]}
bodies = {
    "all_re": {"orderByField": "dateCreated", "orderBy": "DESC", "limit": 100, "offset": 0, "termFilters": [RE]},
    "waw": {"orderByField": "startAuctionAt", "orderBy": "ASC", "limit": 50, "offset": 0,
            "termFilters": [RE, MAZ, {"field": "city", "value": ["Warszawa"]}]},
    "maz": {"orderByField": "startAuctionAt", "orderBy": "ASC", "limit": 100, "offset": 0, "termFilters": [RE, MAZ]},
    "maz_joinable": {"orderByField": "startAuctionAt", "orderBy": "ASC", "limit": 30, "offset": 0,
                     "termFilters": [RE, MAZ, {"field": "joinable", "value": ["true"]}]},
}
first_id = None
for name, body in bodies.items():
    try:
        r = s.post(BASE + "search", headers=H, data=json.dumps(body), timeout=40)
        (out / f"{name}.json").write_text(r.text, encoding="utf-8")
        print(name, r.status_code, len(r.text), r.text[:300])
        if name == "maz" and r.status_code == 200:
            data = r.json()
            items = data.get("items") or data.get("content") or data.get("results") or []
            if isinstance(items, list) and items:
                first_id = items[0].get("id") or items[0].get("itemId")
    except Exception as e:
        print(name, "ERR", str(e)[:300])
    time.sleep(3)
if first_id:
    for name, url in {"item": f"{BASE}{first_id}", "page": f"https://licytacje.komornik.pl/licytacje/{first_id}",
                      "item_ui": f"https://licytacje.komornik.pl/items/{first_id}"}.items():
        try:
            r = s.get(url, headers={"Accept-Language": "pl-PL"}, timeout=40)
            (out / f"{name}.{'json' if name == 'item' else 'html'}").write_text(r.text, encoding="utf-8")
            if name != "item":
                import re as _re
                t = _re.search(r"<title>(.*?)</title>", r.text, _re.S)
                print("   TITLE", t and t.group(1)[:150])
            print(name, r.status_code, len(r.text), r.url)
        except Exception as e:
            print(name, "ERR", str(e)[:300])
        time.sleep(3)
