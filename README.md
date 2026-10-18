# Monitor ogłoszeń nieruchomości

Co 2 godziny sprawdza wyszukiwania na Otodom, OLX, Gratce i Morizonie, filtruje oferty według Twoich kryteriów i publikuje stronę z listą. Działa na GitHub Actions, bez serwera i bez AI.

Na stronie widać:
* nowe oferty od Twojej ostatniej wizyty,
* obniżki cen (z ceną pierwotną),
* cenę za m² i jej pozycję na tle pozostałych ofert,
* to samo mieszkanie wystawione na kilku portalach jako jedną pozycję,
* ostrzeżenie, gdy któryś portal przestał zwracać wyniki.

## Uruchomienie (ok. 10 minut)

1. Załóż konto na github.com i utwórz nowe repozytorium, np. `oferty`. Zaznacz **Public** (darmowe GitHub Pages działa tylko dla publicznych repozytoriów; Twoje wyszukiwania i lista ofert będą widoczne dla każdego, kto zna adres).
2. Wgraj wszystkie pliki z tego folderu (**Add file → Upload files**). Folder `.github` musi trafić do repozytorium razem z resztą. Najprościej przeciągnąć cały rozpakowany folder.
3. Otwórz `config.yaml` w repozytorium (ikona ołówka) i wklej swoje linki wyszukiwań oraz filtry. Instrukcja jest w komentarzach pliku.
4. **Settings → Pages**: Source „Deploy from a branch”, Branch `main`, folder `/docs`. Zapisz.
5. **Settings → Actions → General → Workflow permissions**: zaznacz „Read and write permissions”. Zapisz.
6. **Actions → Monitor ogłoszeń → Run workflow**. Pierwszy przebieg trwa 1 do 3 minut.
7. Strona będzie pod adresem `https://TWOJ-LOGIN.github.io/oferty/`.

## Zmiany

* **Inne wyszukiwanie lub filtry:** edytujesz `config.yaml`, zmiana działa od następnego przebiegu.
* **Częstotliwość:** linia `cron` w `.github/workflows/monitor.yml`. `17 */2 * * *` oznacza co 2 godziny, `17 7-21 * * *` co godzinę w dzień (czas UTC, czyli 1 do 2 godzin mniej niż w Polsce). Częściej niż co godzinę nie warto, portale szybciej zaczną blokować.

## Gdy coś przestanie działać

Na górze strony pojawi się ostrzeżenie z nazwą portalu i opisem błędu. Najczęstsze przyczyny:
* **„Portal zablokował zapytanie (HTTP 403)” lub „ograniczył liczbę zapytań (HTTP 429)”:** portal odrzucił serwery GitHuba. Zmniejsz częstotliwość albo uruchamiaj skrypt na własnym komputerze: `pip install -r requirements.txt`, potem `python monitor.py`.
* **„Nie znaleziono ogłoszeń”:** portal zmienił wygląd strony. Wtedy trzeba poprawić parser w `monitor.py` (opis niżej).

Logi każdego przebiegu są w zakładce **Actions**.

GitHub wyłącza zaplanowane zadania w repozytoriach bez aktywności przez 60 dni. Wyśle wtedy maila, a ponowne włączenie to jedno kliknięcie w zakładce Actions.

## Jak monitor czyta portale

Każdy parser najpierw szuka danych zapisanych przez portal w samej stronie, a dopiero gdy ich brak, czyta HTML.

| Portal | Dane strukturalne | Zapas |
|---|---|---|
| Otodom | `__NEXT_DATA__`, lista `searchAds.items` | HTML kart ogłoszeń |
| OLX | `window.__PRERENDERED_STATE__`, lista `listing.ads` | HTML kart ogłoszeń |
| Gratka | `__NUXT_DATA__` (format devalue), potem JSON-LD | HTML kart ogłoszeń |
| Morizon | jak Gratka (ta sama platforma) | HTML kart ogłoszeń |

Z każdej oferty zapisywane są: cena, metraż, liczba pokoi, miasto, dzielnica, lokalizacja do wyświetlenia, miniatura zdjęcia i link.

Uwagi o portalach (stan z października 2026):
* Otodom i OLX odrzucają zwykłe programy pobierające (HTTP 403 z CloudFront). Przepuszczają zapytania z biblioteki `curl_cffi`, która udaje przeglądarkę Chrome. Dlatego ta biblioteka jest wymagana.
* Otodom dokleja do listy kopię jednej z ofert z zastępczym numerem i bez zdjęć. Parser ją pomija.
* OLX pokazuje ogłoszenia promowane dwa razy na tej samej stronie. Parser liczy je raz.
* Gratka podaje metraż zaokrąglony do pełnych metrów. Parser odtwarza dokładny metraż z ceny i ceny za m².
* Gratka i Morizon w dużej części pokazują te same ogłoszenia. Na stronie są scalane w jedną pozycję.

Czy portale wpuszczają serwery GitHuba, pokazuje zadanie „dostep-do-portali” w workflow **Testy** (zakładka Actions).

## Testy

```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

* `tests/test_parsers.py` sprawdza parsery na prawdziwych stronach wyników zapisanych w `tests/fixtures/`.
* `tests/test_run.py` sprawdza cały przebieg z podmienionym pobieraniem: dwa uruchomienia z różnymi cenami, wykrycie obniżki, scalanie duplikatów, awarię jednego portalu.

Testy uruchamiają się same na GitHubie przy każdej zmianie kodu (workflow **Testy**).

### Gdy portal zmieni wygląd

1. `python tools/check_portals.py` pokaże, który portal przestał zwracać oferty.
2. `python tools/refresh_fixtures.py` pobierze świeże strony do `tests/fixtures/`. Strony są przycinane i pozbawiane danych kontaktowych.
3. `python -m pytest` pokaże, które pola parser przestał odczytywać. Popraw parser w `monitor.py` i zaktualizuj oczekiwane wartości w testach.
