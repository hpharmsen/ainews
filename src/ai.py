import json
import time
from pathlib import Path
import os
from typing import Tuple

import httpx
from justai import Model
from justai.models.basemodel import ModelOverloadException, RatelimitException
from justdays import Day
from pydantic import BaseModel, Field, HttpUrl
from typing import Annotated

from src.database import get_last_newsletter_summaries, cache_file_prefix
from src.s3 import S3
from src.log import lg

COPY_WRITE_MODEL = 'claude-sonnet-4-6'
COPY_WRITE_MODEL_NAME = 'Claude Sonnet 4.6'
ART_DIRECTION_MODEL = "gpt-5"
ART_DIRECTION_MODEL_NAME = "GPT-5"
DESIGN_MODEL = 'gemini-3.1-flash-image-preview' # "gemini-2.5-flash-image"
DESIGN_MODEL_NAME = "Nano Banana"
INFOGRAPHIC_MODEL = 'gemini-3.1-flash-image-preview' #'gemini-3-pro-image-preview'
INFOGRAPHIC_MODEL_NAME = 'Nano Banana 2'
IMAGE_STYLE = 'Mirabel'

PROMPTS_DIR = Path(__file__).parent / 'prompts'
COLORS = ['rood', 'groen', 'grijs', 'bruin', 'oranje', 'paars', 'blauw']
BIG_LABS = ['OpenAI', 'Google', 'Meta', 'Facebook', 'Instagram','Microsoft', 'IBM', 'Apple', 'Amazon', 'xAI',
            'Perplexity', 'Anthropic', 'Nvidia', 'Deepseek', 'GPT-5']
BRANDS = {lab:lab for lab in BIG_LABS}
BRANDS['GPT'] = 'OpenAI'
BRANDS['Claude'] = 'Anthropic'
BRANDS['Grok'] = 'xAI'


def load_prompt(name: str, **kwargs) -> str:
    """Load a prompt template from the prompts folder and substitute variables."""
    text = (PROMPTS_DIR / f'{name}.md').read_text()
    return text.format(**kwargs) if kwargs else text


STYLE_IMAGES = [
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel1.jpg",
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel2.jpg",
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel3.jpg",
    "https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/mirabel4.jpg",
]


class Article(BaseModel):
    title: str = Field(
        description="Korte, informatieve titel (geen clickbait), plain text zonder markdown of HTML"
    )
    summary: str = Field(
        description="4–8 zinnen met regelafbrekingen toegestaan, plain text zonder markdown of HTML"
    )
    links: list[HttpUrl] = Field(
        description="Canonieke bronlinks gebruikt in het artikel"
    )
    sources: list[str] = Field(
        description="Lijst van bronnen die gebruikt werden om de samenvatting te maken"
    )


class Summary(BaseModel):
    articles: Annotated[
        list[Article],
        Field(min_length=1, max_length=8, description="Geselecteerde nieuwsartikelen")
    ]


_BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
}


def _try_url(url: str) -> str | None:
    """Check a single URL, following redirects. Returns resolved URL or None."""
    try:
        resp = httpx.get(url, headers=_BROWSER_HEADERS, follow_redirects=False)
        if 300 <= resp.status_code < 400:
            loc = resp.headers.get('Location')
            if not loc:
                return None
            try:
                loc = httpx.URL(loc, base=httpx.URL(url)).human_repr()
            except Exception:
                pass
            try:
                follow = httpx.get(loc, headers=_BROWSER_HEADERS)
                return loc if follow.status_code == 200 else None
            except Exception:
                return None
        return url if resp.status_code == 200 else None
    except Exception:
        return None


def check_and_resolve_url(url: str) -> str | None:
    """Returns a valid URL (possibly redirected), tries trimming path segments if needed."""
    resolved = _try_url(url)
    if resolved:
        return resolved
    # Try removing trailing path segments one at a time
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    while '/' in path:
        path = path.rsplit('/', 1)[0]
        trimmed = urlunparse(parsed._replace(path=path or '/'))
        resolved = _try_url(trimmed)
        if resolved:
            lg.info(f'Trimmed URL works: {url} -> {resolved}')
            return resolved
    return None


def generate_ai_summary(schedule: str, text: str, verbose=False, cached=True):
    # Load from cache if exists and caching is enabled
    cache_file =  Path(cache_file_prefix(schedule) + "_summary.jsonl")
    if cached and cache_file.is_file():
        with open(cache_file, "r", encoding="utf-8") as f:
            summary = [json.loads(line) for line in f]
            if verbose:
                lg.info("Loaded summary from cache")
            return summary

    # Generate new summary
    model = Model(COPY_WRITE_MODEL, max_tokens=5000)
    max_articles = 6 if schedule == 'daily' else 8
    latest_newsletters = get_last_newsletter_summaries(schedule, limit=5)
    prompt = load_prompt('copywrite',
                         max_articles=max_articles,
                         latest_newsletters=latest_newsletters,
                         news_emails=text)
    lg.info('Generating summary...')

    for attempt in range(5):
        try:
            result = model.prompt(prompt, response_format=Summary, cached=False)
            break
        except Exception as e:
            wait_time = 5 * (2 ** attempt)
            lg.warning(f'Summary generation attempt {attempt + 1}/5 failed: {e}. Retrying in {wait_time}s...')
            time.sleep(wait_time)
    else:
        result = model.prompt(prompt, response_format=Summary, cached=False)

    summary = Summary(**result) if isinstance(result, dict) else result

    # Check the urls by opening them and see if they return a proper web page
    lg.info('Checking links ...')
    for article in summary.articles:
        article.summary = article.summary.strip()
        for link in list(article.links):
            resolved = check_and_resolve_url(str(link))
            if resolved is None:
                lg.warning(f'Link {link} is not valid')
                article.links.remove(link)
            elif resolved != str(link):
                lg.info(f'Redirected {link} -> {resolved}')
                idx = article.links.index(link)
                article.links[idx] = HttpUrl(resolved)

    # Save to cache and convert to dicts for downstream use
    result = []
    with open(cache_file, "w", encoding="utf-8") as f:
        for article in summary.articles:
            article_dict = article.model_dump(mode='json')
            f.write(json.dumps(article_dict, ensure_ascii=False) + "\n")
            result.append(article_dict)

    return result


def generate_ai_image(articles: list[dict], schedule: str, cached: bool, visual_selection: dict, max_retries: int = 5) -> Tuple[int, str]:
    """
    Genereer 1 afbeelding via Responses API + image_generation tool,
    met 4 image-URL's als STIJLREFERENTIE (geen content copy).
    Slaat de 1e gegenereerde afbeelding op als PNG en retourneert het pad (of upload jouw S3).
    """
    out_path = Path(cache_file_prefix(schedule) + '.png')

    if cached and os.path.isfile(out_path):
        lg.info('Loading image from cache')
        article_index = 0
    else:
        article_index = visual_selection['image_article']
        description = visual_selection['image_description']
        prompt = create_image_prompt(articles[article_index], description, schedule)

        lg.info('Generating image...')
        model = Model(DESIGN_MODEL)
        # Retry logic with exponential backoff
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    lg.warning(f'Generating image (attempt {attempt + 1}/{max_retries})...')

                img = model.generate_image(prompt, STYLE_IMAGES, size=(600, 300))
                img.save(out_path, format="PNG")
                lg.info('Image generated successfully')

                break
                
            except (httpx.ReadTimeout, TimeoutError) as e:
                if attempt == max_retries - 1:  # Last attempt
                    lg.error(f'Failed to generate image after {max_retries} attempts')
                    raise Exception(f'Image generation timed out after {max_retries} attempts') from e
                    
                lg.warning(f'Timeout occurred, retrying...')
                time.sleep(min(30 * 2 ** attempt, 300))

            except Exception as e:
                if attempt == max_retries - 1:  # Last attempt
                    lg.error(f'Failed to generate image: {str(e)}')
                    raise
                lg.error(f'Error generating image: {str(e)}. Retrying...')
                time.sleep(min(30 * 2 ** attempt, 300))

    # Upload to S3
    s3 = S3('harmsen.nl')
    for attempt in range(3):
        try:
            url = s3.add(out_path, 'nieuwsbrief/' + out_path.name)
            return article_index, url
        except Exception as e:
            lg.error(f'Error uploading to S3, retrying... (attempt {attempt + 1}/3)')
            time.sleep(5 * (attempt + 1))  # Exponential backoff for S3 upload
    raise TimeoutError('Failed to upload image to S3 after 3 attempts')


def extract_relevant_source_text(article: dict, source_text: str) -> str:
    """Extract alleen de tekst uit de bron die relevant is voor het artikel."""
    prompt = load_prompt('extract_source',
                         title=article.get('title', ''),
                         summary=article.get('summary', ''),
                         source_text=source_text)

    model = Model('claude-haiku-4-5')
    return model.prompt(prompt, return_json=False, cached=False)


def generate_infographic(articles: list[dict], emails_dict: dict[str, str], schedule: str, cached: bool, visual_selection: dict, max_retries: int = 5) -> Tuple[int, str]:
    out_path = Path(cache_file_prefix(schedule) + "_infographic.png")

    if cached and os.path.isfile(out_path):
        lg.info("Loading image from cache")
        article_index = 0
    else:
        article_index = visual_selection['infographic_article']

        # Haal relevante bronteksten op voor geselecteerd artikel
        article = articles[article_index]
        source_texts = []
        for source in article.get('sources', []):
            for email_source, email_text in emails_dict.items():
                if source in email_source:
                    relevant_text = extract_relevant_source_text(article, email_text)
                    if relevant_text.strip():
                        source_texts.append(relevant_text)
                    break
        source_content = '\n\n---\n\n'.join(source_texts) if source_texts else ''

        prompt = load_prompt('infographic',
                             title=article.get('title', ''),
                             summary=article.get('summary', ''),
                             source_content=source_content)

        lg.info("Generating infographic...")
        model = Model(INFOGRAPHIC_MODEL)
        # Retry logic with exponential backoff
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    lg.warning(f"Generating infographic (attempt {attempt + 1}/{max_retries})...")

                img = model.generate_image(prompt)
                if img is None:
                    raise ValueError('Image generation returned None')
                img.save(out_path, format="PNG")
                lg.info("Image generated successfully")
                break

            except (httpx.ReadTimeout, TimeoutError) as e:
                if attempt == max_retries - 1:  # Last attempt
                    lg.error(f"Failed to generate infographic after {max_retries} attempts")
                    raise Exception(
                        f"Infographic generation timed out after {max_retries} attempts"
                    ) from e

                lg.warning(f"Timeout occurred, retrying...")
                time.sleep(min(30 * 2 ** attempt, 300))

            except Exception as e:
                if attempt == max_retries - 1:  # Last attempt
                    lg.error(f"Failed to generate infographic: {str(e)}")
                    raise
                lg.error(f"Error generating infographic: {str(e)}. Retrying...")
                time.sleep(min(30 * 2 ** attempt, 300))

    # Upload to S3
    s3 = S3("harmsen.nl")
    for attempt in range(3):
        try:
            url = s3.add(out_path, "nieuwsbrief/" + out_path.name)
            return article_index, url
        except Exception as e:
            lg.error(f"Error uploading to S3, retrying... (attempt {attempt + 1}/3)")
            time.sleep(5 * (attempt + 1))  # Exponential backoff for S3 upload
    raise TimeoutError("Failed to upload infographic to S3 after 3 attempts")


def select_articles_for_visuals(articles: list[dict]) -> dict:
    """Selecteer twee verschillende artikelen: één voor de illustratie en één voor de infographic."""
    prompt = load_prompt('select_visuals',
                         num_articles=len(articles),
                         articles=articles,
                         max_index=len(articles) - 1)

    model = Model(ART_DIRECTION_MODEL)
    return retry_prompt(model, prompt)


def create_image_prompt(article, description, schedule):
    cache_file = Path(cache_file_prefix(schedule) + "_image_prompt.txt")
    if cache_file.is_file():
        with open(cache_file, 'r', encoding='utf-8') as f:
            return f.read()

    lg.info('Generating image prompt...')
    if schedule == "daily":
        color = COLORS[Day().day_of_week()]
    else:
        color = COLORS[Day().week_number() % len(COLORS)]

    prompt = load_prompt('image_prompt', description=description, color=color)
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
        except (RatelimitException, ModelOverloadException):
            lg.warning('Hitting rate limit or timeout, retrying...')
            time.sleep(5)
    else:
        raise RatelimitException
