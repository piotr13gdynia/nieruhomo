"""TYMCZASOWE: sprawdzenie stronicowania i strony licytacji (uruchamiane w GitHub Actions)."""
import json
import re
import time
from pathlib import Path

from curl_cffi import requests as creq

out = Path("tmp_probe")
out.mkdir(exist_ok=True)
s = creq.Session(impersonate="chrome")
URL = "https://licytacje.komornik.pl/services/item-back/rest/item/search"
H = {"Accept-Language": "pl-PL,pl;q=0.9", "Accept": "application/json", "Content-Type": "application/json"}
F = [{"field": "mainCategory", "value": ["REAL_ESTATE"]}, {"field": "province", "value": ["mazowieckie"]},
     {"field": "joinable", "value": ["true"]}]
ids = {}
for name, extra in {"p0": {"offset": 0}, "p1": {"offset": 5}, "pn1": {"pageNumber": 1}}.items():
    body = {"orderByField": "dateCreated", "orderBy": "DESC", "limit": 5, "termFilters": F, **extra}
    r = s.post(URL, headers=H, data=json.dumps(body), timeout=60)
    items = r.json().get("items", [])
    ids[name] = [i["id"] for i in items]
    for i in items:
        i["mainPhoto"] = i["mainPhoto"][:40]
    (out / f"{name}.json").write_text(json.dumps(r.json(), ensure_ascii=False), encoding="utf-8")
    print(name, r.status_code, ids[name], r.json().get("count"))
    time.sleep(3)
first = ids["p0"][0]
for name, url in {"licytacje": f"https://licytacje.komornik.pl/licytacje/{first}",
                  "items": f"https://licytacje.komornik.pl/items/{first}"}.items():
    r = s.get(url, headers={"Accept-Language": "pl-PL"}, timeout=60)
    t = re.search(r"<title>(.*?)</title>", r.text, re.S)
    print(name, r.status_code, r.url, len(r.text), "TITLE:", t and t.group(1)[:200], "| 404 w treści:", "404" in r.text[:5000])
    time.sleep(3)
