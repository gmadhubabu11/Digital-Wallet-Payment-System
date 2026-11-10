from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship

from app.database import Base
from app.models.base import TimestampMixin


class Account(Base, TimestampMixin):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    name = Column(String(80), nullable=False)
    account_type = Column(String(30), nullable=False, index=True)
    wallet_id = Column(Integer, ForeignKey("wallets.id"), unique=True)  # NULL for system accounts
    balance = Column(Numeric(12, 2), default=0, nullable=False)  # credits - debits

    wallet = relationship("Wallet", back_populates="account")
    entries = relationship("LedgerEntry", back_populates="account")


class Transaction(Base, TimestampMixin):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    transaction_ref = Column(String(30), unique=True, nullable=False, index=True)
    type = Column(String(30), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(String(15), nullable=False, index=True)
    initiated_by = Column(Integer, ForeignKey("users.id"))
    description = Column(String(255))
    is_flagged = Column(Boolean, default=False, nullable=False)
    sender_wallet_id = Column(Integer, ForeignKey("wallets.id"), index=True)
    receiver_wallet_id = Column(Integer, ForeignKey("wallets.id"), index=True)
    parent_id = Column(Integer, ForeignKey("transactions.id"))

    entries = relationship("LedgerEntry", back_populates="transaction")


class LedgerEntry(Base, TimestampMixin):
    """Append-only. Never updated or deleted; mistakes are fixed with a Reversal transaction."""

    __tablename__ = "ledger_entries"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)
    entry_type = Column(String(6), nullable=False)  # Debit | Credit
    amount = Column(Numeric(12, 2), nullable=False)
    balance_after = Column(Numeric(12, 2), nullable=False)

    transaction = relationship("Transaction", back_populates="entries")
    account = relationship("Account", back_populates="entries")


class AdminAction(Base, TimestampMixin):
    __tablename__ = "admin_actions"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    action = Column(String(10), nullable=False)  # Approved | Rejected
    reason = Column(String(255), nullable=False)
