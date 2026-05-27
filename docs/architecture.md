# Architectuur

## Tech stack
- Python 3.x, dependency management via `uv`
- PostgreSQL (via SQLAlchemy) voor opslag van verzonden nieuwsbrieven
- AWS S3 voor opslag van gegenereerde afbeeldingen
- Gmail IMAP voor ophalen van bronmails (label `ai_news`)
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
│   ├── log.py           # justlog setup
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
3. `ai.generate_ai_summary` → list[Article] (gecached als `_summary.jsonl`)
4. `ai.edit_articles` → per-artikel eindredactie van title + summary (gecached als `_edited.jsonl`)
5. `ai.select_articles_for_visuals` → indexen voor image en infographic
6. `ai.generate_ai_image` → header image + S3-URL
7. `ai.generate_infographic` → infographic + S3-URL
8. `formatter.create_html_email` → HTML
9. `database.add_to_database` → DB-record (gebruikt bij dedupe in volgende run)
10. `mailer.send_newsletter` → SMTP-verzending
11. `undelivered.handle_undelivered` → bounce-afhandeling

## Conventies
- Prompts staan los in `src/prompts/*.md`, geladen via `ai.load_prompt(name, **kwargs)`
- Cache-bestanden gebruiken `cache_file_prefix(schedule)` als prefix
- `_NAME` constants per model worden gebruikt in het Colofon
