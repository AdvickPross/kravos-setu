"""Resend limits. NHA M1 CRT_ABHA_106, VRFY_ABHA_305, VRFY_ABHA_405.

The clock is injected throughout, so the sixty-second rule is tested in
microseconds. A test that actually waited would be skipped in CI within a
month, and the rule would stop being checked.
"""

from __future__ import annotations

import threading

import pytest

from abha.errors import AbhaFlowError
from abha.otp_policy import MAX_RESENDS, MIN_INTERVAL_SECONDS, ResendPolicy


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def policy(clock) -> ResendPolicy:
    return ResendPolicy(now=lambda: clock[0])


def test_the_limits_are_the_checklists_numbers():
    """Two resends, sixty seconds. Not tuned, not configurable by accident."""
    assert MAX_RESENDS == 2
    assert MIN_INTERVAL_SECONDS == 60.0


def test_first_resend_is_allowed_immediately(policy: ResendPolicy):
    state = policy.check_and_record("txn-1")
    assert state.resends_used == 1
    assert state.resends_remaining == 1


def test_a_second_resend_inside_the_window_is_refused(policy: ResendPolicy, clock):
    policy.check_and_record("txn-1")
    clock[0] += 59
    with pytest.raises(AbhaFlowError) as exc:
        policy.check_and_record("txn-1")
    assert "wait 1s" in str(exc.value)


def test_a_second_resend_after_the_window_is_allowed(policy: ResendPolicy, clock):
    policy.check_and_record("txn-1")
    clock[0] += 60
    assert policy.check_and_record("txn-1").resends_remaining == 0


def test_the_third_resend_is_refused_however_long_you_wait(
    policy: ResendPolicy, clock
):
    """The budget is two. Waiting does not refill it -- the patient needs a
    new transaction, and the message says so."""
    policy.check_and_record("txn-1")
    clock[0] += 60
    policy.check_and_record("txn-1")
    clock[0] += 600
    with pytest.raises(AbhaFlowError) as exc:
        policy.check_and_record("txn-1")
    assert "limit reached" in str(exc.value)
    assert "new verification" in str(exc.value)


def test_transactions_are_counted_separately(policy: ResendPolicy):
    """Keyed by txnId: one OTP conversation with one person. Keying by mobile
    would conflate a patient's two visits."""
    policy.check_and_record("txn-1")
    # txn-2 starts with a full budget even though txn-1 has spent one.
    assert policy.check_and_record("txn-2").resends_used == 1
    assert policy.state_for("txn-1").resends_used == 1


def test_state_is_readable_without_spending_budget(policy: ResendPolicy):
    """The UI polls this to render its countdown; polling must be free."""
    policy.check_and_record("txn-1")
    for _ in range(5):
        state = policy.state_for("txn-1")
    assert state.resends_used == 1


def test_state_counts_down(policy: ResendPolicy, clock):
    policy.check_and_record("txn-1")
    assert policy.state_for("txn-1").seconds_until_allowed == 60.0
    clock[0] += 25
    assert policy.state_for("txn-1").seconds_until_allowed == 35.0
    clock[0] += 35
    assert policy.state_for("txn-1").can_resend


def test_an_untouched_transaction_can_resend_now(policy: ResendPolicy):
    state = policy.state_for("never-seen")
    assert state.can_resend
    assert state.resends_used == 0


def test_exhausted_budget_reports_cannot_resend(policy: ResendPolicy, clock):
    policy.check_and_record("txn-1")
    clock[0] += 60
    policy.check_and_record("txn-1")
    clock[0] += 600
    assert policy.state_for("txn-1").can_resend is False


def test_missing_txn_id_is_refused(policy: ResendPolicy):
    with pytest.raises(AbhaFlowError):
        policy.check_and_record("")


def test_forget_clears_a_finished_conversation(policy: ResendPolicy):
    policy.check_and_record("txn-1")
    policy.forget("txn-1")
    assert policy.state_for("txn-1").resends_used == 0


def test_concurrent_resends_cannot_both_pass(policy: ResendPolicy):
    """The reason check and record are one operation. Split into a `check()`
    then a `record()`, both of these threads would pass the check and both
    would resend -- which is the failure the limit exists to prevent."""
    allowed: list[int] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        barrier.wait()
        try:
            policy.check_and_record("txn-race")
            allowed.append(1)
        except AbhaFlowError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(allowed) == 1, f"{len(allowed)} concurrent resends got through"


def test_stale_transactions_are_swept(clock):
    """Bounded memory: a busy desk must not accumulate txnIds forever."""
    policy = ResendPolicy(now=lambda: clock[0])
    policy.check_and_record("old-txn")
    clock[0] += 1000
    policy.check_and_record("new-txn")
    assert policy.state_for("old-txn").resends_used == 0
