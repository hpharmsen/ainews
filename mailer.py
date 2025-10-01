import os
import smtplib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, make_msgid
from contextlib import contextmanager

from justdays import Day

from subscribers import get_subscribers
from gmail import Mail
from log import lg

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
        lg().error(f"SMTP Error: {e}")
        raise
    finally:
        if server:
            try:
                server.quit()
            except Exception as e:
                lg().error(f"Error closing SMTP connection: {e}")


def create_message(recipient: str, subject: str, html_content: str, reply_to: str) -> MIMEMultipart:
    # Create message container
    msg = MIMEMultipart("alternative")
    
    # Set basic headers
    msg["Subject"] = subject
    msg["From"] = formataddr(("HP's AI nieuwsbrief", "nieuwsbrief@harmsen.nl"))
    msg["To"] = recipient
    msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain="harmsen.nl")
    msg["X-Entity-Type"] = "newsletter"
    
    # Required headers for bulk email
    unsubscribe_url = f"https://harmsen.nl/nieuwsbrief/afmelden/?email={recipient}"
    msg["List-Unsubscribe"] = f"<mailto:{reply_to}?subject=unsubscribe>, <{unsubscribe_url}>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg["Precedence"] = "bulk"
    msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    
    # Create the plain-text version
    text = """Je ontvangt dit bericht omdat je je hebt aangemeld voor de AI nieuwsbrief.
    Als je deze e-mail niet kunt lezen, bekijk deze dan in je browser: {url}
    
    Uitschrijven kan hier: {unsubscribe}""".format(
        url=unsubscribe_url.replace("afmelden/", ""),
        unsubscribe=unsubscribe_url
    )
    
    # Create both plain and HTML parts
    part1 = MIMEText(text, "plain", "utf-8")
    part2 = MIMEText(html_content, "html", "utf-8")
    
    # Attach parts to message
    msg.attach(part1)
    msg.attach(part2)
    
    return msg


def delete_email(message_id: str) -> bool:
    """ Returns bool: True if deletion was successful, False otherwise """
    try:
        mail = Mail()
    except Exception as e:
        lg().error(f"✗ Error connecting to IMAP server while trying to an email\n{str(e)}")
        return False
    message_id = message_id.strip('<>') # Remove angle brackets if present
    return mail.delete_email(message_id)


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


def get_mailerlog(day:Day) -> set:
    res = set()
    log_file = Path(__file__).parent / 'data' / 'mailerlog.txt'
    with open(log_file, 'r', encoding='utf-8') as f:
        for line in f:
            try:
                schedule, sent_day, receipient = line.strip().split()
                if sent_day == str(day):
                    res.add(receipient)
            except:
                pass
    return res


def send_newsletter(schedule: str, newsletter_html: str, title: str):
    subscribers = get_subscribers(schedule)
    already_mailed = get_mailerlog(Day())
    subscribers = [s for s in subscribers if s not in already_mailed]

    # Rate limiting: Send max 100 emails per batch with 1-second delay
    BATCH_SIZE = 100

    # Collect Message-IDs for deletion after sending
    message_ids_to_delete = []

    with logged_in_smtp() as server:
        for i, recipient in enumerate(subscribers, 1):
            try:
                # Personalize the HTML content
                html = newsletter_html.replace("[EMAIL]", recipient)

                # Create and send the message
                msg = create_message(
                    recipient=recipient,
                    subject=title,
                    html_content=html,
                    reply_to=REPLY_TO_EMAIL
                )

                # Add tracking headers
                msg['X-Campaign-ID'] = f"ai-newsletter-{datetime.now(timezone.utc).strftime('%Y%m%d')}"

                # Send the email
                server.sendmail(DISPLAY_FROM_EMAIL, [recipient], msg.as_string())
                lg().info(f"Email sent to {recipient}")

                # Collect Message-ID for later deletion (if not @harmsen.nl)
                if '@harmsen.nl' not in recipient.lower():
                    message_ids_to_delete.append(msg['Message-ID'])

                # Log successful send
                mailerlog(f"{schedule} {Day()} {recipient}")

                # Rate limiting
                if i % BATCH_SIZE == 0 and i < len(subscribers):
                    lg().info(f"Sent {i} emails, pausing for 60 seconds...")
                    time.sleep(60)
                else:
                    time.sleep(1)  # Small delay between emails

            except Exception as e:
                lg().error(f"Error sending to {recipient}: {str(e)}")

    # Delete sent emails after a delay to allow Gmail to process them
    if message_ids_to_delete:
        lg().info(f"Waiting 10 seconds before deleting {len(message_ids_to_delete)} sent emails...")
        time.sleep(10)

        deleted_count = 0
        for message_id in message_ids_to_delete:
            if delete_email(message_id):
                deleted_count += 1
            time.sleep(1)  # Small delay between deletions

        lg().info(f"Successfully deleted {deleted_count}/{len(message_ids_to_delete)} sent emails")

    lg().info(f"Newsletter sending completed. Sent to {len(subscribers)} recipients.")
    mailerlog()
    update_last_sent_timestamp(schedule)
