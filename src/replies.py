#!/usr/bin/env python3
"""Afhandelaar van de post die binnenkomt op het Gmail-label `nieuwsbrief`.

Leest het label, laat elk binnengekomen bericht classificeren en handelt afmeldingen,
delay-meldingen en harde bounces zelf af. Wat werk voor HP is blijft staan.

Deze module breekt de nieuwsbrief-run nooit af: elke fout wordt een `lg.error` en een
terugkeer, en het tijdstempel schuift dan niet op zodat de volgende run het overdoet.
"""

import json
from datetime import datetime, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from dotenv import load_dotenv
from justlog import lg

from src.ai import classify_reply
from src.gmail import Mail, decode_email_header, html_to_text
from src.subscribers import get_subscriber_status, update_subscription
from src.undelivered import mark_undeliverable, parse_undelivered_emails

LABEL = 'nieuwsbrief'
# Het From-domein scheidt eigen post feilloos van binnenkomende: het Gmail-filter labelt
# hele threads, dus de verzonden nieuwsbrieven en HP's eigen antwoorden staan er ook in.
OWN_DOMAIN = '@harmsen.nl'
SEEN_FILE = Path(__file__).parent.parent / 'data' / 'replies_seen.json'
LOG_FILE = Path(__file__).parent.parent / 'data' / 'replieslog.txt'
# Genoeg om een misclassificatie te herkennen. De mail zelf staat dan in de prullenbak.
LOG_EXCERPT = 200


def last_pass() -> datetime:
    """Tijdstempel van de vorige geslaagde pass. Ontbreekt het, dan is alles nieuw."""
    try:
        with open(SEEN_FILE, 'r') as f:
            moment = datetime.fromisoformat(json.load(f)['last_pass'])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def save_last_pass(moment: datetime) -> None:
    """Leg vast tot waar er gekeken is. Alleen na een pass zonder fout."""
    with open(SEEN_FILE, 'w') as f:
        json.dump({'last_pass': moment.isoformat()}, f, indent=2)


def replieslog(sender: str, category: str, action: str, excerpt: str | None) -> None:
    """Eén tab-gescheiden regel per behandeld bericht, met het fragment op één regel."""
    flat = ' '.join((excerpt or '').split())[:LOG_EXCERPT]
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(f'{datetime.now(timezone.utc).isoformat()}\t{sender}\t{category}\t{action}\t{flat}\n')


def message_date(msg) -> datetime:
    """De Date-header als aware datetime. Onleesbaar of afwezig telt als nu, dus als nieuw."""
    try:
        moment = parsedate_to_datetime(msg.get('Date', ''))
    except (TypeError, ValueError):
        moment = None
    if moment is None:
        return datetime.now(timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def message_text(msg) -> str:
    """De tekst van een bericht. Plaintext als die er is, anders de HTML-variant."""
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
    return plain or (html_to_text(html) if html else '')


def label_uids(mail: Mail) -> list[str]:
    """De UID's in het label. Een leeg label is geen fout, een onbereikbaar label wel."""
    status, _ = mail.mail.select(LABEL, readonly=False)
    if status != 'OK':
        raise RuntimeError(f'kon label {LABEL} niet selecteren')
    status, data = mail.mail.uid('search', None, 'ALL')
    if status != 'OK':
        raise RuntimeError(f'uid search op {LABEL} mislukte')
    return [uid.decode() for uid in data[0].split()] if data and data[0] else []


def handle_unsubscribe(mail: Mail, uid: str, sender: str, body: str) -> bool:
    """Schrijf uit als het adres bekend is, daarna naar de prullenbak."""
    # Alleen uitschrijven na een geslaagde lookup. update_subscription() logt lg.error als
    # er geen rij wordt geraakt, en een afmelding van een onbekend adres moet stil verlopen.
    known = get_subscriber_status(sender) is not None
    if known:
        update_subscription(sender, 'unsubscribed')
    if not mail.delete_email(uid, folder=LABEL):
        if known:
            # De uitschrijving staat al in de database, dus die hoort in het logboek,
            # ook al blijft het bericht staan.
            replieslog(sender, 'afmelding', 'unsubscribed, verwijderen mislukt', body)
        return False
    replieslog(sender, 'afmelding',
               'unsubscribed + verwijderd' if known else 'verwijderd, adres onbekend', body)
    return True


def handle_bounce(mail: Mail, uid: str, sender: str, msg, body: str) -> bool:
    """Verwijder de bounce en tel hem pas daarna mee, nooit andersom."""
    info = mail._extract_original_recipient(msg) or {}
    recipient = info.get('recipient_email')
    if not recipient:
        lg.warning(f'Geen ontvangeradres uit de bounce van {sender}, bericht blijft staan')
        return False
    # Eerst weg, dan tellen: een mislukte verwijdering zou de volgende pass dubbel laten tellen.
    if not mail.delete_email(uid, folder=LABEL):
        return False
    _, to_mark = parse_undelivered_emails([{
        'email_id': uid,
        'recipient_email': recipient,
        'is_spam_rejection': info.get('is_spam_rejection', False),
        'is_permanent': info.get('is_permanent', False),
    }])
    mark_undeliverable(to_mark)
    replieslog(sender, 'bounce', f'{recipient} geteld + verwijderd', body)
    return True


def handle_delay(mail: Mail, uid: str, sender: str, body: str) -> bool:
    """Weg ermee, en niet meetellen. Loopt het alsnog mis, dan volgt er een harde bounce."""
    if not mail.delete_email(uid, folder=LABEL):
        return False
    replieslog(sender, 'delay', 'verwijderd', body)
    return True


def act_on(mail: Mail, uid: str, sender: str, category: str, msg, body: str) -> bool:
    """Voer de actie voor deze categorie uit. True als er iets gebeurd is."""
    if category == 'hp':
        return False  # Blijft onaangeraakt in het label staan
    if category == 'afmelding':
        return handle_unsubscribe(mail, uid, sender, body)
    if category == 'bounce':
        return handle_bounce(mail, uid, sender, msg, body)
    if category == 'delay':
        return handle_delay(mail, uid, sender, body)
    raise ValueError(f'onbekende categorie {category!r} voor {sender}')


def handle_replies() -> None:
    """Eén pass over het label. Gooit nooit iets door naar de aanroeper."""
    started = datetime.now(timezone.utc)
    since = last_pass()
    mail = Mail()
    try:
        if not mail.connect():
            lg.error(f'Afhandelaar kon niet verbinden met IMAP, label {LABEL} niet verwerkt')
            return
        handled = 0
        uids = label_uids(mail)
        for uid in uids:
            msg = mail.get_message(uid)
            if msg is None:
                lg.warning(f'Bericht {uid} in {LABEL} niet op te halen, overgeslagen')
                continue
            sender = parseaddr(msg.get('From', ''))[1].lower()
            if not sender or sender.endswith(OWN_DOMAIN):
                continue
            if message_date(msg) <= since:
                continue
            body = message_text(msg)
            subject = decode_email_header(msg.get('Subject', '')).strip()
            category = classify_reply(sender, subject, body)
            if act_on(mail, uid, sender, category, msg, body):
                handled += 1
        save_last_pass(started)
        lg.info(f'Label {LABEL}: {handled} van {len(uids)} berichten afgehandeld')
    except Exception as e:
        lg.error(f'Fout bij het afhandelen van label {LABEL} - {e}')
    finally:
        mail.close()


if __name__ == '__main__':
    load_dotenv()
    handle_replies()
