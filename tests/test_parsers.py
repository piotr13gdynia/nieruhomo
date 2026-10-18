"""Parsery na prawdziwych stronach wyników zapisanych w tests/fixtures/."""
import re

import pytest

import monitor
from conftest import BASES, load_fixture

FIXTURE_NAMES = ["otodom_mieszkania", "otodom_domy", "olx_mieszkania", "gratka_mieszkania", "morizon_mieszkania"]


def parse(name):
    source = name.split("_")[0]
    return monitor.parse_page(source, load_fixture(name), BASES[source])


def by_id(offers, native_id):
    matches = [o for o in offers if o["native_id"] == native_id]
    assert len(matches) == 1, f"oferta {native_id} powinna wystąpić dokładnie raz"
    return matches[0]


# ---------- wspólne wymagania dla wszystkich portali ----------

@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_pelna_strona_wynikow(name):
    offers = parse(name)
    assert len(offers) >= 30, "pierwsza strona wyników ma co najmniej 30 ofert"
    ids = [o["native_id"] for o in offers]
    assert len(ids) == len(set(ids)), "żadna oferta nie może się powtarzać"


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_kazda_oferta_ma_podstawowe_pola(name):
    source = name.split("_")[0]
    for o in parse(name):
        assert o["url"].startswith("https://"), o
        assert monitor.OFFER_LINK[source].search(o["url"]), o["url"]
        assert monitor.detect_source(o["url"]) == source
        assert o["title"], o
        assert o["price"] and 50_000 <= o["price"] <= 20_000_000, o
        assert o["area"] and 10 <= o["area"] <= 2_000, o
        assert o["location"], o


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_pokoje_zdjecia_i_lokalizacja_w_wiekszosci_ofert(name):
    offers = parse(name)
    n = len(offers)
    assert sum(1 for o in offers if o["rooms"]) >= 0.9 * n
    assert sum(1 for o in offers if o["image"] and o["image"].startswith("https://")) >= 0.9 * n
    assert sum(1 for o in offers if o["city"]) >= 0.9 * n
    assert sum(1 for o in offers if o["district"]) >= 0.7 * n


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_cena_za_m2_w_rozsadnym_zakresie(name):
    for o in parse(name):
        assert 1_000 <= o["price"] / o["area"] <= 60_000, o


# ---------- Otodom ----------

def test_otodom_konkretna_oferta():
    o = by_id(parse("otodom_mieszkania"), "68540364")
    assert o["url"] == "https://www.otodom.pl/pl/oferta/metro-plocka-41-m-w-powojennej-kamienicy-z-winda-na-woli-ID4DAtK"
    assert o["title"] == "Metro Płocka | 41 m² w powojennej kamienicy z WINDĄ na Woli"
    assert o["price"] == 720000
    assert o["area"] == 41.2
    assert o["rooms"] == 2
    assert o["city"] == "Warszawa"
    assert o["district"] == "Wola"
    assert o["location"] == "ul. Wolska, Odolany, Wola, Warszawa"
    assert o["image"].startswith("https://ireland.apollo.olxcdn.com/") and "s=655x491" in o["image"]


def test_otodom_pomija_kopie_z_zastepczym_id():
    # Otodom dokleja na końcu listy kopię pierwszej oferty z id 9000062234 i bez zdjęć
    for name in ("otodom_mieszkania", "otodom_domy"):
        offers = parse(name)
        assert "9000062234" not in {o["native_id"] for o in offers}
        assert len(offers) == 36


def test_otodom_miejscowosc_poza_warszawa():
    o = by_id(parse("otodom_domy"), "68537729")
    assert o["city"] == "Mysiadło"
    assert o["district"] == ""
    assert o["price"] == 1330000 and o["area"] == 140 and o["rooms"] == 4


def test_otodom_slowne_liczby_pokoi():
    assert monitor.rooms_num("EIGHT") == 8
    assert monitor.rooms_num("MORE") == 10
    o = by_id(parse("otodom_domy"), "68540137")
    assert o["rooms"] == 8


def test_otodom_ukryta_cena():
    html = load_fixture("otodom_mieszkania").replace('"hidePrice": false', '"hidePrice": true', 1)
    offers = monitor.parse_otodom(html, BASES["otodom"])
    assert offers[0]["price"] is None


def test_otodom_zapasowe_przeszukanie_json_po_zmianie_struktury():
    html = load_fixture("otodom_mieszkania").replace('"searchAds"', '"wynikiWyszukiwania"', 1)
    offers = monitor.parse_otodom(html, BASES["otodom"])
    assert len(offers) == 36
    assert by_id(offers, "68540364")["price"] == 720000


# ---------- OLX ----------

def test_olx_konkretna_oferta():
    o = by_id(parse("olx_mieszkania"), "1064168917")
    assert o["url"] == "https://www.olx.pl/d/oferta/dostepne-od-zaraz-zamkniete-osiedle-cicho-CID3-ID1a18VN.html"
    assert o["price"] == 688640
    assert o["area"] == 53.8
    assert o["rooms"] == 2
    assert (o["city"], o["district"]) == ("Warszawa", "Białołęka")
    assert o["location"] == "Białołęka, Warszawa"


def test_olx_miniatura_zamiast_zdjecia_w_pelnej_rozdzielczosci():
    for o in parse("olx_mieszkania"):
        assert ";s=516x361" in o["image"], o["image"]


def test_olx_promowane_ogloszenia_bez_powtorzen():
    html = load_fixture("olx_mieszkania")
    assert html.count("ID1bEbiS.html") >= 2, "w fixture to ogłoszenie występuje dwa razy"
    offers = parse("olx_mieszkania")
    assert sum(1 for o in offers if o["url"].endswith("ID1bEbiS.html")) == 1


# ---------- Gratka i Morizon ----------

def test_gratka_konkretna_oferta():
    o = by_id(parse("gratka_mieszkania"), "49336685")
    assert o["url"] == "https://gratka.pl/nieruchomosci/mieszkanie-warszawa-bemowo-doroszewskiego/ob/49336685"
    assert o["price"] == 795000
    assert o["area"] == 68.2, "dokładny metraż odtworzony z ceny za m²"
    assert o["rooms"] == 4
    assert (o["city"], o["district"]) == ("Warszawa", "Bemowo")
    assert o["image"].startswith("https://img1.staticmorizon.com.pl/thumb/")


def test_gratka_ulica_i_osiedle_w_lokalizacji():
    o = by_id(parse("gratka_mieszkania"), "49335953")
    assert o["location"] == "Aluzyjna, Tarchomin, Białołęka, Warszawa"


def test_gratka_oferta_promowana_na_gorze_listy():
    o = by_id(parse("gratka_mieszkania"), "48084247")
    assert o["title"].startswith("Mieszkanie z balkonem na Woli")
    assert o["district"] == "Wola"


def test_morizon_konkretna_oferta():
    o = by_id(parse("morizon_mieszkania"), "mzn2048236679")
    assert o["url"] == "https://www.morizon.pl/oferta/sprzedaz-mieszkanie-warszawa-bemowo-doroszewskiego-68m2-mzn2048236679"
    assert o["price"] == 795000
    assert o["area"] == 68.2
    assert o["rooms"] == 4
    assert o["district"] == "Bemowo"


def test_metraz_zgodny_z_json_ld():
    """Metraż wyliczony z ceny za m² ma się zgadzać z floorSize z JSON-LD tej samej strony."""
    for name in ("gratka_mieszkania", "morizon_mieszkania"):
        source = name.split("_")[0]
        html = load_fixture(name)
        main = {o["url"]: o for o in monitor.parse_nuxt_portal(html, BASES[source], source)}
        ld = monitor.parse_ld_json(html, BASES[source], source)
        assert len(ld) >= 30
        compared = 0
        for o in ld:
            if o["url"] in main and o["area"]:
                assert abs(main[o["url"]]["area"] - o["area"]) <= 0.1, o["url"]
                compared += 1
        assert compared >= 30


@pytest.mark.parametrize("name", ["gratka_mieszkania", "morizon_mieszkania"])
def test_zapas_json_ld_gdy_brak_danych_nuxt(name):
    source = name.split("_")[0]
    html = re.sub(r'<script[^>]*id="__NUXT_DATA__".*?</script>', "", load_fixture(name), flags=re.S)
    offers = monitor.parse_page(source, html, BASES[source])
    assert len(offers) >= 30
    assert all(o["price"] and o["area"] and o["rooms"] for o in offers)


@pytest.mark.parametrize("name", ["gratka_mieszkania", "morizon_mieszkania"])
def test_zapas_html_gdy_brak_danych_strukturalnych(name):
    source = name.split("_")[0]
    html = re.sub(r"<script\b.*?</script>", "", load_fixture(name), flags=re.S)
    offers = monitor.parse_page(source, html, BASES[source])
    assert len(offers) >= 30
    assert sum(1 for o in offers if o["price"] and o["area"]) >= 30


def test_ta_sama_oferta_na_gratce_i_morizonie_ma_te_same_dane():
    g = by_id(parse("gratka_mieszkania"), "49336685")
    m = by_id(parse("morizon_mieszkania"), "mzn2048236679")
    assert (g["price"], g["area"], g["rooms"]) == (m["price"], m["area"], m["rooms"])


# ---------- pomocnicze ----------

def test_nuxt_data_dekoduje_format_devalue():
    html = ('<script type="application/json" id="__NUXT_DATA__">'
            '[["ShallowReactive",1],{"a":2,"b":3,"c":-1},[4,4],"tekst",{"x":5},7]</script>')
    assert monitor.nuxt_data(html) == {"a": [{"x": 7}, {"x": 7}], "b": "tekst", "c": None}


def test_brak_danych_strukturalnych_i_ofert():
    assert monitor.parse_page("otodom", "<html><body>Strona bez ofert</body></html>", BASES["otodom"]) == []


def test_to_num():
    assert monitor.to_num("688 640 zł") == 688640
    assert monitor.to_num("53,80 m²") == 53.8
    assert monitor.to_num("795000.00") == 795000
    assert monitor.to_num(None) is None


def test_with_page():
    assert monitor.with_page("https://x.pl/a?b=1", 1) == "https://x.pl/a?b=1"
    assert monitor.with_page("https://x.pl/a?b=1&page=3", 2) == "https://x.pl/a?b=1&page=2"
