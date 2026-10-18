"""Wykrywanie okazji: porównanie ceny za m² z medianą podobnych ofert.

Grupa podobnych ofert to: miasto, dzielnica, typ nieruchomości i przedział metrażu.
Mediana jest liczona z bazy rynkowej (data/market.json), w której są wszystkie oferty
znalezione na stronach wyników, także te odrzucone przez filtry użytkownika.
"""
from statistics import median

# Odchylenie, przy którym oferta jest oznaczana jako podejrzana (zwykle błąd w metrażu lub cenie,
# udział w nieruchomości albo cena za część lokalu).
SUSPICIOUS = -0.35

DEFAULTS = {
    "prog_procent": 10,
    "min_ofert_w_grupie": 15,
    "przedzialy_metrazu": [35, 50, 70, 100],
}


def settings(config):
    s = dict(DEFAULTS)
    s.update((config or {}).get("okazje") or {})
    return s


def area_band(area, bounds):
    """Przedział metrażu jako napis, np. „35-50 m²”, „do 35 m²”, „100+ m²”."""
    if not area:
        return None
    bounds = sorted(float(b) for b in bounds or [])
    if not bounds:
        return "wszystkie"
    if area < bounds[0]:
        return f"do {bounds[0]:g} m²"
    for lo, hi in zip(bounds, bounds[1:]):
        if lo <= area < hi:
            return f"{lo:g}-{hi:g} m²"
    return f"{bounds[-1]:g}+ m²"


def group_key(o, bounds):
    """Klucz grupy podobnych ofert albo None, gdy brakuje danych."""
    band = area_band(o.get("area"), bounds)
    if not (o.get("city") and o.get("kind") and band and o.get("price")):
        return None
    return (o["city"].strip().lower(), (o.get("district") or "").strip().lower(), o["kind"], band)


def property_key(o):
    """Ta sama nieruchomość na kilku portalach: typ, cena i metraż."""
    return (o.get("kind"), round(o.get("price") or 0), round(o.get("area") or 0))


def market_entry(o, source, run_at_iso):
    return {
        "source": source,
        "price": o.get("price"),
        "area": o.get("area"),
        "kind": o.get("kind"),
        "city": o.get("city") or "",
        "district": o.get("district") or "",
        "first_seen": run_at_iso,
        "last_seen": run_at_iso,
    }


def update_market(market, source, offers_found, run_at_iso):
    """Dopisuje do bazy rynkowej wszystkie oferty z ceną i metrażem."""
    for o in offers_found:
        if not (o.get("price") and o.get("area") and o.get("native_id")):
            continue
        key = f"{source}:{o['native_id']}"
        entry = market.get(key)
        if entry is None:
            market[key] = market_entry(o, source, run_at_iso)
        else:
            entry.update({k: o[k] for k in ("price", "area", "kind", "city", "district") if o.get(k)})
            entry["last_seen"] = run_at_iso


def medians(market, config):
    """Mediana ceny za m² dla każdej grupy. Zwraca {grupa: (mediana, liczba ofert)}."""
    s = settings(config)
    bounds = s["przedzialy_metrazu"]
    groups = {}
    for e in market.values():
        key = group_key(e, bounds)
        if not key or not e.get("area"):
            continue
        # ta sama nieruchomość z kilku portali liczy się raz
        groups.setdefault(key, {})[property_key(e)] = e["price"] / e["area"]
    min_n = int(s["min_ofert_w_grupie"])
    return {k: (median(v.values()), len(v)) for k, v in groups.items() if len(v) >= min_n}


def assess(offer, med, config):
    """Ocena oferty względem mediany jej grupy. Zmienia słownik oferty na miejscu."""
    s = settings(config)
    key = group_key(offer, s["przedzialy_metrazu"])
    offer.pop("deal", None)
    for k in ("median_ppm", "deviation", "group", "group_size", "suspicious"):
        offer.pop(k, None)
    if not key or key not in med or not offer.get("area"):
        return offer
    m, n = med[key]
    ppm = offer["price"] / offer["area"]
    deviation = ppm / m - 1
    offer["median_ppm"] = round(m, 2)
    offer["deviation"] = round(deviation, 4)
    offer["group"] = describe_group(key)
    offer["group_size"] = n
    offer["deal"] = deviation <= -float(s["prog_procent"]) / 100
    if deviation <= SUSPICIOUS:
        offer["suspicious"] = True
    return offer


def describe_group(key):
    city, district, kind, band = key
    parts = [kind, district.title() if district else None, city.title(), band]
    return ", ".join(p for p in parts if p)
