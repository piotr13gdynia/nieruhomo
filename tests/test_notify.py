"""Obszar 4: zbiorcze powiadomienia o nowych okazjach i obniżkach."""
import json

import pytest

import notify

OFFERS = {
    "otodom:1": {"title": "Mieszkanie na Woli", "url": "https://www.otodom.pl/pl/oferta/a-ID1", "price": 600000,
                 "area": 41.2, "rooms": 2, "location": "Odolany, Wola, Warszawa", "deal": True, "deviation": -0.18},
    "olx:2": {"title": "Kawalerka <Mokotów>", "url": "https://www.olx.pl/d/oferta/b-ID2.html", "price": 450000,
              "area": 28, "rooms": 1, "location": "Mokotów, Warszawa"},
    "gratka:3": {"title": "Mała obniżka", "url": "https://gratka.pl/x/ob/3", "price": 495000, "area": 50},
    "otodom:4": {"title": "Dom", "url": "https://www.otodom.pl/pl/oferta/d-ID4", "price": 300000, "area": 120,
                 "deal": True, "deviation": -0.45, "suspicious": True},
}
STATUS = {
    "new_deals": ["otodom:1", "otodom:4"],
    "drop_events": [
        {"key": "olx:2", "old": 500000, "new": 450000},       # 10%: powyżej progu
        {"key": "gratka:3", "old": 500000, "new": 495000},    # 1%: poniżej progu
        {"key": "otodom:1", "old": 650000, "new": 600000},    # już na liście okazji
    ],
}
CONFIG = {"powiadomienia": {"telegram": True, "email": True, "obnizka_procent": 5},
          "adres_strony": "https://example.github.io/oferty/"}
ENV = {"TELEGRAM_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "42", "SMTP_HOST": "smtp.example.com",
       "SMTP_PORT": "587", "SMTP_USER": "ja@example.com", "SMTP_PASSWORD": "haslo", "EMAIL_TO": "ja@example.com"}


class FakeSMTP:
    instances = []

    def __init__(self, host, port):
        self.host, self.port, self.sent, self.logged, self.closed = host, port, [], None, False
        FakeSMTP.instances.append(self)

    def login(self, user, password):
        self.logged = (user, password)

    def send_message(self, msg):
        self.sent.append(msg)

    def quit(self):
        self.closed = True


def test_wybor_zdarzen():
    deals, drops = notify.collect(OFFERS, STATUS, CONFIG)
    assert [d["title"] for d in deals] == ["Dom", "Mieszkanie na Woli"], "największe okazje najpierw"
    assert [(o["title"], round(p)) for o, _, p in drops] == [("Kawalerka <Mokotów>", 10)]


def test_tresc_wiadomosci():
    subject, text, rich = notify.build_message(OFFERS, STATUS, CONFIG, page_url=CONFIG["adres_strony"])
    assert subject == "Nieruchomości: 2 nowe okazje i 1 obniżka"
    assert "18% poniżej mediany: 600 000 zł, 41,2 m², 2 pok., Odolany, Wola, Warszawa" in text
    assert "45% poniżej mediany (sprawdź metraż i cenę)" in text
    assert "taniej o 10% (500 000 zł -> 450 000 zł)" in text
    assert "Mała obniżka" not in text
    assert "Wszystkie oferty: https://example.github.io/oferty/" in text
    assert "&lt;Mokotów&gt;" in rich, "znaki specjalne są bezpieczne dla HTML Telegrama"
    assert "—" not in text and "–" not in text


def test_brak_zdarzen_brak_wiadomosci():
    assert notify.build_message(OFFERS, {"new_deals": [], "drop_events": []}, CONFIG) is None
    assert notify.send_all(OFFERS, {"new_deals": [], "drop_events": []}, CONFIG, env=ENV) == {
        "info": "Brak nowych okazji i obniżek, nic nie wysłano."}


def test_powiadomienia_wylaczone():
    assert notify.send_all(OFFERS, STATUS, {"powiadomienia": {"telegram": False, "email": False}}, env=ENV) == {}
    assert notify.send_all(OFFERS, STATUS, {}, env=ENV) == {}


def test_wysylka_telegram_i_email():
    posted = []
    FakeSMTP.instances.clear()
    result = notify.send_all(OFFERS, STATUS, CONFIG, env=ENV, post=lambda url, body: posted.append((url, body)),
                             smtp_factory=FakeSMTP)
    assert result == {"telegram": "wysłano", "email": "wysłano"}
    url, body = posted[0]
    assert url == "https://api.telegram.org/bot123:abc/sendMessage"
    payload = json.loads(body)
    assert payload["chat_id"] == "42" and payload["parse_mode"] == "HTML"
    assert "Nowe okazje" in payload["text"]
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.logged, smtp.closed) == ("smtp.example.com", 587, ("ja@example.com", "haslo"), True)
    msg = smtp.sent[0]
    assert msg["Subject"] == "Nieruchomości: 2 nowe okazje i 1 obniżka" and msg["To"] == "ja@example.com"


def test_jedna_wiadomosc_na_przebieg():
    posted = []
    notify.send_all(OFFERS, STATUS, {"powiadomienia": {"telegram": True}}, env=ENV,
                    post=lambda url, body: posted.append(body))
    assert len(posted) == 1


def test_brak_sekretow_to_czytelny_blad_a_nie_awaria():
    result = notify.send_all(OFFERS, STATUS, CONFIG, env={}, post=lambda *a: None, smtp_factory=FakeSMTP)
    assert result["telegram"] == "Błąd: Brak sekretów TELEGRAM_TOKEN lub TELEGRAM_CHAT_ID."
    assert result["email"] == "Błąd: Brak sekretów SMTP_HOST lub EMAIL_TO."


def test_blad_jednego_kanalu_nie_blokuje_drugiego():
    def broken(url, body):
        raise RuntimeError("Telegram nie odpowiada")
    result = notify.send_all(OFFERS, STATUS, CONFIG, env=ENV, post=broken, smtp_factory=FakeSMTP)
    assert result == {"telegram": "Błąd: Telegram nie odpowiada", "email": "wysłano"}


def test_dluga_wiadomosc_jest_skracana():
    text = "\n".join(f"• oferta {i} " + "x" * 100 for i in range(100))
    short = notify.truncate(text)
    assert len(short) <= notify.TELEGRAM_LIMIT + 40
    assert short.endswith("i więcej na stronie")


@pytest.mark.parametrize("n,expected", [(1, "nowa okazja"), (3, "nowe okazje"), (5, "nowych okazji"),
                                        (12, "nowych okazji"), (22, "nowe okazje")])
def test_odmiana(n, expected):
    assert notify.plural(n, "nowa okazja", "nowe okazje", "nowych okazji") == expected
