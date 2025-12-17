import os
import sys
import time

from dotenv import load_dotenv

from database import add_to_database
from gmail import get_raw_mail_text, parse_emails_to_dict
from justdays import Day

from ai import generate_ai_summary, generate_ai_image, generate_infographic, select_articles_for_visuals
from formatter import create_html_email
from log import lg
from mailer import send_newsletter
from undelivered import handle_undelivered

VERBOSE = True
MONTHS = ["januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december"]

def parse_command_line():
    args = sys.argv[1:]
    cached = False
    
    # Handle --cached flag
    if "--cached" in args:
        cached = True
        args = [arg for arg in args if arg != "--cached"]
    
    match args:
        case []:
            print_usage()
        case [cmd] if cmd in ("daily", "weekly"):
            return cmd, cached
        case [cmd, *_]:
            print(f"Invalid command: {cmd}")
    sys.exit(1)


def print_usage():
    print("Usage: python main.py <command> [--cached]")
    print("Commands:")
    print("   daily  - Send daily newsletter")
    print("   weekly - Send weekly newsletter")
    print("Options:")
    print("   --cached  - Use cached data when available")
    print("   weekly - Send weekly newsletter")


def create_title(schedule: str) -> str:
    dag = f"{Day().d} {MONTHS[Day().m - 1]}"
    week = Day().week_number()
    title = (
        f"HP's AI daily - {dag}"
        if schedule == "daily"
        else f"HP's AI weekly - week {week}"
    )
    return title


def main():
    lg.info("============ Starting application ============")
    schedule, cached = parse_command_line()
    text = get_raw_mail_text(schedule, cached=cached, verbose=VERBOSE)
    emails_dict = parse_emails_to_dict(text)
    articles = generate_ai_summary(schedule, text, cached=cached, verbose=VERBOSE)

    # Select articles for both visuals in one prompt
    lg.info('Selecting articles for visuals...')
    visual_selection = select_articles_for_visuals(articles)

    # Image
    article_index, image_url = generate_ai_image(articles, schedule, cached=cached, visual_selection=visual_selection)
    articles = [articles[article_index]] + articles[:article_index] + articles[article_index + 1:]

    # Adjust infographic index after reordering (image article moved to front)
    infographic_original_index = visual_selection['infographic_article']
    if infographic_original_index < article_index:
        infographic_adjusted_index = infographic_original_index + 1
    else:
        infographic_adjusted_index = infographic_original_index
    visual_selection['infographic_article'] = infographic_adjusted_index

    # Infographic
    infographic_article_index, infographic_url = generate_infographic(articles, emails_dict, schedule, cached=cached, visual_selection=visual_selection)

    title = create_title(schedule)
    html_mail = create_html_email(schedule, articles, title, image_url, infographic_url, infographic_article_index)
    add_to_database(schedule, title, html_mail, image_url)
    send_newsletter(schedule, html_mail, title)
    time.sleep(60)
    handle_undelivered()


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(override=True)
    lg.setup_logging('data/app.log')
    main()

