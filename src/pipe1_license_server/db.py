from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from pipe1_license_server.models import Base
from pipe1_license_server.settings import ServerSettings


def create_db_engine(settings: ServerSettings) -> Engine:
    connect_args = {}
    if settings.database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(settings.database_url, connect_args=connect_args, future=True)


def create_session_factory(settings: ServerSettings) -> sessionmaker[Session]:
    engine = create_db_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def init_db(settings: ServerSettings) -> None:
    engine = create_db_engine(settings)
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(settings: ServerSettings) -> Iterator[Session]:
    init_db(settings)
    factory = create_session_factory(settings)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
