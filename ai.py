import json
import time
from pathlib import Path
import os

import httpx
from justai import Model
from justdays import Day

from database import get_last_newsletter
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

2) Dit is de tekst van de laatste nieuwsbrief; neem geen artikelen op die hier al in stonden (dedupe op titel/URL/inhoud):
<laatste_nieuwsbrief>
[LATEST_NEWSLETTER]
</laatste_nieuwsbrief>

SELECTIE- EN RANGSCHIKKINGSCriteria
- Sorteer op belangrijkheid voor professionals die AI toepassen:
  1) Ontwikkelingen uit de grote AI-labs en platformreleases
  2) Alles m.b.t. het bouwen en uitrollen van AI-applicaties 
  3) Items die de originele nieuwsbrief zelf als belangrijk markeert
  4) Regelgeving en compliance-updates die directe impact hebben
  5) Significante onderzoeksresultaten met praktische toepasbaarheid
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
- Realiteitscheck: 1 zin met beperking/risico/kanttekening
- Actie (optioneel, a 2 tip in de gehele nieuwsbrief): 1 concrete, testbare stap die de lezer deze week kan nemen

AANWIJZINGEN
- Schrijf ALLES in het Nederlands en in HP-stijl zoals hierboven.
- Citeer niet; parafraseer. (Max. 10 woorden citeren indien essentieel.)
- Gebruik absolute datums waar beschikbaar uit de bron.
- Gebruik per item hoogstens één retorische vraag.
- Gebruik geen markdown-opmaak (geen **vet** of #-koppen) binnen de JSON-velden.
- Links: kies 1–3 meest gezaghebbende/brontechnische URLs (release notes, docs, blog van het lab). Vermijd tracking-parameters.

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

def generate_ai_summary(schedule: str, text: str, verbose=False, cached=True):
    cache_file = Path(__file__).parent / 'cache' / f"{schedule}_summary.jsonl"
    
    # Load from cache if exists and caching is enabled
    if cached and cache_file.is_file():
        with open(cache_file, "r", encoding="utf-8") as f:
            summary = [json.loads(line) for line in f]
            if verbose:
                print("Loaded summary from cache\n")
            return summary

    # Generate new summary
    model = Model(COPY_WRITE_MODEL)
    max_articles = 7 if schedule == 'daily' else 12
    latest_newsletter = get_last_newsletter(schedule).split('<!-- Cards -->',1)[1].split('<!-- Footer -->')[0]
    prompt = COPYWRITE_PROMPT\
                 .replace('[MAX_ARTICLES]', str(max_articles))\
                 .replace('[LATEST_NEWSLETTER]', latest_newsletter)\
                 .replace('[NEWS_EMAILS]', text)
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
        for link in item['links']:
            try:
                response = httpx.get(link)
                if response.status_code != 200:
                    print(f'Status code was {response.status_code} for link {link}')
                    item['links'].remove(link)
            except:
                print(f'Link {link} is not valid')
                item["links"].remove(link)

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


def generate_ai_image(articles: list[dict], schedule: str, cached: bool) -> str:
    """
    Genereer 1 afbeelding via Responses API + image_generation tool,
    met 4 image-URL's als STIJLREFERENTIE (geen content copy).
    Slaat de 1e gegenereerde afbeelding op als PNG en retourneert het pad (of upload jouw S3).
    """
    img_name = f"{Day()}.png" if schedule == "daily" else f"week{Day().week_number()}.png"
    out_path = os.path.join("cache", img_name)
    if cached and os.path.isfile(out_path):
        print('Loading image from cache')
        article_index = 0
    else:
        print('Selecting article for image...')
        article_index, prompt = select_article_for_image(articles)

        prompt, images = add_style_to_prompt(prompt, articles[article_index], schedule)

        print('Generating image...')
        model = Model(DESIGN_MODEL)
        img = model.generate_image(prompt, images, size=(600,300))
        img = img.crop((25, 0, 575, 300)) # Nodig voor Google omdat die er borders omheen zet

        img.save(out_path, format="PNG")

    # Uploaden naar S3
    s3 = S3('harmsen.nl')
    url = s3.add(out_path, img_name)
    return article_index, url


def select_article_for_image(articles: list[dict]) -> tuple[int, str, list]:

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
    3. Schrijf een concrete beeldprompt waarmee ik dit direct in een beeldgeneratie-API kunt invoeren. 
    Maak in die beeldprompt duidelijk dat de inhoud van de afbeelding beeldvullend moet zijn. Ik wil geen randen om de afbeelding heen hebben.      
    4. Geef je antwoord in JSON met 2 velden: 
    - article: index van het artikel dat je hebt geselecteerd (0 tot {len(articles)-1})
    - prompt: de beeldprompt"""

    model = Model(ART_DIRECTION_MODEL)

    res = model.prompt(prompt, return_json=True, cached=False)
    return res['article'], res['prompt']


def add_style_to_prompt(prompt: str, article: str, schedule: str) -> tuple[str, list[str]]:

    if IMAGE_STYLE == 'Illustration':
        style_description = """Editorial-style illustration showing a creative studio environment. 
        The style is modern editorial illustration, clean lines, flat colors with subtle texture, 
        similar to The Economist or Axios illustrations. No robots, no futuristic grids, focus on 
        creativity, product diversity and playful design."""
        images = []

    elif IMAGE_STYLE == 'Mirabel':

        if schedule == 'daily':
            color = COLORS[Day().day_of_week()]
        else:
            color = COLORS[Day().week_number() % len(COLORS)]

        prompt += f"""## Stijl voor de afbeelding
            Gebruik de 3 meegegeven afbeeldingen uitsluitend als STIJLREFERENTIE (kleurpalet, penseelstreek, licht, textuur),
            niet als inhoud die moet worden gerepliceerd. Gebruik hierbij {color} als basiskleur. 
            LET OP! Zet GEEN tekst in de afbeelding.\n"""

        for brand, lab in BRANDS.items():
            if brand in article['title'] or brand in article['summary']:
                prompt += f'\nNeem het {lab} logo op in de afbeelding'

        images = [
            "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel1.jpg",
            # "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel2.jpg",
            "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel3.jpg",
            "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel4.jpg",
        ]
    else:
        assert False, f"Unknown image style: {IMAGE_STYLE}"

    return prompt, images
