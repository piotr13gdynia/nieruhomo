"""Licytacje komornicze z portalu Krajowej Rady Komorniczej (licytacje.komornik.pl).

Portal (aplikacja Nuxt) pobiera wyniki z interfejsu JSON:
POST https://licytacje.komornik.pl/services/item-back/rest/item/search
z filtrami termFilters (mainCategory, province, city, subCategory).
Stary adres licytacje.komornik.pl/Notice/Search przekierowuje na nowy portal.
"""
import json
import re
from datetime import datetime, timedelta

SEARCH_URL = "https://licytacje.komornik.pl/services/item-back/rest/item/search"
ITEM_URL = "https://licytacje.komornik.pl/licytacje/{id}"
SEARCH_PAGE = "https://licytacje.komornik.pl/wyszukiwarka-licytacji"

CATEGORIES = {
    "APARTMENTS": "mieszkanie", "HOUSES": "dom", "LAND": "grunt", "COMMERCIAL_PREMISESISES": "lokal użytkowy",
    "COMMERCIAL_PREMISES": "lokal użytkowy", "GARAGES": "garaż", "WAREHOUSES_AND_HALLS": "magazyn lub hala",
    "OTHER": "inna",
}
# udział w nieruchomości: słowa „udział”, „części” albo ułamek na początku tytułu (nie numer domu „82/9”)
SHARE_RE = re.compile(r"udzia[łl]|częś[cć]|^\s*\d+\s*/\s*\d+\b", re.I)
PAGE_SIZE = 50  # każda licytacja w odpowiedzi ma miniaturę zdjęcia (ok. 33 KB), więc strony są małe

DEFAULTS = {"wlaczone": True, "wojewodztwo": "mazowieckie", "miasto": None, "kategorie": [],
            "cena_max": None, "pomin_udzialy": False, "strony_na_start": 10}


def settings(config):
    s = dict(DEFAULTS)
    s.update((config or {}).get("licytacje") or {})
    return s


def build_query(s, offset=0):
    """Licytacje nieruchomości, do których można jeszcze przystąpić, od najnowszych.

    Filtr miasta po stronie portalu nie działa, więc miasto i rodzaj są filtrowane w passes().
    Bez warunku joinable zapytanie z filtrem województwa przekracza czas odpowiedzi.
    """
    filters = [{"field": "mainCategory", "value": ["REAL_ESTATE"]},
               {"field": "joinable", "value": ["true"]}]
    if s.get("wojewodztwo"):
        filters.append({"field": "province", "value": [str(s["wojewodztwo"]).lower()]})
    return {"orderByField": "dateCreated", "orderBy": "DESC", "limit": PAGE_SIZE, "offset": offset,
            "termFilters": filters}


def address_text(a):
    a = {k: (v.strip() if isinstance(v, str) else v) for k, v in (a or {}).items()}
    street = " ".join(x for x in [a.get("street"), a.get("buildingNo")] if x)
    if street and a.get("flatNo"):
        street += f"/{a['flatNo']}"
    city = " ".join(x for x in [a.get("zipCode"), a.get("city")] if x)
    return ", ".join(x for x in [street, city] if x)


def normalize(item):
    """Licytacja w formacie zapisywanym w data/auctions.json."""
    title = re.sub(r"\s+", " ", item.get("title") or "").strip(" ,;") or "Licytacja nieruchomości"
    sub = item.get("subCategory")
    return {
        "id": str(item["id"]),
        "title": title,
        "url": ITEM_URL.format(id=item["id"]),
        "price": item.get("openingValue"),
        "estimate": item.get("estimate"),
        "date": item.get("startAuctionAt"),
        "end": item.get("endAuctionAt"),
        "address": address_text(item.get("address")),
        "city": ((item.get("address") or {}).get("city") or "").strip(),
        "province": item.get("province") or (item.get("address") or {}).get("province") or "",
        "category": CATEGORIES.get(sub, sub.lower().replace("_", " ") if isinstance(sub, str) else ""),
        "electronic": bool(item.get("eauction")),
        "share": bool(SHARE_RE.search(title)),
        "status": item.get("status"),
    }


def parse_response(data):
    if isinstance(data, str):
        data = json.loads(data)
    items = (data or {}).get("items")
    if not isinstance(items, list):
        raise ValueError("Odpowiedź portalu licytacji ma nieoczekiwany format.")
    return [normalize(i) for i in items if isinstance(i, dict) and i.get("id")]


def passes(a, s):
    if s.get("miasto") and str(s["miasto"]).strip().lower() != (a.get("city") or "").strip().lower():
        return False
    if s.get("kategorie") and a.get("category") not in [str(k).lower() for k in s["kategorie"]]:
        return False
    if s.get("cena_max") and a.get("price") and a["price"] > float(s["cena_max"]):
        return False
    if s.get("pomin_udzialy") and a.get("share"):
        return False
    return True


def default_post(url, body):
    from monitor import post_json  # import tutaj, żeby uniknąć importu w kółko
    return post_json(url, body)


def update(config, stored, run_at, post=None):
    """Pobiera licytacje i łączy z zapisanymi. Zwraca (licytacje, wpis do status.json).

    Gdy licytacje są wyłączone w config.yaml, zwraca (None, None).
    """
    s = settings(config)
    if not s.get("wlaczone"):
        return None, None
    run_iso = run_at.isoformat(timespec="seconds")
    entry = {"source": "komornik", "search": "Licytacje komornicze", "url": SEARCH_PAGE,
             "found": 0, "matched": 0, "error": None}
    post = post or default_post
    known = set(stored)
    max_pages = int(s.get("strony_na_start") or 1) if not stored else 10
    found = []
    try:
        for page in range(max_pages):
            batch = parse_response(post(SEARCH_URL, build_query(s, offset=page * PAGE_SIZE)))
            found += batch
            # od najnowszych: gdy cała strona jest już znana albo niepełna, dalej nie ma nic nowego
            if len(batch) < PAGE_SIZE or all(a["id"] in known for a in batch):
                break
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"Nie udało się pobrać licytacji: {exc}"
        if not found:
            return stored, entry
    entry["found"] = len(found)
    for a in found:
        if not passes(a, s):
            continue
        entry["matched"] += 1
        old = stored.get(a["id"])
        stored[a["id"]] = {**a, "first_seen": (old or {}).get("first_seen", run_iso), "last_seen": run_iso}
    # licytacje, które już się odbyły (z zapasem jednego dnia), znikają
    cutoff = run_at - timedelta(days=1)
    for k in [k for k, v in stored.items() if v.get("date") and _dt(v["date"]) < cutoff]:
        del stored[k]
    return stored, entry


def _dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def for_page(stored, run_at):
    """Licytacje do pokazania na stronie, od najbliższego terminu."""
    run_iso = run_at.isoformat(timespec="seconds")
    out = []
    for a in stored.values():
        if a.get("date") and _dt(a["date"]) < run_at - timedelta(hours=12):
            continue
        out.append({**a, "is_new": a.get("first_seen") == run_iso})
    out.sort(key=lambda a: a.get("date") or "9999")
    return out
