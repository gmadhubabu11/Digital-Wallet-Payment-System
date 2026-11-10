from typing import Optional

from sqlalchemy.orm import Session

from app.models import Account, LedgerEntry, Transaction, Wallet
from app.utils import constants as C
from app.utils.money import mask_phone

SYSTEM_LABEL = {C.T_ADD: "Bank Settlement", C.T_CASHBACK: "Cashback Pool", C.T_WITHDRAW: "Bank Account",
                C.T_WREV: "Withdrawal Clearing", C.T_REV: "Hold Account"}


def party(db: Session, wallet_id: Optional[int], txn: Transaction) -> dict:
    if wallet_id is None:
        return {"name": SYSTEM_LABEL.get(txn.type, "System"), "phone": None}
    w = db.get(Wallet, wallet_id)
    if w.merchant_id:
        return {"name": w.merchant.business_name, "phone": mask_phone(w.owner.phone)}
    return {"name": w.owner.name, "phone": mask_phone(w.owner.phone)}


def txn_view(db: Session, txn: Transaction, viewer_wallet_id: Optional[int]) -> dict:
    sender, receiver = party(db, txn.sender_wallet_id, txn), party(db, txn.receiver_wallet_id, txn)
    direction, other, balance_after = None, None, None
    if viewer_wallet_id is not None:
        if txn.sender_wallet_id == viewer_wallet_id:
            direction, other = C.DEBIT, receiver
        else:
            direction, other = C.CREDIT, sender
        acct = db.query(Account).filter(Account.wallet_id == viewer_wallet_id).one()
        entry = (db.query(LedgerEntry).filter(LedgerEntry.transaction_id == txn.id, LedgerEntry.account_id == acct.id)
                 .order_by(LedgerEntry.id.desc()).first())
        balance_after = entry.balance_after if entry else None
    return {
        "transaction_ref": txn.transaction_ref, "type": txn.type, "amount": txn.amount, "status": txn.status,
        "direction": direction, "other_party": other, "sender": sender, "receiver": receiver,
        "balance_after": balance_after, "description": txn.description, "is_flagged": txn.is_flagged,
        "created_at": txn.created_at,
    }
