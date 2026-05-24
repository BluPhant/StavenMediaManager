import logging
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

logger = logging.getLogger(__name__)

# Ensure the config directory exists before SQLite tries to create the file
os.makedirs(settings.config_dir, exist_ok=True)

engine = create_engine(
    settings.database_url,
    # SQLite requires this when the same connection is used across threads
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
