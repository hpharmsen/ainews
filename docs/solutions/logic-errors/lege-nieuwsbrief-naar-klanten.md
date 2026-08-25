---
title: Lege nieuwsbrief naar 56 abonnees door stil krimpend ophaalvenster
category: logic-errors
date: 2026-08-25
component: newsletter-pipeline
tags: [quality-gate, silent-failure, janitor, logging, llm-output, smtp, time-window]
commit: 38a8adc
plan: docs/plans/2026-08-25-001-bugfix-lege-nieuwsbrief-verstuurd-plan.md
---

# Lege nieuwsbrief naar 56 abonnees door stil krimpend ophaalvenster

## Symptoom

Op 25 augustus 2026 om 08:31 CEST kregen 56 abonnees een daily met precies één "artikel":

> **Geen bruikbaar nieuws beschikbaar**
> De ontvangen e-mail van AI Central bevat geen concrete, verifieerbare nieuwsfeiten die geschikt zijn voor de nieuwsbrief. [...] Er valt daarmee geen substantieel item te destilleren dat voldoet aan de selectiecriteria.

Dit is de interne redenatie van het copywrite-model, verstuurd als nieuwsbrief-inhoud. Janitor is niet afgegaan, dus het kwam pas aan het licht toen HP zijn eigen nieuwsbrief las.

## Root cause

Een keten van vier zwakke plekken, waarvan geen enkele op zichzelf een fout was.

**1. Het ophaalvenster kromp doordat de vorige run laat draaide.**

`get_raw_mail_text()` gebruikte `last_sent.json` direct als ondergrens van het venster. De run van 24 augustus draaide 's avonds (klaar 18:58 CEST) in plaats van 's ochtends, dus `last_sent.daily` stond op `2026-08-24T16:58Z`. Het venster voor de run van 08:18 de volgende ochtend besloeg daardoor 13,5 uur in plaats van 24. Er kwam precies één mail binnen: pure reclame. `cache/2026-08-25_emails.txt` was 2216 bytes tegen de gebruikelijke ~8800.

Het patroon: **een venster dat is verankerd aan "wanneer draaide ik voor het laatst" krimpt bij elke late run, en herstelt zichzelf niet.**

**2. De leegte-check keek naar de verkeerde vraag.**

```python
if not text or not text.strip():   # een reclamemail is niet leeg
```

"Is er tekst" is niet hetzelfde als "is er nieuws".

**3. Het datamodel dwong output af waar niets was.**

```python
class Summary(BaseModel):
    articles: Annotated[list[Article], Field(min_length=1, max_length=8)]
```

`min_length=1` betekent dat het model verplicht was iets op te leveren. Met alleen een reclamemail als input produceerde het een uitleg waarom het niets kon opleveren. Dat is voorspelbaar gedrag, geen modelfout.

**4. Niets tussen samenvatting en verzending, en geen enkele ERROR.**

De editor, header image, infographic (vier pogingen), database en `send_newsletter()` liepen allemaal door. In het hele inhoudelijke pad stond geen `lg.error`, en `JanitorWebhookHandler` luistert alleen op ERROR.

**De stille voorganger.** De ochtendrun van 24 augustus was al afgebroken op de leegte-check, die `lg.warning` logde. Onopgemerkt. Twee stille aborts op rij leidden tot één zichtbare fout bij klanten.

## Oplossing

### Venster klemmen op een ondergrens

```python
# src/gmail.py
MIN_LOOKBACK = {'daily': timedelta(days=1), 'weekly': timedelta(weeks=1)}

def fetch_window_start(schedule: str) -> datetime:
    """Begin van het ophaalvenster: last_sent, maar altijd minstens MIN_LOOKBACK terug."""
    floor = datetime.now(timezone.utc) - MIN_LOOKBACK[schedule]
    try:
        with open(LAST_SENT_FILE, 'r') as f:
            from_date = datetime.fromisoformat(json.load(f)['last_sent'][schedule])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return floor
    if from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=timezone.utc)
    return min(from_date, floor)
```

De bestaande fallback voor een ontbrekend `last_sent.json` werd de ondergrens voor álle gevallen. Netto minder code, want `floor` staat nu op één plek.

**Bijwerking die je moet meenemen:** de selectielus liep UIDs oplopend (oudste eerst) en brak af bij een cap van 10.000 tekens. Een breder venster maakt de nieuwsbrief dan juist ouder in plaats van beter. Sorteer nieuwste-eerst, anders werkt de fix averechts.

### Twee poorten, allebei op ERROR

```python
# main.py, poort 1: voor elke LLM-call
emails_dict = parse_emails_to_dict(text or '')
if len(emails_dict) < MIN_SOURCE_EMAILS:
    lg.error(f"Te weinig bronmails voor '{schedule}': {len(emails_dict)} "
             f"(minimaal {MIN_SOURCE_EMAILS}). Nieuwsbrief niet verstuurd.")
    return

# main.py, poort 2: na edit_articles, voor de dure image-generatie
if reason := check_publishable(articles):
    lg.error(f"Nieuwsbrief '{schedule}' afgekeurd: {reason}. Niet verstuurd.")
    return
```

Beide breken af met `return`, geen exception. Dus geen DB-record, geen verzending, en `last_sent.json` en `mailerlog.txt` blijven ongemoeid, zodat een herstelrun hetzelfde venster pakt.

## De belangrijkste les: valideer je kwaliteitspoort tegen historische data

Het plan eiste oorspronkelijk óók "minimaal 2 artikelen met een geldige bronlink". De redenering klonk sluitend: een model dat geen nieuws vindt, kan ook geen bron opgeven. Het incident-artikel had inderdaad `links: []`.

Gemeten tegen alle 15 bewaarde nieuwsbrieven in `cache/*_edited.jsonl`:

| Criterium | Echte nieuwsbrieven door | Vals afgekeurd |
|---|---|---|
| `count >= 3` | 14/14 | 0 |
| `count >= 3 AND met_link >= 2` | 12/14 | **18 en 22 augustus** |

`check_and_resolve_url()` strijkt zoveel links weg (link rot, paywalls, bot-blocking) dat echte artikelen over AT&T, Nvidia en Goldman Sachs met nul links overblijven. Op 22 augustus hielden 6 legitieme artikelen samen één link over.

Het criterium is geschrapt. Er staat nu een regressietest die de valse afkeuring vastlegt:

```python
def test_publishable_accepts_newsletter_without_links():
    """Regressie op 18 en 22 augustus: check_and_resolve_url() strijkt zoveel links weg
    dat een echte nieuwsbrief er maar een overhoudt. Die mag niet worden afgekeurd."""
    assert check_publishable(_articles(6, with_links=1)) is None
```

**Generaliseer dit:** een kwaliteitspoort die op productie afbreekt heeft twee faalkosten, niet één. Een gemiste bad case is zichtbaar, een valse afkeuring is dat vaak niet, en die kost hier een hele dag nieuwsbrief. Draai elk criterium eerst tegen álle bewaarde historische output voordat je het in de pipeline zet. Dit project had die historie toevallig in `cache/` liggen; dat is goud waard en een reden om zulke caches niet te agressief op te ruimen.

## Tweede bug uit dezelfde logfile

Altijd de rest van de logfile lezen. Op 08:46 brak de SMTP-verbinding:

```
ERROR Error sending to kristinabableyan@gmail.com: Connection unexpectedly closed: [Errno 54] Connection reset by peer
ERROR Error sending to brianlow123@gmail.com: please run connect() first
... 26x meer ...
INFO  Newsletter sending completed. Sent to 56 recipients.
```

27 abonnees kregen niets, en de afsluitregel meldde er alsnog 56 omdat hij `len(subscribers)` printte. `mailerlog.txt` telde er 29. Erger nog: `already_sent_today()` ziet de dag daarna als afgehandeld, dus die 27 krijgen ook bij een volgende run niets.

De contextmanager was hier de blokkade:

```python
with logged_in_smtp() as server:      # kan per definitie niet reconnecten
    for recipient in subscribers:
        server.sendmail(...)
```

Gesplitst in `connect_smtp()` / `quit_smtp()`, met een retry per ontvanger die de dode verbinding weggooit. WARNING op de retry, ERROR pas als de laatste poging faalt, en een foutmelding die de herstelactie noemt:

```python
lg.info(f"Newsletter sending completed. Sent to {len(sent)} of {len(subscribers)} recipients.\n")
if failed:
    lg.error(f"Nieuwsbrief '{schedule}' niet bezorgd bij {len(failed)} van "
             f"{len(subscribers)} abonnees: {', '.join(failed)}. "
             f"Draai `python main.py {schedule} --resend` om alleen deze te herstellen.")
```

## Preventie

1. **Elk pad dat het eindproduct laat vervallen logt ERROR, nooit WARNING.** Janitor luistert op ERROR. Een `lg.warning` in een abort-tak is een fout die niemand ziet. Uitzondering: een bedoelde overslag zoals `already_sent_today()` blijft INFO.
2. **Een afsluitregel telt wat er gebeurde, niet wat de bedoeling was.** `len(subscribers)` in een succesmelding is een leugen zodra er ook maar één verzending faalt.
3. **Verifieer de poort tegen historische output** voordat hij live gaat, en bewaar die historie.
4. **Vensters die aan "vorige run" hangen krijgen een ondergrens.** Anders krimpt een late run het volgende venster, en dat herstelt zichzelf niet.
5. **Een `min_length=1` op LLM-output dwingt een antwoord af waar er geen is.** Als je dat niet kunt versoepelen, vang de degenererende output dan verderop af met een structurele check.
6. **Filter niet op formulering.** De bestaande vangnetfilter matchte op `('wordt overgeslagen', 'wordt daarom overgeslagen')` en miste deze variant. Een lijst met verboden zinnen dekt alleen de fout van gisteren. Tel liever.

## Verificatie

TDD met een expliciete rode commit (`b7f87b5`, 8/19 groen), daarna 19/19.

Omdat een `ImportError` een zwak rood is, is elke nieuwe gedragsassertie apart gemuteerd om te bewijzen dat de test echt iets vangt:

| Mutatie | Test faalt met |
|---|---|
| `check_publishable` keurt nooit af | `de lege nieuwsbrief van 25 augustus kwam er doorheen` |
| `fetch_window_start` zonder klem | `venster is maar 2:00:00, verwacht >= 24u` |
| Geen reconnect in `send_newsletter` | reconnect-test faalt |
| Afsluitregel telt `len(subscribers)` | `Sent to 4 recipients` terwijl er 3 aankwamen |

De laatste twee mutaties reproduceren de productiefout letterlijk. Dat is de test die je wil hebben: hij faalt met exact de tekst die in de logfile stond.

## Nog open

- **Bronbreedte.** Het label `y_ai_news` vangt niet alle AI-nieuwsbrieven. Twee inhoudelijke Substack-mails van 24 augustus zaten in een ander label en bereikten de pipeline nooit. Meer bronnen in het label betekent minder kans dat de poort ooit dichtslaat. Gmail-filterkwestie, geen code.
- **`data/undelivered.json` staat in `.gitignore` maar is tracked** (van vóór die regel), dus elke run maakt de working tree vies. `git rm --cached data/undelivered.json` lost dat op.
