from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    settings = get_settings()
    is_sqlite = settings.database_url.startswith("sqlite")
    connect_args = {"check_same_thread": False} if is_sqlite else {}
    engine_options = {"poolclass": StaticPool} if settings.database_url.endswith(":memory:") else {}
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
        **engine_options,
    )


@lru_cache
def get_session_factory():
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_database_caches() -> None:
    get_session_factory.cache_clear()
    get_engine.cache_clear()
