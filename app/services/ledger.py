from decimal import Decimal
from typing import List, Optional, Tuple

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Account, LedgerEntry, Transaction, Wallet
from app.utils import constants as C
from app.utils.refs import transaction_ref

Leg = Tuple[Account, str, Decimal]


def ensure_system_accounts(db: Session) -> None:
    for acc_type in C.SYSTEM_ACCOUNT_TYPES:
        exists = db.query(Account).filter(Account.account_type == acc_type, Account.wallet_id.is_(None)).first()
        if not exists:
            db.add(Account(name=acc_type, account_type=acc_type, balance=0))
    db.commit()


def system_account(db: Session, acc_type: str) -> Account:
    acc = db.query(Account).filter(Account.account_type == acc_type, Account.wallet_id.is_(None)).first()
    if acc is None:
        raise RuntimeError(f"System account missing: {acc_type}")
    return acc


def wallet_account(db: Session, wallet_id: int) -> Account:
    return db.query(Account).filter(Account.wallet_id == wallet_id).one()


def lock_wallets(db: Session, wallet_ids: List[int]) -> dict:
    """Row-lock wallets, ALWAYS lower id first, so opposite transfers can never deadlock."""
    ids = sorted(set(i for i in wallet_ids if i is not None))
    rows = (db.query(Wallet).filter(Wallet.id.in_(ids)).order_by(Wallet.id)
            .with_for_update().populate_existing().all())
    return {w.id: w for w in rows}


def new_transaction(db: Session, type_: str, amount: Decimal, initiated_by: Optional[int], description: str,
                    sender_wallet_id: Optional[int] = None, receiver_wallet_id: Optional[int] = None,
                    parent_id: Optional[int] = None, flagged: bool = False) -> Transaction:
    for _ in range(5):
        ref = transaction_ref()
        if not db.query(Transaction.id).filter(Transaction.transaction_ref == ref).first():
            break
    txn = Transaction(transaction_ref=ref, type=type_, amount=amount, status=C.S_PENDING,
                      initiated_by=initiated_by, description=description, is_flagged=flagged,
                      sender_wallet_id=sender_wallet_id, receiver_wallet_id=receiver_wallet_id, parent_id=parent_id)
    db.add(txn)
    db.flush()
    return txn


def post(db: Session, txn: Transaction, legs: List[Leg], final_status: str = C.S_SUCCESS) -> None:
    """Write ledger entries. Total debits must equal total credits. Caller commits (single DB transaction)."""
    debits = sum((a for _, t, a in legs if t == C.DEBIT), Decimal("0"))
    credits = sum((a for _, t, a in legs if t == C.CREDIT), Decimal("0"))
    if debits != credits:
        raise RuntimeError(f"Ledger imbalance: debits {debits} != credits {credits}")

    ids = sorted({a.id for a, _, _ in legs})
    locked = {a.id: a for a in db.query(Account).filter(Account.id.in_(ids)).order_by(Account.id)
              .with_for_update().populate_existing().all()}

    for acct, entry_type, amount in legs:
        a = locked[acct.id]
        a.balance = a.balance + amount if entry_type == C.CREDIT else a.balance - amount
        if a.wallet_id is not None:
            if a.balance < 0:
                raise HTTPException(400, "Insufficient balance")
            db.get(Wallet, a.wallet_id).balance = a.balance  # quick copy, same DB transaction
        db.add(LedgerEntry(transaction_id=txn.id, account_id=a.id, entry_type=entry_type,
                           amount=amount, balance_after=a.balance))
    txn.status = final_status
    db.flush()
