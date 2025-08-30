import json
import time
from pathlib import Path
import base64
import io
import os

from openai import OpenAI
from PIL import Image
from justai import Model
from justdays import Day

from database import get_last_newsletter
from s3 import S3

SCRIPT_MODEL = 'gemini-2.5-pro'

COLORS = ['rood', 'groen', 'grijs', 'bruin', 'oranje', 'paars', 'blauw']
BIG_LABS = ['OpenAI', 'Google', 'Meta', 'Facebook', 'Instagram','Microsoft', 'IBM', 'Apple', 'Amazon', 'xAI',
            'Perplexity', 'Anthropic', 'Nvidia', 'Deepseek', 'GPT-5']
BRANDS = {lab:lab for lab in BIG_LABS}
BRANDS['GPT'] = 'OpenAI'
BRANDS['Claude'] = 'Anthropic'
BRANDS['Grok'] = 'xAI'

PROMPT = """DOEL
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
    "title": "Korte, informatieve titel (geen clickbait)",
    "summary": "Zie OUTPUT-STIJLSJABLOON; 4–8 zinnen met regelafbrekingen toegestaan.",
    "links": ["https://canonieke-bron-1", "https://bron-2"]
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
    model = Model(SCRIPT_MODEL)
    max_articles = 7 if schedule == 'daily' else 12
    latest_newsletter = get_last_newsletter(schedule).split('<!-- Cards -->',1)[1].split('<!-- Footer -->')[0]
    prompt = PROMPT\
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


def generate_ai_image(article: dict, schedule: str, cached: bool) -> str:
    """
    Genereer 1 afbeelding via Responses API + image_generation tool,
    met 4 image-URL's als STIJLREFERENTIE (geen content copy).
    Slaat de 1e gegenereerde afbeelding op als PNG en retourneert het pad (of upload jouw S3).
    """
    img_name = f"{Day()}.png" if schedule == "daily" else f"week{Day().week_number()}.png"
    out_path = os.path.join("cache", img_name)
    if cached and os.path.isfile(out_path):
        print('Loading image from cache')
    else:
        print('Generating image...')

        client = OpenAI()

        if schedule == 'daily':
            color = COLORS[Day().day_of_week()]
        else:
            color = COLORS[Day().week_number() % len(COLORS)]

        prompt = (
            "Maak een nieuwe, tekstloze visual die past bij dit AI-nieuwsartikel.\n"
            f'Kop: "{article["title"]}"\n'
            f"Samenvatting:\n<summary>\n{article['summary']}\n</summary>\n\n"
            "Belangrijk: laat de afbeelding zo goed mogelijk aansluiten bij de inhoud van het artikel.\n"
            "Toon concrete opbjecten of afbeeldingen van genoemde personen waar mogelijk.\n"
            "Gebruik de 3 meegegeven afbeeldingen uitsluitend als STIJLREFERENTIE (kleurpalet, penseelstreek, licht, textuur), "
            f"niet als inhoud die moet worden gerepliceerd. Gebruik hierbij {color} als basiskleur. "
            "Geen tekst in de afbeelding."
        )
        for brand, lab in BRANDS.items():
            if brand in article['title'] or brand in article['summary']:
                prompt += f'\nNeem het {lab} logo op in de afbeelding'

        style_refs = [
            "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel1.jpg",
            # "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel2.jpg",
            "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel3.jpg",
            "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel4.jpg",
        ]

        # Responses API call met tool "image_generation"
        resp = client.responses.create(
            model="gpt-5",
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    *[{"type": "input_image", "image_url": url} for url in style_refs],
                ],
            }],
            tools=[{"type": "image_generation"}],
        )

        images_b64 = extract_images(resp)
        if not images_b64:
            # Handige debug: laat zien welke output-types we kregen
            types = [getattr(x, "type", type(x).__name__) for x in (getattr(resp, "output", []) or [])]
            raise RuntimeError(f"Geen afbeelding gevonden in response. Output types: {types}")

        # Decode base64 -> PIL image
        raw = base64.b64decode(images_b64[0])
        img = Image.open(io.BytesIO(raw))

        target_w, target_h = 550, 300
        img_cropped = crop_to_fit(img, target_w, target_h)

        # Sla de eerste afbeelding op (PNG)
        img_cropped.save(out_path, format="PNG")

    # Uploaden naar S3
    s3 = S3('harmsen.nl')
    url = s3.add(out_path, img_name)
    return url


# --- Robuuste extractie van base64 PNG uit verschillende SDK-vormen ---
def extract_images(r):
    images_b64 = []

    # 1) Nieuwere SDK's plaatsen vaak "message" items met content entries
    for item in getattr(r, "output", []) or []:
        itype = getattr(item, "type", None)

        # a) Message met content -> zoek 'output_image' of 'image'
        if itype == "message":
            for part in getattr(item, "content", []) or []: # Hier komt ie
                ptype = getattr(part, "type", None)
                img = getattr(part, "image", None)
                # part.image.base64
                if img is not None and hasattr(img, "base64"):
                    images_b64.append(img.base64)

        # b) Direct image item
        if itype in {"image", "output_image"}:
            img = getattr(item, "image", None)
            if img is not None and hasattr(img, "base64"):
                images_b64.append(img.base64)

        # c) Sommige versies leveren een 'image_generation_call' met 'result'
        if itype == "image_generation_call":
            if hasattr(item, "result") and item.result: # Hier komt ie ook
                # Kan al base64 string zijn
                images_b64.append(item.result)

    # 2) Fallback: oudere voorbeelden met response.output[0].content[0].image.base64
    if not images_b64:
        try:
            maybe = r.output[0].content[0].image.base64
            if maybe:
                images_b64.append(maybe)
        except Exception:
            pass

    return images_b64


def crop_to_fit(image: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = image.size
    target_ratio = target_w / target_h
    src_ratio = src_w / src_h

    if src_ratio > target_ratio:
        # bron is breder dan doel → crop links/rechts
        new_w = int(src_h * target_ratio)
        left = (src_w - new_w) // 2
        box = (left, 0, left + new_w, src_h)
    else:
        # bron is hoger dan doel → crop boven/onder
        new_h = int(src_w / target_ratio)
        top = (src_h - new_h) // 2
        box = (0, top, src_w, top + new_h)

    cropped = image.crop(box)
    return cropped.resize((target_w, target_h), Image.LANCZOS)