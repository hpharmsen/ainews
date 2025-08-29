import os
from typing import List
from sqlalchemy import create_engine, MetaData, select, text, func
from dotenv import load_dotenv


def get_subscribers(status: str) -> List[str]:
    db_url = _normalize_db_url(os.getenv("DATABASE_URL"))
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set or invalid")
    
    engine = create_engine(db_url)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    
    if 'nieuwsbrief_subscriber' not in metadata.tables:
        raise ValueError("Subscriber table not found in database")
    
    subscriber_table = metadata.tables['nieuwsbrief_subscriber']
    
    try:
        with engine.connect() as conn:
            query = select(subscriber_table.c.email).where(
                subscriber_table.c.status == status,
                subscriber_table.c.email.isnot(None)
            )
            result = conn.execute(query)
            return [row[0] for row in result if row[0]]
            
    except Exception as e:
        raise Exception(f"Error getting subscribers: {e}")


def _normalize_db_url(db_url: str) -> str:
    """Ensure the database URL uses the postgresql:// scheme."""
    if db_url and db_url.startswith('postgres://'):
        return 'postgresql://' + db_url[11:]
    return db_url


if __name__ == "__main__":
    load_dotenv()
    subscribers = get_subscribers('daily')
    print(subscribers)