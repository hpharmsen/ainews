import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.dialects.postgresql import insert as pg_insert


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
            print("Successfully connected to the database")

        metadata = MetaData()
        metadata.reflect(bind=engine)

        # Get the podcast table
        assert 'nieuwsbrief_newsletter' in metadata.tables, "Table 'nieuwsbrief_newsletter' not found in the database"

        newsletter_table = metadata.tables['nieuwsbrief_newsletter']
        return engine, newsletter_table

    except Exception as e:
        print(f"Error connecting to the database: {e}")
        print(f"Database URL: {db_url}")
        sys.exit(1)


def add_to_database(schedule, title, newsletter_html, image_url):

    engine, table = db_connect()

    stmt = pg_insert(table).values(
        schedule=schedule,
        title=title,
        sent=datetime.now(),
        text=newsletter_html,
        image_url=image_url
    )

    with engine.begin() as conn:  # begin() doet ook commit automatisch
        conn.execute(stmt)

    print(f"✅ Newsletter added to the database.")