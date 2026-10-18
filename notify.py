"""Powiadomienia: jedna zbiorcza wiadomość na przebieg (Telegram i e-mail).

Wysyłane są tylko nowe okazje i obniżki cen o co najmniej obnizka_procent.
Dane dostępowe wyłącznie ze zmiennych środowiskowych (GitHub Secrets):
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_TO, EMAIL_FROM (opcjonalnie)
"""
import html
import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage

TELEGRAM_LIMIT = 4000  # Telegram przyjmuje do 4096 znaków
MAX_ITEMS = 15


def zl(n):
    return f"{n:,.0f}".replace(",", " ") + " zł"


def settings(config):
    s = {"telegram": False, "email": False, "obnizka_procent": 5}
    s.update((config or {}).get("powiadomienia") or {})
    return s


def collect(offers, status, config):
    """Zdarzenia do powiadomienia: (nowe okazje, obniżki powyżej progu)."""
    s = settings(config)
    deals = [offers[k] for k in status.get("new_deals", []) if k in offers]
    deals.sort(key=lambda o: o.get("deviation", 0))
    deal_keys = set(status.get("new_deals", []))
    drops = []
    for ev in status.get("drop_events", []):
        o = offers.get(ev["key"])
        if not o or ev["key"] in deal_keys:
            continue  # obniżka, która zrobiła z oferty okazję, jest już na liście okazji
        pct = (1 - ev["new"] / ev["old"]) * 100
        if pct >= float(s["obnizka_procent"]):
            drops.append((o, ev, pct))
    drops.sort(key=lambda x: -x[2])
    return deals, drops


def offer_line(o):
    parts = [zl(o["price"])]
    if o.get("area"):
        parts.append(f"{o['area']:g} m²".replace(".", ","))
    if o.get("rooms"):
        parts.append(f"{o['rooms']} pok.")
    if o.get("location"):
        parts.append(o["location"])
    return ", ".join(parts)


def build_message(offers, status, config, page_url=None):
    """Zwraca (temat, tekst, html) albo None, gdy nie ma czego wysyłać."""
    deals, drops = collect(offers, status, config)
    if not deals and not drops:
        return None
    subject_parts = []
    if deals:
        subject_parts.append(f"{len(deals)} {plural(len(deals), 'nowa okazja', 'nowe okazje', 'nowych okazji')}")
    if drops:
        subject_parts.append(f"{len(drops)} {plural(len(drops), 'obniżka', 'obniżki', 'obniżek')}")
    subject = "Nieruchomości: " + " i ".join(subject_parts)

    text, rich = [subject, ""], [f"<b>{html.escape(subject)}</b>", ""]
    if deals:
        text.append("NOWE OKAZJE")
        rich.append("<b>Nowe okazje</b>")
        for o in deals[:MAX_ITEMS]:
            dev = abs(round(o.get("deviation", 0) * 100))
            warn = " (sprawdź metraż i cenę)" if o.get("suspicious") else ""
            line = f"{dev}% poniżej mediany{warn}: {offer_line(o)}"
            text += [f"* {o.get('title', '')}", f"  {line}", f"  {o['url']}"]
            rich.append(f"• <a href=\"{html.escape(o['url'])}\">{html.escape(o.get('title') or 'Ogłoszenie')}</a>\n"
                        f"  {html.escape(line)}")
        if len(deals) > MAX_ITEMS:
            more = f"i {len(deals) - MAX_ITEMS} więcej na stronie"
            text.append(more)
            rich.append(html.escape(more))
        text.append("")
        rich.append("")
    if drops:
        text.append("OBNIŻKI CEN")
        rich.append("<b>Obniżki cen</b>")
        for o, ev, pct in drops[:MAX_ITEMS]:
            line = f"taniej o {pct:.0f}% ({zl(ev['old'])} -> {zl(ev['new'])}): {offer_line(o)}"
            text += [f"* {o.get('title', '')}", f"  {line}", f"  {o['url']}"]
            rich.append(f"• <a href=\"{html.escape(o['url'])}\">{html.escape(o.get('title') or 'Ogłoszenie')}</a>\n"
                        f"  {html.escape(line)}")
        if len(drops) > MAX_ITEMS:
            more = f"i {len(drops) - MAX_ITEMS} więcej na stronie"
            text.append(more)
            rich.append(html.escape(more))
    if page_url:
        text += ["", f"Wszystkie oferty: {page_url}"]
        rich += ["", f"<a href=\"{html.escape(page_url)}\">Wszystkie oferty</a>"]
    return subject, "\n".join(text).strip(), "\n".join(rich).strip()


def plural(n, one, few, many):
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def truncate(text, limit=TELEGRAM_LIMIT):
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit("\n•", 1)[0]
    return cut + "\n…i więcej na stronie"


def send_telegram(text, env, post=None):
    token, chat = env.get("TELEGRAM_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise RuntimeError("Brak sekretów TELEGRAM_TOKEN lub TELEGRAM_CHAT_ID.")
    body = json.dumps({"chat_id": chat, "text": truncate(text), "parse_mode": "HTML",
                       "disable_web_page_preview": True}).encode()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if post:
        return post(url, body)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:200]
        raise RuntimeError(f"Telegram odrzucił wiadomość (HTTP {exc.code}): {detail}") from None
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Nie udało się połączyć z Telegramem: {exc.reason}") from None
    if not data.get("ok"):
        raise RuntimeError(f"Telegram odrzucił wiadomość: {data.get('description')}")
    return data


def send_email(subject, text, env, smtp_factory=None):
    host, to = env.get("SMTP_HOST"), env.get("EMAIL_TO")
    user, password = env.get("SMTP_USER"), env.get("SMTP_PASSWORD")
    if not host or not to:
        raise RuntimeError("Brak sekretów SMTP_HOST lub EMAIL_TO.")
    port = int(env.get("SMTP_PORT") or 465)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = env.get("EMAIL_FROM") or user or to
    msg["To"] = to
    msg.set_content(text)
    if smtp_factory:
        smtp = smtp_factory(host, port)
    elif port == 465:
        smtp = smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30)
    else:
        smtp = smtplib.SMTP(host, port, timeout=30)
        smtp.starttls(context=ssl.create_default_context())
    try:
        if user and password:
            smtp.login(user, password)
        smtp.send_message(msg)
    finally:
        smtp.quit()


def send_all(offers, status, config, env=None, post=None, smtp_factory=None):
    """Wysyła powiadomienia włączone w config.yaml. Zwraca słownik {kanał: wynik} do status.json."""
    env = os.environ if env is None else env
    s = settings(config)
    result = {}
    if not (s.get("telegram") or s.get("email")):
        return result
    msg = build_message(offers, status, config, page_url=config.get("adres_strony"))
    if not msg:
        return {"info": "Brak nowych okazji i obniżek, nic nie wysłano."}
    subject, text, rich = msg
    for channel, enabled, send in (
        ("telegram", s.get("telegram"), lambda: send_telegram(rich, env, post)),
        ("email", s.get("email"), lambda: send_email(subject, text, env, smtp_factory)),
    ):
        if not enabled:
            continue
        try:
            send()
            result[channel] = "wysłano"
        except Exception as exc:  # noqa: BLE001
            result[channel] = f"Błąd: {exc}"
        print(f"Powiadomienie {channel}: {result[channel]}")
    return result
