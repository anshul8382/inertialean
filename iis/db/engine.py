from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from iis.config import settings
from iis.db.models import Base

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = settings.resolved_database_url
        connect_args: dict = {}
        if url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        _engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_database() -> None:
    """Create tables if they do not exist."""
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "mysql":
        with engine.connect() as conn:
            conn.execute(text("SET NAMES utf8mb4"))
            conn.commit()


def database_available() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
