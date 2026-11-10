from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.auth.security import create_access_token, get_current_user, verify_secret
from app.config import settings
from app.database import get_db
from app.models import User
from app.schemas.auth import MeOut, RegisterIn, TokenOut
from app.services.users import create_user_with_wallet
from app.utils import constants as C
from app.utils.email import notify

router = APIRouter(prefix="/auth", tags=["Auth"])


def _me(user: User) -> dict:
    data = MeOut.model_validate(user).model_dump()
    if user.wallet:
        data["wallet_number"] = user.wallet.wallet_number
    if user.merchant:
        data.update(merchant_code=user.merchant.merchant_code, business_name=user.merchant.business_name,
                    business_category=user.merchant.business_category)
    return data


@router.post("/register", response_model=MeOut, status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)):
    if body.role == C.ADMIN:
        raise HTTPException(403, "Admin accounts cannot be created through public registration")
    if body.role not in (C.USER, C.MERCHANT):
        raise HTTPException(422, "role must be User or Merchant")
    user = create_user_with_wallet(db, name=body.name, email=body.email, phone=body.phone, password=body.password,
                                   role=body.role, business_name=body.business_name,
                                   business_category=body.business_category)
    db.commit()
    return _me(user)


@router.post("/login", response_model=TokenOut)
def login(bg: BackgroundTasks, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """Use the account email in the `username` field."""
    user = db.query(User).filter(User.email == form.username.lower()).first()
    if user is None or not verify_secret(form.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    if not user.is_active:
        raise HTTPException(403, "Account is inactive. Contact support")
    notify(bg, user.email, "New login to your wallet", f"Hi {user.name}, a successful login was recorded on your account. "
           "If this was not you, change your password and contact support immediately.")
    return TokenOut(access_token=create_access_token(user), expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user)):
    return _me(user)
