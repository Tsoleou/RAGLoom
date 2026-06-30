"""In-process failed-attempt limiter for sensitive unauthenticated endpoints.

The booth is a single process, so a plain in-memory dict keyed by client IP is
enough — no Redis/sidecar. Used to throttle online brute-force and scrypt-CPU
abuse of ``POST /api/kb/unlock`` (the one expensive endpoint reachable without a
credential, by design, so the kiosk can boot).

The clock is injectable so tests can drive lockout windows without sleeping.
"""

import time as _time


class FailedAttemptLimiter:
    """Per-key failed-attempt counter with exponential-backoff lockout.

    Allow until ``threshold`` consecutive failures accrue for a key; from then on
    each further attempt is locked out for an exponentially growing window
    (``base_lockout`` doubling per extra failure, capped at ``max_lockout``).
    A success clears the key. Callers check :meth:`retry_after` BEFORE doing the
    expensive work, so a locked-out request costs nothing (no scrypt run).
    """

    def __init__(
        self,
        *,
        threshold: int = 5,
        base_lockout: float = 5.0,
        max_lockout: float = 300.0,
        time_fn=_time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._base = base_lockout
        self._max = max_lockout
        self._now = time_fn
        # key -> [consecutive_failures, locked_until_monotonic]
        self._state: dict[str, list] = {}

    def retry_after(self, key: str) -> float:
        """Seconds the key must wait before another attempt, or 0.0 if allowed."""
        st = self._state.get(key)
        if not st:
            return 0.0
        remaining = st[1] - self._now()
        return remaining if remaining > 0 else 0.0

    def record_failure(self, key: str) -> None:
        """Count a failed attempt; arm/extend the lockout once over threshold."""
        st = self._state.setdefault(key, [0, 0.0])
        st[0] += 1
        if st[0] >= self._threshold:
            over = st[0] - self._threshold  # 0 on the first lockout, then grows
            lockout = min(self._base * (2 ** over), self._max)
            st[1] = self._now() + lockout

    def record_success(self, key: str) -> None:
        """Clear all state for a key (successful auth resets the counter)."""
        self._state.pop(key, None)
