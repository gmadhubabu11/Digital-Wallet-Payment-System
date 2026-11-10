from datetime import datetime

from sqlalchemy import Column, DateTime


def utcnow() -> datetime:
    return datetime.utcnow()


class TimestampMixin:
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
