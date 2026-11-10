from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    phone = Column(String(10), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    pin_hash = Column(String(255))
    pin_failed_attempts = Column(Integer, default=0, nullable=False)
    pin_locked_until = Column(DateTime)

    wallet = relationship("Wallet", back_populates="owner", uselist=False, foreign_keys="Wallet.owner_id")
    merchant = relationship("Merchant", back_populates="user", uselist=False)
    kyc = relationship("KYC", back_populates="user", uselist=False, foreign_keys="KYC.user_id")
    bank_accounts = relationship("BankAccount", back_populates="user")
    requests_sent = relationship("MoneyRequest", back_populates="requester", foreign_keys="MoneyRequest.requester_id")
    requests_received = relationship("MoneyRequest", back_populates="payer", foreign_keys="MoneyRequest.payer_id")


class Merchant(Base, TimestampMixin):
    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    business_name = Column(String(150), nullable=False)
    business_category = Column(String(100), nullable=False)
    merchant_code = Column(String(20), unique=True)

    user = relationship("User", back_populates="merchant")
    wallet = relationship("Wallet", back_populates="merchant", uselist=False, foreign_keys="Wallet.merchant_id")
    payments = relationship("MerchantPayment", back_populates="merchant")


class Wallet(Base, TimestampMixin):
    __tablename__ = "wallets"

    id = Column(Integer, primary_key=True)
    wallet_number = Column(String(20), unique=True)
    owner_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), unique=True)
    balance = Column(Numeric(12, 2), default=0, nullable=False)
    kyc_level = Column(String(10), default="Minimum", nullable=False)
    status = Column(String(10), default="Active", nullable=False)
    freeze_reason = Column(String(255))

    owner = relationship("User", back_populates="wallet", foreign_keys=[owner_id])
    merchant = relationship("Merchant", back_populates="wallet", foreign_keys=[merchant_id])
    account = relationship("Account", back_populates="wallet", uselist=False)


class KYC(Base, TimestampMixin):
    __tablename__ = "kyc"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    pan_number = Column(String(10), unique=True, nullable=False)
    aadhaar_last4 = Column(String(4), nullable=False)
    full_name = Column(String(100), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    status = Column(String(10), default="Pending", nullable=False)
    rejection_reason = Column(String(255))
    reviewed_by = Column(Integer, ForeignKey("users.id"))

    user = relationship("User", back_populates="kyc", foreign_keys=[user_id])
