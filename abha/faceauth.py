"""Face authentication. Section 6 of the v1.4 guide.

The shape of this flow is unusual and worth stating plainly, because it
determines the API this module can offer: **the capture does not happen
here.** ABDM's face match runs inside the patient's own ABHA app against
their backend. This service's entire role is to open a transaction, hand the
patient a QR encoding `.../face-auth?txnId=<id>`, and then poll until their
phone reports back.

That makes it the one flow in M1 that is genuinely asynchronous and
human-paced, with three consequences the code has to respect:

* **Polling is the only channel.** There is no callback to this service, so
  `await_capture` blocks. It takes an injected `sleep` so the tests exercise
  the loop without spending real seconds on it.
* **The window is ten minutes.** ABDM expires the QR, and a poll loop with no
  deadline of its own will keep asking about a transaction that died eight
  minutes ago. `face_auth_timeout_seconds` is the deadline.
* **An unknown status must not read as "keep waiting".** `FaceAuthStatus.
  parse` maps anything unrecognised to `FAILED` rather than `PENDING`, so a
  status this code has not seen before ends the loop instead of spinning it
  until timeout.

`COMPLETE` and `VERIFIED` both mean success -- ABDM uses them inconsistently
between the enrolment and login variants of the same endpoint, so both are
accepted. Treating only one as terminal works against whichever variant it
was tested on and hangs against the other.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from abha import endpoints
from abha.client import AbhaClient
from abha.errors import AbhaFlowError
from abha.models import EnrolmentResult, FaceAuthSession, FaceAuthStatus

log = logging.getLogger(__name__)

# ABDM's QR validity for this flow. Held here rather than only in config so
# the value travels with the session object a UI renders a countdown from.
QR_VALIDITY_SECONDS = 600


def start_face_auth(
    client: AbhaClient, *, aadhaar: str, for_login: bool = False
) -> FaceAuthSession:
    """Open a face-auth transaction and build the QR the patient scans.

    `for_login` switches the scope between enrolling a new ABHA and verifying
    an existing one. The endpoint is the same; the scope is not, and sending
    the enrolment scope on a login attempt fails at the final step rather
    than at this one.
    """
    digits = (aadhaar or "").replace(" ", "").replace("-", "")
    if len(digits) != 12 or not digits.isdigit():
        raise AbhaFlowError("aadhaar must be 12 digits")

    scope = ["abha-login", "face-verify"] if for_login else ["abha-enrol", "face-auth"]
    payload = client.post(
        endpoints.ENROL_AUTH_INIT,
        {
            "txnId": "",
            "scope": scope,
            "loginHint": "aadhaar",
            "loginId": client.encrypt(digits),
            "otpSystem": "aadhaar",
        },
        encrypted=True,
    )
    txn_id = str(payload.get("txnId") or "")
    if not txn_id:
        raise AbhaFlowError(
            f"face-auth init returned no txnId: {sorted(payload)!r}"
        )
    return FaceAuthSession(
        txn_id=txn_id,
        qr_url=client.config.face_auth_landing_url(txn_id),
        status=FaceAuthStatus.PENDING,
        expires_in_seconds=QR_VALIDITY_SECONDS,
        message=payload.get("message"),
    )


def poll_once(client: AbhaClient, *, txn_id: str) -> FaceAuthStatus:
    """One `capturePID` check. Does not sleep, does not retry."""
    if not txn_id:
        raise AbhaFlowError("txn_id is required")
    payload = client.post(endpoints.ENROL_CAPTURE_PID, {"txnId": txn_id})
    return FaceAuthStatus.parse(
        payload.get("status") or payload.get("authResult") or payload.get("result")
    )


def await_capture(
    client: AbhaClient,
    *,
    txn_id: str,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> FaceAuthStatus:
    """Poll until the patient's app reports back, or the window closes.

    Returns the terminal status rather than raising on failure: a patient who
    declined or mismatched is a normal outcome a UI has to render, not an
    exception. A timeout does raise -- that one is the integration's problem,
    not the patient's.
    """
    interval = client.config.face_auth_poll_seconds
    deadline = now() + client.config.face_auth_timeout_seconds

    while True:
        status = poll_once(client, txn_id=txn_id)
        if status.is_terminal:
            log.debug("face auth %s finished as %s", txn_id, status.value)
            return status
        if now() >= deadline:
            raise AbhaFlowError(
                f"face auth {txn_id} still PENDING after "
                f"{client.config.face_auth_timeout_seconds:.0f}s -- the QR is "
                f"valid for {QR_VALIDITY_SECONDS}s, so this transaction is dead "
                f"and a new one must be started"
            )
        sleep(interval)


def complete_face_enrolment(
    client: AbhaClient, *, txn_id: str, mobile: str | None = None
) -> EnrolmentResult:
    """Turn a verified face-auth transaction into an ABHA.

    Same endpoint as OTP enrolment, with `authMethods` naming face instead --
    which is why `enrol_by_aadhaar` is not simply reused: its OTP block is
    mandatory there and must be absent here.

    The null-mobile trap applies identically to this path. See
    `abha/enrollment.py`.
    """
    if not txn_id:
        raise AbhaFlowError("txn_id is required")

    auth_data: dict[str, Any] = {"authMethods": ["face"], "face": {"txnId": txn_id}}
    if mobile:
        auth_data["face"]["mobile"] = mobile

    payload = client.post(
        endpoints.ENROL_BY_AADHAAR,
        {"authData": auth_data, "consent": endpoints.ENROLMENT_CONSENT},
    )
    result = EnrolmentResult.from_payload(payload)
    if mobile and not result.mobile_is_linked:
        log.warning(
            "ABHA %s enrolled by face with mobile saved as null -- see "
            "abha/enrollment.py on attaching it",
            result.account.abha_number or "<unknown>",
        )
    return result
