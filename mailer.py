import os
import smtplib
import json
from datetime import datetime, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, make_msgid
from contextlib import contextmanager

from subscribers import get_subscribers
from gmail import Mail

REPLY_TO_EMAIL = "nieuwsbrief@harmsen.nl"
DISPLAY_FROM_EMAIL = "nieuwsbrief@harmsen.nl"


@contextmanager
def logged_in_smtp():
    """Context manager for SMTP connection with login. """

    server = None
    try:
        # Get SMTP settings from environment
        smtp_server = os.getenv('EMAIL_HOST')
        smtp_port = int(os.getenv('EMAIL_PORT', '587'))
        username = os.getenv('EMAIL_HOST_USER')
        password = os.getenv('EMAIL_HOST_PASSWORD')
        
        if not all([smtp_server, username, password]):
            raise ValueError("Missing required SMTP configuration in environment variables")
        
        server = smtplib.SMTP(smtp_server, smtp_port)
        server.starttls()
        server.login(username, password)
        yield server
    except Exception as e:
        print(f"SMTP Error: {e}")
        raise
    finally:
        if server:
            try:
                server.quit()
            except Exception as e:
                print(f"Error closing SMTP connection: {e}")


def create_message(recipient: str, subject: str, html_content: str, reply_to: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = formataddr(("HP's AI nieuwsbrief", "nieuwsbrief@harmsen.nl"))
    msg["To"] = recipient
    msg["Message-ID"] = make_msgid(domain="harmsen.nl")
    msg["List-Unsubscribe"] = (
        f"<mailto:{reply_to}?subject=unsubscribe>, "
        f"<https://harmsen.nl/nieuwsbrief/afmelden/?email={recipient}>"
    )
    msg["X-Entity-Type"] = "newsletter"
    
    part = MIMEText(html_content, "html", "utf-8")
    msg.attach(part)
    
    return msg


def delete_sent_email(message_id: str) -> bool:
    """ Returns bool: True if deletion was successful, False otherwise """
    mail = Mail()
    # Remove angle brackets if present
    message_id = message_id.strip('<>')
    if mail.delete_sent_email_by_message_id(message_id):
        print(f"Deleted sent email with Message-ID: {message_id}")
        return True
    else:
        print(f"Failed to delete sent email with Message-ID: {message_id}")
        return False


def update_last_sent_timestamp(schedule: str) -> None:
    """ schedule is 'daily' or 'weekly' """
    last_sent_file = Path(__file__).parent / 'data' / 'last_sent.json'
    if last_sent_file.exists():
        with open(last_sent_file, 'r') as f:
            last_sent_data = json.load(f)
    else:
        last_sent_data = {"last_sent": {"daily": None, "weekly": None}}

    # Update the timestamp for the current schedule
    last_sent_data['last_sent'][schedule] = datetime.now(timezone.utc).isoformat()

    with open(last_sent_file, 'w') as f:
        json.dump(last_sent_data, f, indent=2)


def mailerlog(s: str=''):
    log_file = Path(__file__).parent / 'data' / 'mailerlog.txt'
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(f"{s}\n")


def send_newsletter(schedule: str, newsletter_html: str, title: str):

    subscribers = get_subscribers(schedule)

    mailerlog(title)

    with logged_in_smtp() as server:
        for recipient in subscribers:
            html = newsletter_html.replace("[EMAIL]", recipient)

            msg = create_message(recipient=recipient, subject=title, html_content=html, reply_to=REPLY_TO_EMAIL)
            server.sendmail(DISPLAY_FROM_EMAIL, [recipient], msg.as_string())
            print(f"Email sent to {recipient}")

            if '@harmsen.nl' not in recipient.lower():
                delete_sent_email(msg['Message-ID'])

            mailerlog(recipient)

        print(f"Newsletter sent successfully to {len(subscribers)} recipients")
        mailerlog()
        update_last_sent_timestamp(schedule)
