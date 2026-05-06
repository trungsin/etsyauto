"""Retry policy helpers for the Etsy uploader worker."""

# Backoff delays (seconds) for each attempt index: attempt 0 → 5s, 1 → 30s, 2 → 300s
_DELAYS = [5, 30, 300]


def next_retry_delay(attempt: int) -> int:
    """Return the delay in seconds before the next retry for a given attempt index.

    attempt 0 → 5 seconds
    attempt 1 → 30 seconds
    attempt 2+ → 300 seconds (5 minutes, capped)
    """
    if attempt < len(_DELAYS):
        return _DELAYS[attempt]
    return _DELAYS[-1]


def should_retry(attempt: int, max_attempts: int = 3) -> bool:
    """Return True if the job should be retried based on the current attempt count.

    attempt: number of attempts already made (1-based after first failure)
    max_attempts: maximum total attempts allowed (default 3)
    """
    return attempt < max_attempts
