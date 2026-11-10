import re
from typing import Optional

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from app.schemas.common import ORM, Phone


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: EmailStr
    phone: Phone
    password: str = Field(min_length=8, max_length=64)
    role: str = Field(default="User", description="User or Merchant (Admin is not allowed)")
    business_name: Optional[str] = Field(default=None, max_length=150)
    business_category: Optional[str] = Field(default=None, max_length=100)

    @field_validator("password")
    @classmethod
    def strong_password(cls, v):
        if not (re.search(r"[A-Za-z]", v) and re.search(r"\d", v)):
            raise ValueError("Password must contain letters and digits")
        return v

    @model_validator(mode="after")
    def merchant_fields(self):
        if self.role == "Merchant" and not (self.business_name and self.business_category):
            raise ValueError("business_name and business_category are required for Merchant")
        return self


class UserOut(ORM):
    id: int
    name: str
    email: str
    phone: str
    role: str
    is_active: bool


class MeOut(UserOut):
    wallet_number: Optional[str] = None
    merchant_code: Optional[str] = None
    business_name: Optional[str] = None
    business_category: Optional[str] = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int
