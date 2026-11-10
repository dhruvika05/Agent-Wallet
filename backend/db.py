from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from config import DATABASE_URL

# "check_same_thread" is a SQLite-only option. If DATABASE_URL ever points to
# another database (for example Postgres on Render), passing it would crash.
is_sqlite = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)

# SQLite ignores foreign keys unless you turn them on for each connection.
# This stops you from saving, say, a payment request for an agent that doesn't exist.
if is_sqlite:
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all database models
Base = declarative_base()


def init_db() -> None:
    """Create all tables. Call this once at app startup, after models are imported."""
    import models  # noqa: F401  (importing registers the tables on Base)
    Base.metadata.create_all(bind=engine)


# Database dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()