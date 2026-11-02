import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from justdays import Day
from justlog import lg
from sqlalchemy import MetaData, and_, create_engine, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import OperationalError

DB_CONNECT_TIMEOUT = 10  # seconden; zonder timeout hangt een dode RDS-host minutenlang


def normalize_db_url(db_url):
    """Ensure the database URL uses the postgresql:// scheme."""
    if db_url and db_url.startswith('postgres://'):
        db_url = 'postgresql://' + db_url[11:]
    return db_url


def connect_with_retry(attempts: int = 3):
    """Connect to the PostgreSQL database with timeout and retry; return the engine and reflected tables."""
    db_url = normalize_db_url(os.getenv('DATABASE_URL'))
    assert db_url, 'DATABASE_URL environment variable not set'

    engine = create_engine(db_url, connect_args={'connect_timeout': DB_CONNECT_TIMEOUT})
    for attempt in range(attempts):
        try:
            metadata = MetaData()
            metadata.reflect(bind=engine)
            break
        except OperationalError as e:
            # Een timeout op de RDS-host is meestal een tijdelijke hapering.
            if attempt == attempts - 1:
                raise
            wait = 2 * 2**attempt
            lg.warning(f'Database connect attempt {attempt + 1}/{attempts} failed: {e}. Retrying in {wait}s...')
            time.sleep(wait)
    return engine, metadata.tables


def db_connect():
    """Connect to the PostgreSQL database and return the engine and newsletter table."""
    engine, tables = connect_with_retry()
    assert 'nieuwsbrief_newsletter' in tables, "Table 'nieuwsbrief_newsletter' not found in the database"
    return engine, tables['nieuwsbrief_newsletter']


def add_to_database(schedule, title, newsletter_html, image_url):
    """
    Voeg een nieuwsbrief toe aan de database, waarbij er maar één per dag per schedule kan bestaan.
    Als er al een nieuwsbrief met dezelfde schedule op dezelfde dag bestaat, wordt deze vervangen.
    Gebruikt de Amsterdam tijdzone voor de datumvergelijking.
    Is de database onbereikbaar, dan logt dit een ERROR en gaat de verzending gewoon door.
    """
    try:
        engine, table = db_connect()
    except OperationalError as e:
        lg.error(f'Error connecting to the database, newsletter not stored: {e}')
        return

    # Huidige tijd in Amsterdam tijdzone
    amsterdam_tz = timezone(timedelta(hours=2))  # CEST (UTC+2)
    now = datetime.now(amsterdam_tz)

    with engine.begin() as conn:
        # Delete any existing newsletter with the same schedule on the same day
        delete_stmt = table.delete().where(and_(table.c.schedule == schedule, func.date(table.c.sent) == now.date()))
        conn.execute(delete_stmt)

        # Insert the new newsletter
        insert_stmt = pg_insert(table).values(
            schedule=schedule, title=title, sent=now, text=newsletter_html, image_url=image_url
        )
        conn.execute(insert_stmt)

    lg.info(f'✅ Nieuwsbrief toegevoegd aan de database (heeft eventuele bestaande voor {now.date()} vervangen).')


MONTHS_NL = [
    'januari',
    'februari',
    'maart',
    'april',
    'mei',
    'juni',
    'juli',
    'augustus',
    'september',
    'oktober',
    'november',
    'december',
]


def get_last_newsletter_summaries(schedule: str, limit: int = 5) -> str:
    """Lees de laatste N summary JSONL-bestanden uit de cache en formatteer voor dedupe."""
    cache_dir = Path(__file__).parent.parent / 'cache'
    suffix = '_summary.jsonl'

    if schedule == 'daily':
        pattern = re.compile(r'^(\d{4}-\d{2}-\d{2})' + re.escape(suffix) + '$')
    else:
        pattern = re.compile(r'^(week\d+)' + re.escape(suffix) + '$')

    # Vind en sorteer bestanden (nieuwste eerst)
    matches = []
    for f in cache_dir.iterdir():
        m = pattern.match(f.name)
        if m:
            matches.append((m.group(1), f))
    matches.sort(key=lambda x: x[0], reverse=True)

    # Skip vandaag (dat is de huidige run)
    today_prefix = str(Day()) if schedule == 'daily' else f'week{Day().week_number()}'
    matches = [(prefix, path) for prefix, path in matches if prefix != today_prefix]

    parts = []
    for prefix, path in matches[:limit]:
        articles = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if schedule == 'daily':
            d = Day(prefix)
            label = f'{d.d} {MONTHS_NL[d.m - 1]} {d.y}'
        else:
            label = prefix
        lines = [f'- "{a["title"]}": {a["summary"][:150]}' for a in articles]
        parts.append(f'Nieuwsbrief {label}:\n' + '\n'.join(lines))

    return '\n\n'.join(parts)


def cache_file_prefix(schedule: str) -> str:
    name = str(Day()) if schedule == 'daily' else f'week{Day().week_number()}'
    return str(Path(__file__).parent.parent / 'cache' / name)


def cleanup_cache(keep_days: int = 14) -> None:
    """Verwijder cache-bestanden ouder dan keep_days dagen."""
    cache_dir = Path(__file__).parent.parent / 'cache'
    if not cache_dir.exists():
        return

    cutoff = Day() - keep_days
    daily_pattern = re.compile(r'^(\d{4}-\d{2}-\d{2})')
    weekly_pattern = re.compile(r'^week(\d+)')
    cutoff_week = cutoff.week_number()
    cutoff_year = cutoff.y
    removed = 0

    for f in cache_dir.iterdir():
        if f.name == '__pycache__':
            continue
        m = daily_pattern.match(f.name)
        if m:
            if Day(m.group(1)) < cutoff:
                f.unlink()
                removed += 1
            continue
        m = weekly_pattern.match(f.name)
        if m:
            week_num = int(m.group(1))
            # Verwijder als het weeknummer ouder is (simpele vergelijking binnen hetzelfde jaar)
            if week_num < cutoff_week and cutoff_year == Day().y:
                f.unlink()
                removed += 1

    if removed:
        lg.info(f'Cache cleanup: {removed} bestanden verwijderd (ouder dan {keep_days} dagen)')
