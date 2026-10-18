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

Na górze strony pojawi się ostrzeżenie z nazwą portalu. Najczęstsze przyczyny:
* **HTTP 403 / 429:** portal zablokował serwery GitHuba. Zmniejsz częstotliwość albo uruchamiaj skrypt na własnym komputerze: `pip install -r requirements.txt`, potem `python monitor.py`.
* **„Nie znaleziono ogłoszeń”:** portal zmienił wygląd strony. Wtedy trzeba poprawić parser w `monitor.py`.

Logi każdego przebiegu są w zakładce **Actions**.

GitHub wyłącza zaplanowane zadania w repozytoriach bez aktywności przez 60 dni. Wyśle wtedy maila, a ponowne włączenie to jedno kliknięcie w zakładce Actions.
