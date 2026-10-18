# Monitor ogłoszeń nieruchomości

## Cel

Osobiste narzędzie, które regularnie przegląda ogłoszenia sprzedaży mieszkań i domów z kilku źródeł, filtruje je według moich kryteriów, wykrywa okazje cenowe i pokazuje wszystko na jednej stronie. Bez użycia API AI. Bez serwera i bez kosztów utrzymania.

Użytkownik jest jeden (ja). To nie jest produkt dla wielu klientów, więc nie budujemy kont, logowania ani płatności.

## Punkt startowy

W repozytorium jest działający prototyp (monitor.py, page.py, template.html, config.yaml, workflow GitHub Actions). Obsługuje Otodom, OLX, Gratkę i Morizon, filtry, historię cen, scalanie duplikatów i stronę z listą. Parsery były testowane tylko na sztucznych stronach, nigdy na prawdziwych portalach. Rozwijaj ten kod, nie pisz od zera, chyba że uzasadnisz to i zapytasz.

## Stos technologiczny

* Python 3.12, biblioteki: curl_cffi (pobieranie stron z odciskiem przeglądarki Chrome), beautifulsoup4, PyYAML. Nowe zależności dodawaj tylko z uzasadnieniem.
* Uruchamianie: GitHub Actions (cron co 2 godziny), wyniki commitowane do repozytorium.
* Strona: statyczny plik docs/index.html na GitHub Pages, dane osadzone w pliku, cała logika interfejsu w czystym JavaScripcie.
* Dane: pliki JSON w katalogu data/. Bez zewnętrznej bazy danych.
* Konfiguracja: config.yaml, edytowany ręcznie. Komentarze i klucze po polsku.

## Zasady

* Zero wywołań API AI (Anthropic, OpenAI ani innych). Cała logika ma być deterministyczna.
* Uprzejme pobieranie: losowe przerwy 3 do 7 sekund między zapytaniami, nie częściej niż co godzinę na wyszukiwanie, tylko pierwsze strony wyników.
* Odporność: awaria jednego źródła nie może zatrzymać pozostałych. Każdy błąd trafia do data/status.json i jest widoczny na stronie.
* Każdy parser ma najpierw szukać danych strukturalnych (JSON osadzony w stronie, np. __NEXT_DATA__ lub __PRERENDERED_STATE__), a dopiero potem parsować HTML.
* Teksty w interfejsie i komunikaty po polsku, zrozumiałe dla osoby nietechnicznej.
* Nie używaj długich myślników w tekstach interfejsu i dokumentacji.
* Sekrety (np. token bota Telegram) wyłącznie w GitHub Secrets, nigdy w repozytorium.

## Obszary prac

Kolejność ma znaczenie. Każdy obszar kończ działającym kodem, testami i krótkim podsumowaniem, co zrobiłeś.

| Obszar | Zakres | Zależy od |
|---|---|---|
| 1. Weryfikacja parserów na żywych stronach | Pobierz prawdziwe strony wyników z czterech portali, zapisz je jako fixtures w tests/fixtures/, popraw parsery tak, żeby poprawnie wyciągały cenę, metraż, pokoje, lokalizację, zdjęcie i link. Testy pytest na fixtures. Jeśli portal blokuje zapytania, opisz to i zaproponuj obejście. | prototyp |
| 2. Szczegóły ogłoszenia | Opcjonalne pobieranie strony pojedynczej oferty tylko dla nowych ogłoszeń: piętro, rok budowy, rynek (pierwotny/wtórny), oferent (prywatny/biuro), dokładna dzielnica. Limit liczby takich zapytań na przebieg w config.yaml. | 1 |
| 3. Wykrywanie okazji | Z zebranej historii liczona mediana ceny za m² dla kombinacji: miasto, dzielnica, typ nieruchomości, przedział metrażu. Oferta jest okazją, gdy jest tańsza od mediany o próg z config.yaml (domyślnie 10%). Mediana liczona tylko przy minimum N ofert w grupie (domyślnie 15), inaczej brak oceny. Na stronie: odchylenie od mediany w procentach i filtr „tylko okazje”. | 1, lepiej po 2 |
| 4. Powiadomienia | Telegram (bot, token w GitHub Secrets) i opcjonalnie e-mail. Wysyłka tylko dla nowych okazji i obniżek powyżej progu, jedna zbiorcza wiadomość na przebieg. Kanały i progi włączane w config.yaml. | 3 |
| 5. Licytacje komornicze | Nowe źródło: obwieszczenia o licytacjach nieruchomości z oficjalnego serwisu komorników (zweryfikuj aktualny adres i strukturę). Osobna sekcja na stronie z datą licytacji i ceną wywołania. | 1 |
| 6. Utrzymanie | Rotacja danych (archiwum starszych niż 60 dni w osobnym pliku), raport zdrowia źródeł na stronie (kiedy ostatnio każde źródło zwróciło wyniki), README aktualne po każdej zmianie. | wszystkie |

## Strona z listą

Zachowaj obecny układ (wiersze z dużą ceną po prawej, skala ceny za m², oznaczenia „Nowa” i „Taniej o X%”, ostrzeżenia o źródłach). Dodawane elementy mają pasować do istniejącego stylu. Strona musi działać na telefonie i w trybie ciemnym.

## Testy

* pytest dla każdego parsera na zapisanych fixtures.
* Test całego przebiegu z podmienioną funkcją fetch: dwa uruchomienia z różnymi cenami, sprawdzenie wykrycia obniżki, scalenia duplikatów i oznaczenia okazji.
* Przed zakończeniem obszaru uruchom pełny zestaw testów.

## Jak ze mną pracować

* Zanim zaczniesz obszar, w kilku zdaniach opisz plan i poczekaj na akceptację.
* Pytaj, gdy decyzja zmienia koszty, wymaga nowego konta lub usługi albo zmienia sposób działania strony.
* Na koniec każdego obszaru: co działa, czego nie udało się zrobić, co muszę zrobić ręcznie (np. dodać sekret w GitHubie).
