"""Sprawdza, czy portale z config.yaml odpowiadają i czy parsery znajdują oferty.

Pobiera pierwszą stronę pierwszego adresu dla każdego portalu (z przerwą 3 do 7 s)
i niczego nie zapisuje. Używane w GitHub Actions, żeby sprawdzić, czy portale
nie blokują serwerów GitHuba.
"""
import random
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import monitor  # noqa: E402


def main():
    config = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    first_url = {}
    for search in config.get("wyszukiwania") or []:
        for url in search.get("adresy") or []:
            source = monitor.detect_source(url)
            if source and source not in first_url:
                first_url[source] = url
    for i, (source, url) in enumerate(first_url.items()):
        if i:
            time.sleep(random.uniform(3, 7))
        try:
            offers = monitor.parse_page(source, monitor.fetch(url), url)
            full = sum(1 for o in offers if o.get("price") and o.get("area"))
            print(f"{source:8} OK   ofert: {len(offers)}, z ceną i metrażem: {full}")
        except monitor.FetchError as exc:
            print(f"{source:8} BŁĄD {exc}")


if __name__ == "__main__":
    main()
