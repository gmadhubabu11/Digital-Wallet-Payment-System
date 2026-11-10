from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import BankAccount, Transaction, User, Withdrawal
from app.schemas.common import txn_result
from app.services import limits, security
from app.services.ledger import lock_wallets, new_transaction, post, system_account, wallet_account
from app.services.payments import _low_balance_alert, _require_active
from app.utils import constants as C
from app.utils.email import notify
from app.utils.money import inr, mask_account, q

MIN_WITHDRAWAL = 100
PROCESS_AFTER = timedelta(minutes=10)


def request_withdrawal(db: Session, user: User, body, bg):
    amount = q(body.amount)
    if amount < MIN_WITHDRAWAL:
        raise HTTPException(400, f"Minimum withdrawal is {inr(MIN_WITHDRAWAL)}")
    if user.role == C.USER and user.wallet.kyc_level != C.KYC_FULL:
        raise HTTPException(403, "Withdrawals to bank need Full KYC. Submit KYC using POST /kyc")
    bank = db.query(BankAccount).filter(BankAccount.id == body.bank_account_id, BankAccount.user_id == user.id,
                                        BankAccount.is_active.is_(True)).first()
    if bank is None:
        raise HTTPException(404, "Bank account not found")
    security.verify_pin(db, user, body.pin)

    wallet = lock_wallets(db, [user.wallet.id])[user.wallet.id]
    _require_active(wallet)
    limits.check_can_send(db, wallet, amount)
    if wallet.balance < amount:
        raise HTTPException(400, f"Insufficient balance. Available balance is {inr(wallet.balance)}")

    txn = new_transaction(db, C.T_WITHDRAW, amount, user.id, f"Withdrawal to {bank.bank_name} {mask_account(bank.account_number)}",
                          sender_wallet_id=wallet.id)
    post(db, txn, [(wallet_account(db, wallet.id), C.DEBIT, amount),
                   (system_account(db, C.ACC_CLEARING), C.CREDIT, amount)], final_status=C.S_PENDING)
    db.add(Withdrawal(transaction_id=txn.id, user_id=user.id, bank_account_id=bank.id, amount=amount, status="Pending"))
    _low_balance_alert(bg, user, wallet.balance)
    return 201, txn_result(txn, f"Withdrawal of {inr(amount)} is pending. It is processed within about 10 minutes", wallet.balance)


def settle_withdrawal(db: Session, w: Withdrawal, bg=None) -> None:
    """Called by the scheduled job. Completes the withdrawal, or fails it and reverses the money."""
    user = db.get(User, w.user_id)
    txn = db.get(Transaction, w.transaction_id)
    bank = db.get(BankAccount, w.bank_account_id)
    wallet = lock_wallets(db, [user.wallet.id])[user.wallet.id]
    clearing = system_account(db, C.ACC_CLEARING)

    if bank.account_number.endswith("0000"):  # simulated bank failure
        rev = new_transaction(db, C.T_WREV, w.amount, None, f"Reversal of failed withdrawal {txn.transaction_ref}",
                              receiver_wallet_id=wallet.id, parent_id=txn.id)
        post(db, rev, [(clearing, C.DEBIT, w.amount), (wallet_account(db, wallet.id), C.CREDIT, w.amount)])
        txn.status = C.S_REVERSED
        w.status, w.failure_reason = "Failed", "Bank rejected the transfer (simulated failure)"
        notify(bg, user.email, "Withdrawal failed",
               f"Your withdrawal of {inr(w.amount)} failed ({w.failure_reason}). The money is back in your wallet.")
    else:
        post(db, txn, [(clearing, C.DEBIT, w.amount), (system_account(db, C.ACC_BANK), C.CREDIT, w.amount)])
        w.status = "Completed"
        notify(bg, user.email, "Withdrawal completed",
               f"{inr(w.amount)} was sent to {bank.bank_name} {mask_account(bank.account_number)}.")
    w.processed_at = datetime.utcnow()
