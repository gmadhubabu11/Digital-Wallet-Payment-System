import csv
import os
import re
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.orm import Session

from app.models import Account, LedgerEntry, Transaction, User
from app.utils import constants as C

STATEMENT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "statements")
os.makedirs(STATEMENT_DIR, exist_ok=True)


def month_range(month: str):
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise HTTPException(422, "month must be in YYYY-MM format, e.g. 2026-10")
    y, m = int(month[:4]), int(month[5:])
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), (m % 12) + 1, 1)
    return start, end


def build(db: Session, user: User, month: str) -> dict:
    wallet = user.wallet
    start, end = month_range(month)
    acct = db.query(Account).filter(Account.wallet_id == wallet.id).one()
    prev = (db.query(LedgerEntry).filter(LedgerEntry.account_id == acct.id, LedgerEntry.created_at < start)
            .order_by(LedgerEntry.id.desc()).first())
    opening = prev.balance_after if prev else Decimal("0.00")
    rows = (db.query(LedgerEntry, Transaction).join(Transaction, Transaction.id == LedgerEntry.transaction_id)
            .filter(LedgerEntry.account_id == acct.id, LedgerEntry.created_at >= start, LedgerEntry.created_at < end)
            .order_by(LedgerEntry.id).all())
    credits = sum((e.amount for e, _ in rows if e.entry_type == C.CREDIT), Decimal("0"))
    debits = sum((e.amount for e, _ in rows if e.entry_type == C.DEBIT), Decimal("0"))
    closing = opening + credits - debits
    lines = [{"date": e.created_at.strftime("%Y-%m-%d %H:%M"), "ref": t.transaction_ref, "type": t.type,
              "description": t.description or "", "debit": e.amount if e.entry_type == C.DEBIT else None,
              "credit": e.amount if e.entry_type == C.CREDIT else None, "balance": e.balance_after} for e, t in rows]
    last_day = end - timedelta(days=1)
    period = f"{start:%d %b %Y} - {last_day:%d %b %Y}"
    return {"wallet_number": wallet.wallet_number, "name": user.name, "period": period, "month": month,
            "opening": opening, "credits": credits, "debits": debits, "closing": closing, "lines": lines}


def to_csv(s: dict) -> str:
    path = os.path.join(STATEMENT_DIR, f"{s['wallet_number']}_{s['month']}.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Wallet", s["wallet_number"]])
        w.writerow(["Name", s["name"]])
        w.writerow(["Period", s["period"]])
        w.writerow(["Opening Balance", s["opening"]])
        w.writerow([])
        w.writerow(["Date", "Reference", "Type", "Description", "Debit", "Credit", "Balance"])
        for l in s["lines"]:
            w.writerow([l["date"], l["ref"], l["type"], l["description"], l["debit"] or "", l["credit"] or "", l["balance"]])
        w.writerow([])
        w.writerow(["Total Credits", s["credits"]])
        w.writerow(["Total Debits", s["debits"]])
        w.writerow(["Closing Balance", s["closing"]])
    return path


def to_pdf(s: dict) -> str:
    path = os.path.join(STATEMENT_DIR, f"{s['wallet_number']}_{s['month']}.pdf")
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
    rs = lambda v: f"Rs. {v:,.2f}"  # noqa: E731  (core PDF fonts have no rupee glyph)
    story = [Paragraph("Wallet Statement", styles["Title"]),
             Paragraph(f"<b>Name:</b> {s['name']} &nbsp;&nbsp; <b>Wallet:</b> {s['wallet_number']} &nbsp;&nbsp; "
                       f"<b>Period:</b> {s['period']}", styles["Normal"]), Spacer(1, 10)]
    summary = Table([["Opening Balance", "Total Credits", "Total Debits", "Closing Balance"],
                     [rs(s["opening"]), rs(s["credits"]), rs(s["debits"]), rs(s["closing"])]], hAlign="LEFT")
    summary.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                                 ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                                 ("PADDING", (0, 0), (-1, -1), 6)]))
    story += [summary, Spacer(1, 12)]
    data = [["Date", "Reference", "Type", "Description", "Debit", "Credit", "Balance"]]
    for l in s["lines"]:
        data.append([l["date"], l["ref"], l["type"], (l["description"] or "")[:40],
                     f"{l['debit']:,.2f}" if l["debit"] else "", f"{l['credit']:,.2f}" if l["credit"] else "", f"{l['balance']:,.2f}"])
    if len(data) == 1:
        data.append(["No transactions in this period", "", "", "", "", "", ""])
    t = Table(data, repeatRows=1, colWidths=[90, 120, 100, 210, 70, 70, 80])
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                           ("ALIGN", (4, 0), (-1, -1), "RIGHT"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f5f9")])]))
    story.append(t)
    doc.build(story)
    return path
