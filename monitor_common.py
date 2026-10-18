"""Funkcje pomocnicze wspólne dla monitora i modułów dodatkowych."""
import json
import re

ROOMS_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "more": 10,
}


_NUXT_WRAPPERS = {"ShallowReactive", "Reactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef"}


def to_num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\xa0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    m = re.search(r"\d+(?:\.\d+)?", s)
    return float(m.group()) if m else None


def rooms_num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    if s in ROOMS_WORDS:
        return ROOMS_WORDS[s]
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def walk(obj):
    """Przechodzi rekurencyjnie po JSON i zwraca wszystkie słowniki."""
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            yield cur
            stack.extend(reversed(list(cur.values())))  # kolejność jak w dokumencie
        elif isinstance(cur, list):
            stack.extend(reversed(cur))


def next_data(html):
    """JSON z <script id="__NEXT_DATA__"> (Otodom)."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def prerendered_state(html):
    """Stan z window.__PRERENDERED_STATE__ (OLX). To napis JSON zapisany jako napis JS."""
    m = re.search(r'window\.__PRERENDERED_STATE__\s*=\s*("(?:[^"\\]|\\.)*")', html)
    if not m:
        return None
    try:
        return json.loads(json.loads(m.group(1)))
    except (json.JSONDecodeError, TypeError):
        return None


def nuxt_data(html):
    """Dane z <script id="__NUXT_DATA__"> (Gratka, Morizon).

    Nuxt zapisuje je w formacie "devalue": płaska tablica, w której obiekty
    i listy zamiast wartości trzymają indeksy innych elementów tablicy.
    """
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        return None
    try:
        flat = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(flat, list) or not flat:
        return None
    cache = {}

    def hydrate(i):
        if not isinstance(i, int) or i < 0 or i >= len(flat):
            return None  # liczby ujemne oznaczają undefined, NaN itp.
        if i in cache:
            return cache[i]
        v = flat[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in _NUXT_WRAPPERS:
                    out = hydrate(v[1]) if len(v) > 1 else None
                elif tag == "Date":
                    out = v[1] if len(v) > 1 else None
                elif tag == "Set":
                    out = [hydrate(x) for x in v[1:]]
                elif tag in ("Map", "null", "BigInt", "RegExp", "Error", "URL", "NuxtError", "Island"):
                    out = None
                else:
                    out = [hydrate(x) for x in v]
            else:
                out = [hydrate(x) for x in v]
        elif isinstance(v, dict):
            out = {}
            cache[i] = out  # zabezpieczenie przed cyklami
            for k, x in v.items():
                out[k] = hydrate(x)
            return out
        else:
            out = v
        cache[i] = out
        return out

    return hydrate(0)


def ld_json_offers(html):
    """Oferty z bloków JSON-LD (schema.org Offer). Zapasowe dane strukturalne."""
    out = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for d in walk(data):
            if d.get("@type") == "Offer" and d.get("url"):
                out.append(d)
    return out


def join_location(*parts):
    seen, out = set(), []
    for p in parts:
        p = (p or "").strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return ", ".join(out)

