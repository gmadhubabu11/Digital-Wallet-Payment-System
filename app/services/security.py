import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.security import hash_secret, verify_secret
from app.config import settings
from app.models import OTP, User
from app.utils import constants as C
from app.utils.email import notify

MAX_PIN_ATTEMPTS = 3
PIN_LOCK_MINUTES = 30
OTP_VALID_MINUTES = 5
OTP_RESEND_SECONDS = 60
OTP_MAX_ATTEMPTS = 3
OTP_PURPOSES = (C.OTP_TRANSFER, C.OTP_BANK, C.OTP_PIN)


# ---------------- PIN ----------------
def validate_pin_strength(pin: str) -> None:
    digits = "0123456789"
    simple = (
        len(set(pin)) == 1                      # 0000, 1111
        or pin in digits or pin in digits[::-1]  # 1234, 4321 (sequences)
        or pin[:2] == pin[2:]                   # 1212
    )
    if simple:
        raise HTTPException(400, "PIN is too simple. Avoid repeated or sequential digits (e.g. 1234, 1111, 0000)")


def set_pin(db: Session, user: User, pin: str) -> None:
    validate_pin_strength(pin)
    user.pin_hash = hash_secret(pin)
    user.pin_failed_attempts = 0
    user.pin_locked_until = None


def verify_pin(db: Session, user: User, pin: str) -> None:
    if not user.pin_hash:
        raise HTTPException(400, "Transaction PIN not set. Set it using POST /wallet/pin")
    now = datetime.utcnow()
    if user.pin_locked_until and user.pin_locked_until > now:
        mins = int((user.pin_locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(423, f"Payments are locked due to wrong PIN attempts. Try again in {mins} minute(s)")
    if verify_secret(pin, user.pin_hash):
        user.pin_failed_attempts = 0
        user.pin_locked_until = None
        return
    user.pin_failed_attempts += 1
    if user.pin_failed_attempts >= MAX_PIN_ATTEMPTS:
        user.pin_locked_until = now + timedelta(minutes=PIN_LOCK_MINUTES)
        user.pin_failed_attempts = 0
        db.commit()  # persist the lock even though the request fails
        raise HTTPException(423, f"Wrong PIN. All payments are locked for {PIN_LOCK_MINUTES} minutes")
    left = MAX_PIN_ATTEMPTS - user.pin_failed_attempts
    db.commit()
    raise HTTPException(400, f"Incorrect PIN. {left} attempt(s) left before payments are locked")


# ---------------- OTP ----------------
def _hash_otp(user_id: int, purpose: str, otp: str) -> str:
    msg = f"{user_id}:{purpose}:{otp}".encode()
    return hmac.new(settings.JWT_SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def send_otp(db: Session, user: User, purpose: str, bg) -> str:
    if purpose not in OTP_PURPOSES:
        raise HTTPException(422, f"purpose must be one of: {', '.join(OTP_PURPOSES)}")
    now = datetime.utcnow()
    last = db.query(OTP).filter(OTP.user_id == user.id).order_by(OTP.id.desc()).first()
    if last and (now - last.created_at).total_seconds() < OTP_RESEND_SECONDS:
        wait = int(OTP_RESEND_SECONDS - (now - last.created_at).total_seconds()) + 1
        raise HTTPException(429, f"Please wait {wait} seconds before requesting a new OTP")
    otp = f"{secrets.randbelow(1_000_000):06d}"
    db.add(OTP(user_id=user.id, purpose=purpose, otp_hash=_hash_otp(user.id, purpose, otp),
               expires_at=now + timedelta(minutes=OTP_VALID_MINUTES)))
    db.commit()
    notify(bg, user.email, f"Your wallet OTP for {purpose}",
           f"Your OTP is {otp}. It is valid for {OTP_VALID_MINUTES} minutes. Do not share it with anyone.")
    return otp


def verify_otp(db: Session, user: User, purpose: str, otp: str, consume: bool = True) -> None:
    now = datetime.utcnow()
    rec = (db.query(OTP).filter(OTP.user_id == user.id, OTP.purpose == purpose, OTP.is_used.is_(False))
           .order_by(OTP.id.desc()).first())
    if rec is None:
        raise HTTPException(400, f"No active OTP for '{purpose}'. Request one using POST /otp/send")
    if rec.expires_at < now:
        raise HTTPException(400, "OTP has expired. Request a new one")
    if rec.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(400, "Too many wrong attempts for this OTP. Request a new one")
    if not hmac.compare_digest(rec.otp_hash, _hash_otp(user.id, purpose, otp)):
        rec.attempts += 1
        left = OTP_MAX_ATTEMPTS - rec.attempts
        db.commit()
        raise HTTPException(400, f"Incorrect OTP. {left} attempt(s) left")
    if consume:
        rec.is_used = True
