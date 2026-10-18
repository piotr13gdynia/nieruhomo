# Monitor ogłoszeń nieruchomości

Co 2 godziny sprawdza wyszukiwania na Otodom, OLX, Gratce i Morizonie, filtruje oferty według Twoich kryteriów, wykrywa okazje cenowe i publikuje stronę z listą. Pokazuje też licytacje komornicze z portalu Krajowej Rady Komorniczej. Działa na GitHub Actions, bez serwera, bez kosztów i bez AI.

Na stronie widać:
* nowe oferty od Twojej ostatniej wizyty,
* **okazje**: oferty tańsze za m² o co najmniej 10% od mediany podobnych ofert (ta sama dzielnica, typ i przedział metrażu), z filtrem „tylko okazje”,
* przy każdej ocenionej ofercie odchylenie od mediany w procentach,
* obniżki cen (z ceną pierwotną),
* szczegóły: piętro, rok budowy, rynek (pierwotny, wtórny), oferent (prywatny, biuro, deweloper), osiedle,
* to samo mieszkanie wystawione na kilku portalach jako jedną pozycję,
* licytacje komornicze z datą licytacji, ceną wywołania i sumą oszacowania,
* stan źródeł: kiedy każde ostatnio zwróciło wyniki, oraz ostrzeżenia o błędach.

Opcjonalnie wysyła jedną zbiorczą wiadomość na Telegram lub e-mail, gdy pojawi się nowa okazja albo obniżka.

## Uruchomienie (ok. 5 minut)

Kod jest już w repozytorium `piotr13gdynia/nieruhomo`. Zostały ustawienia, których nie da się zmienić z kodu:

1. **Settings → General → Default branch**: ustaw `main`.
2. **Settings → General → Danger Zone → Change visibility**: repozytorium musi być **Public**, jeśli korzystasz z darmowego planu GitHub (GitHub Pages dla prywatnych repozytoriów wymaga planu płatnego). Lista ofert i wyszukiwania będą widoczne dla każdego, kto zna adres. Strona ma znacznik `noindex`, więc wyszukiwarki jej nie pokażą.
3. **Settings → Pages**: Source „Deploy from a branch”, Branch `main`, folder `/docs`. Zapisz.
4. **Settings → Actions → General → Workflow permissions**: zaznacz „Read and write permissions”. Zapisz.
5. **Actions → Monitor ogłoszeń → Run workflow**. Pierwszy przebieg trwa 3 do 5 minut, bo buduje bazę do liczenia median.
6. Strona będzie pod adresem `https://piotr13gdynia.github.io/nieruhomo/`.

Swoje wyszukiwania i filtry zmieniasz w `config.yaml` (ikona ołówka w repozytorium). Instrukcja jest w komentarzach pliku.

## Zmiany

* **Inne wyszukiwanie lub filtry:** edytujesz `config.yaml` na gałęzi `main`. Zapisanie zmiany od razu uruchamia monitor, nowa lista jest na stronie po kilku minutach. Wszystkie ustawienia są opisane w komentarzach pliku.
* **Częstotliwość:** linia `cron` w `.github/workflows/monitor.yml`. `17 */2 * * *` oznacza co 2 godziny, `17 7-21 * * *` co godzinę w dzień (czas UTC, czyli 1 do 2 godzin mniej niż w Polsce). Częściej niż co godzinę nie warto, portale szybciej zaczną blokować.
* **Próg okazji:** `okazje: prog_procent` w `config.yaml` (domyślnie 10).
* **Licytacje z innego miasta lub województwa:** sekcja `licytacje` w `config.yaml`.

## Jak liczone są okazje

1. Każdy przebieg zapisuje do `data/market.json` wszystkie oferty znalezione na stronach wyników, także te odrzucone przez Twoje filtry. Dodatkowe wyszukiwania bez limitu ceny (`okazje: adresy_rynku`) służą tylko do tego, żeby mediana nie była zaniżona.
2. Oferty są dzielone na grupy: miasto, dzielnica, typ (mieszkanie, dom) i przedział metrażu (domyślnie do 35, 35-50, 50-70, 70-100 i powyżej 100 m²).
3. Dla grupy z co najmniej 15 ofertami liczona jest mediana ceny za m². Ta sama oferta z kilku portali liczy się raz. Udziały, licytacje i TBS są pomijane.
4. Oferta tańsza od mediany o próg z `config.yaml` jest okazją. Różnica większa niż 35% dostaje dopisek „Sprawdź metraż i cenę”, bo zwykle oznacza błąd w ogłoszeniu albo sprzedaż udziału.

Na start, dopóki baza ma mniej niż 2000 ofert, z adresów rynku pobieranych jest 10 stron zamiast jednej. Pierwsze okazje widać już po pierwszym przebiegu, a w kolejnych dniach ocenionych ofert przybywa. Oferty niewidziane od 60 dni wypadają z bazy rynkowej, a z listy ofert trafiają do `data/archive.json`.

## Powiadomienia (opcjonalnie)

Wiadomość przychodzi raz na przebieg i tylko wtedy, gdy jest nowa okazja albo obniżka ceny o co najmniej `obnizka_procent` (domyślnie 5%). Sekrety dodajesz w repozytorium: **Settings → Secrets and variables → Actions → New repository secret**. Nigdy nie wpisuj ich do `config.yaml`.

### Telegram

1. W Telegramie napisz do **@BotFather**, wyślij `/newbot` i nadaj nazwę. Dostaniesz token w rodzaju `123456789:AA...`. Dodaj go jako sekret `TELEGRAM_TOKEN`.
2. Napisz cokolwiek do swojego nowego bota.
3. Otwórz w przeglądarce `https://api.telegram.org/bot<TOKEN>/getUpdates` (wstaw swój token). Znajdź `"chat":{"id":123456789`. Dodaj tę liczbę jako sekret `TELEGRAM_CHAT_ID`.
4. W `config.yaml` ustaw `powiadomienia: telegram: true`.

### E-mail

Potrzebny jest serwer SMTP. Dla Gmaila: włącz weryfikację dwuetapową i utwórz hasło aplikacji (Konto Google → Bezpieczeństwo → Hasła do aplikacji). Sekrety:

| Sekret | Przykład dla Gmaila |
|---|---|
| `SMTP_HOST` | `smtp.gmail.com` |
| `SMTP_PORT` | `465` |
| `SMTP_USER` | Twój adres Gmail |
| `SMTP_PASSWORD` | hasło aplikacji (16 znaków) |
| `EMAIL_TO` | adres, na który mają przychodzić wiadomości |

Potem w `config.yaml` ustaw `powiadomienia: email: true`. Gdy wysyłka się nie uda, na stronie pojawi się ostrzeżenie z opisem błędu.

## Licytacje komornicze

Źródłem jest portal Krajowej Rady Komorniczej licytacje.komornik.pl (stary adres z wyszukiwarką obwieszczeń przekierowuje na nowy portal). Monitor pobiera licytacje nieruchomości z wybranego województwa, do których można jeszcze przystąpić, od najnowszych. Na co dzień to jedno zapytanie na przebieg. Miasto, rodzaj (`kategorie: [mieszkanie, dom]`), maksymalną cenę wywołania i ukrywanie udziałów ustawiasz w sekcji `licytacje` w `config.yaml`. Licytacje po terminie znikają z listy.

## Pliki danych

| Plik | Zawartość |
|---|---|
| `data/offers.json` | oferty spełniające Twoje filtry, z historią cen |
| `data/market.json` | baza do liczenia median (wszystkie znalezione oferty z 60 dni) |
| `data/archive.json` | oferty niewidziane od ponad 60 dni |
| `data/auctions.json` | licytacje komornicze |
| `data/health.json` | kiedy każde źródło ostatnio zwróciło wyniki |
| `data/status.json` | wynik ostatniego przebiegu, w tym błędy |

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
| Licytacje komornicze | interfejs JSON portalu (`/services/item-back/rest/item/search`) | brak |

Z każdej oferty zapisywane są: cena, metraż, liczba pokoi, miasto, dzielnica, lokalizacja do wyświetlenia, miniatura zdjęcia i link. Z list wyników dochodzą pola dostępne bez dodatkowych zapytań: piętro, oferent, rynek (OLX). Dla nowych ofert z Otodomu, Gratki i Morizonu monitor otwiera stronę ogłoszenia i dopisuje rok budowy, liczbę pięter, rynek, oferenta i osiedle. Limit takich zapytań na przebieg: `szczegoly_na_przebieg` (domyślnie 10, najpierw największe okazje).

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

* `tests/test_parsers.py`: parsery list wyników na prawdziwych stronach zapisanych w `tests/fixtures/`.
* `tests/test_details.py`: szczegóły ogłoszeń ze stron ofert i z list.
* `tests/test_deals.py`: mediany, progi i oznaczanie okazji.
* `tests/test_notify.py`: treść i wysyłka powiadomień (z podmienionym Telegramem i serwerem poczty).
* `tests/test_auctions.py`: licytacje komornicze na zapisanej odpowiedzi portalu.
* `tests/test_maintenance.py`: archiwum i stan źródeł.
* `tests/test_run.py`: cały przebieg z podmienionym pobieraniem: dwa uruchomienia z różnymi cenami, wykrycie obniżki, scalanie duplikatów, oznaczenie okazji, pobranie szczegółów, rozruch bazy rynkowej, awaria jednego portalu.

Testy uruchamiają się same na GitHubie przy każdej zmianie kodu (workflow **Testy**).

### Gdy portal zmieni wygląd

1. `python tools/check_portals.py` pokaże, który portal (albo portal licytacji) przestał zwracać wyniki.
2. `python tools/refresh_fixtures.py` pobierze świeże strony do `tests/fixtures/`. Strony są przycinane i pozbawiane danych kontaktowych.
3. `python -m pytest` pokaże, które pola parser przestał odczytywać. Popraw parser w `monitor.py` i zaktualizuj oczekiwane wartości w testach.
