import json
from pathlib import Path
import base64
import io
import os

from openai import OpenAI
from PIL import Image
from justai import Model
from justdays import Day
from s3 import S3

COLORS = ['rood', 'groen', 'grijs', 'bruin', 'oranje', 'paars', 'blauw']
BIG_LABS = ['OpenAI', 'Google', 'Meta', 'Facebook', 'Instagram','Microsoft', 'IBM', 'Apple', 'Amazon', 'xAI',
            'Perplexity', 'Anthropic', 'Nvidia', 'Deepseek', 'GPT-5']
BRANDS = {lab:lab for lab in BIG_LABS}
BRANDS['GPT'] = 'OpenAI'
BRANDS['Claude'] = 'Anthropic'
BRANDS['Grok'] = 'xAI'

PROMPT = """Onderstaande is een collectie van emailberichten die ik heb ontvangen van verschillende mailinglijsten.
Ik wil weten wat het nieuws is op AI gebied. Doe het volgende:
1. Lees onderstaande mails goed door. 
2. Haal alle nieuws over AI eruit. Voeg dingen die over hetzelfde gaan samen. 
3. Sorteer het nieuws op belangrijkheid. Zet het belangrijkste bericht bovenaan.
    Belangrijk is hierbij:
    - Alles wat te maken heeft met het programmeren van AI applicaties
    - Alle ontwikkelingen vanuit de grote AI labs
    - Alles waarvan de nieuwsbrief aangeeft dat het belangrijk nieuws is.
4. Bewaar alleen de belangrijkste nieuwsberichten. Bewaar er maximaal [MAX_ARTICLES].
5. Geef me van ieder van deze nieuwsberichten een samenvatting van een paar zinnen of paragrafen (als er veel te vertellen is) IN HET NEDERLANDS.
6. Geef bij elk ook bronvermeldingen in de vorm van links naar webpagina's met de originele nieuwsberichten als je die kan vinden.

Geef je antwooord terug in json formaat als volgt:
    {
        "title": "Nieuws 1",
        "summary": "Dit is de samenvatting van nieuws 1",
        "links": ["https://www.nieuws1.be", "https://www.nieuws2.be"]
    },
    {
        "title": "Nieuws 2",
        "summary": "Dit is de samenvatting van nieuws 2",
        "links": []
    },
    {
        "title": "Nieuws 3",
        "summary": "Dit is de samenvatting van nieuws 3",
        "link": ["https://www.nieuws3.be"]
    }
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
    
    # If not in cache, generate new summary
    model = Model('gemini-2.5-flash')
    max_articles = 7 if schedule == 'daily' else 12
    prompt = PROMPT.replace('[MAX_ARTICLES]', str(max_articles)) + text
    print('\nGenerating summary...')
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


def generate_ai_image(article: dict, schedule: str) -> str:
    """
    Genereer 1 afbeelding via Responses API + image_generation tool,
    met 4 image-URL's als STIJLREFERENTIE (geen content copy).
    Slaat de 1e gegenereerde afbeelding op als PNG en retourneert het pad (of upload jouw S3).
    """
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
        "Gebruik de 3 meegegeven afbeeldingen uitsluitend als STIJLREFERENTIE (kleurpalet, penseelstreek, licht, textuur), "
        f"niet als inhoud die moet worden gerepliceerd. Gebruik hierbij {color} als basiskleur. "
        "Geen tekst in de afbeelding."
    )
    for brand, lab in BRANDS.items():
        if brand in article:
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
    img_name = f"{Day()}.png" if schedule == "daily" else f"week{Day().week_number()}.png"
    out_path = os.path.join("cache", img_name)
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