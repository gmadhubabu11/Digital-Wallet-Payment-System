from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Transaction, Wallet
from app.utils import constants as C
from app.utils.money import inr, q

LIMITS = {
    C.KYC_MIN: {"max_balance": Decimal("10000"), "daily": Decimal("10000"), "monthly": Decimal("25000")},
    C.KYC_FULL: {"max_balance": Decimal("200000"), "daily": Decimal("25000"), "monthly": Decimal("100000")},
}


def limits_for(wallet: Wallet) -> dict:
    if wallet.merchant_id:  # merchants: no balance cap, Full-level send limits (assumption, see README)
        return {"max_balance": None, **{k: LIMITS[C.KYC_FULL][k] for k in ("daily", "monthly")}}
    return LIMITS[wallet.kyc_level]


def sent_since(db: Session, wallet_id: int, since: datetime) -> Decimal:
    total = db.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.sender_wallet_id == wallet_id,
        Transaction.type.in_(C.SENDING_TYPES),
        Transaction.status.in_(C.COUNTED_STATUSES),
        Transaction.created_at >= since,
    ).scalar()
    return q(total)


def check_can_send(db: Session, wallet: Wallet, amount: Decimal) -> None:
    lim = limits_for(wallet)
    now = datetime.utcnow()
    for label, key, since in (
        ("Daily", "daily", now.replace(hour=0, minute=0, second=0, microsecond=0)),
        ("Monthly", "monthly", now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)),
    ):
        used = sent_since(db, wallet.id, since)
        if used + amount > lim[key]:
            remaining = max(lim[key] - used, Decimal("0"))
            period = "today" if key == "daily" else "this month"
            raise HTTPException(
                400, f"{label} limit of {inr(lim[key])} reached. You can send {inr(remaining)} more {period}")


def check_can_receive(wallet: Wallet, amount: Decimal, who: str = "Wallet") -> None:
    cap = limits_for(wallet)["max_balance"]
    if cap is not None and wallet.balance + amount > cap:
        raise HTTPException(
            400, f"{who} balance limit of {inr(cap)} would be exceeded "
                 f"({wallet.kyc_level} KYC). Maximum you can add/receive now: {inr(max(cap - wallet.balance, 0))}")
