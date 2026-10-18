"""TYMCZASOWE: zapisuje surowe strony serwisu licytacji do analizy (uruchamiane w GitHub Actions)."""
import re
import time
from pathlib import Path

from curl_cffi import requests as creq

out = Path("tmp_probe")
out.mkdir(exist_ok=True)
s = creq.Session(impersonate="chrome")
H = {"Accept-Language": "pl-PL,pl;q=0.9"}


def save(name, url, **kw):
    try:
        r = s.get(url, headers=H, timeout=40, **kw)
        (out / name).write_bytes(r.content)
        print(name, r.status_code, len(r.content), r.url)
        return r
    except Exception as e:
        print(name, "ERR", str(e)[:200])
    finally:
        time.sleep(3)


home = save("home.html", "https://licytacje.komornik.pl/")
if home is not None:
    for i, src in enumerate(sorted(set(re.findall(r'(/_nuxt/[\w.-]+\.js)', home.text)))):
        save(f"js_{src.split('/')[-1]}", "https://licytacje.komornik.pl" + src)
