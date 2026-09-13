"""Verifying a patient by **ABHA address**. Section 14; NHA M1 VRFY_ABHA_102/202.

This exists as its own module because the obvious implementation is wrong,
and wrong in a way that reads as correct.

An ABHA address looks like a fourth credential alongside Aadhaar, mobile and
ABHA number, so the natural design is a fourth row in `abha/login.py`'s scope
table. That was the first implementation here. The sandbox rejects it:

    POST /v3/profile/login/request/otp
    {"scope": ["abha-address-login", "mobile-verify"],
     "loginHint": "abha-address", ...}
    -> 400 {"scope": "Invalid Scope", "loginHint": "Invalid Login Hint"}

ABHA-address verification is not a variant of ABHA-number login. It is a
separate family on the **PHR host**, with its own search, OTP request and
verify endpoints, and the two families do not share a scope vocabulary. NHA's
M1 checklist says so directly -- VRFY_ABHA_102 and VRFY_ABHA_202 list five
`/v3/phr/web/login/...` paths where the ABHA-number rows list
`/v3/profile/login/...` -- but a reader who has just written the ABHA-host
table will see `abha-address-login` in the scope list and assume it belongs
with the others.

Every shape below was confirmed against the sandbox rather than read off a
PDF, using deliberately non-existent identifiers so validation would pass and
the lookup would fail -- no OTP was ever sent to a real person:

    scope                                    otpSystem   result
    ["abha-address-login","aadhaar-verify"]  aadhaar     User not found  (accepted)
    ["abha-address-login","aadhaar-verify"]  abdm        Invalid Otp System
    ["abha-address-login","mobile-verify"]   abdm        User not found  (accepted)
    ["abha-address-login","mobile-verify"]   aadhaar     Invalid Otp System
    ["abha-address-verify"]                  either      Invalid Scope

"User not found" is the *success* signal in that table: it means the request
was well-formed enough to reach the account lookup. The pairing is the thing
to take away -- `aadhaar-verify` requires `otpSystem: aadhaar` and
`mobile-verify` requires `otpSystem: abdm`, and crossing them fails. That is
why `AddressOtpChannel` couples the two rather than exposing them as
independent arguments a caller can mismatch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from abha import endpoints
from abha.client import AbhaClient
from abha.errors import AbhaApiError, AbhaFlowError
from abha.models import AbhaAccount, LoginResult, OtpChallenge

_HOST = "phr"


class AddressOtpChannel(str, Enum):
    """Where the OTP for an ABHA-address verification is sent.

    AADHAAR -- UIDAI sends it to the Aadhaar-linked mobile (VRFY_ABHA_102).
    MOBILE  -- ABDM sends it to the ABHA-linked communication mobile
               (VRFY_ABHA_202).
    """

    AADHAAR = "aadhaar"
    MOBILE = "mobile"


@dataclass(frozen=True)
class _ChannelSpec:
    scope: tuple[str, ...]
    otp_system: str


# Coupled deliberately: crossing scope and otpSystem returns "Invalid Otp
# System". See the module docstring for the full sandbox-verified matrix.
_CHANNELS: dict[AddressOtpChannel, _ChannelSpec] = {
    AddressOtpChannel.AADHAAR: _ChannelSpec(
        scope=("abha-address-login", "aadhaar-verify"), otp_system="aadhaar"
    ),
    AddressOtpChannel.MOBILE: _ChannelSpec(
        scope=("abha-address-login", "mobile-verify"), otp_system="abdm"
    ),
}


def _require(value: str | None, name: str) -> str:
    if not value:
        raise AbhaFlowError(f"{name} is required and was empty")
    return value


def search_abha_address(client: AbhaClient, *, abha_address: str) -> AbhaAccount | None:
    """Does this ABHA address exist, and what auth methods does it accept?

    `None` rather than an exception for "no such address": it is the ordinary
    answer to an ordinary question, and NHA's checklist requires the UI to
    respond to it by offering ABHA *creation* (VRFY_ABHA_302), which is a
    branch, not an error path.

    The field is `abhaAddress`. `healthId` -- the v1 spelling, still widely
    copied -- is rejected with "Invalid ABHA Address".

    ABDM reports the absence as an **HTTP 400**, not an empty 200, which is
    the detail that makes this function more than a passthrough: left alone,
    the ordinary answer "nobody has that address" arrives as an exception and
    the route turns it into a client error. The UI then shows a failure where
    the checklist requires it to offer ABHA creation.
    """
    try:
        payload = client.post(
            endpoints.PHR_LOGIN_SEARCH,
            {"abhaAddress": _require((abha_address or "").strip(), "abha_address")},
            host=_HOST,
        )
    except AbhaApiError as exc:
        if _is_not_found(exc):
            return None
        raise
    if not payload or _looks_absent(payload.get("message")):
        return None
    return AbhaAccount.from_payload(payload)


def _looks_absent(message: object) -> bool:
    return isinstance(message, str) and "not found" in message.lower()


def _is_not_found(error: AbhaApiError) -> bool:
    """Distinguish "no such address" from "your request was wrong".

    Both are 400. Matching on the message is unattractive but it is the only
    signal ABDM gives -- and the alternative, treating every 400 as absence,
    would swallow a genuinely malformed request and report it to the user as
    "no such patient".
    """
    return error.status_code in (400, 404) and _looks_absent(error.message)


def request_address_otp(
    client: AbhaClient,
    *,
    abha_address: str,
    channel: AddressOtpChannel = AddressOtpChannel.AADHAAR,
) -> OtpChallenge:
    """Send an OTP for an ABHA-address verification.

    `channel` picks both the scope and the OTP system, because ABDM couples
    them; there is deliberately no way to pass one without the other.
    """
    spec = _CHANNELS[channel]
    payload = client.post(
        endpoints.PHR_LOGIN_REQUEST_OTP,
        {
            "scope": list(spec.scope),
            "loginHint": "abha-address",
            "loginId": client.encrypt(_require((abha_address or "").strip(), "abha_address")),
            "otpSystem": spec.otp_system,
        },
        host=_HOST,
        encrypted=True,
    )
    return OtpChallenge.from_payload(payload)


def verify_address_otp(
    client: AbhaClient,
    *,
    txn_id: str,
    otp: str,
    channel: AddressOtpChannel = AddressOtpChannel.AADHAAR,
) -> LoginResult:
    """Exchange the OTP for an X-token scoped to this ABHA address.

    The scope must match the one used to request the OTP, which is why this
    takes the same `channel` rather than a raw scope.

    The token returned here only works against the PHR host. Reading the
    profile with it from `/v3/profile/account` returns 401 -- see
    `abha/profile.py`, whose `via_abha_address` flag exists for this.
    """
    spec = _CHANNELS[channel]
    payload = client.post(
        endpoints.PHR_LOGIN_VERIFY,
        {
            "scope": list(spec.scope),
            "authData": {
                "authMethods": ["otp"],
                "otp": {
                    "txnId": _require(txn_id, "txn_id"),
                    "otpValue": client.encrypt(_require(otp, "otp")),
                },
            },
        },
        host=_HOST,
        encrypted=True,
    )
    return LoginResult.from_payload(payload)
