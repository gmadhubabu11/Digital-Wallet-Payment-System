from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.security import require_roles
from app.database import get_db
from app.models import BankAccount, User, Withdrawal
from app.routers.deps import PageParams, page, to_json
from app.schemas.common import TxnResult
from app.schemas.money import BankAccountIn, WithdrawalIn
from app.services import security, withdrawals
from app.services.idempotency import idem_key, run_idempotent
from app.utils import constants as C
from app.utils.money import mask_account

router = APIRouter(tags=["Bank Accounts & Withdrawals"])
customer = require_roles(C.USER, C.MERCHANT)
MAX_ACCOUNTS = 3


def _bank(b: BankAccount) -> dict:
    return {"id": b.id, "account_holder_name": b.account_holder_name, "account_number": mask_account(b.account_number),
            "ifsc_code": b.ifsc_code, "bank_name": b.bank_name, "is_primary": b.is_primary, "created_at": b.created_at}


@router.post("/bank-accounts", status_code=201)
def add_bank_account(body: BankAccountIn, user: User = Depends(customer), db: Session = Depends(get_db)):
    active = db.query(BankAccount).filter(BankAccount.user_id == user.id, BankAccount.is_active.is_(True)).all()
    if len(active) >= MAX_ACCOUNTS:
        raise HTTPException(400, f"You can link at most {MAX_ACCOUNTS} bank accounts")
    if any(b.account_number == body.account_number and b.ifsc_code == body.ifsc_code for b in active):
        raise HTTPException(409, "This bank account is already linked")
    security.verify_otp(db, user, C.OTP_BANK, body.otp, consume=True)
    primary = body.is_primary or not active
    if primary:
        for b in active:
            b.is_primary = False
    acc = BankAccount(user_id=user.id, account_holder_name=body.account_holder_name, account_number=body.account_number,
                      ifsc_code=body.ifsc_code, bank_name=body.bank_name, is_primary=primary)
    db.add(acc)
    db.commit()
    return to_json(_bank(acc))


@router.get("/bank-accounts")
def list_bank_accounts(user: User = Depends(customer), db: Session = Depends(get_db)):
    rows = db.query(BankAccount).filter(BankAccount.user_id == user.id, BankAccount.is_active.is_(True)).order_by(BankAccount.id).all()
    return to_json([_bank(b) for b in rows])


@router.delete("/bank-accounts/{bank_account_id}")
def delete_bank_account(bank_account_id: int, user: User = Depends(customer), db: Session = Depends(get_db)):
    acc = db.query(BankAccount).filter(BankAccount.id == bank_account_id, BankAccount.user_id == user.id,
                                       BankAccount.is_active.is_(True)).first()
    if acc is None:
        raise HTTPException(404, "Bank account not found")
    if db.query(Withdrawal).filter(Withdrawal.bank_account_id == acc.id, Withdrawal.status == "Pending").first():
        raise HTTPException(409, "This account has a pending withdrawal and cannot be removed yet")
    acc.is_active, was_primary = False, acc.is_primary
    acc.is_primary = False
    if was_primary:
        nxt = db.query(BankAccount).filter(BankAccount.user_id == user.id, BankAccount.is_active.is_(True)).first()
        if nxt:
            nxt.is_primary = True
    db.commit()
    return {"message": "Bank account removed"}


@router.post("/withdrawals", response_model=TxnResult, status_code=201)
def withdraw(body: WithdrawalIn, bg: BackgroundTasks, key: str = Depends(idem_key),
             user: User = Depends(customer), db: Session = Depends(get_db)):
    return run_idempotent(db, user, "POST /withdrawals", key, body.model_dump(mode="json"),
                          lambda: withdrawals.request_withdrawal(db, user, body, bg))


@router.get("/withdrawals")
def list_withdrawals(p: PageParams = Depends(), user: User = Depends(customer), db: Session = Depends(get_db)):
    q = db.query(Withdrawal).filter(Withdrawal.user_id == user.id)
    total = q.count()
    rows = q.order_by(Withdrawal.id.desc()).offset(p.skip).limit(p.limit).all()
    items = [{"id": w.id, "transaction_ref": w.transaction.transaction_ref, "amount": w.amount, "status": w.status,
              "bank_name": w.bank_account.bank_name, "account_number": mask_account(w.bank_account.account_number),
              "processed_at": w.processed_at, "failure_reason": w.failure_reason, "created_at": w.created_at} for w in rows]
    return page(to_json(items), total, p)
