"""Monitor dei bandi pubblicati su https://bandi.mur.gov.it/

Cerca i bandi APERTI nei gruppi scientifico-disciplinari 13/ECON-06 (ECON-06/A)
e 13/ECON-09 (ECON-09/A, ECON-09/B), confronta con quelli già visti (seen.json)
e invia un'email con le novità e l'elenco di tutti i bandi ancora aperti.
Una volta a settimana (il lunedì) invia comunque il riepilogo dei bandi aperti.

Uso:
    python monitor.py            # esecuzione normale (invia email e aggiorna seen.json)
    python monitor.py --dry-run  # stampa il risultato senza inviare né salvare
"""

import html
import json
import os
import re
import smtplib
import sys
import time
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://bandi.mur.gov.it"
SEEN_FILE = Path(__file__).with_name("seen.json")
STATE_FILE = Path(__file__).with_name("stato.json")
RECIPIENTS_FILE = Path(__file__).with_name("destinatari.txt")
PAGE_FILE = Path(__file__).parent / "docs" / "index.html"
# Indirizzo pubblico della pagina con tutti i bandi aperti (GitHub Pages).
# Se è vuoto, l'elenco completo viene messo direttamente nell'email.
PAGE_URL = os.environ.get("PAGE_URL", "")

# Gruppi scientifico-disciplinari (DM 639/2024) da monitorare.
GSD = ["13/ECON-06", "13/ECON-09"]
# Vecchi settori concorsuali (13/B1 = SECS-P/07; 13/B4 = SECS-P/09 e SECS-P/11).
# Il filtro per vecchio settore esiste solo nelle sezioni ricercatori e professori.
OLD_SECTORS = ["13/B1", "13/B4"]
# Riconosce i vostri settori nell'elenco dei settori di un bando.
OUR_SECTORS = re.compile(
    r"ECON-0?[69]\b|SECS-P/0?[79]\b|SECS-P/11|13/B[14]\b"
    r"|Economia aziendale|Finanza aziendale|intermediari finanziari", re.I)

# (etichetta, percorso della ricerca, nome del parametro "stato", parametri extra)
SECTIONS = [
    ("Ricercatori a tempo determinato (RTT / RTD)", "/jobs.php/public/cercaJobs",
     "jv_comp_status_id", {"idsettore": "%", "idqualifica": "%"}),
    ("Contratti di ricerca", "/contrattidiricerca.php/public/cercaFellowship",
     "jf_comp_status_id", {"idarea": "%"}),
    ("Incarichi di ricerca", "/incarichidiricerca.php/public/cercaFellowship",
     "jf_comp_status_id", {"idarea": "%"}),
    ("Incarichi post-doc", "/incarichipostdoc.php/public/cercaFellowship",
     "jf_comp_status_id", {"idarea": "%"}),
    ("Professori associati e ordinari", "/profcalls.php/public/cercaJobs",
     "jv_comp_status_id", {"idsettore": "%", "idqualifica": "%"}),
]

# Rete di sicurezza: bandi aperti SENZA settore indicato, che il filtro per gruppo
# non vede. Se il titolo contiene una di queste parole finiscono in "Da verificare".
# Solo parole specifiche: termini generici come "management" o "banca" portano rumore.
KEYWORDS = re.compile(
    r"aziendal|\bfinanza\b|finanziari[oae]?\b|\bfinancial\b|\bfinance\b|banking|bancari"
    r"|contabil|\bbilanci[o]?\b|accounting|corporate", re.I)
CHECK_LABEL = "Da verificare: bandi senza settore indicato"

# Settori della pagina web, nell'ordine in cui compaiono.
SECTOR_GROUPS = [
    ("ECON-06/A", "Economia aziendale", re.compile(r"ECON-0?6|SECS-P/0?7\b|13/B1\b|Economia aziendale", re.I)),
    ("ECON-09/A", "Finanza aziendale", re.compile(r"ECON-0?9/A|SECS-P/0?9\b|Finanza aziendale", re.I)),
    ("ECON-09/B", "Economia degli intermediari finanziari",
     re.compile(r"ECON-0?9/B|SECS-P/11|intermediari finanziari", re.I)),
]
UNKNOWN_SECTOR = ("Da verificare", "settore non indicato nell'elenco del sito")

# Se il sito non risponde, l'esecuzione viene saltata in silenzio; dopo questi giorni
# senza nessun controllo riuscito lo script segnala l'errore (GitHub manda un'email).
MAX_DAYS_DOWN = 2
URGENT_DAYS = 7

session = requests.Session()
session.headers["User-Agent"] = "Mozilla/5.0 (monitor bandi MUR per gruppo di ricerca)"


class SiteUnavailable(Exception):
    pass


def fetch(path, params):
    for attempt in range(3):
        try:
            r = session.get(BASE + path, params=params, timeout=60)
            r.raise_for_status()
            break
        except requests.RequestException as e:
            if attempt == 2:
                raise SiteUnavailable(str(e)) from e
            time.sleep(15 * (attempt + 1))
    time.sleep(1)
    # Il sito dichiara UTF-8 ma spesso invia caratteri Windows-1252.
    try:
        return r.content.decode("utf-8")
    except UnicodeDecodeError:
        return r.content.decode("cp1252", errors="replace")


def parse_results(page, source_url):
    soup = BeautifulSoup(page, "html.parser")
    header = soup.find("h2", class_="risultato")
    text = soup.get_text(" ", strip=True)
    if "Non sono presenti bandi" in text:
        return []
    m = re.search(r"trovati (\d+) band", header.get_text() if header else "")
    if not m:
        raise RuntimeError(f"Struttura della pagina cambiata, impossibile leggere: {source_url}")
    expected = int(m.group(1))

    bandi = []
    for p in soup.select("#hiddenresult p"):
        link = p.find("a", href=True)
        if not link:
            continue
        strongs = [s.get_text(" ", strip=True) for s in p.find_all("strong")]
        scadenza = p.find("em")
        posti = re.search(r"Numero posti:\s*(\d+)", p.get_text(" ", strip=True))
        bandi.append({
            "id": link["href"],
            "url": BASE + link["href"],
            "titolo": link.get_text(" ", strip=True),
            "ente": strongs[0] if strongs else "",
            "settori": [re.sub(r"^(S\.S\.D\.|Settore)\s*", "", s) for s in strongs[1:]],
            "scadenza": scadenza.get_text(" ", strip=True) if scadenza else "",
            "posti": posti.group(1) if posti else "",
        })
    if len(bandi) != expected:
        raise RuntimeError(
            f"Trovati {len(bandi)} bandi ma il sito ne indica {expected}: {source_url}")
    return bandi


def collect():
    found = {}
    for label, path, status_param, extra in SECTIONS:
        filters = [{"idgsd24": g} for g in GSD]
        if "idsettore" in extra:
            filters += [{"idsettore": s, "idgsd24": "%"} for s in OLD_SECTORS]
        for f in filters:
            params = {status_param: "2-3", "bb_type_code": "%", "idgsd24": "%",
                      **extra, **f, "azione": "cerca"}
            for b in parse_results(fetch(path, params), BASE + path):
                b["nostri"] = [s for s in b["settori"] if OUR_SECTORS.search(s)]
                if b["settori"] and not b["nostri"]:
                    print(f"Scartato, nessun settore ECON-06/ECON-09: {b['titolo'][:90]}")
                    continue
                b["tipo"] = label
                found.setdefault(b["id"], b)

        params = {status_param: "2-3", "bb_type_code": "%", "idgsd24": "%", **extra, "azione": "cerca"}
        for b in parse_results(fetch(path, params), BASE + path):
            if not b["settori"] and KEYWORDS.search(b["titolo"]) and b["id"] not in found:
                b["tipo"] = CHECK_LABEL
                b["nostri"] = []
                b["sezione"] = label
                found[b["id"]] = b
    return found


def deadline(b):
    m = re.search(r"(\d{2}/\d{2}/\d{4})", b["scadenza"])
    return datetime.strptime(m.group(1), "%d/%m/%Y").date() if m else None


def render_item(b, today):
    if b["tipo"] == CHECK_LABEL:
        settore = f"settore non indicato dall'ateneo ({b['sezione']}): verificate nella pagina del bando"
    elif b["nostri"]:
        settore = "Settore: " + ", ".join(b["nostri"])
        others = len(b["settori"]) - len(b["nostri"])
        if others:
            settore += f" (il bando comprende anche altri {others} settori)"
    else:
        settore = "settore indicato solo nella pagina del bando"

    d = deadline(b)
    when = html.escape(b["scadenza"])
    if d:
        days = (d - today).days
        when += " (oggi)" if days == 0 else " (domani)" if days == 1 else f" (tra {days} giorni)"
        if days <= URGENT_DAYS:
            when = f"<b style='color:#c0392b'>{when}</b>"
    details = [html.escape(settore), when]
    if b["posti"]:
        details.append(f"posti: {b['posti']}")
    return (f"<li style='margin-bottom:10px'><b>{html.escape(b['ente'])}</b> - "
            f"<a href='{b['url']}'>{html.escape(b['titolo'])}</a><br>"
            f"<small>{' | '.join(details)}</small></li>")


def render_group(title, bandi, today):
    if not bandi:
        return []
    parts = [f"<h2 style='border-bottom:1px solid #ccc'>{html.escape(title)} ({len(bandi)})</h2>"]
    for label in [s[0] for s in SECTIONS] + [CHECK_LABEL]:
        items = sorted((b for b in bandi if b["tipo"] == label),
                       key=lambda b: deadline(b) or date.max)
        if items:
            parts.append(f"<h3>{html.escape(label)} ({len(items)})</h3><ul>")
            parts += [render_item(b, today) for b in items]
            parts.append("</ul>")
    return parts


def sectors_of(b):
    codes = [code for code, _, rx in SECTOR_GROUPS if any(rx.search(s) for s in b["nostri"])]
    if not codes and any(re.search(r"ECON-0?9|13/B4", s) for s in b["nostri"]):
        codes = ["ECON-09/A", "ECON-09/B"]
    return codes or [UNKNOWN_SECTOR[0]]


def sector_counts(bandi):
    groups = [g[:2] for g in SECTOR_GROUPS] + [UNKNOWN_SECTOR]
    counts = [(code, sum(code in sectors_of(b) for b in bandi)) for code, _ in groups]
    return ", ".join(f"{code}: {n}" for code, n in counts if n)


def build_email(new, still_open, first_run, today):
    everything = new + still_open
    if first_run:
        intro = "Primo avvio del monitor: ecco tutti i bandi attualmente aperti."
    elif new:
        intro = f"Sono usciti {len(new)} nuovi bandi nei vostri settori."
    else:
        intro = "Riepilogo settimanale dei bandi aperti nei vostri settori."
    parts = [f"<p>{intro} In rosso le scadenze entro {URGENT_DAYS} giorni.</p>"]
    parts += render_group("Nuovi bandi", new, today)

    if PAGE_URL:
        if not new:
            soon = [b for b in still_open if deadline(b) and (deadline(b) - today).days <= URGENT_DAYS]
            parts += render_group(f"In scadenza nei prossimi {URGENT_DAYS} giorni", soon, today)
        parts.append(
            f"<p style='margin:24px 0'><a href='{PAGE_URL}' style='background:#1f5f99;color:#fff;"
            f"padding:10px 18px;border-radius:6px;text-decoration:none;font-weight:bold'>"
            f"Vedi tutti i {len(everything)} bandi aperti, divisi per settore e tipologia</a></p>"
            f"<p><small>Bandi aperti per settore: {sector_counts(everything) or 'nessuno'}</small></p>")
    else:
        parts += render_group("Bandi ancora aperti" if new else "Bandi aperti", still_open, today)
        if not everything:
            parts.append("<p>Al momento non ci sono bandi aperti nei vostri settori.</p>")
    parts.append(f"<p><small>Fonte: <a href='{BASE}'>bandi.mur.gov.it</a>. Settori monitorati: "
                 "ECON-06/A, ECON-09/A, ECON-09/B (ex SECS-P/07, SECS-P/09, SECS-P/11).</small></p>")
    return "\n".join(parts)


PAGE_TEMPLATE = """<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Bandi aperti ECON</title>
<style>
:root { --bg:#f6f7f9; --card:#fff; --text:#1d2330; --muted:#5d6675; --line:#e2e5ea;
        --accent:#1f5f99; --urgent:#c0392b; --new:#1e8449; --chip:#eef2f7; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --card:#1d2128; --text:#e6e8eb; --muted:#9aa3b0; --line:#2c323b;
          --accent:#6aa9e0; --urgent:#ff7b6b; --new:#58c785; --chip:#262b33; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width:860px; margin:0 auto; padding:24px 16px 48px; }
h1 { font-size:1.5rem; margin:0 0 4px; }
.sub { color:var(--muted); margin:0 0 20px; }
nav, .filters { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
nav a, .filters button { background:var(--chip); color:var(--text); border:1px solid var(--line);
       border-radius:999px; padding:5px 12px; font-size:.9rem; text-decoration:none; cursor:pointer; }
.filters button.on { background:var(--accent); border-color:var(--accent); color:#fff; }
section { margin-top:28px; }
h2 { font-size:1.2rem; border-bottom:2px solid var(--accent); padding-bottom:4px; }
h2 small { color:var(--muted); font-weight:normal; }
h3 { font-size:1rem; color:var(--muted); margin:18px 0 8px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:8px;
        padding:12px 14px; margin-bottom:10px; }
.card a { color:var(--accent); font-weight:600; text-decoration:none; overflow-wrap:anywhere; }
.card a:hover { text-decoration:underline; }
.ente { font-weight:700; }
.meta { color:var(--muted); font-size:.88rem; margin-top:4px; }
.urgent { color:var(--urgent); font-weight:700; }
.badge { display:inline-block; font-size:.72rem; font-weight:700; color:#fff; background:var(--new);
         border-radius:4px; padding:1px 6px; margin-right:6px; vertical-align:2px; }
.empty { color:var(--muted); font-style:italic; }
footer { color:var(--muted); font-size:.85rem; margin-top:40px; }
</style>
</head>
<body>
<main>
<h1>Bandi aperti &middot; ECON-06/A, ECON-09/A, ECON-09/B</h1>
<p class="sub">Aggiornato il __UPDATED__ &middot; __TOTAL__ bandi aperti &middot; in rosso le scadenze entro __URGENT__ giorni</p>
<nav>__NAV__</nav>
<div class="filters">__FILTERS__</div>
__SECTIONS__
<footer>Fonte: <a href="https://bandi.mur.gov.it/">bandi.mur.gov.it</a>. Un bando con più settori compare
in ciascuno dei vostri settori. Ex SECS-P/07 = ECON-06/A, SECS-P/09 = ECON-09/A, SECS-P/11 = ECON-09/B.</footer>
</main>
<script>
const buttons = document.querySelectorAll('.filters button');
buttons.forEach(btn => btn.addEventListener('click', () => {
  buttons.forEach(b => b.classList.toggle('on', b === btn));
  const t = btn.dataset.tipo;
  document.querySelectorAll('.tipo-block').forEach(el => {
    el.hidden = t !== '*' && el.dataset.tipo !== t;
  });
}));
</script>
</body>
</html>
"""


def build_page(bandi, seen, today):
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Europe/Rome"))
    except Exception:
        now = datetime.now()
    tipi = [s[0] for s in SECTIONS] + [CHECK_LABEL]
    groups = [g[:2] for g in SECTOR_GROUPS] + [UNKNOWN_SECTOR]

    def card(b):
        d = deadline(b)
        when = html.escape(b["scadenza"])
        if d:
            days = (d - today).days
            when += " (oggi)" if days == 0 else " (domani)" if days == 1 else f" (tra {days} giorni)"
            if days <= URGENT_DAYS:
                when = f"<span class='urgent'>{when}</span>"
        first_seen = seen.get(b["id"], {}).get("visto_il", today.isoformat())
        badge = "<span class='badge'>NUOVO</span>" if (today - date.fromisoformat(first_seen)).days <= 7 else ""
        meta = [when]
        if b["posti"]:
            meta.append(f"posti: {b['posti']}")
        others = len(b["settori"]) - len(b["nostri"])
        if b["tipo"] == CHECK_LABEL:
            meta.append(f"sezione: {html.escape(b['sezione'])}")
        elif others > 0:
            meta.append(f"il bando comprende anche altri {others} settori")
        return (f"<div class='card'>{badge}<span class='ente'>{html.escape(b['ente'])}</span><br>"
                f"<a href='{b['url']}' target='_blank' rel='noopener'>{html.escape(b['titolo'])}</a>"
                f"<div class='meta'>{' &middot; '.join(meta)}</div></div>")

    nav, sections = [], []
    for code, name in groups:
        in_sector = [b for b in bandi if code in sectors_of(b)]
        if code == UNKNOWN_SECTOR[0] and not in_sector:
            continue
        anchor = re.sub(r"[^a-z0-9]+", "-", code.lower())
        nav.append(f"<a href='#{anchor}'>{html.escape(code)} ({len(in_sector)})</a>")
        body = []
        for tipo in tipi:
            items = sorted((b for b in in_sector if b["tipo"] == tipo), key=lambda b: deadline(b) or date.max)
            if items:
                body.append(f"<div class='tipo-block' data-tipo='{html.escape(tipo)}'>"
                            f"<h3>{html.escape(tipo)} ({len(items)})</h3>{''.join(card(b) for b in items)}</div>")
        sections.append(f"<section id='{anchor}'><h2>{html.escape(code)} <small>{html.escape(name)}</small></h2>"
                        + ("".join(body) or "<p class='empty'>Nessun bando aperto.</p>") + "</section>")

    present = [t for t in tipi if any(b["tipo"] == t for b in bandi)]
    filters = ["<button class='on' data-tipo='*'>Tutte le tipologie</button>"] + [
        f"<button data-tipo='{html.escape(t)}'>{html.escape(t.split(':')[0])}</button>" for t in present]
    return (PAGE_TEMPLATE
            .replace("__UPDATED__", now.strftime("%d/%m/%Y alle %H:%M"))
            .replace("__TOTAL__", str(len(bandi)))
            .replace("__URGENT__", str(URGENT_DAYS))
            .replace("__NAV__", "".join(nav))
            .replace("__FILTERS__", "".join(filters))
            .replace("__SECTIONS__", "\n".join(sections)))


def read_recipients(fallback):
    # I destinatari possono stare nel secret DESTINATARI (utile se il repository è pubblico)
    # oppure nel file destinatari.txt.
    if os.environ.get("DESTINATARI", "").strip():
        lines = re.split(r"[,;\s]+", os.environ["DESTINATARI"])
    else:
        lines = RECIPIENTS_FILE.read_text(encoding="utf-8").splitlines() if RECIPIENTS_FILE.exists() else []
    recipients = [l.strip() for l in lines if "@" in l and not l.strip().startswith("#")]
    return recipients or [fallback]


def send_email(subject, body_html):
    user = os.environ["GMAIL_INDIRIZZO"].strip()
    password = os.environ["GMAIL_PASSWORD"].replace(" ", "")
    recipients = read_recipients(user)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Monitor bandi MUR <{user}>"
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, password)
        s.sendmail(user, recipients, msg.as_string())


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    dry_run = "--dry-run" in sys.argv
    first_run = not SEEN_FILE.exists()
    seen = load_json(SEEN_FILE)
    state = load_json(STATE_FILE)
    today = date.today()

    try:
        found = collect()
    except SiteUnavailable as e:
        print(f"Il sito bandi.mur.gov.it non risponde: {e}")
        last_ok = date.fromisoformat(state.get("ultimo_controllo_riuscito", "2000-01-01"))
        if (today - last_ok).days >= MAX_DAYS_DOWN:
            sys.exit(f"Nessun controllo riuscito dal {last_ok:%d/%m/%Y}: verificare il sito a mano.")
        print("Riprovo al prossimo controllo.")
        return

    new = [b for bid, b in found.items() if bid not in seen]
    still_open = [b for bid, b in found.items() if bid in seen]
    # Almeno un'email con l'elenco completo ogni settimana (lunedì-domenica).
    last_recap = state.get("ultimo_riepilogo")
    recap_due = not last_recap or date.fromisoformat(last_recap).isocalendar()[:2] != today.isocalendar()[:2]
    print(f"Bandi aperti trovati: {len(found)} - nuovi: {len(new)} - riepilogo dovuto: {recap_due}")

    if new or recap_due:
        if first_run:
            subject = f"[Bandi MUR] {len(new)} bandi aperti ECON-06/ECON-09 (primo avvio)"
        elif new:
            subject = f"[Bandi MUR] {len(new)} nuovi bandi ECON-06/ECON-09"
        else:
            subject = f"[Bandi MUR] Riepilogo settimanale: {len(still_open)} bandi aperti"
        body = build_email(new, still_open, first_run, today)
        if dry_run:
            print(subject)
            print(BeautifulSoup(body, "html.parser").get_text("\n", strip=True))
        else:
            send_email(subject, body)
            state["ultimo_riepilogo"] = today.isoformat()
            print("Email inviata.")

    if not dry_run:
        for b in new:
            seen[b["id"]] = {"visto_il": today.isoformat(), "titolo": b["titolo"],
                             "ente": b["ente"], "tipo": b["tipo"]}
        state["ultimo_controllo_riuscito"] = today.isoformat()
        save_json(SEEN_FILE, seen)
        save_json(STATE_FILE, state)
    PAGE_FILE.parent.mkdir(exist_ok=True)
    PAGE_FILE.write_text(build_page(list(found.values()), seen, today), encoding="utf-8")


if __name__ == "__main__":
    main()
