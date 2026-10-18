"""Utrzymanie danych: archiwum starych ofert i raport zdrowia źródeł."""
from datetime import datetime, timedelta

KEEP_DAYS = 60          # oferty niewidziane dłużej trafiają do archiwum
STALE_HOURS = 24        # źródło bez wyników dłużej niż tyle godzin jest oznaczane na czerwono

SOURCE_NAMES = {"otodom": "Otodom", "olx": "OLX", "gratka": "Gratka", "morizon": "Morizon",
                "komornik": "Licytacje komornicze"}


def rotate(offers, archive, run_at, keep_days=KEEP_DAYS):
    """Przenosi do archiwum oferty niewidziane od keep_days dni. Zwraca (aktywne, archiwum, liczba przeniesionych)."""
    cutoff = run_at - timedelta(days=keep_days)
    active, moved = {}, 0
    for key, o in offers.items():
        if datetime.fromisoformat(o["last_seen"]) >= cutoff:
            active[key] = o
        else:
            archive[key] = {**o, "archived": run_at.isoformat(timespec="seconds")}
            moved += 1
    return active, archive, moved


def prune_market(market, run_at, keep_days=KEEP_DAYS):
    """Baza rynkowa trzyma tylko oferty z ostatnich keep_days dni (stare ceny zaburzałyby medianę)."""
    cutoff = run_at - timedelta(days=keep_days)
    for k in [k for k, v in market.items() if datetime.fromisoformat(v["last_seen"]) < cutoff]:
        del market[k]
    return market


def update_health(health, status, run_at):
    """Aktualizuje stan źródeł na podstawie wpisów status["sources"]. health: {źródło: {...}}."""
    run_iso = run_at.isoformat(timespec="seconds")
    per_source = {}
    for e in status.get("sources", []):
        src = e.get("source")
        if not src:
            continue
        agg = per_source.setdefault(src, {"found": 0, "errors": []})
        agg["found"] += e.get("found", 0)
        if e.get("error"):
            agg["errors"].append(e["error"])
    for src, agg in per_source.items():
        h = health.setdefault(src, {"last_ok": None})
        h["last_run"] = run_iso
        h["last_found"] = agg["found"]
        h["last_error"] = agg["errors"][0] if agg["errors"] and not agg["found"] else None
        if agg["found"]:
            h["last_ok"] = run_iso
    return health


def health_rows(health, run_at):
    """Wiersze tabeli „Stan źródeł” dla strony."""
    rows = []
    for src in sorted(health, key=lambda s: list(SOURCE_NAMES).index(s) if s in SOURCE_NAMES else 99):
        h = health[src]
        last_ok = h.get("last_ok")
        stale = not last_ok or run_at - datetime.fromisoformat(last_ok) > timedelta(hours=STALE_HOURS)
        rows.append({"source": src, "name": SOURCE_NAMES.get(src, src), "last_ok": last_ok,
                     "last_found": h.get("last_found", 0), "last_error": h.get("last_error"), "stale": stale})
    return rows
