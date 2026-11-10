import secrets
import string
from datetime import datetime


def transaction_ref() -> str:
    suffix = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f"TXN-{datetime.utcnow():%Y%m%d}-{suffix}"


def wallet_number(wallet_id: int) -> str:
    return f"WAL-{1000000 + wallet_id}"


def merchant_code(merchant_id: int) -> str:
    return f"MER-{10000 + merchant_id}"
