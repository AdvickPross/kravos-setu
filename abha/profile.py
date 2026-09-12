"""Reading a verified ABHA: profile, QR, card. Sections 9-11 and 14.

Everything here needs the transaction token (`X-token`) that enrolment or
login produced -- the session token alone is not enough, because these calls
return one specific patient's identity and the session token identifies the
*integrator*, not the patient. Passing only the session token returns 401,
which reads like a credentials problem and is not one.

The section-14 split is the trap. A patient verified by **ABHA address**
must be read from the PHR host, and one verified by Aadhaar / mobile / ABHA
number from the ABHA host. The two hosts expose the same information under
different paths, and calling the wrong one with a perfectly valid token
returns 401 or an empty profile rather than a redirect. `via_abha_address`
is threaded through each function for exactly this, rather than being
inferred -- the token itself carries nothing that distinguishes the two, so
only the caller who ran the login knows.
"""

from __future__ import annotations

from typing import Any

from abha import endpoints
from abha.client import AbhaClient
from abha.errors import AbhaFlowError
from abha.models import AbhaProfile


def _require_token(token: str | None) -> str:
    if not token:
        raise AbhaFlowError(
            "an X-token from enrolment or login is required -- the session "
            "token identifies the integrator, not the patient"
        )
    return token


def get_profile(
    client: AbhaClient, *, token: str, via_abha_address: bool = False
) -> AbhaProfile:
    token = _require_token(token)
    if via_abha_address:
        payload = client.get(endpoints.PHR_PROFILE, host="phr", x_token=token)
    else:
        payload = client.get(endpoints.PROFILE_ACCOUNT, x_token=token)
    return AbhaProfile.from_payload(payload)


def get_qr_code(client: AbhaClient, *, token: str) -> dict[str, Any]:
    """The ABHA QR payload, for printing on a card or showing at a desk.

    Returned as the raw mapping rather than a model: the contents are a
    signed blob ABDM defines and this service has no business reshaping.
    """
    return client.get(endpoints.PROFILE_QR_CODE, x_token=_require_token(token))


def get_abha_card(
    client: AbhaClient, *, token: str, via_abha_address: bool = False
) -> bytes:
    """The printable ABHA card as PNG bytes.

    Bytes, not a model -- this endpoint answers with an image, and routing it
    through JSON parsing would raise on a successful response.
    """
    token = _require_token(token)
    path = endpoints.PHR_CARD if via_abha_address else endpoints.PROFILE_ABHA_CARD
    return client.get_bytes(
        path, host="phr" if via_abha_address else "abha", x_token=token
    )
