"""Verifying an existing ABHA, and finding one. Section 7 of the v1.4 guide.

Three credentials start a login on this host -- Aadhaar, mobile and ABHA
number -- and each needs a different combination of three fields: `scope`,
`loginHint` and `otpSystem`. The combinations are not derivable from the
credential; `otpSystem` in particular flips between `aadhaar` (UIDAI sends
the OTP) and `abdm` (ABDM sends it) in a way that does not follow from which
identifier the patient typed.

Getting one of the three wrong produces a 400 that names none of them.

A fourth credential, the **ABHA address**, is not here. It is not a variant
of ABHA-number login but a separate family on the PHR host, and this host
rejects it outright -- see `abha/phr.py`. Every row below was confirmed
against the sandbox: Aadhaar reaches UIDAI (ABDM-1204), mobile and ABHA
number reach ABDM's account lookup (ABDM-1115, ABDM-1114). Reaching the
lookup and being told "not found" is the pass condition; it means the
scope/hint/otpSystem triple was accepted.

So the combinations are a table -- `_METHODS` -- rather than an if-chain
spread through the call sites. The table *is* the specification for this
section, it can be diffed against the guide in one screen, and adding a
credential type is a row rather than a branch.

The other thing this module handles is that login is not always one step.
ABDM permits six ABHA numbers per mobile, so verifying an OTP can return a
*choice* instead of a session: a short-lived `T-token` and a list of
accounts. `LoginResult.needs_account_selection` is the discriminator, and
`select_account` is the second leg. Code that assumes login is single-step
works perfectly in testing -- test Aadhaars usually have one ABHA -- and
fails for exactly the patients most likely to be using the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from abha import endpoints
from abha.client import AbhaClient
from abha.errors import AbhaFlowError
from abha.models import AbhaAccount, LoginResult, OtpChallenge


class LoginMethod(str, Enum):
    """The three credentials the ABHA host accepts.

    ABHA *address* is deliberately absent. It looks like it belongs here and
    it does not: the ABHA host rejects it with "Invalid Scope" and "Invalid
    Login Hint", because address verification is a separate family on the PHR
    host. It lives in `abha/phr.py`. Adding a fourth row here is the mistake
    that module's docstring exists to prevent.
    """

    AADHAAR = "aadhaar"
    MOBILE = "mobile"
    ABHA_NUMBER = "abha-number"


@dataclass(frozen=True)
class _MethodSpec:
    login_hint: str
    otp_system: str
    scope: tuple[str, ...]
    # Whether the identifier is RSA-encrypted before it is sent. All four are
    # today; the flag exists so that a future non-PII identifier does not
    # have to be special-cased inside the request builder.
    encrypt_login_id: bool = True


# The spec for section 7, in one table. See the module docstring.
_METHODS: dict[LoginMethod, _MethodSpec] = {
    # UIDAI sends the OTP to the Aadhaar-linked mobile.
    LoginMethod.AADHAAR: _MethodSpec(
        login_hint="aadhaar",
        otp_system="aadhaar",
        scope=("abha-login", "aadhaar-verify"),
    ),
    # ABDM sends the OTP itself; UIDAI is not involved.
    LoginMethod.MOBILE: _MethodSpec(
        login_hint="mobile",
        otp_system="abdm",
        scope=("abha-login", "mobile-verify"),
    ),
    # Identifier is the ABHA number, but the OTP still originates at UIDAI --
    # the non-obvious combination this table exists for.
    LoginMethod.ABHA_NUMBER: _MethodSpec(
        login_hint="abha-number",
        otp_system="aadhaar",
        scope=("abha-login", "aadhaar-verify"),
    ),
}


def _require(value: str | None, name: str) -> str:
    if not value:
        raise AbhaFlowError(f"{name} is required and was empty")
    return value


def request_login_otp(
    client: AbhaClient,
    *,
    identifier: str,
    method: LoginMethod,
    txn_id: str | None = None,
) -> OtpChallenge:
    """Send a login OTP for whichever credential the patient offered.

    `txn_id` carries an existing transaction on a resend -- ABDM has no
    separate resend endpoint. The two-per-minute limit is policy and lives in
    `abha/otp_policy.py`.
    """
    spec = _METHODS[method]
    login_id = _require((identifier or "").strip(), "identifier")
    body: dict[str, Any] = {"scope": list(spec.scope)}
    if txn_id:
        body["txnId"] = txn_id
    payload = client.post(
        endpoints.LOGIN_REQUEST_OTP,
        {
            **body,
            "loginHint": spec.login_hint,
            "loginId": client.encrypt(login_id) if spec.encrypt_login_id else login_id,
            "otpSystem": spec.otp_system,
        },
        encrypted=spec.encrypt_login_id,
    )
    return OtpChallenge.from_payload(payload)


def verify_login_otp(
    client: AbhaClient, *, txn_id: str, otp: str, method: LoginMethod
) -> LoginResult:
    """Exchange the OTP for a session, or for a list of accounts to choose from.

    The `scope` sent here must match the one sent to `request_login_otp` for
    the same `txnId`; passing the same `method` through both calls is what
    guarantees that, and is why this takes a method rather than a scope.
    """
    spec = _METHODS[method]
    payload = client.post(
        endpoints.LOGIN_VERIFY,
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
        encrypted=True,
    )
    return LoginResult.from_payload(payload)


def select_account(
    client: AbhaClient, *, txn_id: str, account: AbhaAccount | str, t_token: str
) -> LoginResult:
    """Second leg of a multi-account login.

    Accepts either the `AbhaAccount` the user picked or its bare index, so a
    caller holding only what a UI sent back does not have to reconstruct the
    model. The index is encrypted: it identifies an ABHA account and ABDM
    treats it as PII like everything else in this family.

    `t_token` -- not the session token and not the eventual `X-token` -- is
    the only credential that authorises this call. It is deliberately not
    cached anywhere; it is valid for one selection.
    """
    index = account.index if isinstance(account, AbhaAccount) else account
    if not index:
        raise AbhaFlowError(
            "the selected account carries no index -- ABDM only populates it "
            "on multi-account login responses, so this account did not come "
            "from one"
        )
    return LoginResult.from_payload(
        client.post(
            endpoints.LOGIN_VERIFY_USER,
            {"txnId": _require(txn_id, "txn_id"), "ABHANumber": client.encrypt(index)},
            t_token=_require(t_token, "t_token"),
            encrypted=True,
        )
    )


def find_abha(client: AbhaClient, *, abha_address: str) -> list[AbhaAccount]:
    """Find ABHA: look up accounts behind an ABHA address.

    Returns a list because the endpoint does. An empty list means "no such
    address", which is a legitimate answer and not an error -- raising here
    would force every caller into a try/except to handle a normal outcome.
    """
    payload = client.post(
        endpoints.SEARCH_ABHA,
        {"abhaAddress": _require((abha_address or "").strip(), "abha_address")},
    )
    raw: Any = payload.get("accounts") or payload.get("account") or []
    if isinstance(raw, dict):
        # A single hit comes back as a bare object on this endpoint rather
        # than a one-element array.
        raw = [raw]
    return [AbhaAccount.from_payload(a) for a in raw if isinstance(a, dict)]
