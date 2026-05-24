from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from .database import Base


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String(50), nullable=False)
    category = Column(String(200), nullable=False)
    item_name = Column(String(500), nullable=False)
    source_path = Column(String(1000), nullable=False)
    dest_path = Column(String(1000), nullable=True)
    # pending | running | done | error | cancelled
    status = Column(String(20), default="pending", nullable=False)
    progress = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    error_detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
