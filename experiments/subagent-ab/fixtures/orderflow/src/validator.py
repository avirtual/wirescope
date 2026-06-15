"""Order validation stage."""
from dataclasses import dataclass

@dataclass
class Order:
    id: str
    items: list
    currency: str

class ValidationError(Exception):
    pass

def validate_order(order: Order) -> bool:
    if not order.items:
        raise ValidationError("empty order")
    return True

def normalize_currency(code: str) -> str:
    return code.upper().strip()

def _check_item(item: dict) -> bool:
    return "sku" in item and item.get("qty", 0) > 0
