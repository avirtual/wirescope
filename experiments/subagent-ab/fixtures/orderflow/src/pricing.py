"""Pricing stage — the hot path."""

def price_line(unit_cents: int, qty: int) -> int:
    return unit_cents * qty

def apply_tax(subtotal_cents: int, rate_bps: int) -> int:
    return subtotal_cents + subtotal_cents * rate_bps // 10000

class PriceCache:
    def __init__(self):
        self._c = {}
    def get(self, sku: str):
        return self._c.get(sku)
    def put(self, sku: str, cents: int):
        self._c[sku] = cents

def total_for(lines: list) -> int:
    return sum(price_line(u, q) for u, q in lines)
