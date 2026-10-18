"""Obszar 2: szczegóły ogłoszeń z list wyników i ze stron pojedynczych ofert."""
import pytest

import details
import monitor
from conftest import BASES, load_fixture


def listing(name):
    source = name.split("_")[0]
    return {o["native_id"]: o for o in monitor.parse_page(source, load_fixture(name), BASES[source])}


# ---------- strony pojedynczych ofert ----------

def test_otodom_oferta_biura():
    d = details.parse_details("otodom", load_fixture("otodom_oferta"))
    assert d == {"floor": 2, "floors_total": 5, "build_year": 1954, "market": "wtórny",
                 "advertiser": "biuro", "district": "Wola", "subdistrict": "Odolany"}


def test_otodom_oferta_prywatna():
    d = details.parse_details("otodom", load_fixture("otodom_oferta2"))
    assert d["advertiser"] == "prywatny"
    assert (d["floor"], d["floors_total"], d["build_year"]) == (2, 4, 1974)
    assert (d["district"], d["subdistrict"]) == ("Mokotów", "Służew")


@pytest.mark.parametrize("name,source", [("gratka_oferta", "gratka"), ("morizon_oferta", "morizon")])
def test_gratka_i_morizon(name, source):
    d = details.parse_details(source, load_fixture(name))
    assert d == {"floor": 0, "floors_total": 4, "build_year": 1979, "market": "wtórny",
                 "advertiser": "biuro", "district": "Bemowo"}


def test_strona_bez_danych():
    assert details.parse_details("otodom", "<html></html>") == {}
    assert details.parse_details("gratka", "<html></html>") == {}
    assert details.parse_details("olx", load_fixture("olx_mieszkania")) == {}


# ---------- pola z list wyników (bez dodatkowych zapytań) ----------

def test_otodom_lista_pietro_oferent_typ():
    o = listing("otodom_mieszkania")["68540364"]
    assert (o["floor"], o["advertiser"], o["kind"]) == (2, "biuro", "mieszkanie")
    assert listing("otodom_mieszkania")["68540202"]["advertiser"] == "prywatny"
    assert all(x["kind"] == "dom" for x in listing("otodom_domy").values())


def test_olx_lista_rynek_pietro_oferent():
    o = listing("olx_mieszkania")["1064168917"]
    assert (o["floor"], o["market"], o["advertiser"]) == (1, "wtórny", "prywatny")
    assert o["details_checked"] is True, "OLX nie wymaga pobierania stron ofert"
    assert listing("olx_mieszkania")["1089627349"]["market"] == "pierwotny"


def test_gratka_lista_deweloper_i_pietro():
    offers = listing("gratka_mieszkania")
    assert offers["49335145"]["advertiser"] == "deweloper", "biuro sprzedaży dewelopera (SALES_OFFICE)"
    assert (offers["49336629"]["floor"], offers["49336629"]["floors_total"]) == (5, 5)
    assert offers["49336685"]["floor"] == 0


def test_morizon_lista_wlasciciel_to_oferta_prywatna():
    assert listing("morizon_mieszkania")["mzn2047022140"]["advertiser"] == "prywatny"


# ---------- normalizacja ----------

@pytest.mark.parametrize("value,expected", [
    ("floor_2", 2), ("SECOND", 2), ("GROUND", 0), ("ground_floor", 0), ("floor_0", 0), ("CELLAR", -1),
    ("floor_higher_10", 11), ("HIGHER_10", 11), ("garret", None), (3, 3), (None, None), ("", None),
])
def test_normalize_floor(value, expected):
    assert details.normalize_floor(value) == expected


@pytest.mark.parametrize("text,expected", [
    ("parter/4", (0, 4)), ("piętro 5/10", (5, 10)), ("piętro 3", (3, None)), ("parter", (0, None)), (None, (None, None)),
])
def test_parse_floor_formatted(text, expected):
    assert details.parse_floor_formatted(text) == expected


@pytest.mark.parametrize("value,expected", [
    ("SECONDARY", "wtórny"), ("primary", "pierwotny"), ("Rynek wtórny", "wtórny"), ("Wtórny", "wtórny"), (None, None),
])
def test_normalize_market(value, expected):
    assert details.normalize_market(value) == expected


@pytest.mark.parametrize("value,expected", [
    ("private", "prywatny"), ("AGENCY", "biuro"), ("business", "biuro"), ("SALES_OFFICE", "deweloper"),
    ("DEVELOPER", "deweloper"), ("cos innego", None),
])
def test_normalize_advertiser(value, expected):
    assert details.normalize_advertiser(value) == expected


@pytest.mark.parametrize("text,expected", [
    ("https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/warszawa/", "mieszkanie"),
    ("https://www.otodom.pl/pl/wyniki/sprzedaz/dom/mazowieckie/warszawa", "dom"),
    ("/nieruchomosci/mieszkanie-warszawa-bemowo/ob/1", "mieszkanie"),
    ("https://www.olx.pl/nieruchomosci/domy/sprzedaz/", "dom"),
    ("https://www.otodom.pl/pl/wyniki/sprzedaz/dzialka/", "działka"),
    ("Domy", "dom"),
    ("", None),
])
def test_kind_from_text(text, expected):
    assert details.kind_from_text(text) == expected
