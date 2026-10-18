"""Obszar 6: archiwum starych ofert i raport zdrowia źródeł."""
from datetime import datetime, timedelta, timezone

import maintenance

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)


def iso(dt):
    return dt.isoformat(timespec="seconds")


def test_archiwum_ofert_starszych_niz_60_dni():
    offers = {"a": {"last_seen": iso(NOW - timedelta(days=61))}, "b": {"last_seen": iso(NOW - timedelta(days=59))}}
    archive = {"stara": {"last_seen": "2026-01-01T00:00:00+00:00"}}
    active, archive, moved = maintenance.rotate(offers, archive, NOW)
    assert list(active) == ["b"] and moved == 1
    assert sorted(archive) == ["a", "stara"]
    assert archive["a"]["archived"] == iso(NOW)


def test_baza_rynkowa_bez_starych_cen():
    market = {"a": {"last_seen": iso(NOW - timedelta(days=61))}, "b": {"last_seen": iso(NOW)}}
    assert list(maintenance.prune_market(market, NOW)) == ["b"]


def test_zdrowie_zrodel():
    status = {"sources": [
        {"source": "otodom", "found": 36, "error": None},
        {"source": "otodom", "found": 0, "error": "Portal zablokował zapytanie (HTTP 403)."},
        {"source": "olx", "found": 0, "error": "Portal zablokował zapytanie (HTTP 403)."},
    ]}
    health = {"olx": {"last_ok": iso(NOW - timedelta(days=2))}}
    health = maintenance.update_health(health, status, NOW)
    assert health["otodom"] == {"last_ok": iso(NOW), "last_run": iso(NOW), "last_found": 36, "last_error": None}
    assert health["olx"]["last_ok"] == iso(NOW - timedelta(days=2))
    assert health["olx"]["last_error"] == "Portal zablokował zapytanie (HTTP 403)."

    rows = {r["source"]: r for r in maintenance.health_rows(health, NOW)}
    assert rows["otodom"]["stale"] is False and rows["otodom"]["name"] == "Otodom"
    assert rows["olx"]["stale"] is True, "brak wyników od ponad doby"
    assert [r["source"] for r in maintenance.health_rows(health, NOW)] == ["otodom", "olx"]


def test_zrodlo_ktore_nigdy_nie_dzialalo():
    health = maintenance.update_health({}, {"sources": [{"source": "gratka", "found": 0, "error": "x"}]}, NOW)
    row = maintenance.health_rows(health, NOW)[0]
    assert row["last_ok"] is None and row["stale"] is True
