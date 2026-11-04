---
title: Eén IMAP-breuk werd zestien Janitor-issues door een id in de logregel
category: logic-errors
date: 2026-11-04
component: logging
tags: [justlog, janitor, fingerprint, deduplication, imap, connection-errors, retry]
commit: e234306
janitor: "00194-00209"
---

# Eén IMAP-breuk werd zestien Janitor-issues door een id in de logregel

## Symptoom

Op 4 november 2026 om 08:54 viel de IMAP-verbinding met Gmail weg tijdens `handle_replies`
(label `nieuwsbrief`). Bij bericht 6 kwam een Connection reset, bij de 17 berichten daarna een
Broken pipe op dezelfde dode socket. Dat gaf 18 ERRORs en 16 losse Janitor-issues
(00194-00209). Elk issue kreeg een eigen bot-dispatch, en de meeste bots wezen een ander
codepad aan dan het echte.

## Root cause

Twee dingen versterkten elkaar.

**1. Justlog groepeert op de logregel zoals die in de code staat, niet op de ingevulde tekst.**
De fingerprint (`fingerprint_from_record` in het geïnstalleerde pakket justlog 0.9.0, `handlers/_common`) is een hash over
project, logger, exceptionklasse, frame en `message_template(record)`, oftewel `record.msg`.
Justlogs `lg.error(*args, **kwargs)` maakt van `args[0]` de `msg`. Extra args en kwargs gaan
als losse velden mee in de body (`_extra_args`, `_extra_kwargs`). Er wordt geen %-formatting
gedaan.

Een f-string zet de variabele dus ín het template:

```python
lg.error(f"Error fetching message {email_uid}: {str(e)}")   # elk uid een eigen fingerprint
```

Elk bericht kreeg een eigen fingerprint en daarmee een eigen issue. De rate limit van justlog
werkt ook per fingerprint, dus die hielp niet.

**2. De per-bericht fetch slikte verbindingsfouten.** `Mail.get_message` ving elke `Exception`,
logde ERROR en gaf `None` terug. De lus liep daardoor door op een dode socket en logde één
ERROR per resterend bericht. Daarna schoof `save_last_pass` de tijdstempel toch op, waardoor de
volgende pass die 18 berichten als oud oversloeg.

## Oplossing

- Variabelen als veld, met een vaste tekst als eerste argument:

  ```python
  lg.error('Error fetching message', uid=email_uid, error=str(e))
  ```

- `get_message` laat `CONNECTION_ERRORS` door (net als `get_email_details` al deed).
  `retry_once(mail, fetch, folder)` in `src/gmail.py` vangt ze op lusniveau: een WARNING,
  opnieuw verbinden, de map opnieuw selecteren (UID's gelden alleen binnen een geselecteerde
  map) en één nieuwe poging. Een tweede breuk gaat door naar de aanroeper. Die logt één ERROR
  en stopt, en in `handle_replies` blijft de tijdstempel dan staan.
- Een eindfout per abonnee in `mailer.send_newsletter` is een WARNING. De samenvattende ERROR
  na de lus noemt alle gemiste adressen al.

## Preventie

- **Een ERROR binnen een lus heeft nooit een variabele in de eerste string.** Dat geldt voor
  alle justlog-aanroepen, niet alleen hier. Buiten lussen kan een f-string geen kwaad, omdat
  die per run één keer afgaat.
- Liever nog: per item een WARNING en na de lus één samenvattende ERROR.
- Per-item code mag een kapot item afvangen, maar geen dode verbinding. Vang `CONNECTION_ERRORS`
  apart en gooi ze door naar het niveau dat opnieuw kan verbinden.
- Een "laatst verwerkt"-markering schuift alleen op na een geslaagde pass.
- Test: `test_imap_fetch_error_has_a_stable_log_template` controleert dat twee fouten met
  verschillende uid's hetzelfde template hebben.
