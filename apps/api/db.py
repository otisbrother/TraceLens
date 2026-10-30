from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from apps.api.config import settings


def make_engine(url: str):
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {},
        pool_pre_ping=True,
    )
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")

    return engine


engine = make_engine(settings.database_url)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as db:
        yield db
