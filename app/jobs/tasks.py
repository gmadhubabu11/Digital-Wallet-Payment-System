import logging
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import Cashback, IdempotencyKey, MoneyRequest, OTP, Withdrawal
from app.services.payments import credit_cashback
from app.services.reconciliation import run_reconciliation
from app.services.withdrawals import PROCESS_AFTER, settle_withdrawal

log = logging.getLogger("wallet.jobs")


def _run(name, fn):
    db = SessionLocal()
    try:
        fn(db)
    except Exception:
        db.rollback()
        log.exception("Job %s failed", name)
    finally:
        db.close()


def cashback_job():
    def work(db):
        due = db.query(Cashback).filter(Cashback.status == "Pending", Cashback.credit_after <= datetime.utcnow()).all()
        for cb in due:  # one DB transaction per cashback, so one failure never blocks the rest
            try:
                credit_cashback(db, cb)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("Cashback %s failed", cb.id)
        log.info("Cashback job: credited %s cashbacks", len(due))
    _run("cashback", work)


def withdrawal_job():
    def work(db):
        due = db.query(Withdrawal).filter(Withdrawal.status == "Pending",
                                          Withdrawal.created_at <= datetime.utcnow() - PROCESS_AFTER).all()
        for w in due:
            try:
                settle_withdrawal(db, w)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("Withdrawal %s failed to settle", w.id)
        log.info("Withdrawal job: processed %s withdrawals", len(due))
    _run("withdrawal", work)


def request_expiry_job():
    def work(db):
        n = (db.query(MoneyRequest).filter(MoneyRequest.status == "Pending", MoneyRequest.expires_at <= datetime.utcnow())
             .update({"status": "Expired"}, synchronize_session=False))
        db.commit()
        log.info("Request expiry job: expired %s requests", n)
    _run("request_expiry", work)


def cleanup_job():
    def work(db):
        now = datetime.utcnow()
        a = db.query(OTP).filter(OTP.expires_at < now).delete(synchronize_session=False)
        b = db.query(IdempotencyKey).filter(IdempotencyKey.created_at < now - timedelta(hours=24)).delete(synchronize_session=False)
        db.commit()
        log.info("Cleanup job: deleted %s OTPs, %s idempotency keys", a, b)
    _run("cleanup", work)


def reconciliation_job():
    _run("reconciliation", lambda db: run_reconciliation(db))
