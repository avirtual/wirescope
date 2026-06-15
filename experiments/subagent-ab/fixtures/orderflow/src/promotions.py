"""Promotions engine — order-dependent stacking."""

class Promo:
    def __init__(self, code: str, pct: int):
        self.code = code
        self.pct = pct
    def apply(self, cents: int) -> int:
        return cents - cents * self.pct // 100

def stack_promos(cents: int, promos: list) -> int:
    for p in promos:
        cents = p.apply(cents)
    return cents

def best_single(cents: int, promos: list) -> int:
    return min((p.apply(cents) for p in promos), default=cents)
