import re
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORM, OtpStr, PinStr


class WalletOut(ORM):
    id: int
    wallet_number: str
    owner_id: int
    balance: Decimal
    kyc_level: str
    status: str
    freeze_reason: Optional[str] = None


class KycIn(BaseModel):
    pan_number: str
    aadhaar_last4: str = Field(pattern=r"^\d{4}$")
    full_name: str = Field(min_length=2, max_length=100)
    date_of_birth: date

    @field_validator("pan_number")
    @classmethod
    def pan(cls, v):
        v = v.upper().strip()
        if not re.fullmatch(r"[A-Z]{5}[0-9]{4}[A-Z]", v):
            raise ValueError("PAN must be 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F)")
        return v

    @field_validator("date_of_birth")
    @classmethod
    def adult(cls, v):
        if v >= date.today():
            raise ValueError("date_of_birth must be in the past")
        return v


class KycOut(ORM):
    id: int
    user_id: int
    pan_number: str
    aadhaar_last4: str
    full_name: str
    date_of_birth: date
    status: str
    rejection_reason: Optional[str] = None
    created_at: datetime


class RejectIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)


class SetPinIn(BaseModel):
    pin: PinStr


class ChangePinIn(BaseModel):
    old_pin: PinStr
    new_pin: PinStr


class ResetPinIn(BaseModel):
    otp: OtpStr
    new_pin: PinStr


class OtpSendIn(BaseModel):
    purpose: str = Field(description="High Value Transfer | Add Bank Account | PIN Reset")


class OtpVerifyIn(BaseModel):
    purpose: str
    otp: OtpStr
