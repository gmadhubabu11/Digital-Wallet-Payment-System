from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from app.database import Base
from app.models.base import TimestampMixin


class OTP(Base, TimestampMixin):
    __tablename__ = "otps"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    purpose = Column(String(30), nullable=False)
    otp_hash = Column(String(64), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)


class IdempotencyKey(Base, TimestampMixin):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("user_id", "endpoint", "key", name="uq_idem_user_endpoint_key"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    endpoint = Column(String(120), nullable=False)
    key = Column(String(100), nullable=False)
    request_hash = Column(String(64), nullable=False)
    response_body = Column(Text)
    status_code = Column(Integer)
    status = Column(String(15), default="Processing", nullable=False)  # Processing | Completed
