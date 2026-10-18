"""Obszar 3: mediany ceny za m² i oznaczanie okazji."""
import pytest

import deals
import monitor

CONFIG = {"okazje": {"prog_procent": 10, "min_ofert_w_grupie": 3, "przedzialy_metrazu": [35, 50, 70, 100]}}


def offer(price, area, district="Wola", kind="mieszkanie", city="Warszawa"):
    return {"price": price, "area": area, "district": district, "kind": kind, "city": city}


def market_of(*entries):
    return {f"otodom:{i}": e for i, e in enumerate(entries)}


@pytest.mark.parametrize("area,expected", [
    (20, "do 35 m²"), (35, "35-50 m²"), (49.9, "35-50 m²"), (50, "50-70 m²"), (99, "70-100 m²"),
    (100, "100+ m²"), (None, None),
])
def test_przedzialy_metrazu(area, expected):
    assert deals.area_band(area, [35, 50, 70, 100]) == expected


def test_mediana_tylko_dla_grup_z_minimalna_liczba_ofert():
    market = market_of(offer(400000, 40), offer(500000, 40), offer(600000, 40),  # Wola: 3 oferty
                       offer(500000, 40, district="Ursus"), offer(500000, 40, district="Ursus"))  # Ursus: 2
    med = deals.medians(market, CONFIG)
    assert med == {("warszawa", "wola", "mieszkanie", "35-50 m²"): (12500.0, 3)}


def test_ta_sama_oferta_z_kilku_portali_liczy_sie_raz():
    same = offer(500000, 40)
    market = {"otodom:1": dict(same), "olx:2": dict(same), "gratka:3": dict(same),
              "otodom:4": offer(400000, 40), "otodom:5": offer(600000, 40)}
    m, n = deals.medians(market, CONFIG)[("warszawa", "wola", "mieszkanie", "35-50 m²")]
    assert n == 3 and m == 12500


def test_okazja_ponizej_progu():
    med = {("warszawa", "wola", "mieszkanie", "35-50 m²"): (12500.0, 20)}
    o = deals.assess(offer(440000, 40), med, CONFIG)  # 11000 zł/m², 12% poniżej mediany
    assert o["deal"] is True
    assert o["deviation"] == -0.12
    assert o["median_ppm"] == 12500 and o["group_size"] == 20
    assert o["group"] == "mieszkanie, Wola, Warszawa, 35-50 m²"
    assert "suspicious" not in o


def test_brak_okazji_powyzej_progu():
    med = {("warszawa", "wola", "mieszkanie", "35-50 m²"): (12500.0, 20)}
    o = deals.assess(offer(460000, 40), med, CONFIG)  # 8% poniżej
    assert o["deal"] is False and o["deviation"] == -0.08


def test_brak_oceny_gdy_grupa_za_mala():
    o = deals.assess(offer(300000, 40, district="Ursus"), {}, CONFIG)
    assert "deal" not in o and "deviation" not in o


def test_podejrzanie_duza_roznica():
    med = {("warszawa", "wola", "mieszkanie", "35-50 m²"): (12500.0, 20)}
    o = deals.assess(offer(250000, 40), med, CONFIG)
    assert o["deal"] and o["suspicious"]


def test_ponowna_ocena_usuwa_stare_pola():
    med = {("warszawa", "wola", "mieszkanie", "35-50 m²"): (12500.0, 20)}
    o = deals.assess(offer(250000, 40), med, CONFIG)
    deals.assess(o, {}, CONFIG)
    assert not any(k in o for k in ("deal", "deviation", "median_ppm", "suspicious"))


def test_prog_z_konfiguracji():
    med = {("warszawa", "wola", "mieszkanie", "35-50 m²"): (12500.0, 20)}
    cfg = {"okazje": {"prog_procent": 5}}
    assert deals.assess(offer(460000, 40), med, cfg)["deal"] is True


def test_domyslne_ustawienia():
    s = deals.settings({})
    assert s["prog_procent"] == 10 and s["min_ofert_w_grupie"] == 15


def test_baza_rynkowa_aktualizuje_istniejace_wpisy():
    market = {}
    deals.update_market(market, "otodom", [{"native_id": "1", **offer(500000, 40)}], "2026-10-18T10:00:00+00:00")
    deals.update_market(market, "otodom", [{"native_id": "1", **offer(480000, 40)}], "2026-10-18T12:00:00+00:00")
    deals.update_market(market, "otodom", [{"native_id": "2", "price": None, "area": 40}], "2026-10-18T12:00:00+00:00")
    assert list(market) == ["otodom:1"]
    assert market["otodom:1"]["price"] == 480000
    assert market["otodom:1"]["first_seen"] == "2026-10-18T10:00:00+00:00"
    assert market["otodom:1"]["last_seen"] == "2026-10-18T12:00:00+00:00"


@pytest.mark.parametrize("title,ok", [
    ("Mieszkanie 2 pokoje", True), ("Udział 1/2 w mieszkaniu", False), ("Licytacja komornicza", False),
    ("Mieszkanie TBS", False),
])
def test_baza_rynkowa_pomija_udzialy_i_licytacje(title, ok):
    assert monitor.market_ok({"title": title, "price": 500000, "area": 40}) is ok


def test_baza_rynkowa_pomija_absurdalne_ceny():
    assert monitor.market_ok({"title": "x", "price": 1000, "area": 40}) is False
    assert monitor.market_ok({"title": "x", "price": 500000, "area": None}) is False
