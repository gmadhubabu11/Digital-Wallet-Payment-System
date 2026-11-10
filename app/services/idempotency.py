import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Callable, Tuple

from fastapi import Header, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import IdempotencyKey, User

KEY_TTL = timedelta(hours=24)


def idem_key(idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=8, max_length=100,
                                           description="Unique client-generated value, e.g. a UUID")) -> str:
    return idempotency_key


def _hash(body: dict) -> str:
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    return hmac.new(settings.JWT_SECRET_KEY.encode(), raw, hashlib.sha256).hexdigest()


def run_idempotent(db: Session, user: User, endpoint: str, key: str, body: dict,
                   fn: Callable[[], Tuple[int, dict]]) -> JSONResponse:
    """
    fn() performs the money movement WITHOUT committing and returns (status_code, json_body).
    The saved response is committed in the same DB transaction as the money movement.
    Only successful responses are saved; on error the key is released so the client can retry.
    """
    req_hash = _hash(body)
    row = db.query(IdempotencyKey).filter_by(user_id=user.id, endpoint=endpoint, key=key).first()
    if row and datetime.utcnow() - row.created_at > KEY_TTL:  # expired key -> treat as new
        db.delete(row)
        db.commit()
        row = None

    if row:
        if row.request_hash != req_hash:
            raise HTTPException(422, "Idempotency-Key was already used with a different request body")
        if row.status != "Completed":
            raise HTTPException(409, "A request with this Idempotency-Key is still being processed")
        return JSONResponse(json.loads(row.response_body), status_code=row.status_code,
                            headers={"Idempotent-Replayed": "true"})

    row = IdempotencyKey(user_id=user.id, endpoint=endpoint, key=key, request_hash=req_hash, status="Processing")
    db.add(row)
    try:
        db.commit()
    except IntegrityError:  # same key arrived concurrently
        db.rollback()
        raise HTTPException(409, "A request with this Idempotency-Key is still being processed")
    row_id = row.id

    try:
        status_code, payload = fn()
        rec = db.get(IdempotencyKey, row_id)
        rec.response_body, rec.status_code, rec.status = json.dumps(payload), status_code, "Completed"
        db.commit()
    except Exception:
        db.rollback()
        db.query(IdempotencyKey).filter_by(id=row_id).delete()
        db.commit()
        raise
    return JSONResponse(payload, status_code=status_code)
