from decimal import Decimal, ROUND_HALF_UP

TWO = Decimal("0.01")


def D(value) -> Decimal:
    return Decimal(str(value))


def q(value) -> Decimal:
    return D(value).quantize(TWO, rounding=ROUND_HALF_UP)


def inr(value) -> str:
    """Format as Indian rupees, e.g. 200000 -> ₹2,00,000"""
    v = q(value)
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    sign = "-" if v < 0 else ""
    return f"{sign}₹{whole}" + ("" if frac == "00" else f".{frac}")


def mask_phone(phone: str) -> str:
    return f"XXXXXX{phone[-4:]}" if phone else ""


def mask_account(number: str) -> str:
    return "X" * max(len(number) - 4, 0) + number[-4:]
