# Digital Wallet & Payments System (FastAPI + MySQL)

Paytm/PhonePe-style backend: wallets, double-entry ledger, KYC limits, PIN + OTP, idempotent money APIs,
merchant QR payments with cashback and refunds, bank withdrawals, fraud holds, statements, scheduled jobs and reports.

**Stack:** Python 3.9+, FastAPI, Pydantic v2, SQLAlchemy 2, MySQL, Alembic, JWT (PyJWT), Passlib (bcrypt),
qrcode, ReportLab + csv, APScheduler, BackgroundTasks + smtplib, Uvicorn.

## 1. Setup

```bash
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                     # then edit DATABASE_URL, JWT_SECRET_KEY, SMTP_*
mysql -u root -p -e "CREATE DATABASE wallet_db CHARACTER SET utf8mb4;"
alembic upgrade head                                     # creates ALL tables (create_all is never used)
uvicorn app.main:app --reload
```

Swagger UI: http://127.0.0.1:8000/docs (click **Authorize**, enter the account **email** as the username).

Docker alternative: `docker compose up --build` (MySQL + API; migrations run automatically).

### Alembic commands

| Task | Command |
|---|---|
| Apply all migrations | `alembic upgrade head` |
| Roll back one step | `alembic downgrade -1` |
| New migration after changing models | `alembic revision --autogenerate -m "message"` |
| Show current / history | `alembic current` / `alembic history` |
| Detect model/DB drift | `alembic check` |

### SMTP setup

Fill `SMTP_HOST`, `SMTP_PORT` (587, STARTTLS), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` in `.env`.
Gmail: enable 2-step verification and create an **App Password**. Mailtrap also works.
If `SMTP_HOST` is empty, emails are only written to the log, so the app still runs.
Emails are sent through `BackgroundTasks`, so APIs respond immediately. A failed email is logged and never fails a money action.

`OTP_DEV_ECHO=true` also returns the OTP as `dev_otp` in the `/otp/send` response (SMS stand-in for Swagger testing). **Turn it off in production.**

## 2. Test credentials

Created at startup when `ADMIN_EMAIL`/`ADMIN_PASSWORD` are set and `SEED_DEMO=true` (see `.env.example`).

| Role | Email | Phone | Password | PIN | Notes |
|---|---|---|---|---|---|
| Admin | `admin@wallet.com` | 9000000000 | `Admin@12345` | - | from `ADMIN_EMAIL` / `ADMIN_PASSWORD` |
| User 1 | `user1@wallet.com` | 9111111111 | `Test@12345` | `4827` | Full KYC |
| User 2 | `user2@wallet.com` | 9222222222 | `Test@12345` | `4827` | Minimum KYC |
| Merchant | `merchant1@wallet.com` | 9333333333 | `Test@12345` | not set | "Madhu Stores", code `MER-10001` |

## 3. Quick tour (Swagger)

1. Login as user1, then `POST /wallet/add-money` with header `Idempotency-Key: <any uuid>`.
2. Send the same request again: same response plus header `Idempotent-Replayed: true`. Same key with a changed body gives `422`.
3. `POST /transfers` to 9222222222 (PIN 4827). Above Rs 5,000 call `POST /otp/send` (purpose `High Value Transfer`) first.
4. `POST /payments/merchant` with `MER-10001`: the merchant receives the amount minus 1%, the user gets 2% cashback after 24h (max Rs 50).
5. Admin: `GET /reports/reconciliation` runs the ledger check on demand. Users: `GET /statements?month=2026-11&format=pdf` or `csv`.

## 4. Tests

```bash
pytest -q    # 21 tests on SQLite: transfers, idempotency, limits, PIN lock, OTP, holds, refunds,
             # cashback, withdrawals, statements, and the ledger always balancing
```

## 5. Ledger design

Every rupee is a pair (or more) of **ledger entries**. Account balance = credits - debits, and total debits always equal total credits.
`post()` in `app/services/ledger.py` is the only code that writes entries. It checks debits == credits, locks the accounts
(ordered by id), updates balances and writes the entries inside the caller's single DB transaction.
Entries are append-only; corrections are new `Reversal` / `Withdrawal Reversal` transactions.

Accounts: one per wallet (User Wallet / Merchant Wallet) plus the system accounts **Bank Settlement, Cashback Pool, Fees,
Withdrawal Clearing, Hold** (created at startup if missing). `wallets.balance` is a quick copy updated in the same transaction;
the daily reconciliation job proves `wallet.balance == sum(ledger entries)`.

**Example: a user pays Rs 1,000 to a merchant (1% fee)**

| Entry | Account | Type | Amount | Balance after |
|---|---|---|---|---|
| 1 | User wallet (WAL-1000001) | Debit | 1,000.00 | 4,000.00 |
| 2 | Merchant wallet (WAL-1000003) | Credit | 990.00 | 990.00 |
| 3 | Fees (system) | Credit | 10.00 | 10.00 |

Debits 1,000.00 = Credits 990.00 + 10.00. A pending cashback row (Rs 20) is also created; 24 hours later the cashback job posts
`Cashback Pool` Debit 20 / `User wallet` Credit 20.

Other flows:
- Add Money: Bank Settlement Dr / wallet Cr.
- Transfer: sender Dr / receiver Cr. If flagged: sender Dr / **Hold** Cr, then Hold Dr / receiver Cr on approve, or Hold Dr / sender Cr (a Reversal) on reject.
- Withdrawal: wallet Dr / Withdrawal Clearing Cr (Pending), then Clearing Dr / Bank Settlement Cr (Completed), or Clearing Dr / wallet Cr (Withdrawal Reversal).

**Concurrency:** wallets are locked with `SELECT ... FOR UPDATE` in ascending id order (`lock_wallets`), then system accounts in id order,
so opposite transfers can never deadlock.
**Idempotency:** `(user_id, endpoint, key)` is unique; the saved response is committed in the same DB transaction as the money movement; keys expire after 24 hours (cleanup job).

## 6. Scheduled jobs

APScheduler, started with the app (disable with `RUN_SCHEDULER=false`):

- Cashback: hourly
- Withdrawals: every 5 minutes (Pending for more than 10 minutes)
- Request expiry: hourly
- Cleanup: daily 03:00 UTC
- Reconciliation: daily 02:00 UTC (a mismatch is logged and emailed to Admin users)

## 7. Assumptions

1. Times are stored and compared in UTC; "today" and "this month" limits use UTC calendar boundaries.
2. A **frozen wallet can receive** money (Level 3). Level 8's "receiver must have an Active wallet" is read as "must have a wallet". To block frozen receivers, change one check in `execute_transfer`.
3. Merchants are users with role `Merchant`. Their wallet has no balance cap, uses Full-level daily/monthly send limits, and can withdraw without KYC approval. Only role `User` can add money, send, request money and pay merchants.
4. Only successful (2xx) responses are stored for idempotency. If a request fails (wrong PIN, insufficient balance) the key is released so the client can retry. An Add Money with `simulate_status=Failed` is a normal 200 response and is stored.
5. `/otp/verify` checks an OTP without consuming it; the action that needs it (transfer, add bank account, PIN reset) consumes it. The OTP purpose must match.
6. Requesting an OTP within 60 seconds of the previous one returns `429`. A locked PIN returns `423`. Inactive users get `403` at login.
7. Refunds: the 1% platform fee is **not** returned (the merchant bears the whole refund). The refund credit is not blocked by the user's KYC balance cap. A partial refund shrinks pending cashback proportionally; a full refund cancels it.
8. Rejecting an On Hold transfer creates a `Reversal` transaction and marks the original `Reversed`. Approving adds the Hold -> receiver entries to the original transaction. Reversed items stop counting toward limits.
9. "More than 5 transfers in 1 minute" means the 6th transfer within a rolling minute is flagged.
10. Deleting a bank account is a soft delete (withdrawal history stays valid) and is blocked while a withdrawal is Pending. The full account number is stored (needed for the `0000` failure simulation) but only returned masked.
11. Simple PINs rejected: all same digit, ascending/descending runs (1234, 4321) and repeated pairs (1212).
12. The PDF uses "Rs." because ReportLab's built-in fonts have no rupee glyph.
13. Bonus items not implemented: WebSockets, scheduled payments, bill payments, Razorpay, password-protected PDFs. Pytest and Docker are included.
14. List APIs return `{total, skip, limit, items}`. Money values are returned as strings (for example `"2500.50"`) to avoid float rounding.

## 8. Submission checklist

- Alembic migration: `alembic/versions/0001_initial_schema.py`
- Screenshots to capture: Swagger calls, a repeated request with the same `Idempotency-Key` (note `Idempotent-Replayed: true`),
  a PDF and CSV statement from `statements/` (git-ignored), and the `GET /reports/reconciliation` result.
#   D i g i t a l - W a l l e t - P a y m e n t - S y s t e m  
 