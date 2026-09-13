"""Creating an ABHA. Sections 3 and 8 of the v1.4 guide.

The happy path is four calls -- request Aadhaar OTP, enrol, pick an ABHA
address, done -- but two things about it are not obvious from the endpoint
list, and both are the kind of thing that ships broken and is discovered in
production.

**The null-mobile trap.** `enrol_by_aadhaar` takes a mobile number. If it is
not the number linked to that Aadhaar at UIDAI, ABDM enrols the patient
anyway, returns HTTP 200 with a complete-looking profile, and saves `mobile`
as **null**. No error. No warning field. The ABHA exists and is valid, but
has no contact route, so every later OTP login against it fails with a
message about the account, not about the mobile. `EnrolmentResult.
mobile_is_linked` exists to make that state checkable in one line, and
`enrol_by_aadhaar` logs a warning when it happens rather than leaving the
caller to notice. The repair is `request_mobile_otp` + `verify_mobile_otp`,
which is why those are in this module and not filed under "profile update".

**The txnId is the whole flow.** Every call after the first carries the
`txnId` from the one before it, and ABDM expires it. It is not a session, not
a user id, and not reusable across flows -- which is why none of these
functions cache it. The caller holds it, because only the caller knows
whether the human at the other end is still there.

Nothing in this module holds a raw Aadhaar number beyond the single
expression that encrypts it. The plaintext is a parameter, never an
attribute, never logged, and never stored.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from abha import endpoints
from abha.client import AbhaClient
from abha.errors import AbhaFlowError
from abha.models import EnrolmentResult, OtpChallenge

log = logging.getLogger(__name__)

# ABDM wants this exact format inside the `otp` auth block -- not ISO-8601,
# which is what the TIMESTAMP *header* uses. Two different time formats in
# one request is a genuine trap in the spec, so the two live in two places
# with the difference written down: header format is in abha/transport.py.
_OTP_TIMESTAMP = "%Y-%m-%d %H:%M:%S"


def _otp_timestamp() -> str:
    return datetime.now().strftime(_OTP_TIMESTAMP)


def _require(value: str | None, name: str) -> str:
    if not value:
        raise AbhaFlowError(f"{name} is required and was empty")
    return value


def request_aadhaar_otp(
    client: AbhaClient, aadhaar: str, *, txn_id: str | None = None
) -> OtpChallenge:
    """Send an OTP to the mobile UIDAI has on file for this Aadhaar.

    `txn_id` is empty on a first request -- ABDM mints it -- and carries the
    existing transaction on a **resend**, which is the same endpoint rather
    than a separate one. Passing a txnId from an unrelated flow is rejected
    by ABDM, so this is not a field to thread through opportunistically; the
    only correct value is the one the first request returned.

    The resend *limit* is not enforced here. It is policy, not protocol, and
    it lives in `abha/otp_policy.py` so that the count is kept in one place
    rather than once per flow.
    """
    digits = _require(aadhaar, "aadhaar").replace(" ", "").replace("-", "")
    if len(digits) != 12 or not digits.isdigit():
        # Caught here rather than at ABDM: their rejection for a malformed
        # Aadhaar is indistinguishable from their rejection for a valid one
        # that is not enrolled, and the two need very different responses.
        raise AbhaFlowError("aadhaar must be 12 digits")

    payload = client.post(
        endpoints.ENROL_REQUEST_OTP,
        {
            "txnId": txn_id or "",
            "scope": ["abha-enrol"],
            "loginHint": "aadhaar",
            "loginId": client.encrypt(digits),
            "otpSystem": "aadhaar",
        },
        encrypted=True,
    )
    return OtpChallenge.from_payload(payload)


def enrol_by_aadhaar(
    client: AbhaClient,
    *,
    txn_id: str,
    otp: str,
    mobile: str | None = None,
) -> EnrolmentResult:
    """Complete enrolment with the OTP the patient received.

    `mobile` is the number to attach to the new ABHA. Read the module
    docstring before passing one that is not the Aadhaar-linked number.
    """
    otp_block: dict[str, Any] = {
        "timeStamp": _otp_timestamp(),
        "txnId": _require(txn_id, "txn_id"),
        "otpValue": client.encrypt(_require(otp, "otp")),
    }
    if mobile:
        # Plaintext here, deliberately: ABDM encrypts `loginId` and
        # `otpValue` but takes `mobile` inside the auth block in the clear.
        # Encrypting it "for safety" is rejected.
        otp_block["mobile"] = mobile

    payload = client.post(
        endpoints.ENROL_BY_AADHAAR,
        {
            "authData": {"authMethods": ["otp"], "otp": otp_block},
            "consent": endpoints.ENROLMENT_CONSENT,
        },
        encrypted=True,
    )
    result = EnrolmentResult.from_payload(payload)

    if mobile and not result.mobile_is_linked:
        log.warning(
            "ABHA %s enrolled with mobile saved as null -- the supplied number "
            "is not the Aadhaar-linked one. OTP login will fail on this "
            "account until request_mobile_otp/verify_mobile_otp attach it.",
            result.account.abha_number or "<unknown>",
        )
    return result


def request_mobile_otp(
    client: AbhaClient, *, txn_id: str, mobile: str, token: str
) -> OtpChallenge:
    """Start attaching a mobile number to an ABHA mid-enrolment.

    `token` is the transaction token from `enrol_by_aadhaar`, sent as
    `X-token` -- not the session token, which the client adds separately.
    Note `otpSystem` is `abdm` here, not `aadhaar`: the OTP is issued by ABDM
    to the number being attached, not by UIDAI to the one already on file.
    """
    digits = _require(mobile, "mobile").replace(" ", "")
    if len(digits) != 10 or not digits.isdigit():
        raise AbhaFlowError("mobile must be 10 digits")

    payload = client.post(
        endpoints.ENROL_REQUEST_OTP,
        {
            "txnId": _require(txn_id, "txn_id"),
            "scope": ["abha-enrol", "mobile-verify"],
            "loginHint": "mobile",
            "loginId": client.encrypt(digits),
            "otpSystem": "abdm",
        },
        x_token=_require(token, "token"),
        encrypted=True,
    )
    return OtpChallenge.from_payload(payload)


def verify_mobile_otp(
    client: AbhaClient, *, txn_id: str, otp: str, token: str
) -> dict[str, Any]:
    """Finish attaching the mobile number."""
    return client.post(
        endpoints.ENROL_AUTH_BY_ABDM,
        {
            "scope": ["abha-enrol", "mobile-verify"],
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "timeStamp": _otp_timestamp(),
                    "txnId": _require(txn_id, "txn_id"),
                    "otpValue": client.encrypt(_require(otp, "otp")),
                },
            },
        },
        x_token=_require(token, "token"),
        encrypted=True,
    )


# NHA M1 CRT_ABHA_112 states these five rules and says explicitly that they
# are "to be set at the API level" -- not merely hinted next to the input box.
# They are checked here so a rejection arrives before a round trip, and so the
# same rule applies to every caller rather than to whichever UI remembered it.
_ADDRESS_MIN = 8
_ADDRESS_MAX = 18


def validate_abha_address(abha_address: str) -> str:
    """Apply CRT_ABHA_112's rules, or say precisely which one failed.

    A generic "invalid ABHA address" sends the patient guessing between five
    different rules, so each raises its own message.
    """
    value = (abha_address or "").strip()
    # ABDM addresses are sometimes typed with the @sbx suffix; the rules apply
    # to the local part, which is what the API takes.
    local = value.split("@", 1)[0]

    if not (_ADDRESS_MIN <= len(local) <= _ADDRESS_MAX):
        raise AbhaFlowError(
            f"ABHA address must be {_ADDRESS_MIN}-{_ADDRESS_MAX} characters; "
            f"{local!r} is {len(local)}"
        )
    if not re.fullmatch(r"[A-Za-z0-9._]+", local):
        raise AbhaFlowError(
            "ABHA address may contain only letters, digits, one dot and one "
            "underscore"
        )
    if local[0] in "._" or local[-1] in "._":
        raise AbhaFlowError(
            "a dot or underscore must be between characters, never first or last"
        )
    if local.count(".") > 1:
        raise AbhaFlowError("at most one dot is allowed in an ABHA address")
    if local.count("_") > 1:
        raise AbhaFlowError("at most one underscore is allowed in an ABHA address")
    return value


def suggest_abha_addresses(client: AbhaClient, *, txn_id: str) -> list[str]:
    """Candidate ABHA addresses ABDM will accept for this enrolment.

    The `txnId` travels in a header (`Transaction_Id`) on this one call and
    in the body on every other. That inconsistency is ABDM's; it is absorbed
    by `AbhaClient` so no caller has to know.
    """
    payload = client.get(
        endpoints.ENROL_ADDRESS_SUGGESTION,
        transaction_id=_require(txn_id, "txn_id"),
    )
    suggestions = payload.get("abhaAddressList") or payload.get("abhaAddress") or []
    return [str(s) for s in suggestions] if isinstance(suggestions, list) else []


def set_abha_address(
    client: AbhaClient, *, txn_id: str, abha_address: str, preferred: bool = True
) -> dict[str, Any]:
    """Claim an ABHA address for the newly-created ABHA number.

    An ABHA number can carry up to six addresses; `preferred` marks which one
    is the primary. ABDM wants `1`/`0` rather than a JSON boolean here.

    The address is validated first (CRT_ABHA_112). ABDM would reject a
    malformed one too, but with a message that does not say which of the five
    rules was broken.
    """
    return client.post(
        endpoints.ENROL_SET_ADDRESS,
        {
            "txnId": _require(txn_id, "txn_id"),
            "abhaAddress": validate_abha_address(
                _require(abha_address, "abha_address")
            ),
            "preferred": 1 if preferred else 0,
        },
    )
