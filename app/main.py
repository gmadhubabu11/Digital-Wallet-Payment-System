import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import SessionLocal
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.routers import admin, auth, banking, history, money, reports, requests, wallet
from app.services.ledger import ensure_system_accounts
from app.utils.seed import seed

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    with SessionLocal() as db:
        ensure_system_accounts(db)  # system accounts are created at startup if missing
        seed(db)
    if settings.RUN_SCHEDULER:
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Digital Wallet & Payments System", version="1.0.0",
              description="Double-entry ledger wallet: P2P, merchant QR payments, cashback, refunds, withdrawals, KYC, fraud holds.",
              lifespan=lifespan)

for r in (auth.router, wallet.router, admin.router, money.router, requests.router, banking.router, history.router, reports.router):
    app.include_router(r)


@app.get("/", tags=["Health"])
def health():
    return {"status": "ok", "service": "wallet-payments-system"}
