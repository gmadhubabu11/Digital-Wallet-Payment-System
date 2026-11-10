from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.security import get_current_user, require_roles, verify_secret
from app.database import get_db
from app.models import KYC, User
from app.routers.deps import need_wallet
from app.schemas.wallet import (ChangePinIn, KycIn, KycOut, OtpSendIn, OtpVerifyIn, ResetPinIn, SetPinIn, WalletOut)
from app.services import security
from app.config import settings
from app.utils import constants as C

router = APIRouter(tags=["Wallet, KYC, PIN & OTP"])
customer = require_roles(C.USER, C.MERCHANT)


@router.get("/wallet", response_model=WalletOut)
def get_wallet(user: User = Depends(customer)):
    return need_wallet(user)


# ---------------- KYC ----------------
@router.post("/kyc", response_model=KycOut, status_code=201)
def submit_kyc(body: KycIn, user: User = Depends(customer), db: Session = Depends(get_db)):
    existing = db.query(KYC).filter(KYC.user_id == user.id).first()
    if existing and existing.status in ("Pending", "Approved"):
        raise HTTPException(409, f"KYC already {existing.status}")
    if db.query(KYC).filter(KYC.pan_number == body.pan_number, KYC.user_id != user.id).first():
        raise HTTPException(409, "This PAN is already registered")
    if existing:  # re-submission after rejection
        existing.pan_number, existing.aadhaar_last4 = body.pan_number, body.aadhaar_last4
        existing.full_name, existing.date_of_birth = body.full_name, body.date_of_birth
        existing.status, existing.rejection_reason = "Pending", None
        kyc = existing
    else:
        kyc = KYC(user_id=user.id, **body.model_dump())
        db.add(kyc)
    db.commit()
    return kyc


@router.get("/kyc", response_model=KycOut)
def get_kyc(user: User = Depends(customer), db: Session = Depends(get_db)):
    kyc = db.query(KYC).filter(KYC.user_id == user.id).first()
    if kyc is None:
        raise HTTPException(404, "No KYC submitted yet")
    return kyc


# ---------------- PIN ----------------
@router.post("/wallet/pin", status_code=201)
def set_pin(body: SetPinIn, user: User = Depends(customer), db: Session = Depends(get_db)):
    if user.pin_hash:
        raise HTTPException(409, "PIN already set. Use PUT /wallet/pin/change or POST /wallet/pin/reset")
    security.set_pin(db, user, body.pin)
    db.commit()
    return {"message": "Transaction PIN set successfully"}


@router.put("/wallet/pin/change")
def change_pin(body: ChangePinIn, user: User = Depends(customer), db: Session = Depends(get_db)):
    security.verify_pin(db, user, body.old_pin)
    if verify_secret(body.new_pin, user.pin_hash):
        raise HTTPException(400, "New PIN must be different from the old PIN")
    security.set_pin(db, user, body.new_pin)
    db.commit()
    return {"message": "Transaction PIN changed successfully"}


@router.post("/wallet/pin/reset")
def reset_pin(body: ResetPinIn, user: User = Depends(customer), db: Session = Depends(get_db)):
    security.validate_pin_strength(body.new_pin)
    security.verify_otp(db, user, C.OTP_PIN, body.otp, consume=True)
    security.set_pin(db, user, body.new_pin)  # also clears any payment lock
    db.commit()
    return {"message": "Transaction PIN reset successfully"}


# ---------------- OTP ----------------
@router.post("/otp/send")
def send_otp(body: OtpSendIn, bg: BackgroundTasks, user: User = Depends(customer), db: Session = Depends(get_db)):
    otp = security.send_otp(db, user, body.purpose, bg)
    resp = {"message": f"OTP sent to {user.email}. Valid for {security.OTP_VALID_MINUTES} minutes"}
    if settings.OTP_DEV_ECHO:  # dev only: stand-in for SMS so Swagger testing works without a mailbox
        resp["dev_otp"] = otp
    return resp


@router.post("/otp/verify")
def verify_otp(body: OtpVerifyIn, user: User = Depends(customer), db: Session = Depends(get_db)):
    """Checks the OTP without consuming it; it is consumed when the action that needs it is performed."""
    if body.purpose not in security.OTP_PURPOSES:
        raise HTTPException(422, f"purpose must be one of: {', '.join(security.OTP_PURPOSES)}")
    security.verify_otp(db, user, body.purpose, body.otp, consume=False)
    return {"message": "OTP verified", "purpose": body.purpose}
