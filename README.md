# Monitor bandi MUR – ECON-06/A, ECON-09/A, ECON-09/B

Controlla due volte al giorno [bandi.mur.gov.it](https://bandi.mur.gov.it/) e invia un'email quando esce un nuovo bando **aperto** nei gruppi:

- **13/ECON-06**: ECON-06/A Economia aziendale (ex SECS-P/07)
- **13/ECON-09**: ECON-09/A Finanza aziendale (ex SECS-P/09) e ECON-09/B Economia degli intermediari finanziari (ex SECS-P/11)

Sezioni del sito monitorate: ricercatori a tempo determinato (RTT, RTD, PNRR), contratti di ricerca, incarichi di ricerca, incarichi post-doc e chiamate di professori associati e ordinari.

## Come funziona

- `monitor.py` interroga il sito con i filtri per gruppo scientifico-disciplinare. Per ricercatori e professori usa anche i vecchi settori 13/B1 e 13/B4. Scarta i bandi in cui nessuno dei settori elencati è ECON-06/ECON-09.
- I bandi aperti **senza settore indicato** finiscono in "Da verificare" solo se il titolo contiene parole specifiche (aziendale, finanza, finanziario, bilancio, accounting, banking, corporate…).
- `seen.json` tiene l'elenco dei bandi già segnalati, così ogni bando arriva **una sola volta**. `stato.json` ricorda l'ultimo controllo riuscito e l'ultimo riepilogo.
- **Email**: arriva quando esce un bando nuovo e contiene solo le novità. Ogni lunedì arriva anche un riepilogo con i bandi in scadenza nei 7 giorni successivi.
- **Pagina web** (`docs/index.html`): tutti i bandi ancora aperti, divisi per settore e tipologia, aggiornata a ogni controllo. Le email contengono il link alla pagina se la variabile `PAGE_URL` del repository è impostata. Altrimenti l'elenco completo è direttamente nell'email.
- GitHub Actions esegue lo script due volte al giorno. GitHub può ritardare l'avvio anche di qualche ora.
- **Se il sito del MUR non risponde**, il controllo viene saltato senza errori. Se non risponde per 2 giorni di fila, GitHub avvisa via email il proprietario del repository. Lo stesso succede se il sito cambia struttura.

## Configurazione (una sola volta, circa 15 minuti)

Serve **un solo account GitHub**, quello di chi gestisce il monitor. Gli altri ricevono solo le email.

### 1. Password per le app di Gmail

Lo script invia le email da un account Gmail. Potete usarne uno personale o crearne uno apposito per il gruppo.

1. Sull'account Gmail attiva la **verifica in due passaggi** (Account Google → Sicurezza).
2. Vai su <https://myaccount.google.com/apppasswords>, crea una password per le app chiamata "Monitor bandi" e copia il codice di 16 caratteri.

### 2. Repository su GitHub

1. Crea un nuovo repository su GitHub, per esempio `monitor-bandi-mur`.
2. Carica questi file, compresa la cartella `.github/workflows/`.

Il repository può essere **pubblico**: non contiene password né email. Restano private solo nei secrets.

### 3. Secrets

Nel repository vai su **Settings → Secrets and variables → Actions → New repository secret** e crea:

| Nome              | Valore                                                         |
|-------------------|----------------------------------------------------------------|
| `GMAIL_INDIRIZZO` | l'indirizzo Gmail che invia gli avvisi                         |
| `GMAIL_PASSWORD`  | la password per le app di 16 caratteri                         |
| `DESTINATARI`     | le email dei destinatari separate da virgola                   |

Non scrivere mai email o password nei file del repository. `destinatari.txt` serve solo per le prove sul proprio PC ed è escluso da git.

### 4. Pagina web con i bandi aperti

1. **Settings → Pages**. In "Build and deployment" scegli **Deploy from a branch**, branch **main**, cartella **/docs**, poi **Save**.
2. **Settings → Secrets and variables → Actions → scheda Variables → New repository variable**. Name: `PAGE_URL`, Value: l'indirizzo della pagina (es. `https://NOMEUTENTE.github.io/monitor-bandi-mur/`).

La pagina contiene `noindex`, quindi i motori di ricerca non la mostrano. La vede solo chi ha il link.

### 5. Primo avvio

Vai su **Actions → Monitor bandi MUR → Run workflow**.

## Modifiche frequenti

- **Aggiungere o togliere destinatari**: aggiorna il secret `DESTINATARI` (Settings → Secrets and variables → Actions → matita accanto a DESTINATARI).
- **Cambiare settori**: modifica `GSD` e `OLD_SECTORS` all'inizio di `monitor.py`.
- **Cambiare orari**: modifica la riga `cron` in `.github/workflows/monitor.yml`. Gli orari sono in UTC.
- **Provarlo sul proprio PC senza inviare email**: `pip install -r requirements.txt` e poi `python monitor.py --dry-run`.
