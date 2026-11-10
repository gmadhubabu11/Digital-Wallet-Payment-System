import logging
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Account, LedgerEntry, User, Wallet
from app.utils import constants as C
from app.utils.money import q
from app.utils.email import send_email

log = logging.getLogger("wallet.reconciliation")


def run_reconciliation(db: Session) -> dict:
    sums = defaultdict(lambda: {C.DEBIT: Decimal("0"), C.CREDIT: Decimal("0")})
    for acc_id, etype, total in (db.query(LedgerEntry.account_id, LedgerEntry.entry_type, func.sum(LedgerEntry.amount))
                                 .group_by(LedgerEntry.account_id, LedgerEntry.entry_type).all()):
        sums[acc_id][etype] = q(total)

    mismatches = []
    for acc in db.query(Account).all():
        expected = sums[acc.id][C.CREDIT] - sums[acc.id][C.DEBIT]
        if acc.balance != expected:
            mismatches.append(f"Account {acc.id} ({acc.name}): balance {acc.balance} != ledger sum {expected}")
        if acc.wallet_id:
            wallet = db.get(Wallet, acc.wallet_id)
            if wallet.balance != expected:
                mismatches.append(f"Wallet {wallet.wallet_number}: balance {wallet.balance} != ledger sum {expected}")

    total_debits = sum((v[C.DEBIT] for v in sums.values()), Decimal("0"))
    total_credits = sum((v[C.CREDIT] for v in sums.values()), Decimal("0"))
    if total_debits != total_credits:
        mismatches.append(f"Ledger imbalance: total debits {total_debits} != total credits {total_credits}")

    result = {"status": "OK" if not mismatches else "MISMATCH", "wallets_checked": db.query(Wallet).count(),
              "total_debits": total_debits, "total_credits": total_credits, "mismatches": mismatches}
    if mismatches:
        log.error("RECONCILIATION MISMATCH: %s", mismatches)
        recipients = {u.email for u in db.query(User).filter(User.role == C.ADMIN).all()}
        if settings.ADMIN_EMAIL:
            recipients.add(settings.ADMIN_EMAIL)
        for to in recipients:
            send_email(to, "ALERT: Ledger reconciliation mismatch", "\n".join(mismatches))
    else:
        log.info("Reconciliation OK: %s wallets, debits=%s credits=%s", result["wallets_checked"], total_debits, total_credits)
    return result
