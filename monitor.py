"""Monitor ogłoszeń nieruchomości: Otodom, OLX, Gratka, Morizon.

Pobiera wyniki wyszukiwań z config.yaml, filtruje je, zapisuje historię
w data/offers.json i generuje stronę docs/index.html.
"""
import hashlib
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OFFERS_FILE = ROOT / "data" / "offers.json"
STATUS_FILE = ROOT / "data" / "status.json"
KEEP_DAYS = 60  # po tylu dniach nieobecności oferta jest usuwana z historii

ROOMS_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "more": 10,
}

OFFER_LINK = {
    "otodom": re.compile(r"/pl/oferta/[^/?#]+"),
    "olx": re.compile(r"/d/oferta/[^/?#]+\.html"),
    "gratka": re.compile(r"/nieruchomosci/[^?#]*/ob/\d+"),
    "morizon": re.compile(r"/oferta/[^/?#]*mzn\d+"),
}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


# ---------- pobieranie ----------

_session = None


def get_session():
    global _session
    if _session is None:
        try:
            from curl_cffi import requests as creq  # udaje prawdziwą przeglądarkę Chrome
            _session = creq.Session(impersonate="chrome")
        except ImportError:
            import requests
            _session = requests.Session()
            _session.headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            )
    return _session


def fetch(url):
    headers = {"Accept-Language": "pl-PL,pl;q=0.9,en;q=0.6"}
    resp = get_session().get(url, headers=headers, timeout=40)
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.text


def with_page(url, page):
    if page == 1:
        return url
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "page"]
    query.append(("page", str(page)))
    return urlunparse(parts._replace(query=urlencode(query)))


def detect_source(url):
    host = urlparse(url).netloc.lower()
    for name in OFFER_LINK:
        if name in host:
            return name
    return None


# ---------- pomocnicze ----------

def to_num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\xa0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def rooms_num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s in ROOMS_WORDS:
        return ROOMS_WORDS[s]
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def walk(obj):
    """Przechodzi rekurencyjnie po JSON i zwraca wszystkie słowniki."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)


PRICE_RE = re.compile(r"(\d{1,3}(?:[ \xa0\u202f]?\d{3})+(?:[.,]\d+)?|\d{4,})\s*zł(?!\s*/\s*m)", re.I)
AREA_RE = re.compile(r"(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:m²|m2|m\s*kw)", re.I)
ROOMS_RE = re.compile(r"(\d{1,2})\s*(?:pok|pokoje|pokoi|pokój)", re.I)


def parse_text_fields(text):
    prices = [to_num(m.group(1)) for m in PRICE_RE.finditer(text)]
    prices = [p for p in prices if p]
    areas = [to_num(m.group(1)) for m in AREA_RE.finditer(text)]
    areas = [a for a in areas if a and 5 <= a <= 5000]
    rooms = ROOMS_RE.search(text)
    return {
        "price": max(prices) if prices else None,
        "area": areas[0] if areas else None,
        "rooms": int(rooms.group(1)) if rooms else None,
    }


def offer_id_from_url(source, url):
    path = urlparse(url).path
    patterns = {
        "gratka": r"/ob/(\d+)",
        "morizon": r"(mzn\d+)",
        "olx": r"-(ID\w+)\.html",
        "otodom": r"-(ID\w+)$",
    }
    m = re.search(patterns[source], path)
    return m.group(1) if m else hashlib.md5(path.encode()).hexdigest()[:12]


# ---------- parsery portali ----------

def parse_otodom(html, base):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    out = {}
    for d in walk(data):
        if not (isinstance(d.get("slug"), str) and ("totalPrice" in d or "areaInSquareMeters" in d)):
            continue
        price = d.get("totalPrice")
        price = price.get("value") if isinstance(price, dict) else price
        location = ""
        try:
            locs = d["location"]["reverseGeocoding"]["locations"]
            names = [x.get("name") for x in locs if x.get("name")]
            location = ", ".join(reversed(names[-2:]))
        except (KeyError, TypeError):
            pass
        street = ""
        try:
            street = d["location"]["address"]["street"]["name"] or ""
        except (KeyError, TypeError):
            pass
        image = None
        imgs = d.get("images") or []
        if imgs and isinstance(imgs[0], dict):
            image = imgs[0].get("medium") or imgs[0].get("large") or imgs[0].get("small")
        url = "https://www.otodom.pl/pl/oferta/" + d["slug"]
        oid = str(d.get("id") or offer_id_from_url("otodom", url))
        out[oid] = {
            "native_id": oid,
            "url": url,
            "title": d.get("title") or "",
            "price": to_num(price),
            "area": to_num(d.get("areaInSquareMeters")),
            "rooms": rooms_num(d.get("roomsNumber")),
            "location": ", ".join(x for x in [street, location] if x),
            "image": image,
        }
    return list(out.values())


def parse_olx(html, base):
    m = re.search(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', html)
    if not m:
        return []
    try:
        state = json.loads(json.loads(m.group(1)))
        ads = state["listing"]["listing"]["ads"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []
    out = []
    for ad in ads:
        price = None
        p = ad.get("price") or {}
        if isinstance(p.get("regularPrice"), dict):
            price = p["regularPrice"].get("value")
        area = rooms = None
        for param in ad.get("params") or []:
            key = param.get("key")
            val = param.get("normalizedValue") or param.get("value")
            if key == "m":
                area = to_num(val)
            elif key == "rooms":
                rooms = rooms_num(val)
        loc = ad.get("location") or {}
        location = ", ".join(x for x in [loc.get("districtName"), loc.get("cityName")] if x)
        image = None
        photos = ad.get("photos") or []
        if photos and isinstance(photos[0], str):
            image = photos[0].replace("{width}", "480").replace("{height}", "360")
        url = ad.get("url") or ""
        out.append({
            "native_id": str(ad.get("id") or offer_id_from_url("olx", url)),
            "url": url,
            "title": ad.get("title") or "",
            "price": to_num(price),
            "area": area,
            "rooms": rooms,
            "location": location,
            "image": image,
        })
    return out


def parse_generic(html, base, source):
    """Uniwersalny parser kart ogłoszeń. Szuka linków do ofert i czyta tekst ich kart."""
    soup = BeautifulSoup(html, "html.parser")
    pattern = OFFER_LINK[source]

    def offer_links(node):
        found = set()
        for a in node.find_all("a", href=True):
            href = urljoin(base, a["href"]).split("?")[0].split("#")[0]
            if pattern.search(urlparse(href).path):
                found.add(href)
        return found

    results = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("?")[0].split("#")[0]
        if not pattern.search(urlparse(href).path) or href in results:
            continue
        card = a
        while card.parent is not None and card.parent.name not in ("body", "html", "[document]"):
            parent = card.parent
            if len(offer_links(parent)) > 1:
                break
            card = parent
            if "zł" in card.get_text(" ", strip=True) and card.find("img"):
                break
        text = card.get_text(" ", strip=True)
        fields = parse_text_fields(text)
        title = a.get("title") or ""
        if not title:
            heading = card.find(["h2", "h3", "h4", "h5", "h6"])
            title = heading.get_text(" ", strip=True) if heading else a.get_text(" ", strip=True)
        img = card.find("img")
        image = None
        if img:
            image = img.get("src") or img.get("data-src")
            if image and image.startswith("data:"):
                image = img.get("data-src")
        results[href] = {
            "native_id": offer_id_from_url(source, href),
            "url": href,
            "title": title[:200],
            "location": "",
            "image": urljoin(base, image) if image else None,
            **fields,
        }
    return list(results.values())


def parse_page(source, html, base):
    offers = []
    if source == "otodom":
        offers = parse_otodom(html, base)
    elif source == "olx":
        offers = parse_olx(html, base)
    if not offers:
        offers = parse_generic(html, base, source)
    return offers


# ---------- filtry ----------

def passes(offer, f):
    if not f:
        return True
    price, area, rooms = offer.get("price"), offer.get("area"), offer.get("rooms")
    if f.get("cena_min") and (price is None or price < f["cena_min"]):
        return False
    if f.get("cena_max") and (price is None or price > f["cena_max"]):
        return False
    if f.get("metraz_min") and (area is None or area < f["metraz_min"]):
        return False
    if f.get("metraz_max") and (area is None or area > f["metraz_max"]):
        return False
    if f.get("cena_za_m2_max") and price and area and price / area > f["cena_za_m2_max"]:
        return False
    if f.get("pokoje_min") and rooms is not None and rooms < f["pokoje_min"]:
        return False
    if f.get("pokoje_max") and rooms is not None and rooms > f["pokoje_max"]:
        return False
    title = (offer.get("title") or "").lower()
    for word in f.get("wykluczone_slowa") or []:
        if str(word).lower() in title:
            return False
    return True


# ---------- główna pętla ----------

def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def main():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    pages = int(config.get("strony", 1))
    offers = load_json(OFFERS_FILE, {})
    run_at = now_utc()
    status = {"run_at": iso(run_at), "sources": []}
    new_count = drop_count = 0

    for search in config.get("wyszukiwania", []):
        name = search.get("nazwa", "Wyszukiwanie")
        filters = search.get("filtry") or {}
        for url in search.get("adresy", []):
            source = detect_source(url)
            entry = {"search": name, "source": source, "url": url, "found": 0, "matched": 0, "error": None}
            if not source:
                entry["error"] = "Nieobsługiwany portal"
                status["sources"].append(entry)
                continue
            try:
                for page in range(1, pages + 1):
                    page_url = with_page(url, page)
                    html = fetch(page_url)
                    found = parse_page(source, html, page_url)
                    entry["found"] += len(found)
                    for o in found:
                        if not o.get("url") or not passes(o, filters):
                            continue
                        entry["matched"] += 1
                        key = f"{source}:{o['native_id']}"
                        old = offers.get(key)
                        if old is None:
                            new_count += 1
                            offers[key] = {
                                **o, "source": source, "search": name,
                                "first_seen": iso(run_at), "last_seen": iso(run_at),
                                "price_history": [{"date": iso(run_at), "price": o.get("price")}],
                            }
                        else:
                            if o.get("price") and old.get("price") and o["price"] != old["price"]:
                                old["price_history"].append({"date": iso(run_at), "price": o["price"]})
                                if o["price"] < old["price"]:
                                    drop_count += 1
                            for field in ("title", "price", "area", "rooms", "location", "image", "url"):
                                if o.get(field):
                                    old[field] = o[field]
                            old["last_seen"] = iso(run_at)
                    time.sleep(random.uniform(3, 7))
                if entry["found"] == 0:
                    entry["error"] = "Nie znaleziono ogłoszeń na stronie. Portal mógł zablokować zapytanie albo zmienić wygląd."
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"Błąd pobierania: {exc}"
            status["sources"].append(entry)
            print(f"[{source}] {name}: znaleziono {entry['found']}, pasuje {entry['matched']}"
                  + (f" | {entry['error']}" if entry["error"] else ""))

    cutoff = run_at - timedelta(days=KEEP_DAYS)
    offers = {k: v for k, v in offers.items() if datetime.fromisoformat(v["last_seen"]) >= cutoff}

    OFFERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFERS_FILE.write_text(json.dumps(offers, ensure_ascii=False, indent=1), encoding="utf-8")
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Nowych ofert: {new_count}, obniżek: {drop_count}, w bazie: {len(offers)}")

    from page import render
    render(config, offers, status)


if __name__ == "__main__":
    sys.exit(main())
