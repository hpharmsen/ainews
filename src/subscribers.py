import traceback
from typing import List
from contextlib import contextmanager
from sqlalchemy import select, update, func
from dotenv import load_dotenv

from justlog import lg

from src.database import connect_with_retry


@contextmanager
def db():
    """
    Context manager that provides database connection and metadata tables.

    Usage:
        with db() as (conn, tables):
            # use conn and tables
    """
    engine, tables = connect_with_retry()
    try:
        with engine.connect() as conn:
            yield conn, tables
    finally:
        engine.dispose()


def get_subscribers(status: str) -> List[str]:
    with db() as (conn, tables):
        subscriber_table = tables['nieuwsbrief_subscriber']
        query = select(subscriber_table.c.email).where(
            subscriber_table.c.status == status,
            subscriber_table.c.email.isnot(None)
        )
        return [row[0] for row in conn.execute(query) if row[0]]


def get_subscriber_status(email: str) -> dict | None:
    """Get subscriber status and updated_at timestamp. None betekent: geen rij gevonden.

    Databasefouten gaan naar de aanroeper. Ze hier slikken zou None opleveren, hetzelfde
    antwoord als voor een onbekend adres, en dan verdwijnt een afmelding stil in de
    prullenbak zonder dat er iemand wordt uitgeschreven.
    """
    with db() as (conn, tables):
        t = tables['nieuwsbrief_subscriber']
        query = select(t.c.status, t.c.updated_at).where(t.c.email == email)
        row = conn.execute(query).fetchone()
        return {'status': row[0], 'updated_at': row[1]} if row else None


def update_subscription(email: str, status: str) -> bool:
    """Update subscriber status. Resets bounce history when re-subscribing."""
    try:
        with db() as (conn, tables):
            subscriber_table = tables['nieuwsbrief_subscriber']

            with conn.begin():
                query = update(subscriber_table).where(subscriber_table.c.email == email).values(status=status, updated_at=func.now())
                result = conn.execute(query)
                if result.rowcount > 0:
                    lg.info(f"Updated {email} to status '{status}'")
                    # Reset bounce history when re-subscribing
                    if status in ('daily', 'weekly'):
                        from src.undelivered import reset_undelivered
                        reset_undelivered(email)
                    return True
                else:
                    lg.error(f"Failed to update status for {email}")
                    return False

    except Exception as e:
        lg.error(f'Error in update_subscription\n{traceback.format_exc()}')
        return False


if __name__ == "__main__":
    load_dotenv()
    subscribers = get_subscribers('daily')
    print(subscribers)