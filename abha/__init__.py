"""ABDM ABHA V3 integration -- Milestone 1 (Identities).

M1 is ABHA creation and verification. M2 (facility-to-facility record flow)
and M3 (consent) sit on top of it and are not built here; what they will
reuse is the bottom half of this package -- `config`, `session`, `transport`,
`crypto`, `errors` -- which is why those are separated from the flow modules
rather than folded into them.

    from abha import AbhaClient, AbhaConfig, enrollment

    with AbhaClient(AbhaConfig.from_env()) as client:
        challenge = enrollment.request_aadhaar_otp(client, "999999999999")
        result = enrollment.enrol_by_aadhaar(
            client, txn_id=challenge.txn_id, otp="123456", mobile="9999999999"
        )

Layering, bottom up:

    config      hosts per environment, credentials from env
    errors      typed failures, including the K-547 hint
    transport   mandatory headers, retry policy, redaction
    crypto      RSA-OAEP-SHA1 + the cached public certificate
    session     client_credentials token, cached and refreshed
    client      composes the above; the object flows take
    endpoints   every path, in one table
    ---
    enrollment  login  profile  faceauth  biometrics

The flow modules are plain functions taking a client, not classes holding
one. Every ABDM flow is a sequence of independent calls chained by a `txnId`
that only the caller can decide is still alive -- a stateful object would
have to either hold that transaction (and get it wrong when the patient walks
away) or store nothing, in which case it is a namespace with extra steps.
"""

from abha.client import AbhaClient
from abha.config import AbhaConfig, AbhaConfigError, AbhaEnvironment
from abha.errors import (
    AbhaApiError,
    AbhaAuthError,
    AbhaError,
    AbhaFlowError,
    AbhaTransportError,
)
from abha.models import (
    AbhaAccount,
    AbhaProfile,
    EnrolmentResult,
    FaceAuthSession,
    FaceAuthStatus,
    LoginResult,
    OtpChallenge,
)

__all__ = [
    "AbhaAccount",
    "AbhaApiError",
    "AbhaAuthError",
    "AbhaClient",
    "AbhaConfig",
    "AbhaConfigError",
    "AbhaEnvironment",
    "AbhaError",
    "AbhaFlowError",
    "AbhaProfile",
    "AbhaTransportError",
    "EnrolmentResult",
    "FaceAuthSession",
    "FaceAuthStatus",
    "LoginResult",
    "OtpChallenge",
]
