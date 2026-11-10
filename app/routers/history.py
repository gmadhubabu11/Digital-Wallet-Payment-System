from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.auth.security import get_current_user, require_roles
from app.database import get_db
from app.models import Transaction, User
from app.routers.deps import PageParams, page, to_json
from app.services import statements
from app.services.views import txn_view
from app.utils import constants as C

router = APIRouter(tags=["History & Statements"])


@router.get("/transactions")
def list_transactions(
    type_: Optional[str] = Query(None, alias="type", description="e.g. P2P Transfer"),
    status: Optional[str] = None,
    min_amount: Optional[Decimal] = Query(None, ge=0),
    max_amount: Optional[Decimal] = Query(None, ge=0),
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    sort_by: str = Query("date", pattern="^(date|amount)$"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
    p: PageParams = Depends(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Transaction)
    viewer = None
    if user.role != C.ADMIN:
        viewer = user.wallet.id
        # a receiver does not see an incoming transfer while it is On Hold
        q = q.filter(or_(Transaction.sender_wallet_id == viewer,
                         and_(Transaction.receiver_wallet_id == viewer, Transaction.status != C.S_HOLD)))
    if type_:
        q = q.filter(Transaction.type == type_)
    if status:
        q = q.filter(Transaction.status == status)
    if min_amount is not None:
        q = q.filter(Transaction.amount >= min_amount)
    if max_amount is not None:
        q = q.filter(Transaction.amount <= max_amount)
    if from_date:
        q = q.filter(Transaction.created_at >= datetime.combine(from_date, datetime.min.time()))
    if to_date:
        q = q.filter(Transaction.created_at < datetime.combine(to_date + timedelta(days=1), datetime.min.time()))
    col = Transaction.created_at if sort_by == "date" else Transaction.amount
    q = q.order_by(col.asc() if order == "asc" else col.desc(), Transaction.id.desc())
    total = q.count()
    rows = q.offset(p.skip).limit(p.limit).all()
    return page(to_json([txn_view(db, t, viewer) for t in rows]), total, p)


@router.get("/transactions/{transaction_ref}")
def get_transaction(transaction_ref: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    txn = db.query(Transaction).filter(Transaction.transaction_ref == transaction_ref).first()
    if txn is None:
        raise HTTPException(404, "Transaction not found")
    if user.role == C.ADMIN:
        return to_json(txn_view(db, txn, None))
    w = user.wallet.id
    visible = txn.sender_wallet_id == w or (txn.receiver_wallet_id == w and txn.status != C.S_HOLD)
    if not visible:
        raise HTTPException(404, "Transaction not found")
    return to_json(txn_view(db, txn, w))


@router.get("/statements")
def statement(month: str = Query(..., description="YYYY-MM, e.g. 2026-10"),
              format: str = Query("pdf", pattern="^(pdf|csv)$"),
              user: User = Depends(require_roles(C.USER, C.MERCHANT)), db: Session = Depends(get_db)):
    data = statements.build(db, user, month)
    if format == "pdf":
        path = statements.to_pdf(data)
        return FileResponse(path, media_type="application/pdf", filename=path.split("/")[-1])
    path = statements.to_csv(data)
    return FileResponse(path, media_type="text/csv", filename=path.split("/")[-1])
