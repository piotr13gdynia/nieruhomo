"""TYMCZASOWE: rozpoznanie serwisu licytacji komorniczych (uruchamiane w GitHub Actions)."""
import re
import sys
import time

from curl_cffi import requests as creq

s = creq.Session(impersonate="chrome")
urls = sys.argv[1:] or [
    "https://licytacje.komornik.pl/",
    "https://elicytacje.komornik.pl/",
    "https://e-licytacje.komornik.pl/",
    "https://www.komornik.pl/",
]
for u in urls:
    print("=" * 100)
    print("URL", u)
    try:
        r = s.get(u, timeout=40, headers={"Accept-Language": "pl-PL,pl;q=0.9"})
    except Exception as e:
        print("ERR", str(e)[:200])
        continue
    t = r.text
    print("STATUS", r.status_code, "FINAL", r.url, "LEN", len(t), "CT", r.headers.get("content-type"))
    m = re.search(r"<title>(.*?)</title>", t, re.S)
    print("TITLE", m.group(1).strip()[:200] if m else None)
    for marker in ["__NEXT_DATA__", "__NUXT", "ng-version", "data-reactroot", "application/ld+json", "/api/", "graphql"]:
        print("MARK", marker, t.count(marker))
    hrefs = sorted(set(re.findall(r'href="([^"#]{2,200})"', t)))
    print("HREFS", len(hrefs))
    for h in hrefs[:150]:
        print("  H", h)
    for src in sorted(set(re.findall(r'<script[^>]*src="([^"]+)"', t)))[:30]:
        print("  S", src)
    for api in sorted(set(re.findall(r'["\'](/?api/[^"\']{1,120}|https?://[^"\']*api[^"\']{0,120})["\']', t)))[:60]:
        print("  API", api)
    if "json" in (r.headers.get("content-type") or ""):
        print("BODY", t[:3000])
    else:
        body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>", "", t, flags=re.S)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        print("TEXT", text[:2500])
    time.sleep(3)
