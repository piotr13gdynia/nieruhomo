from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

BASES = {
    "otodom": "https://www.otodom.pl/pl/wyniki/sprzedaz/mieszkanie/",
    "olx": "https://www.olx.pl/nieruchomosci/mieszkania/sprzedaz/warszawa/",
    "gratka": "https://gratka.pl/nieruchomosci/mieszkania/warszawa",
    "morizon": "https://www.morizon.pl/mieszkania/warszawa/",
}


def load_fixture(name):
    return (FIXTURES / f"{name}.html").read_text(encoding="utf-8")


@pytest.fixture
def fixture_html():
    return load_fixture
