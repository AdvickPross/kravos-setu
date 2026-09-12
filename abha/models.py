"""Typed shapes for what ABDM returns.

Every model here sets `extra="allow"` and parses through an explicit
`from_payload`, which is a deliberate departure from `backend/models.py`
where the shapes are strict. The reason is that these are not our shapes.

ABDM's v3 responses vary in three ways that a strict model would turn into
outages rather than warnings:

* **Key casing and naming drift between endpoints.** The same value arrives
  as `ABHANumber`, `abhaNumber` and `healthIdNumber` depending on which call
  produced it. `_first` reads all the spellings rather than betting on one.
* **Fields come back masked.** `mobile` is `"XXXXXX8769"`, `abhaNumber` may
  be `"91-****-****-8769"`. These are strings that happen to look like
  redactions; nothing here should try to parse them as numbers.
* **Fields are conditionally absent.** The `mobile` null-on-mismatch
  behaviour (see `abha/enrollment.py`) means a successful enrolment can
  return a profile with no mobile at all.

So: absorb what arrives, surface what is understood, and keep the original
under `raw` so a field this module does not model yet is still reachable
without a code change.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def _first(payload: dict[str, Any], *keys: str) -> Any:
    """First present, non-empty value among several spellings of one field."""
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _as_list(value: Any) -> list[Any]:
    """Coerce a field ABDM sends as either a scalar or an array.

    `authMethods` and `phrAddress` both arrive as a bare string on some
    endpoints and a list on others. `list(value)` would silently shred the
    string into characters, producing an `auth_methods` of `['o', 't', 'p']`
    that no comparison will ever match -- a bug that reads as "the account
    supports no auth methods" rather than as a parse error.
    """
    if value in (None, "", [], {}):
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


class AbhaAuthMethod(str, Enum):
    """The `authMethods` ABDM advertises for an account.

    Worth modelling as an enum rather than passing strings through: the
    account search response tells you which methods a given ABHA supports,
    and offering the patient a method their account does not carry produces a
    failure several calls later, at OTP verification.
    """

    OTP = "otp"
    AADHAAR_OTP = "AADHAAR_OTP"
    MOBILE_OTP = "MOBILE_OTP"
    PASSWORD = "PASSWORD"
    DEMOGRAPHICS = "DEMOGRAPHICS"
    AADHAAR_BIO = "AADHAAR_BIO"
    FACE = "face"
    IRIS = "iris"
    PI = "pi"
    DEMO_AUTH = "demo_auth"
    CHILD = "child"


class FaceAuthStatus(str, Enum):
    """`capturePID` poll states.

    `PENDING` is the only one that means "keep polling". `COMPLETE` and
    `VERIFIED` both mean success -- ABDM uses them inconsistently between the
    enrolment and login variants of the same endpoint, and treating either as
    terminal-success is what makes the poll loop work against both.
    """

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self is not FaceAuthStatus.PENDING

    @property
    def is_success(self) -> bool:
        return self in (FaceAuthStatus.VERIFIED, FaceAuthStatus.COMPLETE)

    @classmethod
    def parse(cls, value: str | None) -> "FaceAuthStatus":
        try:
            return cls((value or "").upper())
        except ValueError:
            # An unrecognised status is not pending. Treating it as pending
            # would spin the poll loop until timeout on a transaction that
            # has already failed.
            return cls.FAILED


class _Loose(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    raw: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)


class AbhaAccount(_Loose):
    """One ABHA identity, as returned by search, login and enrolment.

    `mobile` being `None` is meaningful, not missing data -- see
    `EnrolmentResult.mobile_is_linked`.
    """

    abha_number: str | None = None
    abha_address: str | None = None
    name: str | None = None
    gender: str | None = None
    date_of_birth: str | None = None
    mobile: str | None = None
    email: str | None = None
    state: str | None = None
    district: str | None = None
    kyc_verified: bool | None = None
    status: str | None = None
    auth_methods: list[str] = Field(default_factory=list)
    # Only present when a credential resolves to several accounts; it is the
    # value `login.select_account` sends back to disambiguate.
    index: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AbhaAccount":
        return cls(
            abha_number=_first(payload, "ABHANumber", "abhaNumber", "healthIdNumber"),
            abha_address=_first(
                payload,
                "preferredAbhaAddress",
                "abhaAddress",
                "phrAddress",
                "healthId",
            ),
            name=_first(payload, "name", "fullName"),
            gender=payload.get("gender"),
            date_of_birth=_first(payload, "dob", "dateOfBirth", "birthdate"),
            mobile=payload.get("mobile"),
            email=payload.get("email"),
            state=_first(payload, "stateName", "state"),
            district=_first(payload, "districtName", "district"),
            kyc_verified=_first(payload, "kycVerified", "kycStatus"),
            status=payload.get("status"),
            auth_methods=[str(m) for m in _as_list(_first(payload, "authMethods", "authMethod"))],
            index=_first(payload, "index", "userIndex"),
            raw=payload,
        )


class OtpChallenge(_Loose):
    """The txnId returned by any `request/otp` call, plus what to show a user.

    `masked_identifier` is ABDM's own masking of the destination
    (`XXXXXXX769`). It exists so a UI can say *where* the OTP went without
    this service ever holding the full number.
    """

    txn_id: str
    message: str | None = None
    masked_identifier: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "OtpChallenge":
        return cls(
            txn_id=str(_first(payload, "txnId", "transactionId") or ""),
            message=payload.get("message"),
            masked_identifier=_first(
                payload, "mobileNumber", "maskedMobile", "hint", "email"
            ),
            raw=payload,
        )


class EnrolmentResult(_Loose):
    """The outcome of `enrol/byAadhaar`.

    `is_new` distinguishes a created ABHA from one that already existed --
    ABDM answers both with 200 and the same body shape, differing only in
    `new`/`isNew`. A caller that cannot tell them apart will report a
    duplicate as a fresh enrolment.
    """

    txn_id: str | None = None
    account: AbhaAccount = Field(default_factory=AbhaAccount)
    token: str | None = None
    refresh_token: str | None = None
    is_new: bool | None = None
    message: str | None = None

    @property
    def mobile_is_linked(self) -> bool:
        """Whether the enrolled profile actually carries a mobile number.

        This is the single most consequential thing to check after enrolment.
        When the mobile supplied during enrolment differs from the one linked
        to the Aadhaar, ABDM completes the enrolment *successfully* and saves
        `mobile` as null -- no error, no warning. The ABHA is real but has no
        contact route, so every later OTP-based login on it fails until the
        number is attached separately. See `abha/enrollment.py`.
        """
        return bool(self.account.mobile)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "EnrolmentResult":
        profile = payload.get("ABHAProfile") or payload.get("abhaProfile") or {}
        tokens = payload.get("tokens") or payload
        return cls(
            txn_id=_first(payload, "txnId", "transactionId"),
            account=AbhaAccount.from_payload(profile if profile else payload),
            token=_first(tokens, "token", "accessToken"),
            refresh_token=_first(tokens, "refreshToken"),
            is_new=_first(payload, "new", "isNew"),
            message=payload.get("message"),
            raw=payload,
        )


class LoginResult(_Loose):
    """The outcome of `login/verify`.

    One credential can map to several ABHA accounts -- ABDM allows six ABHA
    numbers per mobile -- so this carries either a finished session (`token`
    set, one account) or a choice to put to the user (`t_token` set,
    `accounts` populated). `needs_account_selection` is the discriminator;
    branching on `len(accounts)` alone is wrong, because a single-account
    response still lists that account.
    """

    txn_id: str | None = None
    token: str | None = None
    refresh_token: str | None = None
    # Short-lived token that authorises only the account-selection call.
    t_token: str | None = None
    accounts: list[AbhaAccount] = Field(default_factory=list)
    message: str | None = None

    @property
    def needs_account_selection(self) -> bool:
        return self.token is None and bool(self.t_token)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LoginResult":
        accounts = _as_list(_first(payload, "accounts", "abhaAccounts"))
        return cls(
            txn_id=_first(payload, "txnId", "transactionId"),
            token=_first(payload, "token", "accessToken"),
            refresh_token=payload.get("refreshToken"),
            t_token=_first(payload, "tToken", "T-token", "tokens"),
            accounts=[AbhaAccount.from_payload(a) for a in accounts if isinstance(a, dict)],
            message=payload.get("message"),
            raw=payload,
        )


class FaceAuthSession(_Loose):
    """A face-auth transaction in flight.

    `qr_url` is handed to the patient (rendered as a QR, or as a deep link on
    mobile); their ABHA app does the capture. This service never sees a face
    image -- it only holds the `txn_id` and polls. The ten-minute validity is
    ABDM's, and `expires_in_seconds` carries it so a UI can show a countdown
    rather than letting the patient stare at a dead QR.
    """

    txn_id: str
    qr_url: str
    status: FaceAuthStatus = FaceAuthStatus.PENDING
    expires_in_seconds: int = 600
    message: str | None = None


class AbhaProfile(_Loose):
    """The full profile from `GET /v3/profile/account`, which is richer than
    the account stub returned by search."""

    account: AbhaAccount = Field(default_factory=AbhaAccount)
    address_line: str | None = None
    pin_code: str | None = None
    profile_photo: str | None = Field(default=None, repr=False)
    abha_addresses: list[str] = Field(default_factory=list)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "AbhaProfile":
        return cls(
            account=AbhaAccount.from_payload(payload),
            address_line=_first(payload, "address", "addressLine"),
            pin_code=_first(payload, "pinCode", "pincode"),
            profile_photo=_first(payload, "profilePhoto", "photo"),
            abha_addresses=[
                str(a)
                for a in _as_list(
                    _first(payload, "phrAddress", "abhaAddresses", "phrAddressList")
                )
            ],
            raw=payload,
        )
