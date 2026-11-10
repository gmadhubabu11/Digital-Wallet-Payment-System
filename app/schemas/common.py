from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

Amount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
Phone = Annotated[str, Field(pattern=r"^\d{10}$", description="10-digit phone number")]
PinStr = Annotated[str, Field(pattern=r"^\d{4}$", description="4-digit PIN")]
OtpStr = Annotated[str, Field(pattern=r"^\d{6}$", description="6-digit OTP")]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel):
    total: int
    skip: int
    limit: int
    items: List[Any]


class TxnResult(BaseModel):
    transaction_ref: str
    type: str
    amount: Decimal
    status: str
    message: str
    balance: Optional[Decimal] = None
    is_flagged: bool = False
    created_at: datetime


def txn_result(txn, message: str, balance=None) -> dict:
    return TxnResult(
        transaction_ref=txn.transaction_ref, type=txn.type, amount=txn.amount, status=txn.status,
        message=message, balance=balance, is_flagged=txn.is_flagged, created_at=txn.created_at,
    ).model_dump(mode="json")
