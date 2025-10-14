import os
import traceback
from typing import List
from contextlib import contextmanager
from sqlalchemy import create_engine, MetaData, select, update
from dotenv import load_dotenv

from log import lg


@contextmanager
def db():
    """
    Context manager that provides database connection and metadata tables.

    Usage:
        with db() as (conn, tables):
            # use conn and tables
    """
    db_url = _normalize_db_url(os.getenv("DATABASE_URL"))
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set or invalid")

    engine = create_engine(db_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)

    try:
        with engine.connect() as conn:
            yield conn, metadata.tables
    finally:
        engine.dispose()


def get_subscribers(status: str) -> List[str]:
    try:
        with db() as (conn, tables):
            subscriber_table = tables['nieuwsbrief_subscriber']
            query = select(subscriber_table.c.email).where(
                subscriber_table.c.status == status,
                subscriber_table.c.email.isnot(None)
            )
            result = conn.execute(query)
            return [row[0] for row in result if row[0]]

    except Exception as e:
        raise Exception(f"Error getting subscribers: {e}")


def update_subscription(email: str, status: str) -> bool:
    """
    Update subscriber status.

    Args:
        email: Subscriber email address
        status: New status for the subscriber

    Returns:
        True if successful, False otherwise
    """
    try:
        with db() as (conn, tables):
            subscriber_table = tables['nieuwsbrief_subscriber']

            with conn.begin():
                query = update(subscriber_table).where(subscriber_table.c.email == email).values(status=status)
                result = conn.execute(query)
                if result.rowcount > 0:
                    lg.info(f"Marked {email} as undeliverable")
                    return True
                else:
                    lg.error(f"Failed to update status for {email}")
                    return False

    except Exception as e:
        lg.error(f'Error in update_subscription\n{traceback.format_exc()}')
        return False


def _normalize_db_url(db_url: str) -> str:
    """Ensure the database URL uses the postgresql:// scheme."""
    if db_url and db_url.startswith('postgres://'):
        return 'postgresql://' + db_url[11:]
    return db_url


if __name__ == "__main__":
    load_dotenv()
    subscribers = get_subscribers('daily')
    print(subscribers)