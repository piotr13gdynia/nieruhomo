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

import auctions
import deals
import maintenance
import notify
from details import (
    DETAIL_SOURCES, kind_from_text, parse_details, normalize_advertiser, normalize_floor, normalize_market, parse_floor_formatted,
)
from monitor_common import (  # noqa: F401  (udostępniane dalej, m.in. dla testów)
    join_location, ld_json_offers, next_data, nuxt_data, prerendered_state, rooms_num, to_num, walk,
)

ROOT = Path(__file__).resolve().parent
OFFERS_FILE = ROOT / "data" / "offers.json"
STATUS_FILE = ROOT / "data" / "status.json"
MARKET_FILE = ROOT / "data" / "market.json"
ARCHIVE_FILE = ROOT / "data" / "archive.json"
HEALTH_FILE = ROOT / "data" / "health.json"
AUCTIONS_FILE = ROOT / "data" / "auctions.json"


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


def post_json(url, body):
    """Zapytanie POST z treścią JSON (interfejs portalu licytacji). Zwraca odpowiedź jako słownik."""
    headers = {"Accept-Language": "pl-PL,pl;q=0.9", "Accept": "application/json",
               "Content-Type": "application/json"}
    try:
        resp = get_session().post(url, headers=headers, data=json.dumps(body), timeout=40)
    except Exception as exc:  # noqa: BLE001
        raise FetchError(f"Nie udało się połączyć z portalem ({str(exc).split('.')[0][:120]}).") from exc
    if resp.status_code >= 400:
        raise FetchError(HTTP_MESSAGES.get(resp.status_code) or f"Portal odrzucił zapytanie (HTTP {resp.status_code}).")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError("Portal zwrócił odpowiedź, której nie da się odczytać.") from exc


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
            "subdistrict": levels.get("residential") or "",
            "location": join_location(street, levels.get("residential"), district, city),
            "image": image,
            "kind": {"FLAT": "mieszkanie", "HOUSE": "dom", "TERRAIN": "działka"}.get(d.get("estate"))
                    or kind_from_text(slug),
            "floor": normalize_floor(d.get("floorNumber")),
            "advertiser": _otodom_advertiser(d),
        })
    return out


def _otodom_advertiser(d):
    if d.get("isPrivateOwner"):
        return "prywatny"
    if d.get("development") or d.get("developmentId"):
        return "deweloper"
    agency = d.get("agency") if isinstance(d.get("agency"), dict) else None
    if agency:
        return normalize_advertiser(agency.get("type")) or "biuro"
    return None


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
        area = rooms = floor = market = None
        for param in ad.get("params") or []:
            key = param.get("key")
            val = param.get("normalizedValue") or param.get("value")
            if key == "m":
                area = to_num(val)
            elif key == "rooms":
                rooms = rooms_num(val)
            elif key == "floor_select":
                floor = normalize_floor(val)
            elif key == "market":
                market = normalize_market(val)
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
            "floor": floor,
            "market": market,
            "advertiser": "biuro" if ad.get("isBusiness") else ("prywatny" if ad.get("isBusiness") is False else None),
            # OLX nie ma osobnych stron szczegółów z dodatkowymi danymi, więc lista wystarcza
            "details_checked": True,
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


def _nuxt_advertiser(contact):
    company = contact.get("company") if isinstance(contact.get("company"), dict) else None
    person = contact.get("person") if isinstance(contact.get("person"), dict) else None
    if company:
        return normalize_advertiser(company.get("type")) or "biuro"
    if person and str(person.get("type", "")).upper() in ("OWNER", "PRIVATE"):
        return "prywatny"
    return None


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
        floor, floors_total = parse_floor_formatted(n.get("floorFormatted"))
        contact = n.get("contact") if isinstance(n.get("contact"), dict) else {}
        out.append({
            "native_id": native_id,
            "url": url,
            "title": title[:200],
            "price": price,
            "area": area,
            "rooms": rooms_num(n.get("numberOfRooms")),
            "city": city,
            "district": district,
            "subdistrict": sub,
            "location": join_location(street, sub, district, city),
            "image": image,
            "kind": kind_from_text(urlparse(url).path),
            "floor": floor,
            "floors_total": floors_total,
            "advertiser": _nuxt_advertiser(contact),
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


UPDATED_FIELDS = ("title", "price", "area", "rooms", "city", "district", "subdistrict", "location", "image",
                  "url", "kind", "floor", "floors_total", "market", "advertiser")
DETAIL_FIELDS = ("floor", "floors_total", "build_year", "market", "advertiser", "district", "subdistrict")

# Ogłoszenia, które zaburzałyby medianę: udziały, licytacje, TBS.
MARKET_EXCLUDED = ("udział", "udzial", "licytac", "komornicz", "tbs", "1/2 ", "1/3 ", "1/4 ", "prawo do lokalu")


def default_sleep():
    time.sleep(random.uniform(3, 7))


class Fetcher:
    """Pobieranie z przerwą 3 do 7 sekund przed każdym zapytaniem oprócz pierwszego."""

    def __init__(self, fetch_fn, sleep):
        self.fetch_fn, self.sleep, self.count = fetch_fn, sleep, 0

    def __call__(self, url):
        if self.count:
            self.sleep()
        self.count += 1
        return self.fetch_fn(url)


def market_ok(o):
    title = (o.get("title") or "").lower()
    if any(w in title for w in MARKET_EXCLUDED):
        return False
    ppm = o["price"] / o["area"] if o.get("price") and o.get("area") else None
    return ppm is not None and 1_000 <= ppm <= 100_000


def scan(url, pages, source, get, run_iso, market, default_kind):
    """Pobiera strony wyników jednego adresu. Zwraca (oferty, wpis do status.json)."""
    found_all = []
    entry = {"source": source, "url": url, "found": 0, "matched": 0, "error": None}
    if not source:
        entry["error"] = "Nieobsługiwany portal. Obsługiwane są otodom.pl, olx.pl, gratka.pl i morizon.pl."
        return found_all, entry
    try:
        for page in range(1, pages + 1):
            page_url = with_page(url, page)
            found = parse_page(source, get(page_url), page_url)
            for o in found:
                o.setdefault("kind", None)
                if not o["kind"]:
                    o["kind"] = default_kind
            entry["found"] += len(found)
            found_all += found
            deals.update_market(market, source, [o for o in found if market_ok(o)], run_iso)
            if not found:
                break
        if entry["found"] == 0:
            entry["error"] = ("Nie znaleziono ogłoszeń na stronie. Portal mógł zablokować zapytanie "
                              "albo zmienić wygląd strony.")
    except FetchError as exc:
        entry["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"Nieoczekiwany błąd: {exc}"
    return found_all, entry


def run(config, offers, run_at, fetch_fn=None, sleep=None, market=None):
    """Jeden przebieg monitora. Nie zapisuje plików, więc da się go testować z podmienionym pobieraniem.

    Zmienia na miejscu słownik market (baza rynkowa do median). Zwraca (offers, status).
    """
    get = Fetcher(fetch_fn or fetch, sleep or default_sleep)
    market = {} if market is None else market
    run_iso = iso(run_at)
    pages = int(config.get("strony", 1))
    status = {"run_at": run_iso, "sources": [], "new": 0, "drops": 0,
              "new_keys": [], "drop_events": [], "new_deals": [], "details": {"checked": 0, "errors": 0}}
    seen_now = set()

    # 1. wyszukiwania użytkownika
    for search in config.get("wyszukiwania") or []:
        name = search.get("nazwa", "Wyszukiwanie")
        filters = search.get("filtry") or {}
        for url in search.get("adresy") or []:
            source = detect_source(url)
            found, entry = scan(url, pages, source, get, run_iso, market, kind_from_text(url) or kind_from_text(name))
            entry["search"] = name
            for o in found:
                if not o.get("url") or not passes(o, filters):
                    continue
                entry["matched"] += 1
                key = f"{source}:{o['native_id']}"
                seen_now.add(key)
                old = offers.get(key)
                if old is None:
                    status["new"] += 1
                    status["new_keys"].append(key)
                    offers[key] = {
                        **o, "source": source, "search": name,
                        "first_seen": run_iso, "last_seen": run_iso,
                        "price_history": [{"date": run_iso, "price": o.get("price")}],
                    }
                    continue
                if o.get("price") and old.get("price") and o["price"] != old["price"]:
                    old.setdefault("price_history", []).append({"date": run_iso, "price": o["price"]})
                    if o["price"] < old["price"]:
                        status["drops"] += 1
                        status["drop_events"].append({"key": key, "old": old["price"], "new": o["price"]})
                for field in UPDATED_FIELDS:
                    if o.get(field) not in (None, ""):
                        old[field] = o[field]
                old["last_seen"] = run_iso
            status["sources"].append(entry)
            log_entry(entry)

    # 2. adresy rynku: tylko do liczenia median
    ok = deals.settings(config)
    start_pages = int(ok.get("strony_na_start") or 1)
    start_threshold = int(ok.get("baza_na_start") or 2000)
    for url in ok.get("adresy_rynku") or []:
        source = detect_source(url)
        n = start_pages if len(market) < start_threshold else 1
        _, entry = scan(url, max(n, 1), source, get, run_iso, market, kind_from_text(url))
        entry["search"] = "Rynek (do median)"
        entry["market_only"] = True
        status["sources"].append(entry)
        log_entry(entry)

    # 3. ocena okazji
    assess_all(offers, market, config, run_iso, status)

    # 4. szczegóły nowych ofert, zaczynając od największych okazji
    fetch_details(offers, config, get, run_at, status)
    assess_all(offers, market, config, run_iso, status)

    maintenance.prune_market(market, run_at)
    status["market_size"] = len(market)
    status["requests"] = get.count
    return offers, status


def assess_all(offers, market, config, run_iso, status):
    med = deals.medians(market, config)
    status["groups_rated"] = len(med)
    for key, o in offers.items():
        was_deal = bool(o.get("deal"))
        deals.assess(o, med, config)
        if o.get("deal") and not was_deal and o.get("last_seen") == run_iso:
            o.setdefault("deal_since", run_iso)
            if key not in status["new_deals"]:
                status["new_deals"].append(key)
        if not o.get("deal"):
            o.pop("deal_since", None)
    # oferta, która przestała być okazją przy drugiej ocenie, nie trafia do powiadomień
    status["new_deals"] = [k for k in status["new_deals"] if offers.get(k, {}).get("deal")]


def fetch_details(offers, config, get, run_at, status):
    limit = int(config.get("szczegoly_na_przebieg", 10) or 0)
    if limit <= 0:
        return
    recent = run_at - timedelta(days=2)
    todo = [(k, o) for k, o in offers.items()
            if o.get("source") in DETAIL_SOURCES and not o.get("details_checked")
            and datetime.fromisoformat(o["first_seen"]) >= recent]
    todo.sort(key=lambda ko: (not ko[1].get("deal"), ko[1].get("deviation", 0), ko[1]["first_seen"]))
    for key, o in todo[:limit]:
        try:
            info = parse_details(o["source"], get(o["url"]))
        except FetchError as exc:
            o["details_attempts"] = o.get("details_attempts", 0) + 1
            o["details_error"] = str(exc)
            if o["details_attempts"] >= 3:
                o["details_checked"] = True
            status["details"]["errors"] += 1
            continue
        for field in DETAIL_FIELDS:
            if info.get(field) not in (None, ""):
                o[field] = info[field]
        o["details_checked"] = True
        o.pop("details_error", None)
        status["details"]["checked"] += 1
    if todo:
        print(f"Szczegóły: sprawdzono {status['details']['checked']}, błędów {status['details']['errors']}, "
              f"w kolejce {max(len(todo) - limit, 0)}")


def log_entry(entry):
    print(f"[{entry['source']}] {entry['search']}: znaleziono {entry['found']}, pasuje {entry['matched']}"
          + (f" | {entry['error']}" if entry["error"] else ""))


def write_json(path, data, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":")) if compact else \
        json.dumps(data, ensure_ascii=False, indent=1)
    path.write_text(text, encoding="utf-8")


def main():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    run_at = now_utc()
    offers = load_json(OFFERS_FILE, {})
    market = load_json(MARKET_FILE, {})
    offers, status = run(config, offers, run_at, market=market)

    # licytacje komornicze: osobne źródło, jego awaria nie zatrzymuje reszty
    auction_list, auction_entry = auctions.update(config, load_json(AUCTIONS_FILE, {}), run_at)
    if auction_entry:
        status["sources"].append(auction_entry)
        log_entry(auction_entry)

    status["notifications"] = notify.send_all(offers, status, config)

    offers, archive, moved = maintenance.rotate(offers, load_json(ARCHIVE_FILE, {}), run_at)
    health = maintenance.update_health(load_json(HEALTH_FILE, {}), status, run_at)

    write_json(OFFERS_FILE, offers)
    write_json(MARKET_FILE, market, compact=True)
    write_json(STATUS_FILE, status)
    write_json(HEALTH_FILE, health)
    if auction_list is not None:
        write_json(AUCTIONS_FILE, auction_list)
    if moved:
        write_json(ARCHIVE_FILE, archive, compact=True)
    deals_now = sum(1 for o in offers.values() if o.get("deal"))
    print(f"Nowych ofert: {status['new']}, obniżek: {status['drops']}, okazji: {deals_now}, "
          f"w bazie: {len(offers)}, w bazie rynkowej: {len(market)}, do archiwum: {moved}, "
          f"zapytań: {status['requests']}")

    from page import render
    render(config, offers, status,
           auctions=auctions.for_page(load_json(AUCTIONS_FILE, {}), run_at),
           health=maintenance.health_rows(health, run_at))


if __name__ == "__main__":
    sys.exit(main())
