import json
import time
from pathlib import Path
import os
from typing import Tuple

import httpx
from justai import Model
from justai.models.basemodel import RatelimitException
from justdays import Day

from database import get_last_newsletter_texts, cache_file_prefix
from s3 import S3

COPY_WRITE_MODEL = 'gemini-2.5-pro'
COPY_WRITE_MODEL_NAME = 'Gemini 2.5 Pro'
ART_DIRECTION_MODEL = "gpt-5"
ART_DIRECTION_MODEL_NAME = "GPT-5"
DESIGN_MODEL = "gemini-2.5-flash-image-preview"
DESIGN_MODEL_NAME = "Nano Banana"
IMAGE_STYLE = 'Mirabel'

COLORS = ['rood', 'groen', 'grijs', 'bruin', 'oranje', 'paars', 'blauw']
BIG_LABS = ['OpenAI', 'Google', 'Meta', 'Facebook', 'Instagram','Microsoft', 'IBM', 'Apple', 'Amazon', 'xAI',
            'Perplexity', 'Anthropic', 'Nvidia', 'Deepseek', 'GPT-5']
BRANDS = {lab:lab for lab in BIG_LABS}
BRANDS['GPT'] = 'OpenAI'
BRANDS['Claude'] = 'Anthropic'
BRANDS['Grok'] = 'xAI'

COPYWRITE_PROMPT = """DOEL
Maak een selectie en samenvatting van AI-nieuws uit ontvangen e-mails, in de schrijfstijl van Hans-Peter Harmsen (HP), geschikt voor een nieuwsbrief.

STIJL-DNA (VOLG STRENG)
- Taal: Nederlands, helder en to the point. Schrijf voor business/tech-lezers.
- Toon: professioneel maar toegankelijk; optimistisch over AI mét kritische kanttekeningen. Humor/woordspeling spaarzaam.
- Perspectief: spreek de lezer aan met “je”; gebruik “ik” alleen als een mini-observatie of test relevant is.
- Retoriek: af en toe een retorische vraag (max. 1 per item) of korte analogie om een concept te verhelderen.
- Structuur in samenvattingen: eerst de kern, daarna impact voor organisaties, dan een nuchtere kanttekening, sluit af met een concrete actie-tip.
- Evidence-first: noem absolute datums (bijv. “29 augustus 2025”) als die in de bron staan; vermijd hype.
- Korte alinea’s en witregels; geen lange opsommingen binnen één zin.
- Vermijd buzzwoorden: "game-changer", "revolutionair", "doorbraak" (tenzij echt gerechtvaardigd)
- Gebruik concrete cijfers: "30% sneller" ipv "veel sneller" maar alleen als die concrete cijfers ook echt in de bron staan.
- Contextualiseer: vergelijk nieuwe ontwikkelingen met bestaande alternatieven. Maar verzin niks!
- Schrijf actionable: wat kan de lezer er morgen mee?

INPUT
1) Lees onderstaande mails:
<nieuws_emails>
[NEWS_EMAILS]
</nieuws_emails>

2) Dit zijn de teksten van de laatste nieuwsbrieven; neem geen artikelen op die hier al in stonden (dedupe op titel/URL/inhoud):
<laatste_nieuwsbrieven>
[LATEST_NEWSLETTERS]
</laatste_nieuwsbrieven>

SELECTIE- EN RANGSCHIKKINGSCriteria
- Sorteer op belangrijkheid voor professionals die AI toepassen:
  1) Artikelen die direct actionable zijn voor mensen die AI willen inzetten in hun organisatie
  2) Ontwikkelingen uit de grote AI-labs en platformreleases
  3) Alles m.b.t. het bouwen en uitrollen van AI-applicaties 
  4) Items die de originele nieuwsbrief zelf als belangrijk markeert
  5) Regelgeving en compliance-updates die directe impact hebben
  6) Significante onderzoeksresultaten met praktische toepasbaarheid
- SKIP: hype zonder substance, speculatieve toekomstvisies, commerciële uitingen, persoonlijke meningen zonder feiten
- Bundel/dedup items die over hetzelfde gaan.
- Bewaar maximaal [MAX_ARTICLES] items

DEDUPE-STRATEGIE
- Match op: bedrijfsnaam + kernonderwerp + tijdsperiode (binnen 2 weken)
- Voorbeelden van dupes: "OpenAI lanceert GPT-5" vs "GPT-5 nu beschikbaar"
- Bij overlap: lees beide en maak en enkele samenvatting die beide beslaat.
- Noteer in je redenatie waarom je bepaalde items hebt gebundeld

OUTPUT-STIJLSJABLOON PER ITEM
- Kort: 1 zin TL;DR met kernfeit en datum waar relevant
- Context: 1 zin die uitlegt waarom dit nu relevant is (timing, markt, concurrentie)
- Business-impact: 1-2 zinnen over concrete gevolgen (kosten, tijd, risico, kansen)
- Realiteitscheck: 1 zin met beperking/risico/kanttekening. Alleen als dat echt relevant is.
- Actie (optioneel, a 2 tip in de gehele nieuwsbrief): 1 concrete, testbare stap die de lezer deze week kan nemen

AANWIJZINGEN
- Schrijf ALLES in het Nederlands en in HP-stijl zoals hierboven.
- Citeer niet; parafraseer. (Max. 10 woorden citeren indien essentieel.)
- Gebruik absolute datums waar beschikbaar uit de bron.
- Gebruik per item hoogstens één retorische vraag.
- Gebruik geen markdown-opmaak (geen **vet** of #-koppen) binnen de JSON-velden.
- Links: kies 1–3 meest gezaghebbende/brontechnische URLs (release notes, docs, blog van het lab). Vermijd tracking-parameters.
- Begin de samenvatting niet met een vrijwel letterlijke herhaling van de titel.
- Hou de titel kort.

KWALITEITSCHECK VOOR ELKE SUMMARY
- Bevat het item concrete, verifieerbare feiten?
- Is de business-relevantie duidelijk uitgelegd?
- Is de actie-tip specifiek genoeg om uit te voeren?
- Staat er minimaal één datum, cijfer of andere concrete detail in?

LINKS-KWALITEIT
- Prioriteer: officiele release notes > labs/company blogs > tech media > andere media
- Vermijd: social media links, tracking URLs, paywall-content
- Controleer: zijn de links nog actief? (indien mogelijk)
- Maximum 2 links per item, tenzij cruciaal

UITVOERFORMAAT (STRICT)
Geef je antwoord terug als een JSON-array met maximaal [MAX_ARTICLES] objecten met precies deze velden:
[
  {
    "title": "Korte, informatieve titel (geen clickbait). Gebruik geen markdown- of html opmaak maar plain text.",
    "summary": "Zie OUTPUT-STIJLSJABLOON; 4–8 zinnen met regelafbrekingen toegestaan. Gebruik geen markdown- of html opmaak maar plain text.",
    "links": ["https://canonieke-bron-1", "https://bron-2"],
    "sources": Lijst van bronnen die gebruikt werden om de samenvatting te maken
  }
]

VALIDATIE VOOR TERUGSTUREN
1. Bovenal: is alles geschreven in in HP-stijl?
2. Zijn het maximaal [MAX_ARTICLES] items?
3. Zijn alle items ongeveer even lang (4-8 zinnen)?
4. Heeft elk item precies één retorische vraag (of nul)?
5. Zijn Staan er niet meer dan 2 actietips in de nieuwbrief en zijn alle actie-tips concreet genoeg ("test X met dataset Y" ipv "overweeg X")?
6. Bevatten alle items concrete data (datum/cijfer/percentage)? En staat deze data ook echt in de brontekst?
7. Staan er geen items in die al in <laatste_nieuwsbrief> staan? Updates op die items mag wel.
8. Alle links zijn geldig ogende https-URLs (zonder UTM’s).

"""

STYLE_IMAGES = [
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel1.jpg",
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel2.jpg",
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel3.jpg",
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel4.jpg",
]


def check_and_resolve_url(url: str) -> str | None:
    """Returns a valid URL (possibly redirected) or None if invalid."""
    try:
        resp = httpx.get(url, follow_redirects=False)
        if 300 <= resp.status_code < 400:
            loc = resp.headers.get('Location')
            if not loc:
                return None
            try:
                loc = httpx.URL(loc, base=httpx.URL(url)).human_repr()
            except Exception:
                pass
            try:
                follow = httpx.get(loc)
                return loc if follow.status_code == 200 else None
            except Exception:
                return None
        return url if resp.status_code == 200 else None
    except Exception:
        return None


def generate_ai_summary(schedule: str, text: str, verbose=False, cached=True):
    # Load from cache if exists and caching is enabled
    cache_file =  Path(cache_file_prefix(schedule) + "_summary.jsonl")
    if cached and cache_file.is_file():
        with open(cache_file, "r", encoding="utf-8") as f:
            summary = [json.loads(line) for line in f]
            if verbose:
                print("Loaded summary from cache\n")
            return summary

    # Generate new summary
    model = Model(COPY_WRITE_MODEL)
    max_articles = 7 if schedule == 'daily' else 12
    latest_newsletters = get_last_newsletter_texts(schedule, limit=5)
    prompt = COPYWRITE_PROMPT\
                 .replace('[MAX_ARTICLES]', str(max_articles))\
                 .replace('[LATEST_NEWSLETTERS]', latest_newsletters)\
                 .replace('[NEWS_EMAILS]', text)
    tokens = model.token_count(prompt)
    print('\nGenerating summary...')
    for _ in range(3):
        try:
            summary = model.prompt(prompt, return_json=True, cached=True)
            break
        except:
            time.sleep(1)
    else:
        summary = model.prompt(prompt, return_json=True, cached=True)

    # Check the urls by opening them and see if they return a proper web page
    print('\nChecking links ...')
    for item in summary:
        for link in list(item['links']):
            resolved = check_and_resolve_url(link)
            if resolved is None:
                print(f'Link {link} is not valid')
                item['links'].remove(link)
            elif resolved != link:
                print(f'Redirected {link} -> {resolved}')
                try:
                    idx = item['links'].index(link)
                    item['links'][idx] = resolved
                except ValueError:
                    pass

    # Save to cache
    with open(cache_file, "w", encoding="utf-8") as f:
        for item in summary:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    if verbose:
        for line in summary:
            print(line['title'])
            print(line['summary'])
            for link in line['links']:
                print(link)
            print()

    return summary


def generate_ai_image(articles: list[dict], schedule: str, cached: bool, max_retries: int = 3) -> Tuple[int, str]:
    """
    Genereer 1 afbeelding via Responses API + image_generation tool,
    met 4 image-URL's als STIJLREFERENTIE (geen content copy).
    Slaat de 1e gegenereerde afbeelding op als PNG en retourneert het pad (of upload jouw S3).
    """
    out_path = Path(cache_file_prefix(schedule) + '.png')
    shadow_img_name = Path(cache_file_prefix(schedule) + '_shadow.png')
    shadow_out_path = os.path.join("cache", shadow_img_name)

    if cached and os.path.isfile(out_path):
        print('Loading image from cache')
        article_index = 0
    else:
        print('Selecting article for image...')
        article_index: int
        article_index, description = select_article_for_image(articles)
        prompt = create_image_prompt(articles[article_index], description, schedule)

        print('Generating image...')
        model = Model(DESIGN_MODEL)
        shadow_model = Model('reve')
        # Retry logic with exponential backoff
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    print(f'Generating image (attempt {attempt + 1}/{max_retries})...')

                try:
                    options = {"aspect_ratio": "16:9"}
                    img = shadow_model.generate_image(prompt, STYLE_IMAGES, options=options, size=(600, 300))
                    img.save(shadow_out_path, format="PNG")
                except:
                    print("Failed to generate shadow image")

                img = model.generate_image(prompt, STYLE_IMAGES, size=(600, 300))
                img.save(out_path, format="PNG")
                print('Image generated successfully')

                break
                
            except (httpx.ReadTimeout, TimeoutError) as e:
                if attempt == max_retries - 1:  # Last attempt
                    print(f'Failed to generate image after {max_retries} attempts')
                    raise Exception(f'Image generation timed out after {max_retries} attempts') from e
                    
                print(f'Timeout occurred, retrying...')
                time.sleep(1)
                
            except Exception as e:
                if attempt == max_retries - 1:  # Last attempt
                    print(f'Failed to generate image: {str(e)}')
                    raise
                print(f'Error generating image: {str(e)}. Retrying...')
                time.sleep(5)  # Shorter delay for non-timeout errors

    # Upload to S3
    s3 = S3('harmsen.nl')
    for attempt in range(3):
        try:
            url = s3.add(out_path, 'nieuwsbrief/' + out_path.name)
            return article_index, url
        except Exception as e:
            print(f'Error uploading to S3, retrying... (attempt {attempt + 1}/3)')
            time.sleep(5 * (attempt + 1))  # Exponential backoff for S3 upload
    raise TimeoutError('Failed to upload image to S3 after 3 attempts')


def select_article_for_image(articles: list[dict]) -> tuple[int, str]:

    prompt = f"""Ik heb een nieuwsbrief over AI met de volgende {len(articles)} artikelen:
    <nieuwsbrief>
    {articles}
    </nieuwsbrief>
    
    Nu wil ik een AI model een afbeelding laten genereren die goed past bij de inhoud. 
    Ik wil geen generiek AI beeld met robots en futuristische lijnen maar het liefst een illustratie 
    die echt past bij de inhoud van één van de artikelen.
    
    Doe de volgende stappen:
    1. Denk na over welk van de {len(articles)} artikelen zich hier het beste voor leent en waarom?
    2. Denk na over wat er dan in die illustratie zou moeten komen te staan.
    3. Geef je antwoord in JSON met 2 velden: 
    - article: index van het artikel dat je hebt geselecteerd (0 tot {len(articles)-1})
    - description: een beschrijving van de afbeelding die je hebt geselecteerd"""

    model = Model(ART_DIRECTION_MODEL)

    res = retry_prompt(model, prompt)
    return res['article'], res['description']


def create_image_prompt(article, description, schedule):
    cache_file = Path(cache_file_prefix(schedule) + "_image_prompt.txt")
    if cache_file.is_file():
        with open(cache_file, 'r', encoding='utf-8') as f:
            return f.read()

    print('Generating image prompt...')
    if schedule == "daily":
        color = COLORS[Day().day_of_week()]
    else:
        color = COLORS[Day().week_number() % len(COLORS)]

    prompt = f"""Schrijf een concrete beeldprompt voor het genereren van een afbeelding bij een artikel uit een nieuwsbrief over AI. 
    Beschrijving van de afbeelding: 
    <beschrijving>
    {description}
    </beschrijving>
    
    De volgende dingen zijn hierbij belangrijk:
    1. De inhoud van de afbeelding moet beeldvullend moet zijn. Ik wil ABSOLUUT geen randen om de afbeelding heen hebben.
    2. Er mag geen tekst in de afbeeldingen mag komen te staan.  
    3. De 3 meegegeven afbeeldingen dienen als STIJLREFERENTIE (kleurpalet, penseelstreek, licht, textuur), 
    4. Die afbeeldingen mogen alleen gebruikt worden voor de stijl; niet als inhoud die moet worden gerepliceerd. 
    5. Zet verder geen stijlbeschrijving in de prompt want dat moet uit de afbeeldingen blijken.
    6. De kleur {color} moet de basiskleur worden voor de afbeelding.

    Retourneer alleen de prompt. Geen andere tekst ervoor of erna."""
    index = 7
    for brand, lab in BRANDS.items():
        if brand in article["title"] or brand in article["summary"]:
            prompt += f"\n    {index}. Het {lab} logo moet opgenomenen worden op in de afbeelding."
            index += 1

    model = Model(ART_DIRECTION_MODEL)
    image_prompt = model.prompt(prompt, return_json=False, cached=False)

    with open(cache_file, 'w', encoding='utf-8') as f:
        f.write(image_prompt)

    return image_prompt


def retry_prompt(model, prompt) -> dict:
    for attempt in range(5):
        try:
            res = model.prompt(prompt, return_json=True, cached=False)
            return res
        except RatelimitException:
            print('Hitting rate limit, retrying...')
            time.sleep(5)
    else:
        raise RatelimitException
