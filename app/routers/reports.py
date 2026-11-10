from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth.security import require_roles
from app.database import get_db
from app.models import User
from app.routers.deps import to_json
from app.services import reports
from app.services.reconciliation import run_reconciliation
from app.services.statements import month_range
from app.utils import constants as C

router = APIRouter(tags=["Reports"])


@router.get("/reports/admin-dashboard")
def admin_dashboard(_: User = Depends(require_roles(C.ADMIN)), db: Session = Depends(get_db)):
    return to_json(reports.admin_dashboard(db))


@router.get("/reports/ledger-summary")
def ledger_summary(from_date: Optional[date] = None, to_date: Optional[date] = None,
                   _: User = Depends(require_roles(C.ADMIN)), db: Session = Depends(get_db)):
    return to_json(reports.ledger_summary(db, from_date, to_date))


@router.get("/reports/reconciliation")
def reconciliation(_: User = Depends(require_roles(C.ADMIN)), db: Session = Depends(get_db)):
    """Runs the same check as the daily reconciliation job, on demand."""
    return to_json(run_reconciliation(db))


@router.get("/reports/merchant-sales")
def merchant_sales(user: User = Depends(require_roles(C.MERCHANT)), db: Session = Depends(get_db)):
    return to_json(reports.merchant_sales(db, user.merchant))


@router.get("/reports/my-spending")
def my_spending(month: Optional[str] = Query(None, description="YYYY-MM, default current month"),
                user: User = Depends(require_roles(C.USER)), db: Session = Depends(get_db)):
    month = month or datetime.utcnow().strftime("%Y-%m")
    month_range(month)  # validates format
    return to_json(reports.my_spending(db, user, month))
