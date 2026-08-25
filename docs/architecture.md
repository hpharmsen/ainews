# Architectuur

## Tech stack
- Python 3.x, dependency management via `uv`
- PostgreSQL (via SQLAlchemy) voor opslag van verzonden nieuwsbrieven
- AWS S3 voor opslag van gegenereerde afbeeldingen
- Gmail IMAP voor ophalen van bronmails (label `y_ai_news`, zie `gmail.FILTER_ON_LABEL`)
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
│   ├── undelivered.py   # Afhandeling van bounces
│   └── prompts/         # Markdown prompt-templates met {placeholders}
├── cache/               # Gecachte AI-output en email-payloads
└── data/                # Runtime data (last_sent.json, mailerlog, etc.)
```

## AI-modellen (in `src/ai.py`)
- `COPY_WRITE_MODEL` (Claude Sonnet 4.6) — selectie + samenvattingen
- `EDITOR_MODEL` (Claude Opus 4.7) — eindredactie per artikel (title + summary)
- `SELECTION_MODEL` (GPT-5) — kiezen welke artikelen visuals krijgen
- `ART_MODEL` (GPT Image 2) — header image
- `INFOGRAPHIC_MODEL` (Nano Banana 2) — infographic

## Data flow (newsletter run)

1. `parse_command_line` → schedule (`daily`/`weekly`) + flags
2. `gmail.get_raw_mail_text` → ruwe mailtekst (gecached in `cache/`)
   - `gmail.fetch_window_start` bepaalt het venster: `last_sent`, maar altijd minstens
     24 uur (daily) of 7 dagen (weekly) terug
   - mails worden nieuwste-eerst geselecteerd tot `MAX_TOTAL_LEN`
3. **Poort 1**: minder dan `MIN_SOURCE_EMAILS` bronmails → `lg.error` en stoppen
4. `ai.generate_ai_summary` → list[Article] (gecached als `_summary.jsonl`)
5. `ai.edit_articles` → per-artikel eindredactie van title + summary (gecached als `_edited.jsonl`)
6. **Poort 2**: `ai.check_publishable` → bij een reden `lg.error` en stoppen
7. `ai.select_articles_for_visuals` → indexen voor image en infographic
8. `ai.generate_ai_image` → header image + S3-URL
9. `ai.generate_infographic` → infographic + S3-URL
10. `formatter.create_html_email` → HTML
11. `database.add_to_database` → DB-record (gebruikt bij dedupe in volgende run)
12. `mailer.send_newsletter` → SMTP-verzending, met reconnect per ontvanger
13. `undelivered.handle_undelivered` → bounce-afhandeling

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

## Conventies
- Prompts staan los in `src/prompts/*.md`, geladen via `ai.load_prompt(name, **kwargs)`
- Cache-bestanden gebruiken `cache_file_prefix(schedule)` als prefix
- `_NAME` constants per model worden gebruikt in het Colofon
