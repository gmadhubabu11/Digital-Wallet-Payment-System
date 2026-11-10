from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (Account, Cashback, LedgerEntry, Merchant, MerchantPayment, Refund, Transaction, User, Wallet)
from app.utils import constants as C


def _dec(v) -> Decimal:
    return Decimal(str(v or 0)).quantize(Decimal('0.01'))


def admin_dashboard(db: Session) -> dict:
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    by_type = (db.query(Transaction.type, func.count(Transaction.id), func.coalesce(func.sum(Transaction.amount), 0))
               .filter(Transaction.created_at >= today, Transaction.status.in_(C.COUNTED_STATUSES))
               .group_by(Transaction.type).all())
    count_status = lambda s: db.query(func.count(Transaction.id)).filter(Transaction.status == s).scalar()  # noqa: E731
    return {
        "total_money_in_wallets": _dec(db.query(func.sum(Wallet.balance)).scalar()),
        "today_transactions": {
            "total_count": sum(c for _, c, _ in by_type),
            "total_volume": sum((_dec(v) for _, _, v in by_type), Decimal("0")),
            "by_type": {t: {"count": c, "volume": _dec(v)} for t, c, v in by_type},
        },
        "failed_transactions": count_status(C.S_FAILED),
        "transactions_on_hold": count_status(C.S_HOLD),
        "cashback_paid": _dec(db.query(func.sum(Transaction.amount)).filter(
            Transaction.type == C.T_CASHBACK, Transaction.status == C.S_SUCCESS).scalar()),
    }


def ledger_summary(db: Session, from_date: Optional[date], to_date: Optional[date]) -> dict:
    accounts = db.query(Account).filter(Account.wallet_id.is_(None)).order_by(Account.id).all()
    q = db.query(LedgerEntry.entry_type, func.coalesce(func.sum(LedgerEntry.amount), 0))
    if from_date:
        q = q.filter(LedgerEntry.created_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.filter(LedgerEntry.created_at < datetime.combine(to_date + timedelta(days=1), datetime.min.time()))
    totals = {t: _dec(v) for t, v in q.group_by(LedgerEntry.entry_type).all()}
    return {
        "system_accounts": [{"account_type": a.account_type, "balance": a.balance} for a in accounts],
        "from_date": from_date, "to_date": to_date,
        "total_debits": totals.get(C.DEBIT, Decimal("0")), "total_credits": totals.get(C.CREDIT, Decimal("0")),
    }


def merchant_sales(db: Session, merchant: Merchant) -> dict:
    payments = (db.query(MerchantPayment, Transaction).join(Transaction, Transaction.id == MerchantPayment.transaction_id)
                .filter(MerchantPayment.merchant_id == merchant.id).all())
    refunds = (db.query(Refund, Transaction).join(Transaction, Transaction.id == Refund.transaction_id)
               .join(MerchantPayment, MerchantPayment.id == Refund.merchant_payment_id)
               .filter(MerchantPayment.merchant_id == merchant.id).all())
    daily = defaultdict(lambda: {"payments": 0, "sales": Decimal("0"), "fees": Decimal("0"), "refunds": Decimal("0")})
    for mp, t in payments:
        d = daily[t.created_at.date().isoformat()]
        d["payments"] += 1
        d["sales"] += mp.amount
        d["fees"] += mp.fee
    for r, t in refunds:
        daily[t.created_at.date().isoformat()]["refunds"] += r.amount
    days = [{"date": k, **v, "net_received": v["sales"] - v["fees"] - v["refunds"]} for k, v in sorted(daily.items())]
    sales = sum((p.amount for p, _ in payments), Decimal("0"))
    fees = sum((p.fee for p, _ in payments), Decimal("0"))
    refunded = sum((r.amount for r, _ in refunds), Decimal("0"))
    return {"business_name": merchant.business_name, "number_of_payments": len(payments), "total_sales": sales,
            "total_refunds": refunded, "refund_count": len(refunds), "platform_fees_paid": fees,
            "net_amount_received": sales - fees - refunded, "daily_sales": days}


def my_spending(db: Session, user: User, month: str) -> dict:
    y, m = int(month[:4]), int(month[5:])
    start, end = datetime(y, m, 1), datetime(y + (m == 12), (m % 12) + 1, 1)
    base = db.query(Transaction).filter(Transaction.sender_wallet_id == user.wallet.id,
                                        Transaction.status.in_(C.COUNTED_STATUSES),
                                        Transaction.created_at >= start, Transaction.created_at < end)
    total = lambda t: _dec(base.filter(Transaction.type == t).with_entities(func.sum(Transaction.amount)).scalar())  # noqa: E731
    by_cat = (base.filter(Transaction.type == C.T_MPAY)
              .join(MerchantPayment, MerchantPayment.transaction_id == Transaction.id)
              .join(Merchant, Merchant.id == MerchantPayment.merchant_id)
              .with_entities(Merchant.business_category, func.sum(Transaction.amount)).group_by(Merchant.business_category).all())
    cashback = db.query(func.sum(Cashback.amount)).filter(Cashback.user_id == user.id, Cashback.status == "Credited",
                                                          Cashback.credited_at >= start, Cashback.credited_at < end).scalar()
    transfers, withdrawals = total(C.T_P2P), total(C.T_WITHDRAW)
    mp_total = sum((_dec(v) for _, v in by_cat), Decimal("0"))
    return {"month": month, "transfers": transfers, "merchant_payments": mp_total,
            "merchant_payments_by_category": {c: _dec(v) for c, v in by_cat}, "withdrawals": withdrawals,
            "total_spent": transfers + mp_total + withdrawals, "cashback_earned": _dec(cashback)}
