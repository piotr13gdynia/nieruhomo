"""Szczegóły ogłoszeń: piętro, rok budowy, rynek, oferent, dokładna dzielnica.

Część tych danych jest już na liście wyników (normalize_* używane przez parsery list),
resztę daje strona pojedynczej oferty (parse_details).
"""
import re

from monitor_common import next_data, nuxt_data, rooms_num, to_num

MARKETS = {"primary": "pierwotny", "secondary": "wtórny", "pierwotny": "pierwotny", "wtórny": "wtórny",
           "wtorny": "wtórny"}

ORDINALS = {
    "GROUND": 0, "FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
    "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "HIGHER_10": 11, "CELLAR": -1,
}


def normalize_market(value):
    if not value:
        return None
    v = str(value).strip().lower()
    v = v.replace("rynek", "").strip()
    return MARKETS.get(v)


def normalize_floor(value):
    """Piętro jako liczba: 0 to parter, -1 suterena, 11 to „powyżej 10”. None, gdy nie wiadomo."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(value)
    v = str(value).strip()
    up = v.upper().replace("FLOOR_", "")
    if up in ORDINALS:
        return ORDINALS[up]
    low = v.lower()
    if low in ("ground_floor", "parter", "floor_0") or low.startswith("parter"):
        return 0
    if low in ("cellar", "suterena", "floor_-1"):
        return -1
    if "higher_10" in low or low in ("floor_11", "powyżej 10"):
        return 11
    if low == "garret" or "poddasze" in low:
        return None
    m = re.search(r"-?\d+", low)
    return int(m.group()) if m else None


def parse_floor_formatted(text):
    """„parter/4”, „piętro 5/10”, „piętro 3” -> (piętro, liczba pięter)."""
    if not text:
        return None, None
    t = str(text).lower().replace(" ", "")
    floors_total = None
    m = re.search(r"/(\d+)", t)
    if m:
        floors_total = int(m.group(1))
    head = t.split("/")[0]
    floor = 0 if head.startswith("parter") else normalize_floor(head)
    return floor, floors_total


def normalize_advertiser(value):
    """Typ oferenta: prywatny, biuro albo deweloper."""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in ("private", "prywatny", "osoba prywatna", "owner", "private_owner"):
        return "prywatny"
    if "develop" in v or v in ("sales_office", "deweloper"):
        return "deweloper"
    if v in ("agency", "business", "biuro", "agent", "biuro nieruchomości"):
        return "biuro"
    return None


def kind_from_text(text):
    """Typ nieruchomości z adresu lub nazwy: mieszkanie, dom, działka, lokal."""
    t = (text or "").lower()
    if re.search(r"mieszkani|kawalerk|apartament|\bflat\b", t):
        return "mieszkanie"
    if re.search(r"(^|[/_-])domy?([/_-]|$)|\bhouse\b|\bdom\b|segment|bliźniak|szeregow", t):
        return "dom"
    if re.search(r"dzialk|działk|grunt|\bplot\b|\bterrain\b", t):
        return "działka"
    if re.search(r"lokal|biur|commercial", t):
        return "lokal"
    return None


# ---------- strony pojedynczych ofert ----------

def _otodom_details(html):
    data = next_data(html)
    try:
        ad = data["props"]["pageProps"]["ad"]
    except (KeyError, TypeError):
        return {}
    chars = {c.get("key"): c.get("value") for c in ad.get("characteristics") or [] if isinstance(c, dict)}
    target = ad.get("target") or {}
    floor_raw = chars.get("floor_no") or (target.get("Floor_no") or [None])[0]
    out = {
        "floor": normalize_floor(floor_raw),
        "floors_total": rooms_num(chars.get("building_floors_num") or target.get("Building_floors_num")),
        "build_year": _year(chars.get("build_year") or target.get("Build_year")),
        "market": normalize_market(chars.get("market") or ad.get("market")),
        "advertiser": normalize_advertiser(
            "developer" if str(ad.get("advertType", "")).upper().startswith("DEVELOPER")
            else target.get("user_type") or ad.get("advertiserType")),
    }
    levels = {}
    try:
        for x in ad["location"]["reverseGeocoding"]["locations"]:
            levels.setdefault(x.get("locationLevel"), x.get("name"))
    except (KeyError, TypeError):
        pass
    if levels.get("district"):
        out["district"] = levels["district"]
    if levels.get("residential"):
        out["subdistrict"] = levels["residential"]
    return out


def _labels(items):
    return {str(x.get("label", "")).strip().lower(): x.get("value") for x in items or [] if isinstance(x, dict)}


def _nuxt_details(html):
    data = nuxt_data(html)
    try:
        p = data["pinia"]["details"]["propertyData"]
    except (KeyError, TypeError):
        return {}
    if not isinstance(p, dict):
        return {}
    info = _labels(p.get("detailedInformation"))
    building = _labels(p.get("buildingDetailedInformation"))
    floor, floors_total = parse_floor_formatted(p.get("floorFormatted"))
    if isinstance(p.get("floor"), int):
        floor = p["floor"]
    floors_total = floors_total or rooms_num(info.get("liczba pięter"))
    contact = p.get("contact") if isinstance(p.get("contact"), dict) else {}
    company = contact.get("company") if isinstance(contact.get("company"), dict) else None
    person = contact.get("person") if isinstance(contact.get("person"), dict) else None
    if company:
        advertiser = normalize_advertiser(company.get("type")) or "biuro"
    elif person and str(person.get("type", "")).upper() in ("OWNER", "PRIVATE"):
        advertiser = "prywatny"
    else:
        advertiser = None
    out = {
        "floor": floor,
        "floors_total": floors_total,
        "build_year": _year(building.get("rok budowy") or info.get("rok budowy")),
        "market": normalize_market(p.get("marketType") or info.get("rynek")),
        "advertiser": advertiser,
    }
    parts = [x for x in ((p.get("location") or {}).get("location") or []) if isinstance(x, str)]
    if len(parts) > 2:
        out["district"] = parts[2]
    if len(parts) > 3:
        out["subdistrict"] = parts[3]
    return out


def _year(value):
    n = to_num(value)
    if n and 1700 <= n <= 2100:
        return int(n)
    return None


DETAIL_PARSERS = {"otodom": _otodom_details, "gratka": _nuxt_details, "morizon": _nuxt_details}

# Portale, dla których strona oferty daje więcej niż lista wyników.
DETAIL_SOURCES = set(DETAIL_PARSERS)


def parse_details(source, html):
    """Szczegóły z HTML strony oferty. Zwraca tylko pola, które udało się odczytać."""
    parser = DETAIL_PARSERS.get(source)
    if not parser:
        return {}
    return {k: v for k, v in parser(html).items() if v is not None and v != ""}
