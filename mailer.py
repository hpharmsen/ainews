import os
import smtplib
import json
from datetime import datetime, timezone
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, make_msgid

from subscribers import get_subscribers


def send_newsletter(schedule: str, newsletter_html: str, title: str):
    """
    Send newsletter to all subscribers with status 'dagelijks'.
    
    Args:
        newsletter_html (str): The HTML content of the newsletter to send
    """
    try:
        # Get subscribers with status 'dagelijks'
        subscribers = get_subscribers(schedule)
        
        if not subscribers:
            print(f"No subscribers found with status {schedule}")
            return
        
        # Email configuration
        sender_login_email = os.getenv('EMAIL_HOST_USER')
        password = os.getenv('EMAIL_HOST_PASSWORD')
        smtp_server = os.getenv('EMAIL_HOST')
        smtp_port = int(os.getenv('EMAIL_PORT', 587))
        reply_to_email = "nieuwsbrief@harmsen.nl"
        display_from_email = "nieuwsbrief@harmsen.nl"

        # Connect to SMTP server and send emails
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_login_email, password)

            # Send emails to all subscribers
            for recipient in subscribers:
                html = newsletter_html.replace("[EMAIL]", recipient)

                msg = MIMEMultipart("alternative")
                msg["Subject"] = title
                msg["From"] = formataddr(("HP's AI nieuwsbrief", display_from_email))
                msg["To"] = recipient
                # Optional but nice for newsletters:
                msg["Message-ID"] = make_msgid(domain="harmsen.nl")
                msg["List-Unsubscribe"] = (
                    f"<mailto:{reply_to_email}?subject=unsubscribe>, <https://harmsen.nl/nieuwsbrief/afmelden/?email={recipient}>"
                )
                msg["X-Entity-Type"] = "newsletter"

                part = MIMEText(html, "html", "utf-8")
                msg.attach(part)

                # Prefer sendmail/send_message with explicit envelope from (may be overridden by Gmail; see notes)
                server.sendmail(
                    display_from_email,  # envelope MAIL FROM (Return-Path)
                    [recipient],
                    msg.as_string(),
                )
                print(f"Email sent to {recipient}")
            
            print(f"Newsletter sent successfully to {len(subscribers)} recipients")
            
            # Update last_sent.json with current timestamp
            last_sent_file = Path(__file__).parent / 'last_sent.json'
            try:
                if last_sent_file.exists():
                    with open(last_sent_file, 'r') as f:
                        last_sent_data = json.load(f)
                else:
                    last_sent_data = {"last_sent": {"daily": None, "weekly": None}}
                
                # Update the timestamp for the current schedule
                last_sent_data['last_sent'][schedule] = datetime.now(timezone.utc).isoformat()
                
                # Write back to the file
                with open(last_sent_file, 'w') as f:
                    json.dump(last_sent_data, f, indent=2)
                
                print(f"Updated last_sent.json with new timestamp for {schedule} newsletter")
                
            except Exception as update_error:
                print(f"Warning: Failed to update last_sent.json: {update_error}")

    except Exception as e:
        print(f"Error sending newsletter: {e}")
        raise
