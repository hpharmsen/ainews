# Plan: Extra tekstcorrectiestap (editor) voor nieuwsbrief-summaries

**Datum:** 2026-05-27
**Type:** feature
**Status:** klaar voor /build

## Doel

De huidige `generate_ai_summary` (Claude Sonnet 4.6) levert vaak tekst die nog niet goed
genoeg loopt. We voegen een extra LLM-stap toe die per artikel als eindredacteur
optreedt: zinnen verbeteren, niet-bestaande woorden eruit halen, anglicismes
vervangen. Het model is `EDITOR_MODEL` (Claude Opus 4.7) — duurder, maar wordt maar
op een korte tekst per artikel toegepast.

## Scope

**Wel in scope**
- Nieuwe functie `edit_articles` in `src/ai.py`
- Per artikel een aparte LLM-call (veiliger, kleinere context)
- Editor mag alleen `title` en `summary` aanpassen; `links` en `sources` worden
  ongewijzigd doorgegeven
- Eigen cache-bestand `_edited.jsonl` naast `_summary.jsonl`
- Herschreven `src/prompts/editor.md` met `{title}` en `{summary}` placeholders en
  een strikt JSON-output-format
- `EDITOR_MODEL_NAME` toevoegen aan het Colofon in `src/formatter.py`
- Tests voor de nieuwe functie

**Niet in scope**
- Hele set in 1 prompt editen (afgewezen: groter risico op gehallucineerde
  veranderingen, verlies van context per item)
- Editor laten beslissen over artikel-selectie of -volgorde
- Aparte editor-runs voor `art_prompt` of `infographic`
- Vervangen van het bestaande summary-model

## Architectuur-keuzes

**Per artikel, niet hele set**
Veiliger, kleinere prompt, geen risico op kruisbestuiving tussen artikelen.
Trade-off: N extra LLM-calls (typisch 4–8). Acceptabel; Opus 4.7 op een tekst van
~8 zinnen is goedkoop én snel genoeg.

**Editor krijgt alleen title + summary**
Editor mag links/sources niet aanraken. Daarmee voorkomen we dat de editor URLs
verzint of bronnen verwijdert. Het tekstpaar gaat erin, het tekstpaar komt eruit;
de rest van het `Article`-object kopieert de Python-code 1-op-1.

**Pydantic response_format**
We gebruiken een nieuw mini-model `EditedArticle(title: str, summary: str)` met
`response_format=EditedArticle` op `model.prompt(...)`, net als bij
`generate_ai_summary`. Geeft typed JSON terug, geen losse string-parsing nodig.

**Eigen cache**
`cache/<schedule>_<date>_edited.jsonl` los van `_summary.jsonl`. Voordelen:
- Bij `--cached` overslaan we de editor-stap
- We behouden de originele summary voor debugging/vergelijking
- Cache-bestand heeft hetzelfde formaat als `_summary.jsonl` zodat downstream code
  (visuals, formatter) er agnostisch mee om kan gaan

**Geen fallback bij failure**
Bij een crash van de editor stoppen we de hele run, net zoals bij summary-failures.
Halfgeëdite output is gevaarlijker dan geen output. Wel: dezelfde retry-loop als
`generate_ai_summary` (5 attempts, exponential backoff).

## Wijzigingen per file

### `src/prompts/editor.md` (herschrijven)

De huidige inhoud is een schets met een lege `<tekst>` tag. Vervangen door een
volledige prompt met:
- Heldere rol (eindredacteur AI-nieuwsbrief)
- Korte versie van de stijlregels die echt voor editing relevant zijn (verwijzen
  niet naar de complete copywrite.md — we houden 't compact)
- `{title}` en `{summary}` placeholders
- Strict JSON-output (`{{"title": "...", "summary": "..."}}`)
- Expliciete instructies: behoud betekenis en feiten; geen toevoegingen; geen
  inhoudelijke veranderingen
- Verbiedt em-dashes (overeenkomstig copywrite-regels)

Voorgestelde inhoud:

```markdown
Je bent eindredacteur van een Nederlandse AI-nieuwsbrief voor business/tech-lezers.

Je krijgt één artikel: een korte titel en een samenvatting van 4–8 zinnen.
Verbeter waar nodig de TEKSTUELE kwaliteit. Verander de inhoud NIET.

LET OP:
- Lopen de zinnen lekker? Mix korte en lange zinnen.
- Staan er rare of niet-bestaande woorden in? Vervang die.
- Anglicismes: vervang door goede Nederlandse alternatieven, behalve voor termen
  die in deze sector standaard zijn (bv. "model", "release", "prompt", "API").
- Em-dashes vervangen door komma's.
- Voltooid verleden tijd voor lanceringen/aankondigingen ("Google heeft ... gebouwd").
- Geen markdown of HTML in de output.
- Geen meta-zinnen ("In dit artikel..."). Geen drieslagen. Geen "niet alleen X
  maar ook Y". Geen "van X tot Y".
- Vermijd: "cruciaal", "essentieel", "fundamenteel", "onderstreept",
  "in een wereld waarin", "het belang van".

WAT JE NIET DOET:
- Geen feiten toevoegen of veranderen.
- Geen cijfers of datums veranderen.
- Geen zinnen schrappen die feitelijke informatie bevatten, ook al lopen ze
  matig — herschrijf ze in plaats daarvan.
- Geen nieuwe alinea-indeling forceren als de bestaande logisch is.
- Als de tekst al goed loopt: geef hem onveranderd terug.

INPUT:
<titel>
{title}
</titel>

<samenvatting>
{summary}
</samenvatting>

UITVOER (strict JSON, geen markdown, geen toelichting):
{{
  "title": "verbeterde of onveranderde titel",
  "summary": "verbeterde of onveranderde samenvatting (4–8 zinnen, \\n\\n tussen alinea's toegestaan)"
}}
```

### `src/ai.py`

Toevoegen onder `Summary`-class:

```python
class EditedArticle(BaseModel):
    title: str = Field(description="Verbeterde of onveranderde titel")
    summary: str = Field(description="Verbeterde of onveranderde samenvatting")
```

Nieuwe functie (plaatsing: direct ná `generate_ai_summary`):

```python
def edit_articles(schedule: str, articles: list[dict], cached: bool = True,
                  verbose: bool = False) -> list[dict]:
    """Loop alle artikelen langs een editor-LLM voor tekstuele verbetering.
    Past alleen title + summary aan; links en sources blijven onaangetast."""
    cache_file = Path(cache_file_prefix(schedule) + "_edited.jsonl")
    if cached and cache_file.is_file():
        with open(cache_file, "r", encoding="utf-8") as f:
            edited = [json.loads(line) for line in f]
            if verbose:
                lg.info("Loaded edited articles from cache")
            return edited

    lg.info('Editing articles ...')
    model = Model(EDITOR_MODEL, max_tokens=2000)
    edited: list[dict] = []
    for idx, article in enumerate(articles):
        prompt = load_prompt('editor',
                             title=article.get('title', ''),
                             summary=article.get('summary', ''))
        for attempt in range(5):
            try:
                result = model.prompt(prompt, response_format=EditedArticle, cached=False)
                break
            except Exception as e:
                wait_time = 5 * (2 ** attempt)
                lg.warning(f'Editor attempt {attempt + 1}/5 for article {idx} failed: {e}. '
                           f'Retrying in {wait_time}s...')
                time.sleep(wait_time)
        else:
            raise RuntimeError(f'Editor failed for article {idx} after 5 attempts')

        ea = EditedArticle(**result) if isinstance(result, dict) else result
        edited.append({
            **article,
            'title': ea.title,
            'summary': ea.summary.strip(),
        })

    with open(cache_file, "w", encoding="utf-8") as f:
        for art in edited:
            f.write(json.dumps(art, ensure_ascii=False) + "\n")
    return edited
```

### `src/formatter.py`

In de import op regel 5–9 `EDITOR_MODEL_NAME` toevoegen:

```python
from src.ai import (
    COPY_WRITE_MODEL_NAME,
    EDITOR_MODEL_NAME,
    ART_MODEL_NAME,
    INFOGRAPHIC_MODEL_NAME,
)
```

In het Colofon (regel 131–140) één regel toevoegen ná "Teksten":

```html
Teksten: {COPY_WRITE_MODEL_NAME}<br>
Eindredactie: {EDITOR_MODEL_NAME}<br>
Header graphic art direction én design: {ART_MODEL_NAME}<br>
```

### `main.py`

Op regel 11 de import uitbreiden:

```python
from src.ai import (generate_ai_summary, edit_articles, generate_ai_image,
                    generate_infographic, select_articles_for_visuals)
```

Op regel 78 ná `generate_ai_summary` en vóór `select_articles_for_visuals`:

```python
articles = generate_ai_summary(schedule, text, cached=cached, verbose=VERBOSE)
articles = edit_articles(schedule, articles, cached=cached, verbose=VERBOSE)
```

### Tests

Nieuw bestand `tests/test_editor.py` of toevoeging aan bestaande test-runner
(`tests/main.py`):

1. **Cache-hit test**: schrijf een fake `_edited.jsonl`, roep `edit_articles`
   met `cached=True`, verifieer dat het bestand wordt teruggegeven zonder
   LLM-call (mock `Model` zodat het zou exploderen bij gebruik).

2. **Velden behouden**: gegeven input-article met `links=[...]` en
   `sources=[...]`, verifieer dat het output-article exact dezelfde links en
   sources heeft (alleen title/summary mogen wijzigen). Mock `Model.prompt` zodat
   het een `EditedArticle` teruggeeft met andere title/summary.

3. **EditedArticle ontleding**: gegeven dat `Model.prompt` een dict teruggeeft
   (geen Pydantic-instance, zoals justai soms doet), verifieer dat de functie
   ook dat correct verwerkt.

4. **Cache-file format**: na een niet-cached run, verifieer dat het
   `_edited.jsonl`-bestand bestaat, één regel per artikel bevat, en dat elke
   regel een geldig JSON-object is met de juiste velden.

Volg het bestaande test-patroon (`tests/main.py` of `pytest`). Integratietest
tegen echte API is **niet** nodig — mock `Model`.

## Stappen (TDD-first)

1. **Tests schrijven** in `tests/test_editor.py` (alle 4 bovenstaande tests, mock
   `justai.Model`). Tests falen omdat `edit_articles` nog niet bestaat.
2. **`EditedArticle`-class** toevoegen in `src/ai.py`.
3. **`edit_articles`** implementeren in `src/ai.py` tot tests groen zijn.
4. **`editor.md`** herschrijven met `{title}` en `{summary}` placeholders.
5. **`main.py`** aanpassen: import + aanroep ná `generate_ai_summary`.
6. **`formatter.py`** Colofon-regel toevoegen + import.
7. **End-to-end smoke**: `python main.py daily --cached --dry-run` draaien. Voor
   de eerste run géén `_edited.jsonl` in `cache/` zodat de editor-stap echt loopt;
   verifieer cache-file aanwezigheid en bekijk de HTML-output (Colofon, leesbaarheid).
8. **Pytest / `python tests/main.py`** groen?
9. **gitnexus_detect_changes** om scope te bevestigen.

## Risico's en mitigaties

| Risico | Mitigatie |
|---|---|
| Editor verzint feiten of cijfers | Expliciete prompt-instructie + per-artikel context (kleiner risico dan in bulk) |
| Editor wijzigt links/sources | Code negeert die velden uit de LLM-output volledig — alleen title/summary uit `EditedArticle` worden gebruikt |
| Opus 4.7 te traag voor dagelijkse run | Max 8 artikelen × korte prompt = ~30–60s totaal; acceptabel |
| Kosten Opus 4.7 | Korte prompts; geaccepteerd door gebruiker. Alternatief: Sonnet 4.6 indien te duur — model is één constante om aan te passen |
| Cache raakt out-of-sync met summary | Cache hangt aan dezelfde `cache_file_prefix(schedule)`-datum; `cleanup_cache()` (zie `database.py`) zal hem meenemen |

## Open vragen

Geen — alle ontwerpkeuzes zijn vastgelegd in dit document. Eventuele aanpassingen
aan de prompt kunnen tijdens `/build` of in een latere `/tweak` worden gedaan.
