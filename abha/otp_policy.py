"""Resend limits. NHA M1 CRT_ABHA_106, VRFY_ABHA_305, VRFY_ABHA_405.

"The system may activate the Resend OTP button maximum 2 times after 60
seconds" is a mandatory M1 item, and it is usually implemented as a disabled
button and a countdown -- which is not an implementation of it at all. A
disabled button is a suggestion: it stops the patient clicking twice and does
nothing about a retry loop, a double-submitted form, or a second browser tab.
ABDM's rate limits apply to the client id, not to the tab, so the cost of
getting this wrong is borne by every other user of the same integration.

So the limit is enforced server-side, here, and the button state becomes a
reflection of this rather than the rule itself. `state_for` exists precisely
so the UI can render the countdown from the same source that enforces it,
instead of keeping a second timer that can disagree with the first.

Keyed by `txnId`, which is the right granularity: it is exactly one OTP
conversation with one person. Keying by mobile number would conflate a
patient's two visits, and keying by session would conflate every patient at
one desk.

In-process and non-persistent, deliberately. The window is 60 seconds and the
budget is two, so a restart forgives at most two OTPs -- against the cost of
a shared store on the request path for state that is stale within a minute.
A multi-worker deployment should move this to Redis; the class is small
enough that swapping `_attempts` for a backing store is the whole change, and
`now` is injected so nothing here reads the clock directly.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable

from abha.errors import AbhaFlowError

# Both from the checklist, not tuned: two resends, sixty seconds apart.
MAX_RESENDS = 2
MIN_INTERVAL_SECONDS = 60.0

# How long a finished conversation stays in memory before it is swept. Longer
# than the OTP is valid for, so a late resend is still refused rather than
# silently permitted by forgetting.
_TTL_SECONDS = 900.0


@dataclass
class _Attempts:
    count: int = 0
    last_at: float = 0.0


@dataclass(frozen=True)
class ResendState:
    """What a UI needs to render the button, from the authority that enforces it."""

    resends_used: int
    resends_remaining: int
    seconds_until_allowed: float

    @property
    def can_resend(self) -> bool:
        return self.resends_remaining > 0 and self.seconds_until_allowed <= 0


class ResendPolicy:
    def __init__(
        self,
        *,
        max_resends: int = MAX_RESENDS,
        min_interval_seconds: float = MIN_INTERVAL_SECONDS,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_resends = max_resends
        self.min_interval_seconds = min_interval_seconds
        self._now = now
        self._lock = threading.Lock()
        self._attempts: dict[str, _Attempts] = {}

    def check_and_record(self, txn_id: str) -> ResendState:
        """Authorise one resend, or refuse it. Records on success.

        Check and record are one operation on purpose. Splitting them leaves
        a window where two concurrent requests both pass the check and both
        resend -- which is the exact failure the limit exists to prevent, and
        which a `check()` / `record()` pair invites at every call site.
        """
        if not txn_id:
            raise AbhaFlowError("txn_id is required to resend an OTP")

        with self._lock:
            self._sweep()
            attempts = self._attempts.setdefault(txn_id, _Attempts())
            now = self._now()

            if attempts.count >= self.max_resends:
                raise AbhaFlowError(
                    f"OTP resend limit reached for this transaction "
                    f"({self.max_resends} resends). Start a new verification "
                    f"rather than retrying this one."
                )

            elapsed = now - attempts.last_at
            if attempts.count and elapsed < self.min_interval_seconds:
                wait = self.min_interval_seconds - elapsed
                raise AbhaFlowError(
                    f"OTP was resent {elapsed:.0f}s ago; wait "
                    f"{wait:.0f}s before resending again."
                )

            attempts.count += 1
            attempts.last_at = now
            return self._state(attempts, now)

    def state_for(self, txn_id: str) -> ResendState:
        """Read-only. Records nothing, so polling it is free."""
        with self._lock:
            attempts = self._attempts.get(txn_id)
            now = self._now()
            return self._state(attempts or _Attempts(), now)

    def forget(self, txn_id: str) -> None:
        """Drop a finished conversation -- call it once the OTP is verified."""
        with self._lock:
            self._attempts.pop(txn_id, None)

    def _state(self, attempts: _Attempts, now: float) -> ResendState:
        if attempts.count == 0:
            wait = 0.0
        else:
            wait = max(0.0, self.min_interval_seconds - (now - attempts.last_at))
        return ResendState(
            resends_used=attempts.count,
            resends_remaining=max(0, self.max_resends - attempts.count),
            seconds_until_allowed=wait,
        )

    def _sweep(self) -> None:
        """Bounded memory. Called under the lock, on write only."""
        cutoff = self._now() - _TTL_SECONDS
        stale = [k for k, v in self._attempts.items() if v.last_at < cutoff]
        for key in stale:
            del self._attempts[key]


_default_policy = ResendPolicy()


def default_policy() -> ResendPolicy:
    return _default_policy
