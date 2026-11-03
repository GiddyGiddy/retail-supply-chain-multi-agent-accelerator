MAX_AUTOMATED_ORDER_VALUE = 50_000


def requires_escalated_review(estimated_value: float) -> bool:
    if estimated_value < 0:
        raise ValueError("estimated_value must not be negative")
    return estimated_value >= MAX_AUTOMATED_ORDER_VALUE
