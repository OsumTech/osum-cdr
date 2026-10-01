"""Customer display precision, independent of accounting precision."""
from decimal import Decimal, ROUND_HALF_UP


def customer_amount(value):
    rounded = Decimal(str(value)).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
    return format(rounded if rounded else abs(rounded), ',.4f')
