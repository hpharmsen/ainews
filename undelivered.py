#!/usr/bin/env python3

import json
from pathlib import Path
from dotenv import load_dotenv

from gmail import Mail
from log import lg
from mailer import delete_email
from subscribers import update_subscription

undelivered_file = Path(__file__).parent / "data" / "undelivered.json"


def load_undelivered_data() -> dict[str, int]:
    """Load undelivered email count data from JSON file."""
    with open(undelivered_file, 'r') as f:
        return json.load(f)


def save_undelivered_data(data: dict[str, int]) -> None:
    """Save undelivered email count data to JSON file."""
    with open(undelivered_file, 'w') as f:
        json.dump(data, f, indent=2)


def get_mail():
    mail = Mail()
    if not mail.connect():
        lg.error('Failed to connect to email server')
        exit(1)
    return mail


def get_undelivered_emails(mail: Mail) -> list[dict[str, str]]:
    lg.info("Retrieving undelivered emails...")
    undelivered_emails = mail.get_undelivered()
    if not undelivered_emails:
        lg.info("No undelivered emails found")
        exit(0)
    lg.info(f"Found {len(undelivered_emails)} undelivered emails")
    return undelivered_emails


def parse_undelivered_emails(undelivered_emails):
    undelivered_data = load_undelivered_data()
    emails_to_delete = []
    emails_to_mark_undeliverable = []
    spam_rejections_count = 0

    for item in undelivered_emails:
        email_id = item["email_id"]
        recipient_email = item["recipient_email"]
        is_spam_rejection = item.get("is_spam_rejection", False)

        # Always add email to deletion list
        emails_to_delete.append(email_id)

        if is_spam_rejection:
            # Spam rejections: delete email but don't count as undeliverable
            spam_rejections_count += 1
            lg.info(f"{recipient_email} - spam filter rejection, deleting email without counting")
        else:
            # Regular delivery failures: increase count
            current_count = undelivered_data.get(recipient_email, 0) + 1
            undelivered_data[recipient_email] = current_count

            # Check if counter reaches 2, mark for undeliverable status
            if current_count >= 2:
                emails_to_mark_undeliverable.append(recipient_email)
                lg.info(
                    f"{recipient_email} reached {current_count} undelivered emails, marking as undeliverable"
                )

    # Save updated undelivered data
    save_undelivered_data(undelivered_data)
    regular_failures = len(undelivered_emails) - spam_rejections_count
    lg.info(f"Updated undelivered counts for {regular_failures} emails ({spam_rejections_count} spam rejections ignored)")
    return emails_to_delete, emails_to_mark_undeliverable


def delete_emails(emails_to_delete):
    deleted_count = 0
    for email_id in emails_to_delete:
        if delete_email(email_id, folder='INBOX'):
            deleted_count += 1
    lg.info(f'Deleted {deleted_count}/{len(emails_to_delete)} undelivered emails')


def mark_undeliverable(emails_to_mark_undeliverable):
    for email_address in emails_to_mark_undeliverable:
        update_subscription(email_address, "undeliverable")
    lg.info(f"{len(emails_to_mark_undeliverable)} emails marked as undeliverable\n")


def handle_undelivered():
    mail = get_mail()
    try:
        undelivered_emails = get_undelivered_emails(mail)
        emails_to_delete, emails_to_mark_undeliverable = parse_undelivered_emails(undelivered_emails)
        delete_emails(emails_to_delete)
        mark_undeliverable(emails_to_mark_undeliverable)
    except Exception as e:
        lg.error(f"Error processing undelivered emails - {e}\n")
    finally:
        mail.close()


if __name__ == '__main__':
    load_dotenv()
    handle_undelivered()
