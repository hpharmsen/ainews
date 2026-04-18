#!/usr/bin/env python3

import json
from pathlib import Path
from dotenv import load_dotenv
from justdays import Day

from src.gmail import Mail
from src.log import lg
from src.mailer import delete_email
from src.subscribers import update_subscription, get_subscriber_status

undelivered_file = Path(__file__).parent.parent / 'data' / 'undelivered.json'

# Thresholds for marking as undeliverable
PERMANENT_BOUNCE_THRESHOLD = 2  # 5xx errors (user doesn't exist, domain gone)
TEMPORARY_BOUNCE_THRESHOLD = 5  # 4xx errors (mailbox full, temp unavailable)
RESET_AFTER_DAYS = 30  # Reset bounce counter after 30 days without bounces


def _new_entry() -> dict:
    """Create a new undelivered tracking entry."""
    return {'count': 0, 'permanent_count': 0, 'last_bounce': None}


def _migrate_entry(value) -> dict:
    """Migrate old format (plain int) to new format."""
    if isinstance(value, int):
        return {'count': value, 'permanent_count': 0, 'last_bounce': None}
    return value


def load_undelivered_data() -> dict[str, dict]:
    """Load and migrate undelivered email tracking data."""
    try:
        with open(undelivered_file, 'r') as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    return {email: _migrate_entry(v) for email, v in raw.items()}


def save_undelivered_data(data: dict[str, dict]) -> None:
    """Save undelivered email tracking data."""
    with open(undelivered_file, 'w') as f:
        json.dump(data, f, indent=2)


def reset_undelivered(email: str) -> None:
    """Remove bounce history for an email address (e.g. on re-subscribe)."""
    data = load_undelivered_data()
    if email in data:
        del data[email]
        save_undelivered_data(data)
        lg.info(f'Reset bounce history for {email}')


def cleanup_stale_entries(data: dict[str, dict]) -> dict[str, dict]:
    """Remove entries that haven't bounced in RESET_AFTER_DAYS days."""
    today = Day()
    cleaned = {}
    for email, entry in data.items():
        last_bounce = entry.get('last_bounce')
        if last_bounce and (today - Day(last_bounce)) >= RESET_AFTER_DAYS:
            lg.info(f'{email} has not bounced in {RESET_AFTER_DAYS}+ days, resetting counter')
            continue
        cleaned[email] = entry
    return cleaned


def get_mail():
    mail = Mail()
    if not mail.connect():
        lg.error('Failed to connect to email server')
        exit(1)
    return mail


def get_undelivered_emails(mail: Mail) -> list[dict[str, str]]:
    lg.info('Retrieving undelivered emails...')
    undelivered_emails = mail.get_undelivered()
    if not undelivered_emails:
        lg.info('No undelivered emails found')
        exit(0)
    lg.info(f'Found {len(undelivered_emails)} undelivered emails')
    return undelivered_emails


def parse_undelivered_emails(undelivered_emails):
    undelivered_data = load_undelivered_data()
    undelivered_data = cleanup_stale_entries(undelivered_data)

    emails_to_delete = []
    emails_to_mark_undeliverable = []
    spam_rejections_count = 0
    today = str(Day())

    for item in undelivered_emails:
        email_id = item['email_id']
        recipient_email = item['recipient_email']
        is_spam_rejection = item.get('is_spam_rejection', False)
        is_permanent = item.get('is_permanent', False)

        emails_to_delete.append(email_id)

        if is_spam_rejection:
            spam_rejections_count += 1
            lg.info(f'{recipient_email} - spam filter rejection, deleting without counting')
            continue

        # Update tracking entry
        entry = undelivered_data.get(recipient_email, _new_entry())
        entry['count'] += 1
        if is_permanent:
            entry['permanent_count'] += 1
        entry['last_bounce'] = today
        undelivered_data[recipient_email] = entry

        # Check thresholds: permanent bounces are more severe
        threshold = PERMANENT_BOUNCE_THRESHOLD if entry['permanent_count'] > 0 else TEMPORARY_BOUNCE_THRESHOLD
        bounce_type = '5xx permanent' if is_permanent else '4xx temporary'

        if entry['count'] >= threshold:
            emails_to_mark_undeliverable.append(recipient_email)
            lg.info(
                f'{recipient_email} reached {entry["count"]} bounces ({bounce_type}), '
                f'threshold={threshold}, marking as undeliverable'
            )

    save_undelivered_data(undelivered_data)
    regular_failures = len(undelivered_emails) - spam_rejections_count
    lg.info(f'Updated undelivered counts for {regular_failures} emails ({spam_rejections_count} spam rejections ignored)')
    return emails_to_delete, emails_to_mark_undeliverable


def delete_emails(emails_to_delete):
    deleted_count = 0
    for email_id in emails_to_delete:
        if delete_email(email_id, folder='INBOX'):
            deleted_count += 1
    lg.info(f'Deleted {deleted_count}/{len(emails_to_delete)} undelivered emails')


def mark_undeliverable(emails_to_mark_undeliverable):
    marked = 0
    for email_address in emails_to_mark_undeliverable:
        # Check if subscriber re-subscribed after their last bounce
        sub = get_subscriber_status(email_address)
        if sub and sub['status'] in ('daily', 'weekly') and sub.get('updated_at'):
            data = load_undelivered_data()
            entry = data.get(email_address, {})
            last_bounce = entry.get('last_bounce')
            if last_bounce and sub['updated_at'].replace(tzinfo=None) > Day(last_bounce).as_datetime():
                lg.info(f'{email_address} re-subscribed after last bounce, resetting counter')
                reset_undelivered(email_address)
                continue
        update_subscription(email_address, 'undeliverable')
        marked += 1
    lg.info(f'{marked} emails marked as undeliverable\n')


def handle_undelivered():
    mail = get_mail()
    try:
        undelivered_emails = get_undelivered_emails(mail)
        emails_to_delete, emails_to_mark_undeliverable = parse_undelivered_emails(undelivered_emails)
        delete_emails(emails_to_delete)
        mark_undeliverable(emails_to_mark_undeliverable)
    except Exception as e:
        lg.error(f'Error processing undelivered emails - {e}\n')
    finally:
        mail.close()


if __name__ == '__main__':
    load_dotenv()
    handle_undelivered()
