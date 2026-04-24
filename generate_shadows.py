"""Generate shadow images for the last two weeks of newsletters using GPT 5.4 Image 2."""
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv
from justdays import Day

from src.log import lg

SHADOW_MODEL = 'openrouter/openai/gpt-5.4-image-2'
CACHE_DIR = Path(__file__).parent / 'cache'


def _run_generation(code: str, timeout: int = 600) -> bool:
    """Run a generation snippet in a subprocess to avoid connection pool issues."""
    try:
        result = subprocess.run(
            [sys.executable, '-c', code],
            capture_output=True, text=True, timeout=timeout,
            cwd=str(Path(__file__).parent),
        )
        if result.returncode != 0:
            lg.error(f'Subprocess failed: {result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown"}')
            return False
        return True
    except subprocess.TimeoutExpired:
        lg.error(f'Subprocess timed out after {timeout}s')
        return False


def generate_shadow_header(date_str: str):
    out_path = CACHE_DIR / f'{date_str}_shadow.png'
    prompt_file = CACHE_DIR / f'{date_str}_image_prompt.txt'
    if out_path.is_file():
        lg.info(f'{date_str}: shadow header exists, skipping')
        return
    if not prompt_file.is_file():
        lg.info(f'{date_str}: no image prompt, skipping')
        return

    lg.info(f'{date_str}: generating shadow header...')
    code = f'''
import os
os.chdir("{Path(__file__).parent}")
from dotenv import load_dotenv
load_dotenv(override=True)
from justai import Model
from src.ai import STYLE_IMAGES
from pathlib import Path
prompt = Path("{prompt_file}").read_text()
model = Model("{SHADOW_MODEL}")
img = model.generate_image(prompt, STYLE_IMAGES, size=(600, 300))
img.save("{out_path}", format="PNG")
'''
    if _run_generation(code):
        lg.info(f'{date_str}: shadow header saved')
    else:
        lg.error(f'{date_str}: shadow header failed')


def generate_shadow_infographic(date_str: str):
    out_path = CACHE_DIR / f'{date_str}_infographic_shadow.png'
    summary_file = CACHE_DIR / f'{date_str}_summary.jsonl'
    if out_path.is_file():
        lg.info(f'{date_str}: shadow infographic exists, skipping')
        return
    if not summary_file.is_file():
        lg.info(f'{date_str}: no summary, skipping')
        return

    lg.info(f'{date_str}: generating shadow infographic...')
    code = f'''
import os, json
os.chdir("{Path(__file__).parent}")
from dotenv import load_dotenv
load_dotenv(override=True)
from justai import Model
from src.ai import load_prompt
from pathlib import Path
articles = [json.loads(l) for l in Path("{summary_file}").read_text().splitlines() if l.strip()]
a = articles[0]
prompt = load_prompt("infographic", title=a.get("title",""), summary=a.get("summary",""), source_content="")
model = Model("{SHADOW_MODEL}")
img = model.generate_image(prompt)
img.save("{out_path}", format="PNG")
'''
    if _run_generation(code):
        lg.info(f'{date_str}: shadow infographic saved')
    else:
        lg.error(f'{date_str}: shadow infographic failed')


def main():
    lg.setup_logging('data/app.log')
    today = Day()
    start = today - 14

    dates = []
    d = start
    while d <= today:
        date_str = str(d)
        if (CACHE_DIR / f'{date_str}_summary.jsonl').is_file():
            dates.append(date_str)
        d = d + 1

    lg.info(f'Generating shadow images for {len(dates)} days')

    for date_str in dates:
        generate_shadow_header(date_str)
        generate_shadow_infographic(date_str)


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(override=True)
    main()
