"""Cały przebieg monitora z podmienioną funkcją pobierania stron."""
import json
from datetime import datetime, timedelta, timezone

import monitor
import page
from conftest import load_fixture

URLS = {
    "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/mazowieckie/warszawa/warszawa/warszawa?by=LATEST": "otodom_mieszkania",
    "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/warszawa/": "olx_mieszkania",
    "https://gratka.pl/nieruchomosci/mieszkania/warszawa?sort=newest": "gratka_mieszkania",
    "https://www.morizon.pl/mieszkania/warszawa/": "morizon_mieszkania",
}
CONFIG = {
    "strony": 1,
    "ukryj_po_dniach": 3,
    "wyszukiwania": [{"nazwa": "Mieszkania", "adresy": list(URLS), "filtry": {"cena_max": 800000}}],
}
T1 = datetime(2026, 10, 18, 20, 17, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=2)


def otodom_with_price(offer_id, new_price):
    """Fixture Otodomu, w której jedna oferta ma inną cenę."""
    html = load_fixture("otodom_mieszkania")
    data = monitor.next_data(html)
    for item in data["props"]["pageProps"]["data"]["searchAds"]["items"]:
        if str(item["id"]) == offer_id:
            item["totalPrice"]["value"] = new_price
    start = html.index('<script id="__NEXT_DATA__"')
    end = html.index("</script>", start)
    return (html[:start] + '<script id="__NEXT_DATA__" type="application/json">'
            + json.dumps(data, ensure_ascii=False) + html[end:])


def make_fetch(overrides=None, failing=()):
    calls = []

    def fake_fetch(url):
        calls.append(url)
        if url in failing:
            raise monitor.FetchError("Portal zablokował zapytanie (HTTP 403).")
        name = URLS[url]
        return (overrides or {}).get(name) or load_fixture(name)

    fake_fetch.calls = calls
    return fake_fetch


def no_sleep():
    pass


def two_runs():
    offers, status1 = monitor.run(CONFIG, {}, T1, fetch_fn=make_fetch(), sleep=no_sleep)
    cheaper = {"otodom_mieszkania": otodom_with_price("68540364", 650000)}
    offers, status2 = monitor.run(CONFIG, offers, T2, fetch_fn=make_fetch(cheaper), sleep=no_sleep)
    return offers, status1, status2


def test_pierwszy_przebieg_zapisuje_oferty_ze_wszystkich_portali():
    offers, status = monitor.run(CONFIG, {}, T1, fetch_fn=make_fetch(), sleep=no_sleep)
    assert [s["error"] for s in status["sources"]] == [None] * 4
    assert {o["source"] for o in offers.values()} == {"otodom", "olx", "gratka", "morizon"}
    assert status["new"] == len(offers) == sum(s["matched"] for s in status["sources"])
    assert status["drops"] == 0
    assert all(o["price"] <= 800000 for o in offers.values()), "filtr cena_max działa"
    o = offers["otodom:68540364"]
    assert o["first_seen"] == o["last_seen"] == "2026-10-18T20:17:00+00:00"
    assert o["price_history"] == [{"date": "2026-10-18T20:17:00+00:00", "price": 720000.0}]
    assert o["city"] == "Warszawa" and o["district"] == "Wola"


def test_drugi_przebieg_wykrywa_obnizke_ceny():
    offers, _, status2 = two_runs()
    assert status2["new"] == 0
    assert status2["drops"] == 1
    o = offers["otodom:68540364"]
    assert o["price"] == 650000
    assert [h["price"] for h in o["price_history"]] == [720000, 650000]
    assert o["first_seen"] == "2026-10-18T20:17:00+00:00"
    assert o["last_seen"] == "2026-10-18T22:17:00+00:00"


def test_strona_pokazuje_obnizke_i_scala_duplikaty():
    offers, _, status2 = two_runs()
    items = page.build_items(CONFIG, offers, status2)
    otodom = next(i for i in items if i["url"].endswith("ID4DAtK"))
    assert otodom["price"] == 650000 and otodom["first_price"] == 720000

    # to samo mieszkanie na Gratce i Morizonie to jedna pozycja z odnośnikiem do drugiego portalu
    bemowo = [i for i in items if i["price"] == 795000 and round(i["area"]) == 68]
    assert len(bemowo) == 1
    sources = {bemowo[0]["source"]} | {x["source"] for x in bemowo[0]["others"]}
    assert sources == {"gratka", "morizon"}

    # oferta z Otodomu i OLX z tą samą ceną i metrażem też jest scalona
    mokotow = [i for i in items if i["price"] == 780000 and i["area"] == 47.32]
    assert len(mokotow) == 1
    assert {mokotow[0]["source"]} | {x["source"] for x in mokotow[0]["others"]} >= {"otodom", "olx"}

    assert len(items) < len(offers)


def test_awaria_jednego_zrodla_nie_zatrzymuje_pozostalych():
    failing = {next(u for u, n in URLS.items() if n == "olx_mieszkania")}
    offers, status = monitor.run(CONFIG, {}, T1, fetch_fn=make_fetch(failing=failing), sleep=no_sleep)
    errors = {s["source"]: s["error"] for s in status["sources"]}
    assert errors["olx"] == "Portal zablokował zapytanie (HTTP 403)."
    assert errors["otodom"] is None and errors["gratka"] is None and errors["morizon"] is None
    assert {o["source"] for o in offers.values()} == {"otodom", "gratka", "morizon"}


def test_pusta_strona_jest_zglaszana_jako_blad():
    blank = {"gratka_mieszkania": "<html><body>Przepraszamy, spróbuj później</body></html>"}
    _, status = monitor.run(CONFIG, {}, T1, fetch_fn=make_fetch(blank), sleep=no_sleep)
    gratka = next(s for s in status["sources"] if s["source"] == "gratka")
    assert gratka["found"] == 0 and "Nie znaleziono ogłoszeń" in gratka["error"]


def test_nieobslugiwany_portal():
    config = {"wyszukiwania": [{"nazwa": "X", "adresy": ["https://example.com/szukaj"]}]}
    _, status = monitor.run(config, {}, T1, fetch_fn=make_fetch(), sleep=no_sleep)
    assert status["sources"][0]["error"].startswith("Nieobsługiwany portal")


def test_przerwy_miedzy_zapytaniami():
    pauses = []
    fetch = make_fetch()
    monitor.run(CONFIG, {}, T1, fetch_fn=fetch, sleep=lambda: pauses.append(1))
    assert len(fetch.calls) == 4
    assert len(pauses) == 3, "przerwa przed każdym zapytaniem oprócz pierwszego"


def test_stare_oferty_sa_usuwane_z_historii():
    offers, _ = monitor.run(CONFIG, {}, T1, fetch_fn=make_fetch(), sleep=no_sleep)
    later = T1 + timedelta(days=monitor.KEEP_DAYS + 1)
    offers, _ = monitor.run({"wyszukiwania": []}, offers, later, fetch_fn=make_fetch(), sleep=no_sleep)
    assert offers == {}


def test_main_zapisuje_pliki_i_strone(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(json.dumps(CONFIG), encoding="utf-8")
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "OFFERS_FILE", tmp_path / "data" / "offers.json")
    monkeypatch.setattr(monitor, "STATUS_FILE", tmp_path / "data" / "status.json")
    monkeypatch.setattr(page, "OUTPUT", tmp_path / "docs" / "index.html")
    monkeypatch.setattr(page, "ROOT", tmp_path)
    monkeypatch.setattr(monitor, "fetch", make_fetch())
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)

    monitor.main()

    offers = json.loads((tmp_path / "data" / "offers.json").read_text(encoding="utf-8"))
    status = json.loads((tmp_path / "data" / "status.json").read_text(encoding="utf-8"))
    html = (tmp_path / "docs" / "index.html").read_text(encoding="utf-8")
    assert len(offers) > 100
    assert len(status["sources"]) == 4
    assert "/*__DATA__*/null" not in html
    assert "Metro Płocka" in html
