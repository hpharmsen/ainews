#!/Users/hp/scripts/venv/bin/python
import os
import imaplib
import email
import email.header
import json
import re
import time
from datetime import datetime, timezone, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from email.header import decode_header
from pathlib import Path

import html2text

from src.database import cache_file_prefix
from justlog import lg

FILTER_ON_LABEL='y_ai_news'
LAST_SENT_FILE = Path(__file__).parent.parent / 'data' / 'last_sent.json'
# Ondergrens voor het ophaalvenster. Zonder deze klem verkleint een late vorige run
# het venster van de volgende, en dan blijft er te weinig nieuws over.
MIN_LOOKBACK = {'daily': timedelta(days=1), 'weekly': timedelta(weeks=1)}
# De kop van een nieuwsbrief is menubalk plus advertentieblok; het nieuws met de bronlinks
# staat daaronder. Bij 2000 tekens per mail bleef alleen die kop over en had het model geen
# enkele URL om uit te kiezen.
MAX_LEN_PER_MAIL = 8000
MAX_TOTAL_LEN = 50_000
# Onder deze grens is de plaintext-variant een uitgeklede versie zonder bronlinks
# (AlphaSignal levert 1 URL in 8400 tekens) en is de HTML-versie de enige bron van links.
MIN_URLS_IN_PLAIN = 3
URL_PATTERN = re.compile(r'https?://')
# SELECTED_SENDERS = [
# 'aitidbits+ai-coding@substack.com',
# 'aiminds@mail.beehiiv.com'
# ]


def html_to_text(html: str) -> str:
    """Zet een HTML-mail om naar tekst met de links als markdown erin."""
    converter = html2text.HTML2Text()
    converter.body_width = 0  # Geen harde regelafbrekingen middenin een URL
    converter.ignore_images = True
    converter.ignore_emphasis = True
    converter.ignore_tables = True
    return converter.handle(html)


class Mail:
    def __init__(self):
        self.mail = None
        self.email_user = os.getenv('EMAIL_HOST_USER')
        self.email_pass = os.getenv('EMAIL_HOST_PASSWORD')
        self.imap_server = os.getenv('EMAIL_IMAP_SERVER', 'imap.gmail.com')
        self.imap_port = int(os.getenv('EMAIL_IMAP_PORT', 993))

    def connect(self, attempts: int = 3) -> bool:
        """Connect to the IMAP server, met retry op transiente verbindingsfouten."""
        for attempt in range(attempts):
            try:
                self.mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
                self.mail.login(self.email_user, self.email_pass)
                return True
            except Exception as e:
                # Alleen netwerkblips zijn een retry waard. [Errno 54] Connection reset by peer is
                # een OSError, een fout wachtwoord een imaplib.IMAP4.error en die gaat niet over.
                if not isinstance(e, (OSError, imaplib.IMAP4.abort)) or attempt == attempts - 1:
                    lg.error(f"Failed to connect to IMAP server: {str(e)}")
                    return False
                wait = 2 * 2 ** attempt
                lg.warning(f'IMAP connect attempt {attempt + 1}/{attempts} failed: {e}. '
                           f'Retrying in {wait}s...')
                time.sleep(wait)
        return False

    def delete_email(self, identifier, folder='[Gmail]/Sent Mail'):
        """
        Move an email to trash.

        Args:
            identifier: Either a UID (numeric string) or Message-ID
            folder: Mailbox to search in (default: Sent Mail)
        """
        try:
            # Select the folder
            status, _ = self.mail.select(f'"{folder}"' if '/' in folder else folder, readonly=False)
            if status != 'OK':
                lg.error(f"✗ Failed to select {folder} folder")
                return False

            # Determine if identifier is a UID or Message-ID
            if identifier.isdigit():
                # It's a UID, use it directly
                email_uid = identifier.encode('utf-8')
            else:
                # It's a Message-ID, search for it
                status, email_uids = self.mail.uid('search', None, f'(HEADER Message-ID "{identifier}")')
                if status != 'OK' or not email_uids or not email_uids[0]:
                    lg.error(f"✗ Email with Message-ID {identifier} not found in {folder}")
                    return False
                email_uid = email_uids[0].split()[0]

            # Move to trash
            result = self.mail.uid('copy', email_uid, '[Gmail]/Trash')
            if result[0] == 'OK':
                self.mail.uid('store', email_uid, '+FLAGS', '\\Deleted')
                self.mail.expunge()
                lg.info(f"✓ Deleted email {identifier} from {folder}")
                return True
            lg.error(f"✗ Failed to delete email {identifier} from {folder}.\nResult was {result}")
            return False
        except Exception as e:
            lg.error(f"✗ Failed to delete email {identifier} from {folder}\nException was: {str(e)}")
            return False

    def get_emails(self):
        """Retrieve all email UIDs from the specified label."""
        try:
            status, _ = self.mail.select(FILTER_ON_LABEL, readonly=False)
            if status != 'OK':
                lg.error(f"Failed to access label: {FILTER_ON_LABEL}")
                return []

            status, email_uids = self.mail.uid('search', None, 'ALL')
            if status != 'OK' or not email_uids or not email_uids[0]:
                lg.error(f"No emails found in label: {FILTER_ON_LABEL}")
                return []

            return email_uids[0].split()
        except Exception as e:
            lg.error(f"Error getting emails: {str(e)}")
            return []

    def get_email_details(self, email_uid):
        """Get the sender, date, subject, and flags of an email."""
        try:
            # First fetch the flags to check if the email is starred
            status, flags_data = self.mail.uid('fetch', email_uid, '(FLAGS)')
            if status != 'OK' or not flags_data:
                return None
            
            # Convert all data to string for easier parsing
            flags_str = ''
            if isinstance(flags_data[0], tuple):
                # Handle tuple format: [(b'1 (FLAGS (\\Seen \\Flagged))', b'Body text...')]
                flags_str = flags_data[0][1].decode('utf-8', errors='ignore') if len(flags_data[0]) > 1 else flags_data[0][0].decode('utf-8', errors='ignore')
            else:
                # Handle list format: [b'1 (FLAGS (\\Seen \\Flagged))']
                flags_str = b' '.join(flags_data).decode('utf-8', errors='ignore')
            
            # Extract flags between parentheses after FLAGS
            flags_start = flags_str.find('FLAGS (') + 7
            flags_end = flags_str.find(')', flags_start)
            if flags_start > 6 and flags_end > flags_start:
                flags_section = flags_str[flags_start:flags_end]
                # Extract all words starting with backslash
                import re
                flags = re.findall(r'\\(\w+)', flags_section)
            else:
                flags = []
                
            # Check for any flag that might indicate a starred/important email
            is_starred = any(flag.lower() in ['flagged', 'starred', 'star', 'important'] for flag in flags)
            
            # Now fetch the email headers
            status, msg_data = self.mail.uid('fetch', email_uid, '(BODY.PEEK[HEADER.FIELDS (FROM DATE SUBJECT)])')
            if status != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
                return None

            msg = email.message_from_bytes(msg_data[0][1])
            
            # Extract sender information
            from_header = msg.get('From', '')
            if not from_header:
                sender_name = 'Unknown Sender'
                sender_email = ''
            else:
                sender_name, sender_email = parseaddr(from_header)
                if not sender_name and sender_email:
                    sender_name = sender_email.split('@')[0]
                elif not sender_name:
                    sender_name = 'Unknown Sender'

            # Parse date
            date_str = msg.get('Date', '')
            try:
                date = parsedate_to_datetime(date_str) if date_str else datetime.now(timezone.utc)
            except (TypeError, ValueError):
                date = datetime.now(timezone.utc)

            # Get subject, handle encoding
            subject = msg.get('Subject', 'No Subject')
            if subject.startswith('=?'):
                try:
                    subject = email.header.decode_header(subject)[0][0]
                    if isinstance(subject, bytes):
                        subject = subject.decode('utf-8', errors='replace')
                except Exception:
                    pass

            return {
                'id': email_uid,
                'sender_name': sender_name,
                'sender_email': sender_email,
                'date': date,
                'subject': subject.strip(),
                'is_starred': is_starred
            }
        except Exception:
            return None

    def get_message(self, email_uid):
        """De volledige mail als geparst message-object, of None."""
        try:
            status, msg_data = self.mail.uid('fetch', email_uid, '(RFC822)')
            if status != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
                return None
            return email.message_from_bytes(msg_data[0][1])
        except Exception as e:
            lg.error(f"Error fetching message {email_uid}: {str(e)}")
            return None

    def get_email_body(self, email_uid):
        """
        Get the email body for a given email UID, as text with the source links intact.

        Plaintext heeft de voorkeur: die is compacter en bij de meeste nieuwsbrieven staan
        de links er als markdown in. Levert een afzender een plaintext-variant zonder links,
        dan is de HTML-versie de enige plek waar de bronnen nog staan.

        Args:
            email_uid: The UID of the email to retrieve

        Returns:
            str: The email body text or None if not found
        """
        msg = self.get_message(email_uid)
        if msg is None:
            return None
        try:
            plain = html = None
            for part in msg.walk():  # Ook bij niet-multipart levert dit het bericht zelf
                if 'attachment' in str(part.get('Content-Disposition')):
                    continue
                content_type = part.get_content_type()
                if content_type not in ('text/plain', 'text/html'):
                    continue
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode('utf-8', errors='ignore')
                if content_type == 'text/plain' and plain is None:
                    plain = text
                elif content_type == 'text/html' and html is None:
                    html = text

            if plain and len(URL_PATTERN.findall(plain)) >= MIN_URLS_IN_PLAIN:
                return plain
            if html:
                return html_to_text(html)
            return plain

        except Exception as e:
            lg.error(f"Error getting email body: {str(e)}")
            return None

    def get_undelivered(self) -> list[dict[str, str]]:
        """
        Get undelivered emails from Mail Delivery Subsystem.

        Returns:
            List of dicts with 'email_id' and 'recipient_email' keys
        """
        try:
            if not self.mail:
                if not self.connect():
                    lg.error('Failed to connect to IMAP server')
                    return []

            # Select inbox
            status, _ = self.mail.select('INBOX', readonly=True)
            if status != 'OK':
                lg.error('Failed to access INBOX')
                return []

            # Search for emails from Mail Delivery Subsystem or Mail Delivery System
            status, email_uids = self.mail.uid('search', None, '(OR (FROM "Mail Delivery Subsystem") (FROM "Mail Delivery System"))')
            if status != 'OK' or not email_uids or not email_uids[0]:
                return []

            undelivered_emails = []

            for email_uid in email_uids[0].split():
                # Get the full email message
                status, msg_data = self.mail.uid('fetch', email_uid, '(RFC822)')
                if status != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
                    continue

                msg = email.message_from_bytes(msg_data[0][1])

                # Extract the original recipient from the bounce message
                recipient_info = self._extract_original_recipient(msg)
                if recipient_info and recipient_info.get('recipient_email'):
                    undelivered_emails.append({
                        'email_id': email_uid.decode('utf-8'),
                        'recipient_email': recipient_info['recipient_email'],
                        'is_spam_rejection': recipient_info.get('is_spam_rejection', False),
                        'is_permanent': recipient_info.get('is_permanent', False)
                    })

            return undelivered_emails

        except Exception as e:
            lg.error(f'Error getting undelivered emails: {str(e)}')
            return []

    def _extract_original_recipient(self, msg) -> dict | None:
        """
        Extract the original recipient email from a bounce message.

        Args:
            msg: Email message object

        Returns:
            Dict with 'recipient_email' and 'is_spam_rejection' keys, or None if not found
        """
        try:
            # Check various headers that might contain the original recipient
            headers_to_check = [
                'Final-Recipient',
                'Original-Recipient',
                'X-Failed-Recipients'
            ]

            import re

            # Check DSN Status header for permanent vs temporary
            status_header = msg.get('Status', '')
            diagnostic = msg.get('Diagnostic-Code', '')
            is_permanent = bool(re.search(r'5\.\d+\.\d+', status_header + ' ' + diagnostic))

            for header in headers_to_check:
                value = msg.get(header, '')
                if value:
                    # Extract email from header value (format might be 'rfc822;email@domain.com')
                    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', value)
                    if email_match:
                        return {
                            'recipient_email': email_match.group(),
                            'is_spam_rejection': False,
                            'is_permanent': is_permanent
                        }

            # If headers don't contain the info, search in the message body
            body = None
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                        break
            else:
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')

            if body:
                import re

                # Check if this is a spam filter rejection
                spam_indicators = [
                    r'listed on.*spamrl\.com',
                    r'URL.*is listed',
                    r'spam.*filter',
                    r'blacklist',
                    r'blocked.*spam'
                ]

                is_spam_rejection = any(
                    re.search(indicator, body, re.IGNORECASE)
                    for indicator in spam_indicators
                )

                # Detect permanent (5xx) vs temporary (4xx) bounce
                # Look for SMTP status codes like "550", "5.1.1", "452", "4.2.2"
                is_permanent = bool(re.search(
                    r'(?:^|\s)5\.\d+\.\d+|(?:^|\s)5[0-9]{2}\s',
                    body, re.MULTILINE
                ))

                # Look for patterns to extract recipient email from bounce messages
                patterns = [
                    # Gmail delivery incomplete pattern: "delivering your message to email@domain.com"
                    r'delivering your message to\s+([\w\.-]+@[\w\.-]+\.\w+)',
                    # Standard bounce patterns
                    r'(?:failed|error|bounce).*?[\s:]+([\w\.-]+@[\w\.-]+\.\w+)',
                    r'(?:recipient|address).*?[\s:]+([\w\.-]+@[\w\.-]+\.\w+)',
                    r'<([\w\.-]+@[\w\.-]+\.\w+)>.*?(?:failed|error|bounce)',
                    r'([\w\.-]+@[\w\.-]+\.\w+).*?(?:failed|error|bounce|undelivered)',
                    # Generic email address pattern as fallback
                    r'([\w\.-]+@[\w\.-]+\.\w+)'
                ]

                for pattern in patterns:
                    matches = re.findall(pattern, body, re.IGNORECASE | re.MULTILINE)
                    if matches:
                        return {
                            'recipient_email': matches[0],
                            'is_spam_rejection': is_spam_rejection,
                            'is_permanent': is_permanent
                        }

            return None

        except Exception:
            return None

    def close(self):
        """Close the connection to the IMAP server."""
        if self.mail:
            try:
                self.mail.close()
                self.mail.logout()
            except Exception:
                pass


def fetch_window_start(schedule: str) -> datetime:
    """Begin van het ophaalvenster: last_sent, maar altijd minstens MIN_LOOKBACK terug."""
    floor = datetime.now(timezone.utc) - MIN_LOOKBACK[schedule]
    try:
        with open(LAST_SENT_FILE, 'r') as f:
            from_date = datetime.fromisoformat(json.load(f)['last_sent'][schedule])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return floor
    if from_date.tzinfo is None:
        from_date = from_date.replace(tzinfo=timezone.utc)
    return min(from_date, floor)


def get_raw_mail_text(schedule: str, cached: bool=False, verbose: bool=False):
    cache_file = Path(cache_file_prefix(schedule) + '_emails.txt')

    if cached and cache_file.is_file():
        with open(cache_file, 'r', encoding='utf-8') as f:
            lg.info("Using cached emails")
            return f.read()

    lg.info("Connecting to email ...")
    mail = Mail()
    
    if not mail.connect():
        lg.error("Failed to connect to the email server.")
        return

    lg.info("Fetching emails ...")
    email_ids = mail.get_emails()
    if not email_ids:
        return

    from_date = fetch_window_start(schedule)

    candidates = []
    for email_id in email_ids:
        details = mail.get_email_details(email_id)
        if not details or not details.get('date'):
            continue
        email_date = details['date']
        # Make sure email_date is timezone-aware
        if email_date.tzinfo is None:
            email_date = email_date.replace(tzinfo=timezone.utc)
        if email_date >= from_date:
            candidates.append((email_date, email_id, details))

    # Nieuwste eerst: bij een breder venster mag de cap niet het verse nieuws afkappen.
    text = ""
    for _, email_id, details in sorted(candidates, key=lambda c: c[0], reverse=True):
        sender_name = decode_email_header(details['sender_name'])
        subject = decode_email_header(details['subject'])
        body = str(mail.get_email_body(email_id))[:MAX_LEN_PER_MAIL]
        email_text = ' ==================================================\n' + \
        f"Source: {sender_name} {details['sender_email']}\n" + \
        f"Date: {details['date']}\n" + \
        f"Subject: {subject}\n" + \
        body + "\n\n"
        if len(text + email_text) > MAX_TOTAL_LEN:
            break
        text += email_text

    if text:
        with open(cache_file, 'w', encoding='utf-8') as f:
            f.write(text)

    return text


def decode_email_header(header_str: str) -> str:
    parts = []
    for part, encoding in decode_header(header_str):
        if isinstance(part, bytes):
            parts.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            parts.append(part)
    return "".join(parts)


def parse_emails_to_dict(raw_text: str) -> dict[str, str]:
    """Parse raw email text into dict mapping source identifier to full text."""
    emails = {}
    parts = raw_text.split('==================================================')
    for part in parts:
        if not part.strip():
            continue
        for line in part.split('\n'):
            if line.startswith('Source:'):
                source = line.replace('Source:', '').strip()
                emails[source] = part.strip()
                break
    return emails
