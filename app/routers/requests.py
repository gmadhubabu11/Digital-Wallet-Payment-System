from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth.security import require_roles
from app.database import get_db
from app.models import MoneyRequest, User
from app.routers.deps import PageParams, page, to_json
from app.schemas.money import MoneyRequestIn, PayRequestIn
from app.services import payments
from app.services.idempotency import idem_key, run_idempotent
from app.schemas.common import txn_result
from app.utils import constants as C
from app.utils.email import notify
from app.utils.money import inr, mask_phone, q

router = APIRouter(prefix="/money-requests", tags=["Request Money"])
user_only = require_roles(C.USER)
EXPIRY = timedelta(hours=48)


def _view(r: MoneyRequest) -> dict:
    return {"id": r.id, "requester": r.requester.name, "requester_phone": mask_phone(r.requester.phone),
            "payer": r.payer.name, "payer_phone": mask_phone(r.payer.phone), "amount": r.amount, "note": r.note,
            "status": r.status, "expires_at": r.expires_at, "transaction_id": r.transaction_id, "created_at": r.created_at}


def _expire_if_needed(db: Session, r: MoneyRequest) -> None:
    if r.status == "Pending" and r.expires_at <= datetime.utcnow():
        r.status = "Expired"
        db.commit()


@router.post("", status_code=201)
def create_request(body: MoneyRequestIn, bg: BackgroundTasks, user: User = Depends(user_only), db: Session = Depends(get_db)):
    payer = db.query(User).filter(User.phone == body.payer_phone, User.role == C.USER).first()
    if payer is None:
        raise HTTPException(404, "User not found for this phone number")
    if payer.id == user.id:
        raise HTTPException(400, "You cannot request money from yourself")
    pending = db.query(MoneyRequest).filter(MoneyRequest.requester_id == user.id, MoneyRequest.payer_id == payer.id,
                                            MoneyRequest.status == "Pending", MoneyRequest.expires_at > datetime.utcnow()).count()
    if pending >= 5:
        raise HTTPException(400, "You already have 5 pending requests to this person")
    r = MoneyRequest(requester_id=user.id, payer_id=payer.id, amount=q(body.amount), note=body.note,
                     status="Pending", expires_at=datetime.utcnow() + EXPIRY)
    db.add(r)
    db.commit()
    notify(bg, payer.email, "Money request received",
           f"{user.name} has requested {inr(r.amount)}" + (f" ({r.note})" if r.note else "") + ". It expires in 48 hours.")
    return to_json(_view(r))


@router.get("")
def list_requests(direction: str = Query("all", pattern="^(all|sent|received)$"),
                  status: Optional[str] = Query(None, pattern="^(Pending|Paid|Declined|Cancelled|Expired)$"),
                  p: PageParams = Depends(), user: User = Depends(user_only), db: Session = Depends(get_db)):
    query = db.query(MoneyRequest)
    if direction == "sent":
        query = query.filter(MoneyRequest.requester_id == user.id)
    elif direction == "received":
        query = query.filter(MoneyRequest.payer_id == user.id)
    else:
        query = query.filter(or_(MoneyRequest.requester_id == user.id, MoneyRequest.payer_id == user.id))
    if status:
        query = query.filter(MoneyRequest.status == status)
    total = query.count()
    rows = query.order_by(MoneyRequest.id.desc()).offset(p.skip).limit(p.limit).all()
    for r in rows:
        if r.status == "Pending" and r.expires_at <= datetime.utcnow():
            r.status = "Expired"
    db.commit()
    return page(to_json([_view(r) for r in rows]), total, p)


def _get(db: Session, request_id: int, lock: bool = False) -> MoneyRequest:
    query = db.query(MoneyRequest).filter(MoneyRequest.id == request_id)
    r = (query.with_for_update() if lock else query).first()
    if r is None:
        raise HTTPException(404, "Money request not found")
    return r


@router.put("/{request_id}/pay")
def pay_request(request_id: int, body: PayRequestIn, bg: BackgroundTasks, key: str = Depends(idem_key),
                user: User = Depends(user_only), db: Session = Depends(get_db)):
    def work():
        r = _get(db, request_id, lock=True)
        if r.payer_id != user.id:
            raise HTTPException(403, "Only the payer can pay this request")
        if r.status == "Pending" and r.expires_at <= datetime.utcnow():
            r.status = "Expired"
            db.commit()
        if r.status != "Pending":
            raise HTTPException(400, f"Request is {r.status} and can no longer be paid")
        txn, msg = payments.execute_transfer(db, user, r.requester, r.amount, r.note or "Money request payment",
                                             body.pin, body.otp, bg)
        r.status, r.transaction_id = "Paid", txn.id
        return 200, txn_result(txn, msg, user.wallet.balance)

    return run_idempotent(db, user, f"PUT /money-requests/{request_id}/pay", key, body.model_dump(mode="json"), work)


@router.put("/{request_id}/decline")
def decline_request(request_id: int, user: User = Depends(user_only), db: Session = Depends(get_db)):
    r = _get(db, request_id, lock=True)
    if r.payer_id != user.id:
        raise HTTPException(403, "Only the payer can decline this request")
    _expire_if_needed(db, r)
    if r.status != "Pending":
        raise HTTPException(400, f"Request is already {r.status}")
    r.status = "Declined"
    db.commit()
    return to_json(_view(r))


@router.put("/{request_id}/cancel")
def cancel_request(request_id: int, user: User = Depends(user_only), db: Session = Depends(get_db)):
    r = _get(db, request_id, lock=True)
    if r.requester_id != user.id:
        raise HTTPException(403, "Only the requester can cancel this request")
    _expire_if_needed(db, r)
    if r.status != "Pending":
        raise HTTPException(400, f"Request is already {r.status}")
    r.status = "Cancelled"
    db.commit()
    return to_json(_view(r))
