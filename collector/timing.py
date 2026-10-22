"""Shared monotonic budget for one whole collection, across all backends."""

import math
import time

DEFAULT_MAX_DURATION_SECONDS = 150 * 60


class CollectionDeadline(TimeoutError):
    """The shared collection budget expired; no further game reads may start."""


def positive_duration(value):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Duration must be positive and finite")
    return value


def remaining_seconds(deadline, clock=time.monotonic):
    if deadline is None:
        return None
    remaining = deadline - clock()
    if remaining <= 0:
        raise CollectionDeadline("Collection deadline reached")
    return remaining
