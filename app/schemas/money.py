import re
from datetime import datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORM, Amount, OtpStr, Phone, PinStr


class AddMoneyIn(BaseModel):
    amount: Amount
    source: Literal["UPI", "Card", "Net Banking"]
    simulate_status: Literal["Success", "Failed"] = "Success"


class TransferIn(BaseModel):
    receiver_phone: Phone
    amount: Amount
    note: Optional[str] = Field(default=None, max_length=255)
    pin: PinStr
    otp: Optional[OtpStr] = None


class MerchantPayIn(BaseModel):
    merchant_code: str = Field(pattern=r"^MER-\d+$")
    amount: Amount
    pin: PinStr


class RefundIn(BaseModel):
    amount: Amount
    reason: Optional[str] = Field(default=None, max_length=255)


class MoneyRequestIn(BaseModel):
    payer_phone: Phone
    amount: Amount
    note: Optional[str] = Field(default=None, max_length=255)


class PayRequestIn(BaseModel):
    pin: PinStr
    otp: Optional[OtpStr] = None


class BankAccountIn(BaseModel):
    account_holder_name: str = Field(min_length=2, max_length=100)
    account_number: str
    ifsc_code: str
    bank_name: str = Field(min_length=2, max_length=100)
    is_primary: bool = False
    otp: OtpStr

    @field_validator("account_number")
    @classmethod
    def acc(cls, v):
        if not re.fullmatch(r"\d{9,18}", v):
            raise ValueError("Account number must be 9 to 18 digits")
        return v

    @field_validator("ifsc_code")
    @classmethod
    def ifsc(cls, v):
        v = v.upper().strip()
        if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", v):
            raise ValueError("IFSC must be 11 characters: 4 letters, '0', then 6 letters/digits (e.g. SBIN0001234)")
        return v


class WithdrawalIn(BaseModel):
    bank_account_id: int
    amount: Amount
    pin: PinStr


class AdminDecisionIn(BaseModel):
    reason: str = Field(min_length=3, max_length=255)
