"""Pobiera prawdziwe strony wyników z portali i zapisuje je jako fixtures testów.

Użycie:
    python tools/refresh_fixtures.py               # pobiera strony z portali
    python tools/refresh_fixtures.py --z-katalogu D # przycina strony zapisane wcześniej w katalogu D

Strony są przycinane, żeby repozytorium nie puchło i nie przechowywało danych
osobowych: zostają tylko dane strukturalne czytane przez parsery i HTML kart
ogłoszeń (dla parsera zapasowego). Usuwane są style, pozostałe skrypty,
numery telefonów, adresy e-mail, dane kontaktowe i opisy ogłoszeń.
"""
import argparse
import json
import random
import re
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import details  # noqa: E402
import monitor  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"

PAGES = {
    "otodom_mieszkania": "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/mazowieckie/warszawa/warszawa/warszawa?priceMax=800000&by=LATEST&direction=DESC",
    "otodom_domy": "https://www.otodom.pl/pl/wyniki/sprzedaz/dom/mazowieckie/warszawa/warszawa/warszawa?priceMax=1500000&by=LATEST&direction=DESC",
    "olx_mieszkania": "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/warszawa/?search%5Bfilter_float_price%3Ato%5D=800000&search%5Border%5D=created_at%3Adesc",
    "gratka_mieszkania": "https://gratka.pl/nieruchomosci/mieszkania/warszawa?cena-calkowita%3Amax=800000&sort=newest",
    "morizon_mieszkania": "https://www.morizon.pl/mieszkania/warszawa/?ps%5Bprice_to%5D=800000",
}

PHONE_RE = re.compile(r"(?<![\w/=.-])(?:\+48[ -]?)?(?:\d{3}[ -]?\d{3}[ -]?\d{3}|\d{2}[ -]\d{3}[ -]\d{2}[ -]\d{2})(?![\w/=.-])")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PRIVATE_KEYS = {"contact", "user", "description", "phone", "phones", "email", "person", "seller",
                "unconfirmedUserAuthToken", "userSessionId", "currentUser", "owner", "contactDetails",
                "userAdverts", "advertOwner", "salesManagoData", "similarProperties", "advertiserProperties"}

# Strony pojedynczych ofert (obszar 2: szczegóły ogłoszenia).
DETAIL_PAGES = {
    "otodom_oferta": "https://www.otodom.pl/pl/oferta/metro-plocka-41-m-w-powojennej-kamienicy-z-winda-na-woli-ID4DAtK",
    "otodom_oferta2": "https://www.otodom.pl/pl/oferta/mieszkanie-47m2-balkon-i-piwnica-ID4DAr8",
    "gratka_oferta": "https://gratka.pl/nieruchomosci/mieszkanie-warszawa-bemowo-doroszewskiego/ob/49336685",
    "morizon_oferta": "https://www.morizon.pl/oferta/sprzedaz-mieszkanie-warszawa-bemowo-doroszewskiego-68m2-mzn2048236679",
}


def scrub_text(s):
    return EMAIL_RE.sub("anonim@example.com", PHONE_RE.sub("000000000", s))


def contact_types(contact):
    """Z danych kontaktowych zostaje tylko typ oferenta (np. AGENCY, SALES_OFFICE, PRIVATE)."""
    if not isinstance(contact, dict):
        return None
    return {k: {"type": v.get("type")} for k, v in contact.items() if isinstance(v, dict) and v.get("type")}


def prune(obj):
    """Usuwa klucze z danymi kontaktowymi i anonimizuje napisy."""
    if isinstance(obj, dict):
        out = {k: prune(v) for k, v in obj.items() if k not in PRIVATE_KEYS}
        if "contact" in obj and contact_types(obj["contact"]):
            out["contact"] = contact_types(obj["contact"])
        if isinstance(obj.get("agency"), dict):
            out["agency"] = {"type": obj["agency"].get("type")}  # bez nazwy i telefonów biura
        return out
    if isinstance(obj, list):
        return [prune(v) for v in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def devalue_encode(value):
    """Zapisuje dane w formacie devalue używanym przez Nuxt (odwrotność monitor.nuxt_data)."""
    flat, index = [], {}

    def add(v):
        if isinstance(v, (str, int, float, bool)) or v is None:
            key = (type(v).__name__, v)
            if key in index:
                return index[key]
            if v is None:
                flat.append(None)
            else:
                flat.append(v)
            index[key] = len(flat) - 1
            return index[key]
        i = len(flat)
        flat.append(None)
        if isinstance(v, dict):
            flat[i] = {k: add(x) for k, x in v.items()}
        else:
            flat[i] = [add(x) for x in v]
        return i

    flat.append(["ShallowReactive", 1])
    add(value)
    return flat


def strip_html(html):
    """Zostawia szkielet HTML z kartami ogłoszeń, bez stylów, skryptów i grafik wektorowych."""
    html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    for tag in ("script", "style", "svg", "noscript", "iframe", "template"):
        html = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", html, flags=re.S | re.I)
    html = re.sub(r"<(link|meta)\b[^>]*>", "", html, flags=re.I)
    html = re.sub(r'\s(style|class|data-[\w-]+|srcset|sizes|aria-[\w-]+)="[^"]*"', "", html)
    html = re.sub(r"\sdata-[\w-]+(?=[\s/>])", "", html)  # atrybuty bez wartości, np. data-v-0cf85a73
    html = re.sub(r"\n\s*\n+", "\n", html)
    return scrub_text(html)


def body_of(html):
    m = re.search(r"<body\b[^>]*>(.*)</body>", html, re.S | re.I)
    return m.group(1) if m else html


def trim(source, html):
    blocks = []
    if source == "otodom":
        data = monitor.next_data(html)
        sa = data["props"]["pageProps"]["data"]["searchAds"]
        keep = {"props": {"pageProps": {"data": {"searchAds": {"items": sa["items"], "pagination": sa.get("pagination")}}}}}
        blocks.append('<script id="__NEXT_DATA__" type="application/json">'
                      + json.dumps(prune(keep), ensure_ascii=False).replace("</", "<\\/") + "</script>")
    elif source == "olx":
        state = monitor.prerendered_state(html)
        listing = state["listing"]["listing"]
        keep = {"listing": {"listing": {k: listing.get(k) for k in ("pageNumber", "totalElements", "totalPages", "ads")}}}
        literal = json.dumps(json.dumps(prune(keep), ensure_ascii=False), ensure_ascii=False).replace("</", "<\\/")
        blocks.append(f"<script>window.__PRERENDERED_STATE__= {literal};</script>")
    else:
        data = monitor.nuxt_data(html)
        result = data["pinia"]["listing"]["result"]["searchResult"]
        keep = {"pinia": {"listing": {"result": {"searchResult": {
            "properties": result["properties"], "topPromoted": result.get("topPromoted")}}}}}
        flat = devalue_encode(prune(keep))
        blocks.append('<script type="application/json" data-nuxt-data="nuxt-app" data-ssr="true" id="__NUXT_DATA__">'
                      + json.dumps(flat, ensure_ascii=False).replace("</", "<\\/") + "</script>")
        for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>.*?</script>', html, re.S):
            if '"Offer"' in m.group(0):
                blocks.append(scrub_text(m.group(0)))
    body = strip_html(body_of(html)) if source in ("gratka", "morizon") else ""
    return ("<!doctype html>\n<html lang=\"pl\">\n<head>\n<meta charset=\"utf-8\">\n"
            + "\n".join(blocks) + "\n</head>\n<body>\n" + body + "\n</body>\n</html>\n")


def trim_detail(source, html):
    if source == "otodom":
        ad = monitor.next_data(html)["props"]["pageProps"]["ad"]
        keep = {"props": {"pageProps": {"ad": prune(ad)}}}
        block = ('<script id="__NEXT_DATA__" type="application/json">'
                 + json.dumps(keep, ensure_ascii=False).replace("</", "<\\/") + "</script>")
    else:
        data = monitor.nuxt_data(html)
        keep = {"pinia": {"details": {"propertyData": data["pinia"]["details"]["propertyData"]}}}
        block = ('<script type="application/json" data-nuxt-data="nuxt-app" data-ssr="true" id="__NUXT_DATA__">'
                 + json.dumps(devalue_encode(prune(keep)), ensure_ascii=False).replace("</", "<\\/") + "</script>")
    return "<!doctype html>\n<html lang=\"pl\">\n<head>\n<meta charset=\"utf-8\">\n" + block + "\n</head>\n<body></body>\n</html>\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--z-katalogu", dest="src_dir", help="katalog z wcześniej pobranymi stronami <nazwa>.html")
    args = ap.parse_args()
    FIXTURES.mkdir(parents=True, exist_ok=True)
    lines = ["# Fixtures parserów", "",
             f"Strony wyników pobrane {date.today().isoformat()} i przycięte przez `tools/refresh_fixtures.py`.", ""]
    for i, (name, url) in enumerate(PAGES.items()):
        source = name.split("_")[0]
        if args.src_dir:
            html = (Path(args.src_dir) / f"{name}.html").read_text(encoding="utf-8")
        else:
            if i:
                time.sleep(random.uniform(3, 7))
            html = monitor.fetch(url)
        out = trim(source, html)
        found = monitor.parse_page(source, out, url)
        (FIXTURES / f"{name}.html").write_text(out, encoding="utf-8")
        print(f"{name}: {len(html) // 1024} KB -> {len(out) // 1024} KB, ofert: {len(found)}")
        lines.append(f"* `{name}.html`: {url}")
    lines += ["", "Strony pojedynczych ofert:", ""]
    for name, url in DETAIL_PAGES.items():
        source = name.split("_")[0]
        if args.src_dir:
            html = (Path(args.src_dir) / f"{name}.html").read_text(encoding="utf-8")
        else:
            time.sleep(random.uniform(3, 7))
            html = monitor.fetch(url)
        out = trim_detail(source, html)
        (FIXTURES / f"{name}.html").write_text(out, encoding="utf-8")
        print(f"{name}: {len(html) // 1024} KB -> {len(out) // 1024} KB, szczegóły: {details.parse_details(source, out)}")
        lines.append(f"* `{name}.html`: {url}")
    (FIXTURES / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
