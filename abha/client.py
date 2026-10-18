"""The one object the flow modules hold: config + session + key + transport.

Everything above this line in the stack (`enrollment`, `login`, `profile`,
`biometrics`) states *what* it wants; this decides how a request is dressed
and what to do when the answer is a refusal.

Two recoveries live here because both are invisible to the caller and both
otherwise surface as an inexplicable 400:

**Stale session.** ABDM returns 401 when a token this process still believes
is valid has been revoked, or when the clocks disagree. `_with_recovery`
drops the cached token and replays once. Exactly once -- a retry loop against
an auth failure is how an integration gets its client id rate-limited.

**Rotated public key.** When ABDM rotates its certificate, ciphertext built
with the cached key stops decrypting and comes back as a generic
"invalid Aadhaar"-class 400, which is a deeply misleading message: the
Aadhaar is fine, the key is stale. This one is *not* replayed, deliberately.
The body reaching this layer is already encrypted -- the flow module called
`encrypt()` while building it -- so a replay would resend the same stale
ciphertext and fail identically. What happens instead is that the cached key
is dropped and the error is re-raised with a line saying so, which makes the
caller's next attempt succeed against a fresh key. Making the replay real
would mean deferring encryption to send time via a marker object threaded
through the transport; that is a lot of machinery to avoid one user-visible
retry of an event with a 24-hour TTL, and it still could not help the
flows whose `txnId` has already advanced.

The heuristic for recognising it is narrow on purpose -- it fires only when
the request actually carried ciphertext and the error text mentions
decryption -- because a broad version would drop the key on genuine
bad-input errors and turn every typo into an extra certificate fetch.

Header assembly is the other reason this class exists. Which of `X-token`,
`T-token` and `BENEFIT-NAME` a call needs is a property of the endpoint, not
something a caller should be remembering at each site; `post`/`get` take them
as keyword arguments and drop the ones left unset.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import httpx

from abha import endpoints
from abha.config import AbhaConfig
from abha.crypto import PublicKeyCache, encrypt
from abha.errors import AbhaApiError, AbhaFlowError
from abha.session import SessionManager
from abha.transport import Transport, base_headers

log = logging.getLogger(__name__)

# Substrings that mark a 400 as "your ciphertext did not decrypt" rather than
# "your data is wrong". Matched case-insensitively against code and message.
_STALE_KEY_MARKERS = ("decrypt", "invalid encryption", "cipher", "bad padding")


class AbhaClient:
    def __init__(
        self,
        config: AbhaConfig,
        *,
        http_client: httpx.Client | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or Transport(
            http_client, timeout=config.timeout_seconds
        )
        self.session = SessionManager(config, self.transport)
        self._keys = PublicKeyCache()

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> "AbhaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- encryption --------------------------------------------------------

    def encrypt(self, value: str) -> str:
        """Encrypt one identity field with ABDM's current public key."""
        return encrypt(value, self._keys.get(self._fetch_certificate))

    def _fetch_certificate(self) -> str:
        # The certificate endpoint is public, but ABDM still wants the
        # session token on it; calling it unauthenticated returns 401.
        payload = self.transport.send_json(
            "GET",
            self.config.abha_url(endpoints.PUBLIC_CERTIFICATE),
            headers=base_headers(extra={"Authorization": self.session.authorization()}),
        )
        key = payload.get("publicKey") or payload.get("data")
        if not key:
            raise AbhaApiError(
                f"certificate response carried no publicKey: {sorted(payload)!r}",
                status_code=200,
            )
        return key

    # -- requests ----------------------------------------------------------

    def post(
        self,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        host: str = "abha",
        x_token: str | None = None,
        t_token: str | None = None,
        benefit_name: str | None = None,
        transaction_id: str | None = None,
        encrypted: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            path,
            body=body,
            host=host,
            x_token=x_token,
            t_token=t_token,
            benefit_name=benefit_name,
            transaction_id=transaction_id,
            encrypted=encrypted,
            extra_headers=extra_headers,
        )

    def get(
        self,
        path: str,
        *,
        host: str = "abha",
        x_token: str | None = None,
        t_token: str | None = None,
        benefit_name: str | None = None,
        transaction_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            path,
            body=None,
            host=host,
            x_token=x_token,
            t_token=t_token,
            benefit_name=benefit_name,
            transaction_id=transaction_id,
        )

    def get_bytes(
        self,
        path: str,
        *,
        host: str = "abha",
        x_token: str | None = None,
    ) -> bytes:
        """For the endpoints that answer with a PNG rather than JSON.

        The ABHA card comes back as image bytes; routing it through
        `send_json` would raise on a perfectly successful response.
        """
        url = self._url(path, host)
        response = self.transport.send(
            "GET", url, headers=self._headers(x_token=x_token)
        )
        return response.content

    # -- internals ---------------------------------------------------------

    def _url(self, path: str, host: str) -> str:
        if host == "abha":
            return self.config.abha_url(path)
        if host == "phr":
            return self.config.phr_url(path)
        if host == "gateway":
            return self.config.gateway_url(path)
        raise AbhaFlowError(f"unknown host {host!r}; expected abha, phr or gateway")

    def _headers(
        self,
        *,
        x_token: str | None = None,
        t_token: str | None = None,
        benefit_name: str | None = None,
        transaction_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        headers = base_headers(
            extra={
                "Authorization": self.session.authorization(),
                # ABDM writes this one without the Bearer prefix in some
                # examples and with it in others.
                "X-token": _bearer(x_token),
                "T-token": _bearer(t_token),
                "BENEFIT-NAME": benefit_name,
                "Transaction_Id": transaction_id,
            }
        )

        if extra_headers:
            headers.update(extra_headers)

        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None,
        host: str,
        x_token: str | None,
        t_token: str | None,
        benefit_name: str | None,
        transaction_id: str | None,
        encrypted: bool = False,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._url(path, host)

        def send() -> dict[str, Any]:
            headers = self._headers(
                x_token=x_token,
                t_token=t_token,
                benefit_name=benefit_name,
                transaction_id=transaction_id,
                extra_headers=extra_headers,
            )
            return self.transport.send_json(
                method, url, headers=headers, json_body=body
            )

        return self._with_recovery(send, encrypted=encrypted, url=url)

    def _with_recovery(
        self,
        send: Callable[[], dict[str, Any]],
        *,
        encrypted: bool,
        url: str,
    ) -> dict[str, Any]:
        try:
            return send()
        except AbhaApiError as exc:
            if exc.status_code == 401:
                log.info("session token rejected on %s; re-authenticating once", url)
                self.session.invalidate()
                return send()
            if encrypted and _looks_like_stale_key(exc):
                # Dropped, not replayed -- see the module docstring. The next
                # attempt re-encrypts against a freshly fetched key.
                log.info("ciphertext rejected on %s; dropping cached public key", url)
                self._keys.invalidate()
                exc.message += (
                    " -- the cached ABDM public key has been discarded; this "
                    "usually means the certificate rotated, and retrying the "
                    "flow will re-encrypt against a fresh one"
                )
                raise exc from None
            raise


def _bearer(token: str | None) -> str | None:
    if not token:
        return None
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def _looks_like_stale_key(error: AbhaApiError) -> bool:
    if error.status_code != 400:
        return False
    haystack = f"{error.code or ''} {error.message}".lower()
    return any(marker in haystack for marker in _STALE_KEY_MARKERS)
