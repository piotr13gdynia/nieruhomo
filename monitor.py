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


class FetchError(RuntimeError):
    """Błąd pobierania z komunikatem zrozumiałym dla osoby nietechnicznej."""


HTTP_MESSAGES = {
    403: "Portal zablokował zapytanie (HTTP 403).",
    429: "Portal ograniczył liczbę zapytań (HTTP 429). Spróbuj rzadziej uruchamiać monitor.",
    404: "Adres wyszukiwania nie istnieje (HTTP 404). Sprawdź link w config.yaml.",
}


def fetch(url):
    headers = {"Accept-Language": "pl-PL,pl;q=0.9,en;q=0.6"}
    try:
        resp = get_session().get(url, headers=headers, timeout=40)
    except Exception as exc:  # noqa: BLE001
        text = str(exc)
        if "timed out" in text.lower() or "timeout" in text.lower():
            raise FetchError("Portal nie odpowiedział w ciągu 40 sekund.") from exc
        raise FetchError(f"Nie udało się połączyć z portalem ({text.split('.')[0][:120]}).") from exc
    if resp.status_code >= 400:
        msg = HTTP_MESSAGES.get(resp.status_code)
        if not msg:
            msg = (f"Błąd po stronie portalu (HTTP {resp.status_code})." if resp.status_code >= 500
                   else f"Portal odrzucił zapytanie (HTTP {resp.status_code}).")
        raise FetchError(msg)
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
            stack.extend(reversed(list(cur.values())))  # kolejność jak w dokumencie
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


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


# ---------- dane strukturalne osadzone w stronach ----------

def next_data(html):
    """JSON z <script id="__NEXT_DATA__"> (Otodom)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def prerendered_state(html):
    """Stan z window.__PRERENDERED_STATE__ (OLX). To napis JSON zapisany jako napis JS."""
    m = re.search(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', html)
    if not m:
        return None
    try:
        return json.loads(json.loads(m.group(1)))
    except (json.JSONDecodeError, TypeError):
        return None


_NUXT_WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


def nuxt_data(html):
    """Dane z <script id="__NUXT_DATA__"> (Gratka, Morizon).

    Nuxt zapisuje je w formacie "devalue": płaska tablica, w której obiekty
    i listy zamiast wartości trzymają indeksy innych elementów tablicy.
    """
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        flat = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(flat, list) or not flat:
        return None
    cache = {}

    def hydrate(i):
        if not isinstance(i, int) or i < 0 or i >= len(flat):
            return None  # liczby ujemne oznaczają undefined, NaN itp.
        if i in cache:
            return cache[i]
        v = flat[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in _NUXT_WRAPPERS:
                    out = hydrate(v[1]) if len(v) > 1 else None
                elif tag == "Date":
                    out = v[1] if len(v) > 1 else None
                elif tag == "Set":
                    out = [hydrate(x) for x in v[1:]]
                elif tag in ("Map", "null", "BigInt", "RegExp", "Error", "URL", "NuxtError", "Island"):
                    out = None
                else:
                    out = [hydrate(x) for x in v]
            else:
                out = [hydrate(x) for x in v]
        elif isinstance(v, dict):
            out = {}
            cache[i] = out  # zabezpieczenie przed cyklami
            for k, x in v.items():
                out[k] = hydrate(x)
            return out
        else:
            out = v
        cache[i] = out
        return out

    return hydrate(0)


def ld_json_offers(html):
    """Oferty z bloków JSON-LD (schema.org Offer). Zapasowe dane strukturalne."""
    out = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for d in walk(data):
            if d.get("@type") == "Offer" and d.get("url"):
                out.append(d)
    return out


def join_location(*parts):
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return ", ".join(out)


# ---------- parsery portali ----------

def parse_otodom(html, base):
    data = next_data(html)
    if not data:
        return []
    try:
        items = data["props"]["pageProps"]["data"]["searchAds"]["items"]
    except (KeyError, TypeError):
        # struktura się zmieniła: szukamy w całym JSON słowników wyglądających jak oferta
        items = [d for d in walk(data) if isinstance(d.get("slug"), str)
                 and ("totalPrice" in d or "areaInSquareMeters" in d)]
    # Otodom dokleja do listy kopię jednej z ofert z zastępczym id i bez zdjęć.
    # Przy powtórzonym adresie zostaje wersja z większą liczbą zdjęć.
    unique = {}
    for d in items or []:
        slug = d.get("slug")
        if not isinstance(slug, str):
            continue
        if slug not in unique or len(d.get("images") or []) > len(unique[slug].get("images") or []):
            unique[slug] = d
    out = []
    for slug, d in unique.items():
        price = d.get("totalPrice")
        price = price.get("value") if isinstance(price, dict) else price
        if d.get("hidePrice"):
            price = None
        loc = d.get("location") or {}
        levels = {}
        try:
            for x in loc["reverseGeocoding"]["locations"]:
                levels.setdefault(x.get("locationLevel"), x.get("name"))
        except (KeyError, TypeError):
            pass
        street = ""
        try:
            street = loc["address"]["street"]["name"] or ""
        except (KeyError, TypeError):
            pass
        city = levels.get("city_or_village") or ""
        district = levels.get("district") or ""
        image = None
        imgs = d.get("images") or []
        if imgs and isinstance(imgs[0], dict):
            image = imgs[0].get("medium") or imgs[0].get("large") or imgs[0].get("small")
        url = "https://www.otodom.pl/pl/oferta/" + slug
        out.append({
            "native_id": str(d.get("id") or offer_id_from_url("otodom", url)),
            "url": url,
            "title": d.get("title") or "",
            "price": to_num(price),
            "area": to_num(d.get("areaInSquareMeters")),
            "rooms": rooms_num(d.get("roomsNumber")),
            "city": city,
            "district": district,
            "location": join_location(street, levels.get("residential"), district, city),
            "image": image,
        })
    return out


def _olx_image(ad):
    """Miniatura z photosSet (rozmiar 2x), inaczej pierwsze zdjęcie z rozmiarem zmniejszonym."""
    sets = ad.get("photosSet") or []
    if sets and isinstance(sets[0], str):
        variants = [v.strip().split(" ")[0] for v in sets[0].split(",") if v.strip()]
        if variants:
            return variants[1] if len(variants) > 1 else variants[0]
    photos = ad.get("photos") or []
    if photos and isinstance(photos[0], str):
        url = photos[0].replace("{width}", "516").replace("{height}", "361")
        return re.sub(r";s=\d+x\d+", ";s=516x361", url)
    return None


def parse_olx(html, base):
    state = prerendered_state(html)
    try:
        ads = state["listing"]["listing"]["ads"]
    except (KeyError, TypeError):
        return []
    out, seen = [], set()
    for ad in ads or []:
        url = ad.get("url") or ""
        native_id = str(ad.get("id") or offer_id_from_url("olx", url))
        if not url or native_id in seen:
            continue  # ogłoszenia promowane potrafią wystąpić na stronie dwa razy
        seen.add(native_id)
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
        city, district = loc.get("cityName") or "", loc.get("districtName") or ""
        out.append({
            "native_id": native_id,
            "url": url,
            "title": ad.get("title") or "",
            "price": to_num(price),
            "area": area,
            "rooms": rooms,
            "city": city,
            "district": district,
            "location": join_location(district, city),
            "image": _olx_image(ad),
        })
    return out


# Adres miniatury w tym samym formacie, którego Gratka i Morizon używają w swoim JSON-LD.
NUXT_THUMB = "https://img1.staticmorizon.com.pl/thumb/{id}/3x2_m:fill_and_crop/{name}.jpg"


def _nuxt_listing_nodes(data):
    """Węzły ofert z wyników wyszukiwania (zwykłe i promowane na górze listy)."""
    nodes = []
    try:
        result = data["pinia"]["listing"]["result"]["searchResult"]
        nodes += result["properties"]["nodes"] or []
        nodes += (result.get("topPromoted") or {}).get("nodes") or []
    except (KeyError, TypeError):
        pass
    if not nodes:
        for d in walk(data):
            if d.get("idOnFrontend") and d.get("url") and "price" in d:
                nodes.append(d)
    return nodes


def parse_nuxt_portal(html, base, source):
    """Gratka i Morizon: ta sama platforma, dane w __NUXT_DATA__."""
    data = nuxt_data(html)
    if not data:
        return []
    out, seen = [], set()
    for n in _nuxt_listing_nodes(data):
        if not isinstance(n, dict):
            continue
        url = urljoin(base, n.get("url") or "")
        if not OFFER_LINK[source].search(urlparse(url).path):
            continue
        native_id = str(n.get("idOnFrontend") or offer_id_from_url(source, url))
        if native_id in seen:
            continue
        seen.add(native_id)
        price = to_num((n.get("price") or {}).get("amount")) if isinstance(n.get("price"), dict) else to_num(n.get("price"))
        ppm = to_num((n.get("priceM2") or {}).get("amount")) if isinstance(n.get("priceM2"), dict) else None
        area = to_num(n.get("area"))
        if price and ppm:
            # pole "area" jest zaokrąglone do pełnych metrów, a cena za m² liczona z dokładnego metrażu
            area = round(price / ppm, 2)
        loc = n.get("location") or {}
        parts = [x for x in (loc.get("location") or []) if isinstance(x, str)]
        city = parts[1] if len(parts) > 1 else ""
        district = parts[2] if len(parts) > 2 else ""
        sub = parts[3] if len(parts) > 3 else ""
        street = loc.get("street") or ""
        image = None
        photos = n.get("photos") or []
        if photos and isinstance(photos[0], dict) and photos[0].get("id"):
            image = NUXT_THUMB.format(id=photos[0]["id"], name=photos[0].get("name") or "zdjecie")
        title = (n.get("advertisementText") or n.get("title") or "").strip()
        out.append({
            "native_id": native_id,
            "url": url,
            "title": title[:200],
            "price": price,
            "area": area,
            "rooms": rooms_num(n.get("numberOfRooms")),
            "city": city,
            "district": district,
            "location": join_location(street, sub, district, city),
            "image": image,
        })
    return out


def parse_ld_json(html, base, source):
    """Zapas: oferty z JSON-LD, gdy główne dane strukturalne są niedostępne."""
    out, seen = [], set()
    for d in ld_json_offers(html):
        url = urljoin(base, d["url"])
        if not OFFER_LINK[source].search(urlparse(url).path) or url in seen:
            continue
        seen.add(url)
        item = d.get("itemOffered") or {}
        addr = item.get("address") or {}
        size = item.get("floorSize") or {}
        image = d.get("image")
        if isinstance(image, list):
            image = image[0] if image else None
        out.append({
            "native_id": offer_id_from_url(source, url),
            "url": url,
            "title": (d.get("name") or "")[:200],
            "price": to_num(d.get("price")),
            "area": to_num(size.get("value") if isinstance(size, dict) else size),
            "rooms": rooms_num(item.get("numberOfRooms")),
            "city": "",
            "district": addr.get("addressLocality") or "",
            "location": join_location(addr.get("streetAddress"), addr.get("addressLocality")),
            "image": image if isinstance(image, str) else None,
        })
    return out


def parse_generic(html, base, source):
    """Uniwersalny parser kart ogłoszeń. Szuka linków do ofert i czyta tekst ich kart.

    Ostatnia deska ratunku, gdy portal przestanie osadzać dane strukturalne.
    """
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
            "city": "",
            "district": "",
            "location": "",
            "image": urljoin(base, image) if image else None,
            **fields,
        }
    return list(results.values())


STRUCTURED_PARSERS = {
    "otodom": [parse_otodom],
    "olx": [parse_olx],
    "gratka": [lambda h, b: parse_nuxt_portal(h, b, "gratka"), lambda h, b: parse_ld_json(h, b, "gratka")],
    "morizon": [lambda h, b: parse_nuxt_portal(h, b, "morizon"), lambda h, b: parse_ld_json(h, b, "morizon")],
}


def parse_page(source, html, base):
    """Najpierw dane strukturalne, a gdy ich brak, parsowanie HTML."""
    for parser in STRUCTURED_PARSERS.get(source, []):
        offers = parser(html, base)
        if offers:
            return offers
    return parse_generic(html, base, source)


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


UPDATED_FIELDS = ("title", "price", "area", "rooms", "city", "district", "location", "image", "url")


def run(config, offers, run_at, fetch_fn=None, sleep=None):
    """Jeden przebieg: pobiera wyszukiwania, filtruje i aktualizuje słownik ofert.

    Zwraca (offers, status). Nie zapisuje plików, więc da się go testować
    z podmienioną funkcją pobierania.
    """
    fetch_fn = fetch_fn or fetch
    sleep = sleep or (lambda: time.sleep(random.uniform(3, 7)))
    pages = int(config.get("strony", 1))
    status = {"run_at": iso(run_at), "sources": [], "new": 0, "drops": 0}
    first = True

    for search in config.get("wyszukiwania") or []:
        name = search.get("nazwa", "Wyszukiwanie")
        filters = search.get("filtry") or {}
        for url in search.get("adresy") or []:
            source = detect_source(url)
            entry = {"search": name, "source": source, "url": url, "found": 0, "matched": 0, "error": None}
            if not source:
                entry["error"] = "Nieobsługiwany portal. Obsługiwane są otodom.pl, olx.pl, gratka.pl i morizon.pl."
                status["sources"].append(entry)
                continue
            try:
                for page in range(1, pages + 1):
                    if not first:
                        sleep()  # przerwa między zapytaniami, żeby nie obciążać portali
                    first = False
                    page_url = with_page(url, page)
                    html = fetch_fn(page_url)
                    found = parse_page(source, html, page_url)
                    entry["found"] += len(found)
                    for o in found:
                        if not o.get("url") or not passes(o, filters):
                            continue
                        entry["matched"] += 1
                        key = f"{source}:{o['native_id']}"
                        old = offers.get(key)
                        if old is None:
                            status["new"] += 1
                            offers[key] = {
                                **o, "source": source, "search": name,
                                "first_seen": iso(run_at), "last_seen": iso(run_at),
                                "price_history": [{"date": iso(run_at), "price": o.get("price")}],
                            }
                            continue
                        if o.get("price") and old.get("price") and o["price"] != old["price"]:
                            old.setdefault("price_history", []).append({"date": iso(run_at), "price": o["price"]})
                            if o["price"] < old["price"]:
                                status["drops"] += 1
                        for field in UPDATED_FIELDS:
                            if o.get(field):
                                old[field] = o[field]
                        old["last_seen"] = iso(run_at)
                if entry["found"] == 0:
                    entry["error"] = ("Nie znaleziono ogłoszeń na stronie. Portal mógł zablokować zapytanie "
                                      "albo zmienić wygląd strony.")
            except FetchError as exc:
                entry["error"] = str(exc)
            except Exception as exc:  # noqa: BLE001
                entry["error"] = f"Nieoczekiwany błąd: {exc}"
            status["sources"].append(entry)
            print(f"[{source}] {name}: znaleziono {entry['found']}, pasuje {entry['matched']}"
                  + (f" | {entry['error']}" if entry["error"] else ""))

    cutoff = run_at - timedelta(days=KEEP_DAYS)
    offers = {k: v for k, v in offers.items() if datetime.fromisoformat(v["last_seen"]) >= cutoff}
    return offers, status


def main():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    offers = load_json(OFFERS_FILE, {})
    offers, status = run(config, offers, now_utc())

    OFFERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFERS_FILE.write_text(json.dumps(offers, ensure_ascii=False, indent=1), encoding="utf-8")
    STATUS_FILE.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Nowych ofert: {status['new']}, obniżek: {status['drops']}, w bazie: {len(offers)}")

    from page import render
    render(config, offers, status)


if __name__ == "__main__":
    sys.exit(main())
