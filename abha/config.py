"""Environment selection and every base URL M1 talks to.

ABDM splits the identity surface across *three* hosts, and the split is not
intuitive -- it is the first thing that goes wrong in an integration:

* the **gateway** issues the session token (`dev.abdm.gov.in` in sandbox,
  `apis.abdm.gov.in` in production) -- a different host from the APIs the
  token is then used against;
* the **ABHA** host serves enrolment, login, profile and card
  (`abhasbx.abdm.gov.in/abha/api` vs `abha.abdm.gov.in/api/abha` -- note that
  the path segments swap order between environments, they are not a prefix
  change);
* the **PHR** host serves ABHA-*address* login and the face-auth landing page
  the patient's phone opens (`.../v3/phr/web` under the ABHA host in sandbox,
  but a standalone `phr.abdm.gov.in` host in production).

Hard-coding any one of these inside a flow module is how a service ends up
sandbox-only. They live here, keyed by environment, and nothing else in the
package builds a URL from a literal.

Credentials come from the environment and are never defaulted. A missing
client secret raises at construction rather than producing a 401 twelve
frames deep in a request the caller cannot see.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class AbhaEnvironment(str, Enum):
    SANDBOX = "sandbox"
    PRODUCTION = "production"


@dataclass(frozen=True)
class _Hosts:
    gateway: str
    abha: str
    phr: str
    # Sent as the ``X-CM-ID`` header on the session call. ABDM calls this the
    # consent-manager id; it is the environment discriminator, not a tenant.
    cm_id: str


_HOSTS: dict[AbhaEnvironment, _Hosts] = {
    AbhaEnvironment.SANDBOX: _Hosts(
        gateway="https://dev.abdm.gov.in",
        abha="https://abhasbx.abdm.gov.in/abha/api",
        phr="https://abhasbx.abdm.gov.in/abha/api/v3/phr/web",
        cm_id="sbx",
    ),
    AbhaEnvironment.PRODUCTION: _Hosts(
        gateway="https://apis.abdm.gov.in",
        abha="https://abha.abdm.gov.in/api/abha",
        phr="https://phr.abdm.gov.in/api/phr/web/v3",
        cm_id="abdm",
    ),
}


class AbhaConfigError(RuntimeError):
    """Raised when the process is not configured to talk to ABDM at all.

    Distinct from an API rejection: this one is fixable by editing `.env`,
    never by retrying.
    """


@dataclass(frozen=True)
class AbhaConfig:
    client_id: str
    client_secret: str
    environment: AbhaEnvironment = AbhaEnvironment.SANDBOX
    timeout_seconds: float = 30.0
    # ABDM documents the session token as expiring in 1200s. Treating it as
    # expired early costs one extra session call an hour; treating it as
    # valid a moment too long costs a failed clinical request, so the
    # asymmetry is deliberate.
    token_leeway_seconds: int = 60
    # The ABHA app's face-auth QR is valid for 10 minutes and ABDM asks for a
    # 5-10s poll. Both are protocol facts, so they are configuration, not
    # constants buried in the face-auth module.
    face_auth_poll_seconds: float = 5.0
    face_auth_timeout_seconds: float = 600.0
    # M2: the facility id registered in HFR/the bridge. Sent as X-HIP-ID on
    # HIP-originated calls. Optional so M1 keeps working without it; the M2
    # client refuses to send without one.
    hip_id: str = ""
    # M2: the HIP name patients see (sms/notify2 sends it). Should match the
    # hipName registered against the facility in HFR.
    hip_name: str = "Kravos Setu"
    # M2: verify the gateway JWT on inbound /api/v3/hip/* callbacks. Only
    # ever switched off for local curl testing.
    verify_gateway_tokens: bool = True

    @property
    def _hosts(self) -> _Hosts:
        return _HOSTS[self.environment]

    @property
    def cm_id(self) -> str:
        return self._hosts.cm_id

    @property
    def session_url(self) -> str:
        return f"{self._hosts.gateway}/api/hiecm/gateway/v3/sessions"

    def abha_url(self, path: str) -> str:
        return f"{self._hosts.abha}/{path.lstrip('/')}"

    def phr_url(self, path: str) -> str:
        return f"{self._hosts.phr}/{path.lstrip('/')}"

    def gateway_url(self, path: str) -> str:
        """HIE-CM (M2/M3) APIs: linking, consent, data flow."""
        return f"{self._hosts.gateway}/{path.lstrip('/')}"

    @property
    def gateway_jwks_url(self) -> str:
        # Documented v3 certs API (ABDM sandbox guide 3.2.3). It needs the
        # session token plus REQUEST-ID/TIMESTAMP/X-CM-ID like any v3 call.
        # It serves the central-registry keys that sign both our session
        # token and the gateway's callback tokens.
        return f"{self._hosts.gateway}/api/hiecm/gateway/v3/certs"

    @property
    def gateway_issuer(self) -> str:
        return f"{self._hosts.gateway}/auth/realms/central-registry"

    def face_auth_landing_url(self, txn_id: str) -> str:
        """The URL encoded into the QR the patient scans with the ABHA app.

        Not called by this service -- it is handed to the patient's phone,
        which is why it is built from the PHR host and not the ABHA one.
        """
        return f"{self._hosts.phr}/face-auth?txnId={txn_id}"

    @classmethod
    def from_env(cls) -> AbhaConfig:
        client_id = os.getenv("ABDM_CLIENT_ID", "").strip()
        client_secret = os.getenv("ABDM_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise AbhaConfigError(
                "ABDM_CLIENT_ID and ABDM_CLIENT_SECRET must be set -- copy "
                ".env.example to .env and fill in the sandbox credentials from "
                "https://sandbox.abdm.gov.in. They are never committed."
            )
        raw_env = os.getenv("ABDM_ENVIRONMENT", "sandbox").strip().lower()
        try:
            environment = AbhaEnvironment(raw_env)
        except ValueError:
            raise AbhaConfigError(
                f"ABDM_ENVIRONMENT must be one of "
                f"{', '.join(e.value for e in AbhaEnvironment)}, got {raw_env!r}"
            ) from None
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            environment=environment,
            timeout_seconds=float(os.getenv("ABDM_TIMEOUT_SECONDS", "30")),
            hip_id=os.getenv("ABDM_HIP_ID", "").strip(),
            hip_name=os.getenv("ABDM_HIP_NAME", "").strip() or "Kravos Setu",
            verify_gateway_tokens=os.getenv(
                "ABDM_VERIFY_GATEWAY_TOKENS", "true"
            ).strip().lower() not in ("0", "false", "no"),
        )

    @classmethod
    def is_configured(cls) -> bool:
        """Whether ABHA routes should be mounted at all.

        The rest of Kravos Setu must keep working with no ABDM credentials --
        the corpus demo does not need them -- so this is a check, not an
        assertion.
        """
        return bool(
            os.getenv("ABDM_CLIENT_ID", "").strip()
            and os.getenv("ABDM_CLIENT_SECRET", "").strip()
        )

    def __repr__(self) -> str:  # pragma: no cover - defensive, not logic
        # The secret must never reach a log line, a traceback, or a FastAPI
        # error page. dataclass's generated repr would print it verbatim.
        return (
            f"AbhaConfig(client_id={self.client_id!r}, "
            f"client_secret=<redacted>, environment={self.environment.value!r})"
        )
