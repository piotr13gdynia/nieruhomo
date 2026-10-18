"""Obszar 5: licytacje komornicze z licytacje.komornik.pl (odpowiedź API zapisana w fixtures)."""
import json
from datetime import datetime, timezone

import auctions
from conftest import FIXTURES

RESPONSE = json.loads((FIXTURES / "licytacje_mazowieckie.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 18, 22, 0, tzinfo=timezone.utc)


def fake_post(pages):
    """Podmieniony POST: kolejne strony wyników według offsetu."""
    calls = []

    def post(url, body):
        calls.append(body)
        assert url == auctions.SEARCH_URL
        start = body["offset"]
        items = pages[start:start + body["limit"]]
        return {"count": len(pages), "items": items}

    post.calls = calls
    return post


def test_zapytanie_do_portalu():
    q = auctions.build_query(auctions.settings({"licytacje": {"wojewodztwo": "Mazowieckie"}}), offset=50)
    assert q["orderByField"] == "dateCreated" and q["orderBy"] == "DESC"
    assert q["offset"] == 50 and q["limit"] == auctions.PAGE_SIZE
    assert {"field": "province", "value": ["mazowieckie"]} in q["termFilters"]
    assert {"field": "joinable", "value": ["true"]} in q["termFilters"]


def test_odczyt_licytacji():
    found = {a["id"]: a for a in auctions.parse_response(RESPONSE)}
    assert len(found) == 30
    a = found["85752"]
    assert a["title"] == "Lokal mieszkalny 64,20m2 04-372 Warszawa, ul. Kobielska 82/9"
    assert a["url"] == "https://licytacje.komornik.pl/licytacje/85752"
    assert (a["price"], a["estimate"]) == (697500.0, 930000.0)
    assert a["date"] == "2026-10-21T12:00:00+02:00"
    assert a["address"] == "ul. Kobielska 82/9, 04-372 Warszawa"
    assert (a["city"], a["category"], a["electronic"], a["share"]) == ("Warszawa", "mieszkanie", True, False)


def test_udzial_jest_rozpoznawany():
    found = {a["id"]: a for a in auctions.parse_response(RESPONSE)}
    assert found["76120"]["share"] is True
    assert found["76120"]["category"] == "dom"


def test_filtry_miasta_rodzaju_ceny_i_udzialow():
    cfg = {"licytacje": {"miasto": "Warszawa", "kategorie": ["mieszkanie"], "cena_max": 1000000,
                         "pomin_udzialy": True}}
    stored, entry = auctions.update(cfg, {}, NOW, post=fake_post(RESPONSE["items"]))
    assert entry["error"] is None and entry["found"] == 30
    assert sorted(stored) == ["85537", "85752"], "mieszkania w Warszawie do 1 mln zł bez udziałów"


def test_pierwszy_przebieg_pobiera_kilka_stron_kolejne_jedna():
    items = [dict(RESPONSE["items"][i % 30], id=1000 + i) for i in range(120)]
    post = fake_post(items)
    stored, _ = auctions.update({}, {}, NOW, post=post)
    assert [c["offset"] for c in post.calls] == [0, 50, 100]
    assert len(stored) == 120

    post = fake_post(items)
    auctions.update({}, stored, NOW, post=post)
    assert [c["offset"] for c in post.calls] == [0], "znane licytacje: wystarczy pierwsza strona"


def test_nowe_licytacje_i_zachowanie_daty_pierwszego_pojawienia():
    stored, _ = auctions.update({}, {}, NOW, post=fake_post(RESPONSE["items"]))
    later = datetime(2026, 10, 19, 0, 0, tzinfo=timezone.utc)
    stored, _ = auctions.update({}, stored, later, post=fake_post(RESPONSE["items"]))
    assert stored["85752"]["first_seen"] == NOW.isoformat(timespec="seconds")
    page = auctions.for_page(stored, later)
    assert not any(a["is_new"] for a in page)


def test_minione_licytacje_znikaja_a_lista_jest_od_najblizszej():
    stored, _ = auctions.update({}, {}, NOW, post=fake_post(RESPONSE["items"]))
    page = auctions.for_page(stored, NOW)
    dates = [a["date"] for a in page]
    assert dates == sorted(dates)
    after = datetime(2026, 12, 31, tzinfo=timezone.utc)
    stored, _ = auctions.update({}, stored, after, post=fake_post([]))
    assert stored == {}


def test_blad_portalu_nie_przerywa_monitora():
    def broken(url, body):
        raise RuntimeError("HTTP 503")
    old = {"1": {"id": "1", "date": "2026-11-01T10:00:00+01:00"}}
    stored, entry = auctions.update({}, old, NOW, post=broken)
    assert stored == old
    assert entry["error"] == "Nie udało się pobrać licytacji: HTTP 503"


def test_wylaczone_licytacje():
    assert auctions.update({"licytacje": {"wlaczone": False}}, {}, NOW, post=fake_post([])) == (None, None)


def test_nieoczekiwany_format_odpowiedzi():
    stored, entry = auctions.update({}, {}, NOW, post=lambda url, body: {"error": "x"})
    assert "nieoczekiwany format" in entry["error"]
