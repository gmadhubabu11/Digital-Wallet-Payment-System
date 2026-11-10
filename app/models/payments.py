from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin


class MerchantPayment(Base, TimestampMixin):
    __tablename__ = "merchant_payments"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    fee = Column(Numeric(12, 2), nullable=False)
    refunded_amount = Column(Numeric(12, 2), default=0, nullable=False)
    status = Column(String(20), default="Paid", nullable=False)  # Paid | Partially Refunded | Refunded

    merchant = relationship("Merchant", back_populates="payments")
    transaction = relationship("Transaction")
    user = relationship("User")
    refunds = relationship("Refund", back_populates="payment")
    cashback = relationship("Cashback", back_populates="payment", uselist=False)


class Refund(Base, TimestampMixin):
    __tablename__ = "refunds"

    id = Column(Integer, primary_key=True)
    merchant_payment_id = Column(Integer, ForeignKey("merchant_payments.id"), nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), unique=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(String(255))

    payment = relationship("MerchantPayment", back_populates="refunds")


class Cashback(Base, TimestampMixin):
    __tablename__ = "cashbacks"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    merchant_payment_id = Column(Integer, ForeignKey("merchant_payments.id"), unique=True, nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(10), default="Pending", nullable=False, index=True)  # Pending | Credited | Cancelled
    credit_after = Column(DateTime, nullable=False)
    credited_at = Column(DateTime)
    transaction_id = Column(Integer, ForeignKey("transactions.id"))

    payment = relationship("MerchantPayment", back_populates="cashback")


class BankAccount(Base, TimestampMixin):
    __tablename__ = "bank_accounts"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    account_holder_name = Column(String(100), nullable=False)
    account_number = Column(String(18), nullable=False)
    ifsc_code = Column(String(11), nullable=False)
    bank_name = Column(String(100), nullable=False)
    is_primary = Column(Boolean, default=False, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)  # soft delete keeps withdrawal history valid

    user = relationship("User", back_populates="bank_accounts")


class Withdrawal(Base, TimestampMixin):
    __tablename__ = "withdrawals"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), unique=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    bank_account_id = Column(Integer, ForeignKey("bank_accounts.id"), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(10), default="Pending", nullable=False, index=True)
    processed_at = Column(DateTime)
    failure_reason = Column(String(255))

    transaction = relationship("Transaction")
    bank_account = relationship("BankAccount")


class MoneyRequest(Base, TimestampMixin):
    __tablename__ = "money_requests"

    id = Column(Integer, primary_key=True)
    requester_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    payer_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    note = Column(String(255))
    status = Column(String(10), default="Pending", nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    transaction_id = Column(Integer, ForeignKey("transactions.id"))

    requester = relationship("User", back_populates="requests_sent", foreign_keys=[requester_id])
    payer = relationship("User", back_populates="requests_received", foreign_keys=[payer_id])
