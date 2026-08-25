---
title: "fix: nieuwsbrief zonder nieuws mag nooit verstuurd worden"
type: fix
status: active
date: 2026-08-25
source: gmail
gmail_thread_id: 1a0379dd7362bb21
gmail_url: https://mail.google.com/mail/u/0/#all/1a0379dd7362bb21
sender: HP's AI nieuwsbrief <nieuwsbrief@harmsen.nl>
subject: "HP's AI daily - 25 augustus"
---

# 🐛 fix: nieuwsbrief zonder nieuws mag nooit verstuurd worden

## Overview

Op 25 augustus 2026 ging om 08:25 CEST een daily naar 56 abonnees met precies één "artikel":

> **Geen bruikbaar nieuws beschikbaar**
> De ontvangen e-mail van AI Central bevat geen concrete, verifieerbare nieuwsfeiten die geschikt zijn voor de nieuwsbrief. De mail beschrijft een algemene tip over het analyseren van PDF-rapporten met AI, maar noemt geen specifieke tools, cijfers, datums of productlanceringen. De overige inhoud bestaat uit advertenties en een promotie van HubSpot. Er valt daarmee geen substantieel item te destilleren dat voldoet aan de selectiecriteria.

Dit is interne redenatie van het copywrite-model die als nieuwsbrief-inhoud bij klanten is beland. De pipeline heeft nergens gemerkt dat er niets te melden was, en Janitor is niet afgegaan.

Deze fix legt een harde kwaliteitspoort vóór verzending, escaleert naar Janitor in plaats van stil te falen, en repareert het ophaalvenster dat de honger veroorzaakte.

Dezelfde logfile legde een tweede bug bloot: de SMTP-verbinding brak halverwege de batch, 27 abonnees kregen niets, en de afsluitregel meldde alsnog `Sent to 56 recipients`. Die gaat mee in dit plan.

## Problem Statement

### Wat er feitelijk gebeurde

| Stap | Bewijs | Effect |
|---|---|---|
| 1. Daily van 24 aug draaide 's avonds, klaar 18:58 CEST | `data/app.log` | `update_last_sent_timestamp()` zette `last_sent.daily` op `2026-08-24T16:58Z` |
| 2. `get_raw_mail_text()` gebruikt die timestamp als `from_date` | `src/gmail.py:400` | Ophaalvenster voor de run van 25 aug 08:18 CEST was **13,5 uur** in plaats van ~24 uur |
| 3. Eén mail in label `y_ai_news` binnen dat venster | `cache/2026-08-25_emails.txt` is 2216 bytes, normaal ~8800 | Enige bron: reclamemail van `gptcentral@mail.beehiiv.com`, 24 aug 17:31Z |
| 4. Leegte-check kijkt alleen of de string leeg is | `main.py:73` | Eén reclamemail is niet leeg, dus de pipeline liep door |
| 5. `Summary` eist minimaal één artikel | `src/ai.py:56`, `Field(min_length=1, max_length=8)` | Het model **moest** iets opleveren en produceerde een meta-artikel met `links: []` |
| 6. Skip-filter matcht alleen twee vaste phrases | `src/ai.py:145`, `('wordt overgeslagen', 'wordt daarom overgeslagen')` | Deze formulering ontsnapte |
| 7. Geen enkele controle tussen samenvatting en verzending | `main.py:78-113` | Editor, header image, infographic (4 pogingen), database en `send_newsletter()` liepen allemaal door |
| 8. Geen `lg.error` in het inhoudelijke pad | `main.py:122-124` | `JanitorWebhookHandler` pikt alleen ERROR op, dus HP kreeg geen melding |

### De stille voorganger

De run van **24 augustus 's ochtends bestaat niet**. Er zijn geen cache-bestanden voor die datum en `data/mailerlog.txt` heeft geen `daily 2026-08-24` regels. De ochtendrun is afgebroken op `main.py:73-75`, en die tak logt `lg.warning`. Janitor luistert alleen naar ERROR, dus ook dat is onopgemerkt gebleven. Pas 's avonds is er handmatig een run gedaan.

Twee stille aborts op rij hebben zo geleid tot één zichtbare fout bij klanten.

### Er was wél nieuws

In het label `y_ai_news` zat op 24 augustus alleen de AI Central-reclame. Twee inhoudelijke Substack-mails van diezelfde dag (`claudecodemasterclass@substack.com` over Claude Code, `shrivu@substack.com` over agent harnesses) zitten in een ander Gmail-label en komen dus nooit in de pipeline. Dat is een aparte constatering over de labelfilter, niet de oorzaak van dit incident, maar het onderstreept dat de bron smal is en de pipeline daar niet tegen bestand is.

## Proposed Solution

Drie lagen tegen de lege nieuwsbrief, van goedkoop naar hard, plus een losstaande reparatie van de verzending. Elke laag alleen zo groot als nodig.

### Laag 1: venster kan niet meer uithongeren

`from_date` wordt geklemd op een minimum lookback, los van wanneer de vorige run toevallig draaide.

### Laag 2: vroege poort op de bron

Direct na het ophalen van de mails: te weinig bronnen betekent `lg.error` en afbreken, vóórdat er LLM- en image-kosten worden gemaakt.

### Laag 3: harde poort op de inhoud

Vlak vóór `send_newsletter()`: een structurele controle op de artikelen. Structureel, niet op formulering, want een phrase-lijst is per definitie incompleet. Dat is precies waarom laag 6 van de root cause faalde.

Bij elke afbreking: `lg.error` zodat Janitor afgaat. Nooit meer `lg.warning` op een pad dat de nieuwsbrief laat vervallen.

### Laag 4: verzending die niet liegt

`send_newsletter()` herstelt een verbroken SMTP-verbinding, telt eerlijk, en escaleert de abonnees die het niet gehaald hebben.

## Technical Considerations

### Wijziging 1: `src/gmail.py` — minimum lookback

In `get_raw_mail_text()` staat nu een `try/except` die bij een ontbrekend of kapot `last_sent.json` terugvalt op "24 uur geleden" (daily) of "een week geleden" (weekly). Die fallback wordt de ondergrens voor álle gevallen:

```python
# src/gmail.py, in get_raw_mail_text()
min_lookback = timedelta(days=1) if schedule == 'daily' else timedelta(weeks=1)
floor = datetime.now(timezone.utc) - min_lookback
try:
    with open(last_sent_file) as f:
        from_date = datetime.fromisoformat(json.load(f)['last_sent'][schedule])
    if from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=timezone.utc)
    from_date = min(from_date, floor)
except (FileNotFoundError, KeyError, json.JSONDecodeError, TypeError, ValueError):
    from_date = floor
```

Dit levert netto minder code op dan de huidige constructie, omdat `floor` nu op één plek staat.

Overlap met de vorige nieuwsbrief is geen probleem: `generate_ai_summary()` geeft de laatste vijf nieuwsbrieven mee via `get_last_newsletter_summaries()` en de copywrite-prompt dedupliceert daar expliciet op (`src/prompts/copywrite.md`, sectie DEDUPE-STRATEGIE).

### Wijziging 2: `src/gmail.py` — nieuwste mails eerst

Bijwerking van wijziging 1: de loop in `get_raw_mail_text()` doorloopt UIDs oplopend (oudste eerst) en breekt af zodra `text` boven 10.000 tekens komt. Met een breder venster betekent dat structureel dat het oudste nieuws wint en het nieuws van vanochtend eruit valt. Zonder deze aanpassing maakt wijziging 1 de nieuwsbrief ouder in plaats van beter.

Fix: verzamel de kwalificerende mails eerst, sorteer aflopend op datum, en vul dan tot de cap.

Deze wijziging hoort bij dit plan omdat hij een regressie van wijziging 1 voorkomt, niet als losse verbetering.

### Wijziging 3: `main.py` — vroege poort op de bron

```python
# main.py, vervangt regel 72-77
text = get_raw_mail_text(schedule, cached=cached, verbose=VERBOSE)
emails_dict = parse_emails_to_dict(text or '')
if len(emails_dict) < MIN_SOURCE_EMAILS:
    lg.error(f"Te weinig bronmails voor '{schedule}': {len(emails_dict)} "
             f"(minimaal {MIN_SOURCE_EMAILS}). Nieuwsbrief niet verstuurd.")
    return
```

`MIN_SOURCE_EMAILS = 2` als constante in `main.py`. Op de incidentdag was dit 1, dus de run was hier al gestopt. Op alle normale dagen ligt dit ruim boven 2 (`cache/*_emails.txt` van 20 t/m 23 augustus bevatten telkens meerdere bronblokken).

Let op: `parse_emails_to_dict()` wordt hiermee naar voren gehaald. De bestaande aanroep op `main.py:77` vervalt.

### Wijziging 4: `src/ai.py` — harde poort op de inhoud

Nieuwe functie, direct onder de Pydantic-modellen:

```python
# src/ai.py
MIN_ARTICLES = 3

def check_publishable(articles: list[dict]) -> str | None:
    """Geeft een reden terug waarom de nieuwsbrief niet verstuurd mag worden, of None."""
    if len(articles) < MIN_ARTICLES:
        return f'{len(articles)} artikelen, minimaal {MIN_ARTICLES} nodig'
    empty = [a.get('title', '?') for a in articles if not a.get('summary', '').strip()]
    if empty:
        return f'lege samenvatting bij: {", ".join(empty)}'
    return None
```

Aanroep in `main.py`, direct na `edit_articles()` en dus vóór de image-generatie:

```python
if reason := check_publishable(articles):
    lg.error(f"Nieuwsbrief '{schedule}' afgekeurd: {reason}. Niet verstuurd.")
    return
```

Waarom op de telling en niet op formulering: het meta-artikel van 25 augustus stond alleen. Een volgende variant zal anders geformuleerd zijn maar dezelfde vorm hebben, want een model dat geen nieuws vindt levert geen zes items op. Een lijst met verboden zinnen dekt alleen de fout van gisteren.

**Geen criterium op bronlinks.** Een eis van minimaal twee artikelen met een geldige link keurt, gemeten tegen de 15 bewaarde nieuwsbrieven in `cache/*_edited.jsonl`, 18 en 22 augustus onterecht af. `check_and_resolve_url()` strijkt zoveel links weg dat echte artikelen over AT&T, Nvidia en Goldman Sachs met nul links overblijven. De telling alleen scheidt wel schoon: 14 echte nieuwsbrieven erdoor, alleen 25 augustus tegengehouden.

`MIN_ARTICLES = 3` is een bewuste keuze van HP. De copywrite-prompt vraagt om minimaal 4 items, dus dit geeft één item speling voor een dunne zaterdag zonder dat een echte leegte erdoorheen glipt.

### Wijziging 5: `src/ai.py` — skip-filter vereenvoudigen

De phrase-filter op `src/ai.py:144-151` blijft staan als goedkope opschoning van "dit item stond al in een eerdere nieuwsbrief"-notities, maar is niet langer de vangnetlaag. Die rol neemt `check_publishable()` over. Uitbreiden van de phrase-lijst met de formulering van 25 augustus doen we bewust niet: dat is de fout van vandaag repareren en die van morgen laten staan.

### Wijziging 6: `main.py` — geen stille aborts meer

Alle paden die ertoe leiden dat er geen nieuwsbrief uitgaat, loggen `lg.error`. Dat is precies waarom de ochtendrun van 24 augustus onopgemerkt bleef. Concreet vervalt de `lg.warning` op `main.py:74`.

De uitzondering blijft `already_sent_today()` op `main.py:68-70`: dat is een normale, bedoelde overslag en blijft `lg.info`.

### Wijziging 7: `src/mailer.py` — SMTP-reconnect en eerlijke telling

Tweede bug uit dezelfde logfile, door HP in scope gebracht. Op 25 augustus 08:46 brak de SMTP-verbinding met `Connection reset by peer`. Daarna faalden 27 opeenvolgende verzendingen met `please run connect() first`, want `send_newsletter()` heeft geen reconnect. De afsluitregel meldde vervolgens `Sent to 56 recipients` terwijl `data/mailerlog.txt` er 29 telt.

Drie deelproblemen, drie kleine ingrepen in `send_newsletter()` (`src/mailer.py:165-229`):

**7a. Reconnect bij een verbroken verbinding.** De verbinding wordt lui opgezet en na een fout weggegooid, zodat de volgende poging een verse verbinding krijgt. Per ontvanger één retry. Dit volgt de regel "retry op transient, ERROR pas als de laatste poging faalt":

```python
# src/mailer.py, in send_newsletter()
sent, failed = [], []
server = None
for i, recipient in enumerate(subscribers, 1):
    for attempt in range(2):
        try:
            if server is None:
                server = connect_smtp()
            msg = create_message(recipient=recipient, subject=title,
                                 html_content=newsletter_html.replace('[EMAIL]', recipient),
                                 reply_to=REPLY_TO_EMAIL)
            msg['X-Campaign-ID'] = f"ai-newsletter-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
            server.sendmail(DISPLAY_FROM_EMAIL, [recipient], msg.as_string())
            lg.info(f'Email sent to {recipient}')
            sent.append(recipient)
            mailerlog(f'{schedule} {Day()} {recipient}')
            if '@harmsen.nl' not in recipient.lower():
                message_ids_to_delete.append(msg['Message-ID'])
            break
        except (smtplib.SMTPException, OSError) as e:
            quit_smtp(server)
            server = None  # forceer een verse verbinding bij de retry
            if attempt == 0:
                lg.warning(f'SMTP-fout bij {recipient}: {e}. Opnieuw verbinden...')
            else:
                lg.error(f'Verzending naar {recipient} definitief mislukt: {e}')
                failed.append(recipient)
```

De `logged_in_smtp()` context manager wordt hiermee gesplitst in `connect_smtp()` en `quit_smtp()`, omdat een `with`-blok rond de hele lus per definitie geen reconnect kan doen. De context manager zelf vervalt, hij heeft daarna geen aanroepers meer.

**7b. Eerlijke afsluitregel.** `len(subscribers)` vervangen door de werkelijke telling, en een ERROR als er iets misging:

```python
lg.info(f'Newsletter sending completed. Sent to {len(sent)} of {len(subscribers)} recipients.')
if failed:
    lg.error(f"Nieuwsbrief '{schedule}' niet bezorgd bij {len(failed)} van "
             f"{len(subscribers)} abonnees: {', '.join(failed)}. "
             f"Draai `python main.py {schedule} --resend` om alleen deze te herstellen.")
```

De foutmelding noemt de herstelactie expliciet, want die is niet vanzelfsprekend. `send_newsletter()` filtert al op `get_mailerlog(Day())` (`src/mailer.py:167-168`), dus een `--resend` mailt uitsluitend de gemiste abonnees. Zonder `--resend` blokkeert `already_sent_today()` de herstelrun.

**7c. Ruis bij het sluiten.** `lg.error` op het mislukken van `server.quit()` (`src/mailer.py:48`) wordt `lg.warning`. Een verbinding die niet netjes dichtgaat is geen incident en hoort Janitor niet wakker te maken, zeker niet nu een gebroken verbinding het normale pad is geworden.

## System-Wide Impact

- **Interaction graph:** `main()` roept `get_raw_mail_text()` aan, daarna `parse_emails_to_dict()`, `generate_ai_summary()`, `edit_articles()`, `select_articles_for_visuals()`, `generate_ai_image()`, `generate_infographic()`, `create_html_email()`, `add_to_database()`, `send_newsletter()`, `handle_undelivered()`. De nieuwe poorten zitten op positie 2 en 5 en breken de keten af met een `return`, geen exception. Alles erna draait dus niet, inclusief `add_to_database()`.
- **Error propagation:** `lg.error` gaat via `JanitorWebhookHandler` (`main.py:122-124`, actief bij `JANITOR_ERRORS=1`) naar Janitor. Er is geen retry op deze poorten, want de oorzaak is inhoudelijk en niet transient. Dat volgt de regel "retry op transient, error op definitief".
- **State lifecycle:** afbreken vóór `add_to_database()` betekent geen halve nieuwsbrief in de database. De cache-bestanden (`cache/<datum>_emails.txt`, `_summary.jsonl`) blijven wel staan, wat gewenst is: HP kan met `--cached` de situatie reproduceren. `last_sent.json` en `mailerlog.txt` worden niet aangeraakt, want die worden pas in `send_newsletter()` bijgewerkt. Een herstelrun later op de dag pakt dus hetzelfde venster.
- **Kosten:** de vroege poort (wijziging 3) draait vóór elke LLM-aanroep. De incidentrun van 25 augustus verbruikte een copywrite-call, twee editor-calls, een selectie-call, een header image en vier infographic-pogingen voor niets.
- **API surface parity:** er is één entrypoint (`main.py`). Geen andere interface die dezelfde route neemt.

## Acceptance Criteria

- [ ] `get_raw_mail_text('daily', ...)` kijkt altijd minstens 24 uur terug, ook als `last_sent.daily` twee uur geleden is
- [ ] `get_raw_mail_text('weekly', ...)` kijkt altijd minstens 7 dagen terug
- [ ] Bij een breder venster komen de nieuwste mails in de 10.000-tekens-selectie, niet de oudste
- [ ] Bij minder dan 2 bronmails: `lg.error` en geen enkele LLM-aanroep
- [ ] Bij minder dan 3 artikelen: `lg.error` en geen verzending
- [ ] Bij een lege samenvatting in enig artikel: `lg.error` en geen verzending
- [ ] Geen valse afkeuring: alle 14 bewaarde echte nieuwsbrieven komen er wel doorheen
- [ ] Elk pad dat de nieuwsbrief laat vervallen logt op ERROR, niet op WARNING
- [ ] Afgekeurde nieuwsbrieven komen niet in de database en werken `last_sent.json` niet bij
- [ ] Regressietest: de exacte artikellijst uit `cache/2026-08-25_summary.jsonl` wordt afgekeurd
- [ ] Een verbroken SMTP-verbinding leidt tot een reconnect, niet tot een reeks `please run connect() first`
- [ ] De afsluitregel noemt het werkelijke aantal bezorgde mails, niet `len(subscribers)`
- [ ] Bij mislukte bezorging: `lg.error` met de namen van de gemiste abonnees en de `--resend` herstelactie
- [ ] Een geslaagde retry logt WARNING, geen ERROR
- [ ] `uv run tests/main.py` slaagt volledig

## Test Plan

Uitbreiden van `tests/main.py`, in de bestaande stijl (losse functies, geregistreerd in de lijst in `main()`). Geen pytest introduceren.

```python
# tests/main.py
def test_publishable_rejects_incident_2026_08_25():
    """De echte payload van 25 augustus moet worden afgekeurd."""
    from src.ai import check_publishable
    articles = [{
        'title': 'Geen bruikbaar nieuws beschikbaar',
        'summary': 'De ontvangen e-mail van AI Central bevat geen concrete...',
        'links': [],
        'sources': ['AI Central, Kris'],
    }]
    assert check_publishable(articles) is not None

def test_publishable_accepts_normal_newsletter():
    """Een normale set van 4 artikelen met links komt erdoor."""

def test_publishable_accepts_newsletter_without_links():
    """Regressie op 18 en 22 augustus: 6 echte artikelen waarvan er maar een
    een link overhoudt, mag niet worden afgekeurd."""

def test_publishable_rejects_empty_summary():
    """Artikel met een lege of whitespace-only summary wordt afgekeurd."""

def test_lookback_floor_when_last_send_was_recent():
    """last_sent 2 uur geleden -> from_date ligt toch 24 uur terug."""

def test_lookback_uses_last_sent_when_older_than_floor():
    """last_sent 3 dagen geleden -> from_date blijft die 3 dagen."""

def test_smtp_reconnects_after_broken_connection():
    """Mock-server gooit SMTPServerDisconnected bij ontvanger 3.
    Verwacht: reconnect, alle ontvangers alsnog bezorgd, geen ERROR."""

def test_smtp_reports_actual_count_not_subscriber_count():
    """Twee ontvangers falen definitief -> afsluitregel noemt het echte aantal
    en er staat een ERROR met beide adressen in."""
```

De eerste test is de belangrijkste: die faalt op de huidige code en slaagt na de fix. Voer hem uit vóórdat je iets wijzigt, om te bevestigen dat hij het incident echt vangt.

Handmatige verificatie na implementatie:

```bash
~/.local/bin/uv run tests/main.py
~/.local/bin/uv run python main.py daily --cached --dry-run
```

De tweede draait op de cache van vandaag, die nog het incidentmateriaal bevat, en moet afbreken met een ERROR-regel in `data/app.log`.

## Success Metrics

- Nul nieuwsbrieven met minder dan 3 artikelen bij abonnees, permanent
- Elke afgebroken run levert binnen een minuut een Janitor-melding op
- Geen stille dagen meer: een ontbrekende run is zichtbaar in Janitor in plaats van pas achteraf in `mailerlog.txt`

## Dependencies & Risks

- **`JANITOR_ERRORS` staat op `1`**, bevestigd door HP. De escalatie via `JanitorWebhookHandler` (`main.py:122-124`) is dus actief. Geen actie nodig.
- **Vals-positieve afkeuringen.** Een echt dunne dag kan nu leiden tot geen nieuwsbrief. Dat is de bedoeling en het is wat HP heeft gevraagd, maar het maakt de Janitor-melding actiegericht: HP moet er iets mee kunnen. De foutmelding bevat daarom de concrete reden en de getallen.
- **Bredere venster geeft meer overlap.** Opgevangen door de bestaande dedupe tegen de laatste vijf nieuwsbrieven. Als er in de praktijk toch herhaling optreedt, is dat een prompt-kwestie en geen reden om het venster weer te versmallen.
- **Geen wijziging aan de labelfilter.** `FILTER_ON_LABEL = 'y_ai_news'` blijft zoals het is. Dat de twee Substack-mails van 24 augustus buiten dat label vielen is een aparte constatering, zie hieronder.

## Buiten scope

**Bronbreedte.** Het label `y_ai_news` vangt niet alle AI-nieuwsbrieven die binnenkomen. Twee inhoudelijke Substack-mails van 24 augustus zaten in een ander label. Meer bronnen in het label betekent minder kans dat de nieuwe poort ooit dichtslaat. Dit is een Gmail-filterkwestie, geen code, en dus niets om hier te bouwen.

## Sources & References

### Aanleiding

- Verzonden nieuwsbrief: Gmail thread `1a0379dd7362bb21`, "HP's AI daily - 25 augustus", 25 aug 2026 08:31 CEST
- Melding van HP via `/hp:mailfix`

### Bewijsmateriaal in de repo

- `data/app.log` regels 4872-4983: de volledige run van 25 augustus, inclusief de vier infographic-pogingen en de SMTP-breuk
- `cache/2026-08-25_summary.jsonl`: het meta-artikel, één regel, `links: []`
- `cache/2026-08-25_emails.txt`: 2216 bytes, één bron
- `cache/2026-08-2{0,1,2,3}_emails.txt`: 6656 tot 8853 bytes ter vergelijking
- `data/mailerlog.txt`: geen `daily 2026-08-24` regels, 29 regels voor `daily 2026-08-25`

### Raakvlakken in de code

- `main.py:63-115` orkestratie
- `main.py:122-124` Janitor-handler
- `src/gmail.py:375-435` `get_raw_mail_text()`
- `src/gmail.py:448-460` `parse_emails_to_dict()`
- `src/ai.py:53-57` `Summary` model met `min_length=1`
- `src/ai.py:144-151` bestaande skip-filter
- `src/mailer.py:165-229` `send_newsletter()`
- `src/prompts/copywrite.md:43` "minimaal 4 en maximaal {max_articles} items"
