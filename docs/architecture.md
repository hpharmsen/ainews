# Architectuur

## Tech stack
- Python 3.x, dependency management via `uv`
- PostgreSQL (via SQLAlchemy) voor opslag van verzonden nieuwsbrieven
- AWS S3 voor opslag van gegenereerde afbeeldingen
- Gmail IMAP voor ophalen van bronmails (label `y_ai_news`, zie `gmail.FILTER_ON_LABEL`) en
  voor het afhandelen van binnengekomen post (label `nieuwsbrief`, zie `replies.LABEL`)
- SMTP voor verzending naar abonnees
- LLMs via `justai` (Claude, GPT, Gemini)

## Project layout

```
ainews/
├── main.py              # Entry point, command parsing, orchestration
├── src/
│   ├── ai.py            # AI summary, art direction, image en infographic generatie
│   ├── gmail.py         # IMAP ophalen en parsen van bronmails
│   ├── formatter.py     # HTML-mail template (incl. Colofon)
│   ├── mailer.py        # SMTP-verzending + log
│   ├── database.py      # Newsletter-opslag, cache helpers
│   ├── subscribers.py   # Abonnee-administratie
│   ├── s3.py            # S3 upload van images
│   ├── replies.py       # Afhandelaar van het label nieuwsbrief
│   ├── undelivered.py   # Bouncetelling en undeliverable-drempels
│   └── prompts/         # Markdown prompt-templates met {placeholders}
├── cache/               # Gecachte AI-output en email-payloads
└── data/                # Runtime data (last_sent.json, mailerlog, etc.)
```

## AI-modellen (in `src/ai.py`)
- `COPY_WRITE_MODEL` (Claude Sonnet 4.6) — selectie + samenvattingen
- `EDITOR_MODEL` (Claude Opus 4.7) — eindredactie per artikel (title + summary)
- `CLASSIFY_MODEL` (Jev 1.13 via OpenRouter) — categorie van binnengekomen post. Een System
  One model: het genereert geen tekst maar geeft per categorie een gekalibreerde kans, in
  tienden van een seconde. Aanroep via `Model.classify()`, niet via `prompt()`.
- `SELECTION_MODEL` (GPT-5) — kiezen welke artikelen visuals krijgen
- `ART_MODEL` (GPT Image 2) — header image
- `INFOGRAPHIC_MODEL` (Nano Banana 2) — infographic

## Data flow (newsletter run)

1. `parse_command_line` → schedule (`daily`/`weekly`) + flags
2. `replies.handle_replies` → eerste pass over het label `nieuwsbrief`, vóór de abonneelijst
   wordt opgehaald. Draait niet bij `--dry-run`
3. `gmail.get_raw_mail_text` → ruwe mailtekst (gecached in `cache/`)
   - `gmail.fetch_window_start` bepaalt het venster: `last_sent`, maar altijd minstens
     24 uur (daily) of 7 dagen (weekly) terug
   - mails worden nieuwste-eerst geselecteerd tot `MAX_TOTAL_LEN`
4. **Poort 1**: minder dan `MIN_SOURCE_EMAILS` bronmails → `lg.error` en stoppen
5. `ai.generate_ai_summary` → list[Article] (gecached als `_summary.jsonl`)
6. `ai.edit_articles` → per-artikel eindredactie van title + summary (gecached als `_edited.jsonl`)
7. **Poort 2**: `ai.check_publishable` → bij een reden `lg.error` en stoppen
8. `ai.select_articles_for_visuals` → indexen voor image en infographic
9. `ai.generate_ai_image` → header image + S3-URL
10. `ai.generate_infographic` → infographic + S3-URL
11. `formatter.create_html_email` → HTML
12. `database.add_to_database` → DB-record (gebruikt bij dedupe in volgende run)
13. `mailer.send_newsletter` → SMTP-verzending, met reconnect per ontvanger
14. `replies.handle_replies` → tweede pass, 60 seconden na verzending. Bounces en
    delay-meldingen komen pas ná verzending binnen

## Afhandelaar van het label `nieuwsbrief` (`src/replies.py`)

Eén pass over één IMAP-folder, en dat label is de enige bron, ook voor bounces. De volgorde
binnen de pass draagt de garanties:

1. Filteren is goedkoop en gebeurt vóór het model: post van `@harmsen.nl` valt weg (het
   Gmail-filter labelt hele threads, dus de verzonden nieuwsbrieven en HP's eigen antwoorden
   staan er ook in), en zo ook post met een `Date` op of vóór het tijdstempel
2. `ai.classify_reply` → `afmelding`, `delay`, `bounce` of `hp`. Onder `MIN_CONFIDENCE` (0.90)
   wordt het `hp`: twijfel leidt nooit tot een uitschrijving of een verwijdering
3. Per categorie één actie. Bij `afmelding` alleen uitschrijven na een geslaagde lookup, want
   `update_subscription()` logt `lg.error` als er geen rij wordt geraakt en een afmelding van
   een onbekend adres moet stil verlopen. Bij `bounce` eerst verwijderen en pas bij succes
   tellen, zodat een mislukte verwijdering niet tot dubbeltellen leidt. Bij `delay` alleen
   verwijderen. Bij `hp` gebeurt niets
4. Elke uitgevoerde actie schrijft één tab-gescheiden regel naar `data/replieslog.txt`. Dat is
   het enige spoor zodra een bericht in de prullenbak ligt, en dus het vangnet onder een
   misclassificatie
5. Het tijdstempel in `data/replies_seen.json` is het moment waarop de pass begón, zodat post
   die tijdens de pass binnenkomt de volgende keer alsnog wordt gezien. Het schuift alleen op
   als de pass geen fout raakte

Verwijderen gaat per bericht via het UID in de labelfolder, nooit per thread: de overige
berichten in dezelfde thread blijven staan.

## Kwaliteitspoorten

Er gaat nooit een nieuwsbrief uit zonder inhoud. Twee poorten breken de run af met `return`,
geen exception, dus alles erna draait niet: geen DB-record, geen verzending, `last_sent.json`
en `mailerlog.txt` blijven ongemoeid en een herstelrun pakt hetzelfde venster.

| Poort | Waar | Criterium |
|---|---|---|
| Bron | `main.py`, na ophalen | minstens `MIN_SOURCE_EMAILS` (2) bronmails |
| Inhoud | `main.py`, na `edit_articles` | minstens `MIN_ARTICLES` (3) artikelen, geen lege samenvatting |

De inhoudspoort telt bewust geen bronlinks: `check_and_resolve_url()` strijkt zoveel links
weg dat echte nieuwsbrieven regelmatig op nul uitkomen.

## Foutmelding en escalatie
- Alles gaat via `justlog.lg`. Bij `JANITOR_ERRORS=1` hangt `JanitorWebhookHandler` aan de logger.
- Janitor luistert op ERROR. **Elk pad dat de nieuwsbrief laat vervallen logt daarom `lg.error`**,
  nooit `lg.warning`. Een stille abort is precies hoe het incident van 25 augustus 2026 onopgemerkt bleef.
- Transient faalgedrag (SMTP-breuk, rate limits, model-overload) logt `lg.warning` per poging en
  `lg.error` pas als de laatste poging faalt.
- De afhandelaar uit `src/replies.py` breekt de nieuwsbrief-run nooit af. Elke fout wordt daar
  één `lg.error` en een terugkeer; het tijdstempel schuift dan niet op, dus de volgende run doet
  het werk over. Geen enkel pad roept `exit()` aan. Een afmelding van een onbekend adres is de
  uitzondering die juist géén `lg.error` mag geven: er valt niets uit te schrijven en het
  gebeurt te zelden voor een Janitor-issue.

## Conventies
- Prompts staan los in `src/prompts/*.md`, geladen via `ai.load_prompt(name, **kwargs)`
- Cache-bestanden gebruiken `cache_file_prefix(schedule)` als prefix
- `_NAME` constants per model worden gebruikt in het Colofon
