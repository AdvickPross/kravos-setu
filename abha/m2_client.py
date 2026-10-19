"""HIP-side calls to the ABDM HIE-CM (gateway host).

Every call here is HIP -> gateway. Headers come from this server's own
config and session and never from an inbound request. Forwarding a caller's
``Authorization`` or ``X-HIP-ID`` would let anyone who can reach this server
act as the HIP.

The caller passes ``request_id``, because ABDM echoes it back as
``response.requestId`` in the async callback. The transaction log is keyed on
it, so the caller has to know it before the request leaves.
"""

from __future__ import annotations

from typing import Any

from abha.client import AbhaClient
from abha.config import AbhaConfigError

TOKEN_GENERATE = "/api/hiecm/v3/token/generate-token"
LINK_CARE_CONTEXT = "/api/hiecm/hip/v3/link/carecontext"
UIL_ON_DISCOVER = "/api/hiecm/user-initiated-linking/v3/patient/care-context/on-discover"
UIL_ON_INIT = "/api/hiecm/user-initiated-linking/v3/link/care-context/on-init"
UIL_ON_CONFIRM = "/api/hiecm/user-initiated-linking/v3/link/care-context/on-confirm"
CONTEXT_NOTIFY = "/api/hiecm/hip/v3/link/context/notify"
SMS_NOTIFY = "/api/hiecm/hip/v3/link/patient/links/sms/notify2"
CONSENT_ON_NOTIFY = "/api/hiecm/consent/v3/request/hip/on-notify"
HI_ON_REQUEST = "/api/hiecm/data-flow/v3/health-information/hip/on-request"
HI_NOTIFY = "/api/hiecm/data-flow/v3/health-information/notify"
BRIDGE_SERVICES = "/api/hiecm/gateway/v3/bridge-services"
BRIDGE_URL = "/api/hiecm/gateway/v3/bridge/url"


class AbdmM2Client:
    def __init__(self, client: AbhaClient):
        self.client = client

    @property
    def hip_id(self) -> str:
        hip_id = self.client.config.hip_id
        if not hip_id:
            raise AbhaConfigError(
                "ABDM_HIP_ID must be set for M2 -- the facility id this "
                "bridge is registered under on the ABDM sandbox."
            )
        return hip_id

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        request_id: str,
        with_hip_id: bool,
        extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "REQUEST-ID": request_id,
            "X-CM-ID": self.client.config.cm_id,
        }
        if with_hip_id:
            headers["X-HIP-ID"] = self.hip_id
        headers.update(extra or {})
        return self.client.post(
            path, body=body, host="gateway", extra_headers=headers
        )

    # -- HIP-initiated linking ----------------------------------------------

    def generate_link_token(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        return self._post(TOKEN_GENERATE, body, request_id=request_id, with_hip_id=True)

    def link_care_context(
        self, body: dict[str, Any], *, link_token: str, request_id: str
    ) -> dict[str, Any]:
        # In v3 the link token travels as a header, not in the body.
        return self._post(
            LINK_CARE_CONTEXT,
            body,
            request_id=request_id,
            with_hip_id=True,
            extra={"X-LINK-TOKEN": link_token},
        )

    # -- user-initiated linking: the HIP's replies ---------------------------

    def on_discover(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        # The spec requires X-HIU-ID here (and only here). Its example value
        # is a facility id, so it is ours.
        return self._post(
            UIL_ON_DISCOVER,
            body,
            request_id=request_id,
            with_hip_id=False,
            extra={"X-HIU-ID": self.hip_id},
        )

    def on_init(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        return self._post(UIL_ON_INIT, body, request_id=request_id, with_hip_id=False)

    def on_confirm(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        return self._post(UIL_ON_CONFIRM, body, request_id=request_id, with_hip_id=False)

    # -- HIP-initiated linking: updates and deep-link SMS ----------------------

    def context_notify(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        return self._post(CONTEXT_NOTIFY, body, request_id=request_id, with_hip_id=True)

    def sms_notify(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        return self._post(SMS_NOTIFY, body, request_id=request_id, with_hip_id=False)

    # -- consent and data flow ---------------------------------------------------

    def consent_on_notify(self, body: dict[str, Any], *, request_id: str) -> dict[str, Any]:
        return self._post(CONSENT_ON_NOTIFY, body, request_id=request_id, with_hip_id=False)

    def health_information_on_request(
        self, body: dict[str, Any], *, request_id: str
    ) -> dict[str, Any]:
        return self._post(HI_ON_REQUEST, body, request_id=request_id, with_hip_id=False)

    def health_information_notify(
        self, body: dict[str, Any], *, request_id: str
    ) -> dict[str, Any]:
        return self._post(HI_NOTIFY, body, request_id=request_id, with_hip_id=False)

    # -- bridge registration (ABDM sandbox guide 3.2.4, 3.2.7) ------------------

    def bridge_services(self) -> dict[str, Any]:
        """Our bridge: its callback URL and the HIP/HIU services linked to it.
        Needs no HIP id, so it works before one is issued."""
        return self.client.get(
            BRIDGE_SERVICES, host="gateway", extra_headers={"X-CM-ID": self.client.config.cm_id}
        )

    def update_bridge_url(self, url: str) -> dict[str, Any]:
        return self.client.patch(
            BRIDGE_URL,
            {"url": url},
            host="gateway",
            extra_headers={"X-CM-ID": self.client.config.cm_id},
        )
