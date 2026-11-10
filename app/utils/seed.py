import logging
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.models import KYC, User
from app.auth.security import hash_secret
from app.services.security import set_pin
from app.services.users import create_user_with_wallet
from app.utils import constants as C

log = logging.getLogger("wallet.seed")

DEMO_PIN = "4827"
DEMO_PASSWORD = "Test@12345"


def seed(db: Session) -> None:
    if settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD and not db.query(User).filter(User.email == settings.ADMIN_EMAIL).first():
        db.add(User(name="Platform Admin", email=settings.ADMIN_EMAIL, phone="9000000000",
                    password_hash=hash_secret(settings.ADMIN_PASSWORD), role=C.ADMIN))
        db.commit()
        log.info("Admin created: %s", settings.ADMIN_EMAIL)

    if settings.SEED_DEMO and not db.query(User).filter(User.email == "user1@wallet.com").first():
        u1 = create_user_with_wallet(db, name="Ravi Kumar", email="user1@wallet.com", phone="9111111111",
                                     password=DEMO_PASSWORD, role=C.USER)
        u2 = create_user_with_wallet(db, name="Sita Devi", email="user2@wallet.com", phone="9222222222",
                                     password=DEMO_PASSWORD, role=C.USER)
        create_user_with_wallet(db, name="Madhu Stores", email="merchant1@wallet.com", phone="9333333333",
                                password=DEMO_PASSWORD, role=C.MERCHANT, business_name="Madhu Stores",
                                business_category="Groceries")
        for u in (u1, u2):
            set_pin(db, u, DEMO_PIN)
        u1.wallet.kyc_level = C.KYC_FULL  # demo user 1 is Full KYC, demo user 2 stays Minimum
        db.add(KYC(user_id=u1.id, pan_number="ABCDE1234F", aadhaar_last4="1234", full_name="Ravi Kumar",
                   date_of_birth=date(1995, 5, 20), status="Approved"))
        db.commit()
        log.info("Demo users created")
