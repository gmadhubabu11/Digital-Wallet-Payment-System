from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.auth.security import hash_secret
from app.models import Account, Merchant, User, Wallet
from app.utils import constants as C
from app.utils.refs import merchant_code, wallet_number


def create_user_with_wallet(db: Session, *, name, email, phone, password, role,
                            business_name=None, business_category=None) -> User:
    if db.query(User).filter(User.email == email.lower()).first():
        raise HTTPException(409, "Email already registered")
    if db.query(User).filter(User.phone == phone).first():
        raise HTTPException(409, "Phone number already registered")

    user = User(name=name, email=email.lower(), phone=phone, password_hash=hash_secret(password), role=role)
    db.add(user)
    db.flush()

    merchant = None
    if role == C.MERCHANT:
        merchant = Merchant(user_id=user.id, business_name=business_name, business_category=business_category)
        db.add(merchant)
        db.flush()
        merchant.merchant_code = merchant_code(merchant.id)

    wallet = Wallet(owner_id=user.id, merchant_id=merchant.id if merchant else None,
                    balance=0, kyc_level=C.KYC_MIN, status=C.WALLET_ACTIVE)
    db.add(wallet)
    db.flush()
    wallet.wallet_number = wallet_number(wallet.id)
    db.add(Account(name=f"{wallet.wallet_number} ({name})", account_type=C.ACC_MERCHANT if merchant else C.ACC_USER,
                   wallet_id=wallet.id, balance=0))
    db.flush()
    return user
