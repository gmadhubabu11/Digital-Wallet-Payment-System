from decimal import Decimal

from fastapi import HTTPException, Query
from fastapi.encoders import jsonable_encoder


class PageParams:
    def __init__(self, skip: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        self.skip, self.limit = skip, limit


def page(items, total: int, p: PageParams) -> dict:
    return {"total": total, "skip": p.skip, "limit": p.limit, "items": items}


def need_wallet(user):
    if user.wallet is None:
        raise HTTPException(404, "Wallet not found")
    return user.wallet


def to_json(obj):
    return jsonable_encoder(obj, custom_encoder={Decimal: lambda d: f"{d:.2f}"})
