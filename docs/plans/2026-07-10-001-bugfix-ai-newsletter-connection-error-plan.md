---
date: 2026-07-10
type: bugfix
source: gmail
gmail_thread_id: 19f4802993bf5237
gmail_url: https://mail.google.com/mail/u/0/#inbox/19f4802993bf5237
sender: hp@harmsen.nl
subject: "AI Newsletter error: Connection error."
---

# Plan: `retry_prompt` vangt geen transiente connection errors

**Datum:** 2026-07-10
**Type:** bugfix
**Status:** klaar voor /bug

## Bug

De newsletter run crasht bij een transiente netwerkfout tegen de OpenAI Responses API.
De laatste twee runs (2026-07-09 02:12 en 10:52) crashten allebei op dezelfde plek:

```
File "/Users/hp/proj/ainews/src/ai.py", line 368, in select_articles_for_visuals
  return retry_prompt(model, prompt)
File "/Users/hp/proj/ainews/src/ai.py", line 374, in retry_prompt
  res = model.prompt(prompt, return_json=True, cached=False)
  ...
httpcore.ReadError: [Errno 54] Connection reset by peer
  → httpx.ReadError
  → openai.APIConnectionError: Connection error.
  → justai.models.basemodel.ConnectionException: Connection error.
```

Beide keren was het model `SELECTION_MODEL` (GPT-5) via `select_articles_for_visuals`.

## Root cause

`retry_prompt` in `src/ai.py:371-380` heet retry maar vangt alleen `RatelimitException` en
`ModelOverloadException`. Alle andere justai-excepties — waaronder de meest voorkomende
transiente fout, `ConnectionException` — vallen erdoorheen en crashen de hele run.

```python
def retry_prompt(model, prompt) -> dict:
    for attempt in range(5):
        try:
            res = model.prompt(prompt, return_json=True, cached=False)
            return res
        except (RatelimitException, ModelOverloadException):
            lg.warning('Hitting rate limit or timeout, retrying...')
            time.sleep(5)
    else:
        raise RatelimitException
```

Dat is de kern. Nevenpunt: de sleep is een vaste 5 seconden (geen backoff), en de
melding "rate limit or timeout" dekt de lading niet als we ook connection errors gaan
vangen.

## Fix

Breid de except-tuple uit met `ConnectionException` en voeg exponentiële backoff toe.
Geen nieuwe abstractie, geen aparte retry-helper — de bestaande functie doet het werk,
hij vangt gewoon de verkeerde set excepties.

**Wijzigingen in `src/ai.py`:**

1. Import: `ConnectionException` toevoegen aan de bestaande justai-import.
2. `retry_prompt`:
   - `ConnectionException` toevoegen aan de except-tuple
   - Exponentiële backoff: `time.sleep(min(5 * 2 ** attempt, 60))` (5s, 10s, 20s, 40s, 60s)
   - Log de exception-class + boodschap, niet een generieke tekst
   - Bij uitputten van 5 pogingen: laatste exception re-raisen in plaats van een lege
     `RatelimitException` (behoud van diagnostische info)

## Scope

**Wel in scope**
- Fix `retry_prompt` in `src/ai.py` (één functie, één exception-tuple, één sleep)
- Import bijwerken
- Test: unit test die simuleert dat `model.prompt` de eerste twee keer
  `ConnectionException` gooit en de derde keer een dict teruggeeft — moet de dict
  teruggeven zonder te crashen

**Niet in scope**
- Retry-logica introduceren in andere plekken die `model.prompt` direct aanroepen
  (`generate_ai_summary`, `edit_articles`, etc). Als die ook connection-errors moeten
  overleven is dat een aparte discussie — nu alleen de gemelde crash-site fixen.
- Circuit breaker, jitter, aparte retry-config
- Herstructurering van de exception-hiërarchie in justai

## Testplan

1. **Unit test** (`tests/test_ai.py` of nieuwe file):
   - Mock `model.prompt` met een `side_effect` lijst: `[ConnectionException("boom"),
     ConnectionException("boom"), {"image_index": 0, "infographic_index": 1}]`
   - Assert dat `retry_prompt(mock, "prompt")` het dict teruggeeft
   - Assert dat `mock.prompt` 3 keer is aangeroepen
2. **Unit test uitputting**: 5x `ConnectionException` → laatste exception moet re-raised
   worden (niet een lege `RatelimitException`)
3. **Manuele smoke**: `python main.py daily --cached` moet nog steeds werken (cache
   overslaan van select_visuals wordt niet geraakt, maar controleer dat de wijziging
   niets breekt in de happy path)

## Open vragen

Geen. De fix is klein en zelf-bevattend.
