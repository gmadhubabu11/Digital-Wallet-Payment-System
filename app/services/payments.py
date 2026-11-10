from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AdminAction, Cashback, Merchant, MerchantPayment, Refund, Transaction, User
from app.schemas.common import txn_result
from app.services import limits, security
from app.services.ledger import (lock_wallets, new_transaction, post, system_account, wallet_account)
from app.utils import constants as C
from app.utils.email import notify
from app.utils.money import inr, q

LOW_BALANCE = Decimal("100")
FEE_RATE = Decimal("0.01")
CASHBACK_RATE = Decimal("0.02")
CASHBACK_CAP = Decimal("50")
REFUND_WINDOW = timedelta(days=7)


def _low_balance_alert(bg, user: User, balance: Decimal) -> None:
    if balance < LOW_BALANCE:
        notify(bg, user.email, "Low balance alert",
               f"Hi {user.name}, your wallet balance is {inr(balance)}. Add money to keep paying without interruption.")


def _require_active(wallet) -> None:
    if wallet.status == C.WALLET_FROZEN:
        raise HTTPException(403, "Your wallet is frozen. You can receive money but cannot send, pay or withdraw")


# ---------------------------------------------------------------- Add Money
def add_money(db: Session, user: User, body, bg):
    amount = q(body.amount)
    if amount < 10 or amount > 50000:
        raise HTTPException(400, "Amount must be between ₹10 and ₹50,000 per request")
    wallet = lock_wallets(db, [user.wallet.id])[user.wallet.id]
    txn = new_transaction(db, C.T_ADD, amount, user.id, f"Add money via {body.source}", receiver_wallet_id=wallet.id)

    if body.simulate_status == "Failed":  # failed attempt: saved, but no ledger entries
        txn.status = C.S_FAILED
        db.flush()
        return 200, txn_result(txn, f"Payment via {body.source} failed. No money was added", wallet.balance)

    limits.check_can_receive(wallet, amount)
    post(db, txn, [(system_account(db, C.ACC_BANK), C.DEBIT, amount),
                   (wallet_account(db, wallet.id), C.CREDIT, amount)])
    notify(bg, user.email, "Money added to wallet", f"{inr(amount)} was added via {body.source}. New balance: {inr(wallet.balance)}.")
    return 201, txn_result(txn, f"{inr(amount)} added to your wallet", wallet.balance)


# ---------------------------------------------------------------- P2P Transfer
def execute_transfer(db: Session, sender: User, receiver: User, amount: Decimal, note, pin, otp, bg):
    amount = q(amount)
    if receiver is None or receiver.role != C.USER or receiver.wallet is None:
        raise HTTPException(404, "Receiver not found")
    if receiver.id == sender.id:
        raise HTTPException(400, "You cannot send money to yourself")

    needs_otp = amount > C.OTP_THRESHOLD
    if needs_otp and not otp:
        raise HTTPException(400, f"OTP is required for transfers above {inr(C.OTP_THRESHOLD)}. Request one using POST /otp/send")
    security.verify_pin(db, sender, pin)
    if needs_otp:
        security.verify_otp(db, sender, C.OTP_TRANSFER, otp, consume=True)

    wallets = lock_wallets(db, [sender.wallet.id, receiver.wallet.id])
    sw, rw = wallets[sender.wallet.id], wallets[receiver.wallet.id]
    _require_active(sw)
    limits.check_can_send(db, sw, amount)
    if sw.balance < amount:
        raise HTTPException(400, f"Insufficient balance. Available balance is {inr(sw.balance)}")
    limits.check_can_receive(rw, amount, who="Receiver's")

    recent = db.query(func.count(Transaction.id)).filter(
        Transaction.sender_wallet_id == sw.id, Transaction.type == C.T_P2P,
        Transaction.status.in_(C.COUNTED_STATUSES),
        Transaction.created_at >= datetime.utcnow() - timedelta(minutes=1)).scalar()
    flagged = amount > C.FLAG_AMOUNT or recent >= 5  # 6th transfer inside one minute

    txn = new_transaction(db, C.T_P2P, amount, sender.id, note or f"Transfer to {receiver.name}",
                          sender_wallet_id=sw.id, receiver_wallet_id=rw.id, flagged=flagged)
    sender_acc = wallet_account(db, sw.id)

    if flagged:
        post(db, txn, [(sender_acc, C.DEBIT, amount), (system_account(db, C.ACC_HOLD), C.CREDIT, amount)],
             final_status=C.S_HOLD)
        notify(bg, sender.email, "Transfer put on hold",
               f"Your transfer {txn.transaction_ref} of {inr(amount)} was flagged for review and is On Hold. "
               f"The money is safe and will be released or returned after admin review.")
        _low_balance_alert(bg, sender, sw.balance)
        return txn, f"Transfer of {inr(amount)} is On Hold for admin review"

    post(db, txn, [(sender_acc, C.DEBIT, amount), (wallet_account(db, rw.id), C.CREDIT, amount)])
    notify(bg, sender.email, "Money sent", f"You sent {inr(amount)} to {receiver.name}. Ref: {txn.transaction_ref}. Balance: {inr(sw.balance)}.")
    notify(bg, receiver.email, "Money received", f"You received {inr(amount)} from {sender.name}. Ref: {txn.transaction_ref}. Balance: {inr(rw.balance)}.")
    _low_balance_alert(bg, sender, sw.balance)
    return txn, f"{inr(amount)} sent to {receiver.name}"


def transfer(db: Session, sender: User, body, bg):
    receiver = db.query(User).filter(User.phone == body.receiver_phone).first()
    txn, msg = execute_transfer(db, sender, receiver, body.amount, body.note, body.pin, body.otp, bg)
    return 201, txn_result(txn, msg, sender.wallet.balance)


# ---------------------------------------------------------------- Merchant payment, cashback
def merchant_payment(db: Session, user: User, body, bg):
    amount = q(body.amount)
    merchant = db.query(Merchant).filter(Merchant.merchant_code == body.merchant_code).first()
    if merchant is None:
        raise HTTPException(404, "Merchant not found")
    security.verify_pin(db, user, body.pin)

    wallets = lock_wallets(db, [user.wallet.id, merchant.wallet.id])
    uw, mw = wallets[user.wallet.id], wallets[merchant.wallet.id]
    _require_active(uw)
    limits.check_can_send(db, uw, amount)
    if uw.balance < amount:
        raise HTTPException(400, f"Insufficient balance. Available balance is {inr(uw.balance)}")

    fee = q(amount * FEE_RATE)
    net = amount - fee
    txn = new_transaction(db, C.T_MPAY, amount, user.id, f"Payment to {merchant.business_name}",
                          sender_wallet_id=uw.id, receiver_wallet_id=mw.id)
    post(db, txn, [(wallet_account(db, uw.id), C.DEBIT, amount),
                   (wallet_account(db, mw.id), C.CREDIT, net),
                   (system_account(db, C.ACC_FEES), C.CREDIT, fee)])
    mp = MerchantPayment(transaction_id=txn.id, user_id=user.id, merchant_id=merchant.id,
                         amount=amount, fee=fee, refunded_amount=Decimal("0"), status="Paid")
    db.add(mp)
    db.flush()

    cb = min(q(amount * CASHBACK_RATE), CASHBACK_CAP)
    if cb > 0:
        db.add(Cashback(user_id=user.id, merchant_payment_id=mp.id, amount=cb, status="Pending",
                        credit_after=datetime.utcnow() + timedelta(hours=24)))

    notify(bg, user.email, "Payment successful", f"You paid {inr(amount)} to {merchant.business_name}. Ref: {txn.transaction_ref}."
           + (f" Cashback of {inr(cb)} will be credited within 24 hours." if cb > 0 else ""))
    notify(bg, merchant.user.email, "Payment received",
           f"{user.name} paid {inr(amount)}. Platform fee {inr(fee)}; you received {inr(net)}. Ref: {txn.transaction_ref}.")
    _low_balance_alert(bg, user, uw.balance)
    msg = f"Paid {inr(amount)} to {merchant.business_name}" + (f". Cashback {inr(cb)} pending" if cb > 0 else "")
    return 201, txn_result(txn, msg, uw.balance)


def credit_cashback(db: Session, cb: Cashback, bg=None) -> None:
    user = db.get(User, cb.user_id)
    wallets = lock_wallets(db, [user.wallet.id])
    cashback_acc = system_account(db, C.ACC_CASHBACK)
    txn = new_transaction(db, C.T_CASHBACK, cb.amount, None, "Cashback on merchant payment",
                          receiver_wallet_id=user.wallet.id)
    post(db, txn, [(cashback_acc, C.DEBIT, cb.amount), (wallet_account(db, user.wallet.id), C.CREDIT, cb.amount)])
    cb.status, cb.credited_at, cb.transaction_id = "Credited", datetime.utcnow(), txn.id
    notify(bg, user.email, "Cashback credited", f"{inr(cb.amount)} cashback was credited to your wallet.")


# ---------------------------------------------------------------- Refund
def refund(db: Session, merchant_user: User, txn_ref: str, body, bg):
    amount = q(body.amount)
    mp = (db.query(MerchantPayment).join(Transaction, Transaction.id == MerchantPayment.transaction_id)
          .filter(Transaction.transaction_ref == txn_ref).with_for_update().first())
    if mp is None:
        raise HTTPException(404, "Merchant payment not found")
    if mp.merchant_id != merchant_user.merchant.id:
        raise HTTPException(403, "Only the merchant who received this payment can refund it")
    original = db.get(Transaction, mp.transaction_id)
    if datetime.utcnow() - original.created_at > REFUND_WINDOW:
        raise HTTPException(400, "Refund window of 7 days has passed")
    remaining = mp.amount - mp.refunded_amount
    if amount > remaining:
        raise HTTPException(400, f"Refund exceeds the original payment. You can refund at most {inr(remaining)} more")

    payer = db.get(User, mp.user_id)
    wallets = lock_wallets(db, [merchant_user.wallet.id, payer.wallet.id])
    mw, uw = wallets[merchant_user.wallet.id], wallets[payer.wallet.id]
    _require_active(mw)
    if mw.balance < amount:
        raise HTTPException(400, f"Insufficient merchant balance. Available balance is {inr(mw.balance)}")

    txn = new_transaction(db, C.T_REFUND, amount, merchant_user.id, body.reason or "Merchant refund",
                          sender_wallet_id=mw.id, receiver_wallet_id=uw.id, parent_id=original.id)
    post(db, txn, [(wallet_account(db, mw.id), C.DEBIT, amount), (wallet_account(db, uw.id), C.CREDIT, amount)])
    db.add(Refund(merchant_payment_id=mp.id, transaction_id=txn.id, amount=amount, reason=body.reason))
    mp.refunded_amount += amount
    mp.status = "Refunded" if mp.refunded_amount >= mp.amount else "Partially Refunded"

    cb = db.query(Cashback).filter(Cashback.merchant_payment_id == mp.id).first()
    if cb and cb.status == "Pending":  # cancel / shrink pending cashback in proportion to what is still paid
        cb_new = q(min(q(mp.amount * CASHBACK_RATE), CASHBACK_CAP) * (mp.amount - mp.refunded_amount) / mp.amount)
        if cb_new <= 0:
            cb.status = "Cancelled"
        else:
            cb.amount = cb_new

    notify(bg, payer.email, "Refund received", f"{inr(amount)} was refunded to your wallet by {merchant_user.merchant.business_name}.")
    notify(bg, merchant_user.email, "Refund issued", f"You refunded {inr(amount)} to {payer.name}. Ref: {txn.transaction_ref}.")
    return 201, txn_result(txn, f"Refunded {inr(amount)}", mw.balance)


# ---------------------------------------------------------------- Admin review of On Hold transfers
def review_hold(db: Session, admin: User, txn_ref: str, approve: bool, reason: str, bg):
    txn = (db.query(Transaction).filter(Transaction.transaction_ref == txn_ref).with_for_update().first())
    if txn is None:
        raise HTTPException(404, "Transaction not found")
    if txn.status != C.S_HOLD:
        raise HTTPException(400, f"Only On Hold transactions can be reviewed (current status: {txn.status})")

    wallets = lock_wallets(db, [txn.sender_wallet_id, txn.receiver_wallet_id])
    sw, rw = wallets[txn.sender_wallet_id], wallets[txn.receiver_wallet_id]
    hold = system_account(db, C.ACC_HOLD)
    sender, receiver = sw.owner, rw.owner

    if approve:
        limits.check_can_receive(rw, txn.amount, who="Receiver's")
        post(db, txn, [(hold, C.DEBIT, txn.amount), (wallet_account(db, rw.id), C.CREDIT, txn.amount)])
        notify(bg, sender.email, "Transfer approved", f"Your transfer {txn.transaction_ref} of {inr(txn.amount)} was approved and delivered.")
        notify(bg, receiver.email, "Money received", f"You received {inr(txn.amount)} from {sender.name}. Ref: {txn.transaction_ref}.")
    else:
        rev = new_transaction(db, C.T_REV, txn.amount, admin.id, f"Reversal of {txn.transaction_ref}: {reason}",
                              receiver_wallet_id=sw.id, parent_id=txn.id)
        post(db, rev, [(hold, C.DEBIT, txn.amount), (wallet_account(db, sw.id), C.CREDIT, txn.amount)])
        txn.status = C.S_REVERSED
        notify(bg, sender.email, "Transfer rejected",
               f"Your transfer {txn.transaction_ref} of {inr(txn.amount)} was rejected ({reason}). The money is back in your wallet.")

    db.add(AdminAction(transaction_id=txn.id, admin_id=admin.id, action="Approved" if approve else "Rejected", reason=reason))
    db.flush()
    return txn
