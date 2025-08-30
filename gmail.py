#!/Users/hp/scripts/venv/bin/python
import os
import imaplib
import email
import email.header
import json
from datetime import datetime, timezone, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from email.header import decode_header
from pathlib import Path

FILTER_ON_LABEL='ai_news'
# SELECTED_SENDERS = [
# 'aitidbits+ai-coding@substack.com',
# 'aiminds@mail.beehiiv.com'
# ]

class Mail:
    def __init__(self):
        self.mail = None
        self.email_user = os.getenv('EMAIL_HOST_USER')
        self.email_pass = os.getenv('EMAIL_HOST_PASSWORD')
        self.imap_server = os.getenv('EMAIL_IMAP_SERVER', 'imap.gmail.com')
        self.imap_port = int(os.getenv('EMAIL_IMAP_PORT', 993))

    def connect(self):
        """Connect to the IMAP server."""
        try:
            self.mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
            self.mail.login(self.email_user, self.email_pass)
            return True
        except Exception as e:
            print(f"Failed to connect to IMAP server: {str(e)}")
            return False

    def get_emails(self):
        """Retrieve all email IDs from the specified label."""
        try:
            status, _ = self.mail.select(FILTER_ON_LABEL, readonly=False)
            if status != 'OK':
                print(f"Failed to access label: {FILTER_ON_LABEL}")
                return []

            status, email_ids = self.mail.search(None, 'ALL')
            if status != 'OK' or not email_ids or not email_ids[0]:
                print(f"No emails found in label: {FILTER_ON_LABEL}")
                return []

            return email_ids[0].split()
        except Exception as e:
            print(f"Error getting emails: {str(e)}")
            return []

    def get_email_details(self, email_id):
        """Get the sender, date, subject, and flags of an email."""
        try:
            # First fetch the flags to check if the email is starred
            status, flags_data = self.mail.fetch(email_id, '(FLAGS)')
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
            status, msg_data = self.mail.fetch(email_id, '(BODY.PEEK[HEADER.FIELDS (FROM DATE SUBJECT)])')
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
                'id': email_id,
                'sender_name': sender_name,
                'sender_email': sender_email,
                'date': date,
                'subject': subject.strip(),
                'is_starred': is_starred
            }
        except Exception:
            return None

    def get_email_body(self, email_id):
        """
        Get the email body for a given email ID.
        
        Args:
            email_id: The ID of the email to retrieve
            
        Returns:
            str: The email body text or None if not found
        """
        try:
            status, msg_data = self.mail.fetch(email_id, '(RFC822)')
            if status != 'OK' or not msg_data or not isinstance(msg_data[0], tuple):
                return None
                
            msg = email.message_from_bytes(msg_data[0][1])
            
            # Walk through the email parts to find the text/plain or text/html part
            body = None
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition'))
                    
                    # Skip any text/plain (txt) attachments
                    if 'attachment' not in content_disposition:
                        if content_type == 'text/plain':
                            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                        elif content_type == 'text/html' and body is None:
                            # Use HTML as fallback if no plain text version is available
                            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
            else:
                # Not multipart - just get the payload
                body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                
            return body
            
        except Exception as e:
            print(f"Error getting email body: {str(e)}")
            return None

    def close(self):
        """Close the connection to the IMAP server."""
        if self.mail:
            try:
                self.mail.close()
                self.mail.logout()
            except Exception:
                pass


def get_raw_mail_text(schedule: str, cached: bool=False, verbose: bool=False):

    cache_file = Path(__file__).parent / 'cache' / f'{schedule}_emails.txt'

    if cached and cache_file.is_file():
        with open(cache_file, 'r', encoding='utf-8') as f:
            print("Using cached emails")
            return f.read()

    print("Connecting to email ...")
    mail = Mail()
    
    if not mail.connect():
        print("Failed to connect to the email server.")
        return

    print("Fetching emails ...")
    email_ids = mail.get_emails()
    if not email_ids:
        return

    # Read last sent date from last_sent.json
    last_sent_file = Path(__file__).parent / 'last_sent.json'
    try:
        with open(last_sent_file, 'r') as f:
            last_sent_data = json.load(f)
        from_date = datetime.fromisoformat(last_sent_data['last_sent'][schedule])
        if from_date.tzinfo is None:
            from_date = from_date.replace(tzinfo=timezone.utc)
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        # Fallback to default date if file doesn't exist or is invalid
        from_date = datetime.now(timezone.utc) - timedelta(weeks=1) if schedule == 'weekly' else datetime.now(timezone.utc) - timedelta(days=1)

    text = ""
    for email_id in email_ids:
        details = mail.get_email_details(email_id)
        if not details:
            continue
        if details.get('date'):
            email_date = details['date']
            # Make sure email_date is timezone-aware
            if email_date.tzinfo is None:
                email_date = email_date.replace(tzinfo=timezone.utc)
            if email_date >= from_date:
                sender_name = decode_email_header(details['sender_name'])
                if verbose:
                    subject = decode_email_header(details['subject'])
                    print(f"{sender_name} {details['sender_email']} - {details['date']} - {subject}")
                body = mail.get_email_body(email_id)
                text += body + "\n\n"

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
