from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth.security import require_roles
from app.database import get_db
from app.models import KYC, Transaction, User, Wallet
from app.routers.deps import PageParams, page, to_json
from app.schemas.money import AdminDecisionIn
from app.schemas.wallet import KycOut, ReasonIn, RejectIn, WalletOut
from app.services import payments
from app.services.views import txn_view
from app.utils import constants as C
from app.utils.email import notify

router = APIRouter(prefix="/admin", tags=["Admin"])
admin_only = require_roles(C.ADMIN)


@router.get("/kyc")
def list_kyc(status: Optional[str] = Query(None, pattern="^(Pending|Approved|Rejected)$"),
             p: PageParams = Depends(), _: User = Depends(admin_only), db: Session = Depends(get_db)):
    q = db.query(KYC)
    if status:
        q = q.filter(KYC.status == status)
    total = q.count()
    rows = q.order_by(KYC.id.desc()).offset(p.skip).limit(p.limit).all()
    return page([KycOut.model_validate(k).model_dump(mode="json") for k in rows], total, p)


@router.get("/wallets")
def list_wallets(p: PageParams = Depends(), _: User = Depends(admin_only), db: Session = Depends(get_db)):
    total = db.query(Wallet).count()
    rows = db.query(Wallet).order_by(Wallet.id).offset(p.skip).limit(p.limit).all()
    return page([WalletOut.model_validate(w).model_dump(mode="json") for w in rows], total, p)


@router.put("/kyc/{kyc_id}/approve", response_model=KycOut)
def approve_kyc(kyc_id: int, bg: BackgroundTasks, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    kyc = db.get(KYC, kyc_id)
    if kyc is None:
        raise HTTPException(404, "KYC not found")
    if kyc.status != "Pending":
        raise HTTPException(400, f"KYC is already {kyc.status}")
    kyc.status, kyc.rejection_reason, kyc.reviewed_by = "Approved", None, admin.id
    user = db.get(User, kyc.user_id)
    user.wallet.kyc_level = C.KYC_FULL
    db.commit()
    notify(bg, user.email, "KYC approved", "Your KYC is approved. Your wallet is upgraded to Full KYC with higher limits.")
    return kyc


@router.put("/kyc/{kyc_id}/reject", response_model=KycOut)
def reject_kyc(kyc_id: int, body: RejectIn, bg: BackgroundTasks, admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    kyc = db.get(KYC, kyc_id)
    if kyc is None:
        raise HTTPException(404, "KYC not found")
    if kyc.status != "Pending":
        raise HTTPException(400, f"KYC is already {kyc.status}")
    kyc.status, kyc.rejection_reason, kyc.reviewed_by = "Rejected", body.reason, admin.id
    db.commit()
    notify(bg, db.get(User, kyc.user_id).email, "KYC rejected", f"Your KYC was rejected: {body.reason}. You can submit it again.")
    return kyc


@router.put("/wallets/{wallet_id}/freeze", response_model=WalletOut)
def freeze_wallet(wallet_id: int, body: ReasonIn, _: User = Depends(admin_only), db: Session = Depends(get_db)):
    wallet = db.get(Wallet, wallet_id)
    if wallet is None:
        raise HTTPException(404, "Wallet not found")
    wallet.status, wallet.freeze_reason = C.WALLET_FROZEN, body.reason
    db.commit()
    return wallet


@router.put("/wallets/{wallet_id}/unfreeze", response_model=WalletOut)
def unfreeze_wallet(wallet_id: int, _: User = Depends(admin_only), db: Session = Depends(get_db)):
    wallet = db.get(Wallet, wallet_id)
    if wallet is None:
        raise HTTPException(404, "Wallet not found")
    wallet.status, wallet.freeze_reason = C.WALLET_ACTIVE, None
    db.commit()
    return wallet


@router.get("/flagged-transactions")
def flagged(p: PageParams = Depends(), _: User = Depends(admin_only), db: Session = Depends(get_db)):
    q = db.query(Transaction).filter(Transaction.is_flagged.is_(True))
    total = q.count()
    rows = q.order_by(Transaction.id.desc()).offset(p.skip).limit(p.limit).all()
    return page([to_json(txn_view(db, t, None)) for t in rows], total, p)


@router.put("/transactions/{transaction_ref}/approve")
def approve_txn(transaction_ref: str, body: AdminDecisionIn, bg: BackgroundTasks,
                admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    txn = payments.review_hold(db, admin, transaction_ref, True, body.reason, bg)
    db.commit()
    return {"transaction_ref": txn.transaction_ref, "status": txn.status, "message": "Transfer approved and delivered"}


@router.put("/transactions/{transaction_ref}/reject")
def reject_txn(transaction_ref: str, body: AdminDecisionIn, bg: BackgroundTasks,
               admin: User = Depends(admin_only), db: Session = Depends(get_db)):
    txn = payments.review_hold(db, admin, transaction_ref, False, body.reason, bg)
    db.commit()
    return {"transaction_ref": txn.transaction_ref, "status": txn.status, "message": "Transfer rejected and money returned to sender"}
