import os
import uuid
from datetime import datetime, timedelta

os.environ["DATABASE_URL"] = "sqlite:///./test_wallet.db"
os.environ["RUN_SCHEDULER"] = "false"
os.environ["ADMIN_EMAIL"] = "admin@wallet.com"
os.environ["ADMIN_PASSWORD"] = "Admin@12345"
os.environ["OTP_DEV_ECHO"] = "true"
os.environ["SMTP_HOST"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import OTP  # noqa: E402
from app.services.ledger import ensure_system_accounts  # noqa: E402
from app.utils.seed import seed  # noqa: E402

PIN = "4827"


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)  # tests only; the app itself uses Alembic
    with SessionLocal() as db:
        ensure_system_accounts(db)
        seed(db)
    yield


@pytest.fixture()
def client():
    return TestClient(app)


class Actor:
    def __init__(self, client, email, phone, role="User", **extra):
        self.c, self.email, self.phone, self.role = client, email, phone, role
        r = client.post("/auth/register", json={"name": email.split("@")[0].title(), "email": email, "phone": phone,
                                                "password": "Passw0rd!", "role": role, **extra})
        assert r.status_code == 201, r.text
        self.info = r.json()
        r = client.post("/auth/login", data={"username": email, "password": "Passw0rd!"})
        assert r.status_code == 200, r.text
        self.h = {"Authorization": f"Bearer {r.json()['access_token']}"}

    def key(self):
        return {**self.h, "Idempotency-Key": str(uuid.uuid4())}

    def set_pin(self):
        assert self.c.post("/wallet/pin", json={"pin": PIN}, headers=self.h).status_code == 201

    def add(self, amount):
        r = self.c.post("/wallet/add-money", json={"amount": str(amount), "source": "UPI"}, headers=self.key())
        assert r.status_code == 201, r.text
        return r.json()

    def balance(self):
        return float(self.c.get("/wallet", headers=self.h).json()["balance"])

    def otp(self, purpose):
        with SessionLocal() as db:  # skip the 60s resend wait
            db.query(OTP).update({"created_at": datetime.utcnow() - timedelta(minutes=2)})
            db.commit()
        r = self.c.post("/otp/send", json={"purpose": purpose}, headers=self.h)
        assert r.status_code == 200, r.text
        return r.json()["dev_otp"]


@pytest.fixture()
def admin(client):
    r = client.post("/auth/login", data={"username": "admin@wallet.com", "password": "Admin@12345"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_full_kyc(client, actor, admin_h, pan="ABCDE1234F"):
    r = client.post("/kyc", json={"pan_number": pan, "aadhaar_last4": "1234", "full_name": "Test User",
                                  "date_of_birth": "1995-01-01"}, headers=actor.h)
    assert r.status_code == 201, r.text
    assert client.put(f"/admin/kyc/{r.json()['id']}/approve", headers=admin_h).status_code == 200


@pytest.fixture()
def alice(client):
    a = Actor(client, "alice@test.com", "9876543210")
    a.set_pin()
    return a


@pytest.fixture()
def bob(client):
    b = Actor(client, "bob@test.com", "9876500001")
    b.set_pin()
    return b


@pytest.fixture()
def shop(client):
    return Actor(client, "shop@test.com", "9876500002", role="Merchant", business_name="Chai Point", business_category="Food")


def reconcile(client, admin_h):
    r = client.get("/reports/reconciliation", headers=admin_h)
    assert r.status_code == 200
    return r.json()
