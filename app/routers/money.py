import io

import qrcode
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth.security import require_roles
from app.database import get_db
from app.models import MerchantPayment, Transaction, User
from app.routers.deps import PageParams, page, to_json
from app.schemas.common import TxnResult
from app.schemas.money import AddMoneyIn, MerchantPayIn, RefundIn, TransferIn
from app.services import payments
from app.services.idempotency import idem_key, run_idempotent
from app.services.views import txn_view
from app.utils import constants as C
from app.utils.money import mask_phone

router = APIRouter(tags=["Money Movement"])
user_only = require_roles(C.USER)
merchant_only = require_roles(C.MERCHANT)


@router.post("/wallet/add-money", response_model=TxnResult, status_code=201)
def add_money(body: AddMoneyIn, bg: BackgroundTasks, key: str = Depends(idem_key),
              user: User = Depends(user_only), db: Session = Depends(get_db)):
    return run_idempotent(db, user, "POST /wallet/add-money", key, body.model_dump(mode="json"),
                          lambda: payments.add_money(db, user, body, bg))


@router.post("/transfers", response_model=TxnResult, status_code=201)
def send_money(body: TransferIn, bg: BackgroundTasks, key: str = Depends(idem_key),
               user: User = Depends(user_only), db: Session = Depends(get_db)):
    return run_idempotent(db, user, "POST /transfers", key, body.model_dump(mode="json"),
                          lambda: payments.transfer(db, user, body, bg))


@router.get("/transfers/{transaction_ref}")
def get_transfer(transaction_ref: str, user: User = Depends(require_roles(C.USER, C.ADMIN)), db: Session = Depends(get_db)):
    txn = db.query(Transaction).filter(Transaction.transaction_ref == transaction_ref, Transaction.type == C.T_P2P).first()
    if txn is None:
        raise HTTPException(404, "Transfer not found")
    if user.role == C.ADMIN:
        return to_json(txn_view(db, txn, None))
    if user.wallet.id not in (txn.sender_wallet_id, txn.receiver_wallet_id):
        raise HTTPException(404, "Transfer not found")
    return to_json(txn_view(db, txn, user.wallet.id))


@router.get("/merchants/me/qr")
def merchant_qr(user: User = Depends(merchant_only)):
    img = qrcode.make(user.merchant.merchant_code)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": f'inline; filename="{user.merchant.merchant_code}.png"'})


@router.post("/payments/merchant", response_model=TxnResult, status_code=201)
def pay_merchant(body: MerchantPayIn, bg: BackgroundTasks, key: str = Depends(idem_key),
                 user: User = Depends(user_only), db: Session = Depends(get_db)):
    return run_idempotent(db, user, "POST /payments/merchant", key, body.model_dump(mode="json"),
                          lambda: payments.merchant_payment(db, user, body, bg))


@router.post("/payments/{transaction_ref}/refund", response_model=TxnResult, status_code=201)
def refund_payment(transaction_ref: str, body: RefundIn, bg: BackgroundTasks, key: str = Depends(idem_key),
                   user: User = Depends(merchant_only), db: Session = Depends(get_db)):
    return run_idempotent(db, user, f"POST /payments/{transaction_ref}/refund", key, body.model_dump(mode="json"),
                          lambda: payments.refund(db, user, transaction_ref, body, bg))


@router.get("/merchants/me/payments")
def merchant_payments(p: PageParams = Depends(), user: User = Depends(merchant_only), db: Session = Depends(get_db)):
    q = db.query(MerchantPayment).filter(MerchantPayment.merchant_id == user.merchant.id)
    total = q.count()
    rows = q.order_by(MerchantPayment.id.desc()).offset(p.skip).limit(p.limit).all()
    items = [{"transaction_ref": mp.transaction.transaction_ref, "customer": mp.user.name,
              "customer_phone": mask_phone(mp.user.phone), "amount": mp.amount, "platform_fee": mp.fee,
              "net_amount": mp.amount - mp.fee, "refunded_amount": mp.refunded_amount, "status": mp.status,
              "created_at": mp.created_at} for mp in rows]
    return page(to_json(items), total, p)
