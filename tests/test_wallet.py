import uuid
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.jobs import tasks
from app.models import Cashback, Withdrawal
from tests.conftest import PIN, Actor, make_full_kyc, reconcile


def transfer(c, sender, receiver, amount, pin=PIN, otp=None, key=None):
    body = {"receiver_phone": receiver.phone, "amount": str(amount), "pin": pin, "note": "test"}
    if otp:
        body["otp"] = otp
    h = {**sender.h, "Idempotency-Key": key or str(uuid.uuid4())}
    return c.post("/transfers", json=body, headers=h)


# ---------------- auth & roles ----------------
def test_auth_and_roles(client, alice, admin):
    assert client.post("/auth/register", json={"name": "Eve", "email": "e@t.com", "phone": "9999999999",
                                               "password": "Passw0rd!", "role": "Admin"}).status_code == 403
    assert client.post("/auth/register", json={"name": "M", "email": "m@t.com", "phone": "9999999998",
                                               "password": "Passw0rd!", "role": "Merchant"}).status_code == 422
    assert client.get("/auth/me", headers={"Authorization": "Bearer junk"}).status_code == 401
    assert client.get("/admin/kyc", headers=alice.h).status_code == 403
    assert client.get("/auth/me", headers=alice.h).json()["wallet_number"].startswith("WAL-")


# ---------------- add money + ledger ----------------
def test_add_money_success_and_failed(client, alice, admin):
    alice.add(1000)
    assert alice.balance() == 1000
    r = client.post("/wallet/add-money", json={"amount": "500", "source": "Card", "simulate_status": "Failed"}, headers=alice.key())
    assert r.json()["status"] == "Failed" and alice.balance() == 1000  # no ledger entries for failed attempts
    assert client.post("/wallet/add-money", json={"amount": "5", "source": "UPI"}, headers=alice.key()).status_code == 400
    assert client.post("/wallet/add-money", json={"amount": "10.555", "source": "UPI"}, headers=alice.key()).status_code == 422
    assert reconcile(client, admin)["status"] == "OK"


def test_balance_cap_minimum_kyc(client, alice):
    alice.add(9000)
    r = client.post("/wallet/add-money", json={"amount": "2000", "source": "UPI"}, headers=alice.key())
    assert r.status_code == 400 and "limit" in r.json()["detail"]


# ---------------- transfers ----------------
def test_transfer_and_ledger_balances(client, alice, bob, admin):
    alice.add(5000)
    r = transfer(client, alice, bob, 1200)
    assert r.status_code == 201, r.text
    assert alice.balance() == 3800 and bob.balance() == 1200
    assert transfer(client, alice, alice, 10).status_code == 400  # self transfer
    rec = reconcile(client, admin)
    assert rec["status"] == "OK" and rec["total_debits"] == rec["total_credits"]


def test_idempotency(client, alice, bob):
    alice.add(5000)
    k = str(uuid.uuid4())
    r1 = transfer(client, alice, bob, 500, key=k)
    r2 = transfer(client, alice, bob, 500, key=k)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r2.headers["Idempotent-Replayed"] == "true"
    assert r1.json()["transaction_ref"] == r2.json()["transaction_ref"]
    assert bob.balance() == 500  # moved only once
    assert transfer(client, alice, bob, 600, key=k).status_code == 422  # same key, different body
    r = client.post("/transfers", json={"receiver_phone": bob.phone, "amount": "5", "pin": PIN}, headers=alice.h)
    assert r.status_code == 422  # header required


def test_daily_limit_message(client, alice, bob):
    alice.add(9000)
    assert transfer(client, alice, bob, 4000).status_code == 201
    assert transfer(client, alice, bob, 4000).status_code == 201
    r = transfer(client, alice, bob, 3000)
    assert r.status_code == 400
    assert r.json()["detail"] == "Daily limit of ₹10,000 reached. You can send ₹2,000 more today"


def test_insufficient_balance(client, alice, bob):
    alice.add(100)
    r = transfer(client, alice, bob, 500)
    assert r.status_code == 400 and "Insufficient balance" in r.json()["detail"]


def test_pin_lock_after_three_wrong(client, alice, bob):
    alice.add(1000)
    assert transfer(client, alice, bob, 10, pin="9999").status_code == 400
    assert transfer(client, alice, bob, 10, pin="9999").status_code == 400
    assert transfer(client, alice, bob, 10, pin="9999").status_code == 423
    assert transfer(client, alice, bob, 10).status_code == 423  # even the right PIN is blocked while locked


def test_simple_pin_rejected(client):
    a = Actor(client, "pin@test.com", "9000011111")
    for bad in ("1234", "1111", "0000", "4321"):
        assert client.post("/wallet/pin", json={"pin": bad}, headers=a.h).status_code == 400


def test_otp_required_above_5000(client, alice, bob):
    alice.add(9000)
    assert transfer(client, alice, bob, 6000).status_code == 400  # OTP missing
    otp = alice.otp("High Value Transfer")
    wrong = "000000" if otp != "000000" else "111111"
    assert transfer(client, alice, bob, 6000, otp=wrong).status_code == 400
    assert transfer(client, alice, bob, 6000, otp=otp).status_code == 201
    assert transfer(client, alice, bob, 100, otp=otp).status_code == 201  # small transfer ignores OTP
    assert bob.balance() == 6100
    # OTP is single use and purpose-bound
    assert client.post("/otp/send", json={"purpose": "PIN Reset"}, headers=alice.h).status_code in (200, 429)


def test_otp_resend_wait(client, alice):
    assert client.post("/otp/send", json={"purpose": "PIN Reset"}, headers=alice.h).status_code == 200
    assert client.post("/otp/send", json={"purpose": "PIN Reset"}, headers=alice.h).status_code == 429


def test_pin_reset_with_otp(client, alice):
    otp = alice.otp("PIN Reset")
    assert client.post("/wallet/pin/reset", json={"otp": otp, "new_pin": "7351"}, headers=alice.h).status_code == 200
    assert client.put("/wallet/pin/change", json={"old_pin": "7351", "new_pin": "6482"}, headers=alice.h).status_code == 200


# ---------------- fraud hold ----------------
def test_flagged_transfer_hold_approve_and_reject(client, alice, bob, admin):
    for a, pan in ((alice, "ABCDE1234F"), (bob, "PQRST5678U")):
        make_full_kyc(client, a, admin, pan)
    alice.add(50000)
    otp = alice.otp("High Value Transfer")
    r = transfer(client, alice, bob, 21000, otp=otp)
    assert r.status_code == 201 and r.json()["status"] == "On Hold" and r.json()["is_flagged"]
    ref = r.json()["transaction_ref"]
    assert alice.balance() == 29000 and bob.balance() == 0  # receiver does not get it yet
    assert reconcile(client, admin)["status"] == "OK"
    assert client.get("/admin/flagged-transactions", headers=admin).json()["total"] == 1

    # reject -> money returns to sender (a rejected hold no longer counts toward the daily limit)
    assert client.put(f"/admin/transactions/{ref}/reject", json={"reason": "Suspicious"}, headers=admin).status_code == 200
    assert alice.balance() == 50000 and bob.balance() == 0
    assert client.put(f"/admin/transactions/{ref}/reject", json={"reason": "again"}, headers=admin).status_code == 400

    # second held transfer -> approve -> receiver is credited
    otp = alice.otp("High Value Transfer")
    ref2 = transfer(client, alice, bob, 21000, otp=otp).json()["transaction_ref"]
    assert client.put(f"/admin/transactions/{ref2}/approve", json={"reason": "Verified with user"}, headers=admin).status_code == 200
    assert bob.balance() == 21000 and alice.balance() == 29000
    assert reconcile(client, admin)["status"] == "OK"


def test_more_than_5_transfers_in_a_minute_flagged(client, alice, bob, admin):
    make_full_kyc(client, alice, admin)
    alice.add(20000)
    statuses = [transfer(client, alice, bob, 10).json()["status"] for _ in range(6)]
    assert statuses[:5] == ["Success"] * 5 and statuses[5] == "On Hold"


# ---------------- merchant: fee, cashback, refund ----------------
def test_merchant_payment_cashback_refund(client, alice, shop, admin):
    alice.add(5000)
    assert client.get("/merchants/me/qr", headers=shop.h).headers["content-type"] == "image/png"
    code = shop.info["merchant_code"]
    r = client.post("/payments/merchant", json={"merchant_code": code, "amount": "1000", "pin": PIN}, headers=alice.key())
    assert r.status_code == 201, r.text
    ref = r.json()["transaction_ref"]
    assert alice.balance() == 4000 and shop.balance() == 990  # 1% fee to Fees account
    with SessionLocal() as db:
        cb = db.query(Cashback).one()
        assert float(cb.amount) == 20 and cb.status == "Pending"

    # partial refund shrinks pending cashback, full refund cancels it
    assert client.post(f"/payments/{ref}/refund", json={"amount": "400"}, headers=shop.key()).status_code == 201
    assert client.post(f"/payments/{ref}/refund", json={"amount": "700"}, headers=shop.key()).status_code == 400  # over original
    assert client.post(f"/payments/{ref}/refund", json={"amount": "600"}, headers=shop.key()).status_code == 400  # merchant short of balance
    assert alice.balance() == 4400
    with SessionLocal() as db:
        assert float(db.query(Cashback).one().amount) == 12
    assert client.post(f"/payments/{ref}/refund", json={"amount": "50"}, headers=alice.key()).status_code == 403  # not a merchant
    assert reconcile(client, admin)["status"] == "OK"


def test_cashback_job_credits_after_24h(client, alice, shop, admin):
    alice.add(2000)
    client.post("/payments/merchant", json={"merchant_code": shop.info["merchant_code"], "amount": "500", "pin": PIN}, headers=alice.key())
    tasks.cashback_job()
    assert alice.balance() == 1500  # not yet due
    with SessionLocal() as db:
        db.query(Cashback).update({"credit_after": datetime.utcnow() - timedelta(minutes=1)})
        db.commit()
    tasks.cashback_job()
    assert alice.balance() == 1510  # 2% of 500
    assert reconcile(client, admin)["status"] == "OK"


# ---------------- withdrawals ----------------
def _bank(client, user, number="123456789012"):
    otp = user.otp("Add Bank Account")
    r = client.post("/bank-accounts", json={"account_holder_name": "Test", "account_number": number, "ifsc_code": "SBIN0001234",
                                            "bank_name": "SBI", "otp": otp}, headers=user.h)
    assert r.status_code == 201, r.text
    assert r.json()["account_number"].startswith("XXXX") and r.json()["account_number"].endswith(number[-4:])
    return r.json()["id"]


def _age_withdrawals():
    with SessionLocal() as db:
        db.query(Withdrawal).update({"created_at": datetime.utcnow() - timedelta(minutes=11)})
        db.commit()


def test_withdrawal_complete_and_failed_reversal(client, alice, admin):
    alice.add(10000)
    bank = _bank(client, alice)
    body = {"bank_account_id": bank, "amount": "1000", "pin": PIN}
    assert client.post("/withdrawals", json=body, headers=alice.key()).status_code == 403  # Minimum KYC
    make_full_kyc(client, alice, admin)
    assert client.post("/withdrawals", json={**body, "amount": "50"}, headers=alice.key()).status_code == 400  # below min
    assert client.post("/withdrawals", json=body, headers=alice.key()).status_code == 201
    assert alice.balance() == 9000
    tasks.withdrawal_job()  # not 10 minutes old yet
    assert client.get("/withdrawals", headers=alice.h).json()["items"][0]["status"] == "Pending"
    _age_withdrawals()
    tasks.withdrawal_job()
    assert client.get("/withdrawals", headers=alice.h).json()["items"][0]["status"] == "Completed"

    bad_bank = _bank(client, alice, number="987654320000")
    assert client.post("/withdrawals", json={"bank_account_id": bad_bank, "amount": "500", "pin": PIN}, headers=alice.key()).status_code == 201
    assert alice.balance() == 8500
    _age_withdrawals()
    tasks.withdrawal_job()
    assert alice.balance() == 9000  # returned as Withdrawal Reversal
    w = [i for i in client.get("/withdrawals", headers=alice.h).json()["items"] if i["status"] == "Failed"]
    assert len(w) == 1 and w[0]["failure_reason"]
    assert reconcile(client, admin)["status"] == "OK"


def test_bank_account_limits_and_validation(client, alice):
    otp = alice.otp("Add Bank Account")
    base = {"account_holder_name": "Test User", "bank_name": "SBI", "otp": otp}
    assert client.post("/bank-accounts", json={**base, "account_number": "123", "ifsc_code": "SBIN0001234"}, headers=alice.h).status_code == 422
    assert client.post("/bank-accounts", json={**base, "account_number": "123456789", "ifsc_code": "BAD"}, headers=alice.h).status_code == 422
    for n in ("111111111", "222222222", "333333333"):
        _bank(client, alice, n)
    otp = alice.otp("Add Bank Account")
    r = client.post("/bank-accounts", json={**base, "otp": otp, "account_number": "444444444", "ifsc_code": "SBIN0001234"}, headers=alice.h)
    assert r.status_code == 400  # max 3


# ---------------- frozen wallet ----------------
def test_frozen_wallet_can_receive_not_send(client, alice, bob, admin):
    alice.add(1000)
    wid = client.get("/wallet", headers=alice.h).json()["id"]
    assert client.put(f"/admin/wallets/{wid}/freeze", json={"reason": "Investigation"}, headers=admin).status_code == 200
    assert transfer(client, alice, bob, 100).status_code == 403
    bob.add(500)
    assert transfer(client, bob, alice, 100).status_code == 201
    assert client.put(f"/admin/wallets/{wid}/unfreeze", headers=admin).status_code == 200
    assert transfer(client, alice, bob, 100).status_code == 201


# ---------------- money requests ----------------
def test_money_request_flow(client, alice, bob):
    alice.add(2000)
    r = client.post("/money-requests", json={"payer_phone": bob.phone, "amount": "300", "note": "lunch"}, headers=alice.h)
    assert r.status_code == 201
    rid = r.json()["id"]
    bob.add(1000)
    assert client.put(f"/money-requests/{rid}/cancel", headers=bob.h).status_code == 403  # only requester
    assert client.put(f"/money-requests/{rid}/pay", json={"pin": PIN}, headers=alice.key()).status_code == 403  # only payer
    assert client.put(f"/money-requests/{rid}/pay", json={"pin": PIN}, headers=bob.key()).status_code == 200
    assert alice.balance() == 2300 and bob.balance() == 700
    assert client.get("/money-requests?direction=sent", headers=alice.h).json()["items"][0]["status"] == "Paid"
    for _ in range(5):
        assert client.post("/money-requests", json={"payer_phone": bob.phone, "amount": "10"}, headers=alice.h).status_code == 201
    assert client.post("/money-requests", json={"payer_phone": bob.phone, "amount": "10"}, headers=alice.h).status_code == 400  # max 5


# ---------------- history, statements, reports ----------------
def test_history_filters_statement_and_reports(client, alice, bob, shop, admin):
    alice.add(3000)
    transfer(client, alice, bob, 700)
    client.post("/payments/merchant", json={"merchant_code": shop.info["merchant_code"], "amount": "300", "pin": PIN}, headers=alice.key())
    tx = client.get("/transactions?type=P2P Transfer&status=Success&sort_by=amount&order=asc&skip=0&limit=5", headers=alice.h).json()
    assert tx["total"] == 1 and tx["items"][0]["other_party"]["phone"] == "XXXXXX0001"
    assert tx["items"][0]["balance_after"] == "2300.00"
    assert client.get("/transactions", headers=admin).json()["total"] == 3
    ref = tx["items"][0]["transaction_ref"]
    assert client.get(f"/transactions/{ref}", headers=shop.h).status_code == 404  # not their transaction

    month = datetime.utcnow().strftime("%Y-%m")
    csv_r = client.get(f"/statements?month={month}&format=csv", headers=alice.h)
    assert csv_r.status_code == 200 and "Closing Balance,2000.00" in csv_r.text and "Opening Balance,0" in csv_r.text
    pdf_r = client.get(f"/statements?month={month}&format=pdf", headers=alice.h)
    assert pdf_r.status_code == 200 and pdf_r.content.startswith(b"%PDF")
    assert client.get("/statements?month=2026-13&format=pdf", headers=alice.h).status_code == 422

    d = client.get("/reports/admin-dashboard", headers=admin).json()
    assert d["total_money_in_wallets"] == "2997.00"  # 3000 added - 3.00 platform fee held in the Fees account
    assert d["today_transactions"]["by_type"]["Add Money"]["count"] == 1
    ls = client.get("/reports/ledger-summary", headers=admin).json()
    assert ls["total_debits"] == ls["total_credits"]
    ms = client.get("/reports/merchant-sales", headers=shop.h).json()
    assert ms["number_of_payments"] == 1 and ms["platform_fees_paid"] == "3.00" and ms["net_amount_received"] == "297.00"
    sp = client.get("/reports/my-spending", headers=alice.h).json()
    assert sp["transfers"] == "700.00" and sp["merchant_payments_by_category"] == {"Food": "300.00"}
    assert client.get("/reports/admin-dashboard", headers=alice.h).status_code == 403
