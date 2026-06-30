"""Unit tests for the unlock-endpoint failed-attempt limiter (S2).

Deterministic, no sleeps — the limiter takes an injectable clock."""

from api.rate_limit import FailedAttemptLimiter


def test_lockout_after_threshold_then_expires_and_escalates():
    clock = {"t": 1000.0}
    lim = FailedAttemptLimiter(
        threshold=3, base_lockout=5.0, max_lockout=100.0, time_fn=lambda: clock["t"]
    )

    # below threshold → still allowed
    lim.record_failure("ip")
    lim.record_failure("ip")
    assert lim.retry_after("ip") == 0.0

    # hitting the threshold arms a base-length lockout
    lim.record_failure("ip")
    assert lim.retry_after("ip") == 5.0

    # lockout expires once the window passes
    clock["t"] += 5.0
    assert lim.retry_after("ip") == 0.0

    # the next failure escalates exponentially (over=1 → base*2)
    lim.record_failure("ip")
    assert lim.retry_after("ip") == 10.0

    # backoff is capped at max_lockout no matter how many failures pile up
    for _ in range(20):
        lim.record_failure("ip")
    assert 0 < lim.retry_after("ip") <= 100.0

    # a success wipes the key clean
    lim.record_success("ip")
    assert lim.retry_after("ip") == 0.0


def test_keys_are_independent():
    clock = {"t": 0.0}
    lim = FailedAttemptLimiter(threshold=1, base_lockout=5.0, time_fn=lambda: clock["t"])
    lim.record_failure("attacker")        # locks just this key
    assert lim.retry_after("attacker") == 5.0
    assert lim.retry_after("operator") == 0.0  # a different IP is unaffected
