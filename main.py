import os
import sys

from dotenv import load_dotenv

from database import add_to_database
from gmail import get_raw_mail_text
from justdays import Day

from ai import generate_ai_summary, generate_ai_image
from formatter import create_html_email
from mailer import send_newsletter

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


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(override=True)

    schedule, use_cached = parse_command_line()
    text = get_raw_mail_text(schedule, cached=use_cached, verbose=VERBOSE)
    articles = generate_ai_summary(schedule, text, cached=use_cached, verbose=VERBOSE)
    article_index, image_url = generate_ai_image(articles, schedule, cached=use_cached)
    articles = [articles[article_index]] + articles[:article_index] + articles[article_index + 1 :]
    title = create_title(schedule)
    html_mail = create_html_email(schedule, articles, title, image_url)
    add_to_database(schedule, title, html_mail, image_url)
    send_newsletter(schedule, html_mail, title)