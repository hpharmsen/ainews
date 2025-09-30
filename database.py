import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sqlalchemy import create_engine, MetaData, text, select, desc, func, and_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from justdays import Day

from log import lg


def normalize_db_url(db_url):
    """Ensure the database URL uses the postgresql:// scheme."""
    if db_url and db_url.startswith('postgres://'):
        db_url = 'postgresql://' + db_url[11:]
    return db_url


def db_connect():
    """Connect to the PostgreSQL database and return the engine and podcast table."""
    # Get and normalize the database URL
    db_url = normalize_db_url(os.getenv("DATABASE_URL"))
    assert db_url, "DATABASE_URL environment variable not set"

    try:
        engine = create_engine(db_url)

        # Test the connection
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

        metadata = MetaData()
        metadata.reflect(bind=engine)

        # Get the podcast table
        assert 'nieuwsbrief_newsletter' in metadata.tables, "Table 'nieuwsbrief_newsletter' not found in the database"

        newsletter_table = metadata.tables['nieuwsbrief_newsletter']
        return engine, newsletter_table

    except Exception as e:
        lg().error(f"Error connecting to the database: {e}\nDatabase URL: {db_url}")
        sys.exit(1)


def add_to_database(schedule, title, newsletter_html, image_url):
    """
    Voeg een nieuwsbrief toe aan de database, waarbij er maar één per dag per schedule kan bestaan.
    Als er al een nieuwsbrief met dezelfde schedule op dezelfde dag bestaat, wordt deze vervangen.
    Gebruikt de Amsterdam tijdzone voor de datumvergelijking.
    """
    engine, table = db_connect()
    
    # Huidige tijd in Amsterdam tijdzone
    amsterdam_tz = timezone(timedelta(hours=2))  # CEST (UTC+2)
    now = datetime.now(amsterdam_tz)
    
    with engine.begin() as conn:
        # Delete any existing newsletter with the same schedule on the same day
        delete_stmt = table.delete().where(
            and_(
                table.c.schedule == schedule,
                func.date(table.c.sent) == now.date()
            )
        )
        conn.execute(delete_stmt)
        
        # Insert the new newsletter
        insert_stmt = pg_insert(table).values(
            schedule=schedule,
            title=title,
            sent=now,
            text=newsletter_html,
            image_url=image_url
        )
        conn.execute(insert_stmt)
    
    lg().info(f"✅ Nieuwsbrief toegevoegd aan de database (heeft eventuele bestaande voor {now.date()} vervangen).")


def     get_last_newsletter_texts(schedule: str, limit: int = 1) -> str:
    engine, table = db_connect()
    stmt = (
        select(table.c.text)
        .where(table.c.schedule == schedule)
        .order_by(desc(table.c.sent))
        .limit(limit)
    )

    with engine.connect() as conn:
        records = conn.execute(stmt).scalars().all()

    parts = []
    for text in records:
        try:
            part = text.split("<!-- Cards -->", 1)[1].split("<!-- Footer -->")[0]
            part = re.sub(r"<[^>]*>", "", part)
            parts += [part]
        except IndexError:
            # fallback als markers ontbreken
            parts += [text]

    return "".join(parts)


def cache_file_prefix(schedule: str) -> str:
    name =  str(Day()) if schedule == "daily" else f"week{Day().week_number()}"
    return str(Path(__file__).parent / 'cache' / name)
