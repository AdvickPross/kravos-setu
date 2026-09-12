"""The session token every other call depends on.

One exchange -- `clientId` + `clientSecret` -> `accessToken` -- against the
*gateway* host, which is not the host the token is then used against. See
`abha/config.py` for why that matters.

The lifetime is short: ABDM documents 1200 seconds for the access token and
1800 for the refresh token. That is short enough that a naive "fetch a token
per request" implementation looks fine in testing and only shows up later, as
a doubled request count against a rate-limited sandbox. It is also short
enough that "fetch once at startup" breaks any process that lives longer than
twenty minutes. So the token is cached and proactively refreshed a minute
early, and the refresh is guarded by a lock.

The lock is the part worth being careful about. Under concurrent load an
unguarded refresh has every in-flight request notice the expiry at the same
moment and fire its own session call, which is both wasteful and, against a
rate-limited endpoint, self-defeating. `_lock` serialises the refresh, and
the second waiter re-checks validity after acquiring it rather than
refreshing again -- the standard double-checked shape, which is only correct
*because* of that second check.

There is one deliberate omission. ABDM issues a `refreshToken`, and this
class stores it but does not use it to renew. Re-running `client_credentials`
costs exactly the same single round trip, cannot fail in a state this class
would then have to recover from, and needs no second code path. The token is
kept because callers occasionally need to hand it on, not because the refresh
grant buys anything here.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from abha.config import AbhaConfig
from abha.errors import AbhaAuthError
from abha.transport import Transport, base_headers

log = logging.getLogger(__name__)


@dataclass
class SessionToken:
    access_token: str
    expires_at: float
    refresh_token: str | None = None
    refresh_expires_at: float | None = None
    token_type: str = "bearer"
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def is_valid(self, *, leeway_seconds: int = 60, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < (
            self.expires_at - leeway_seconds
        )

    @property
    def authorization(self) -> str:
        """ABDM expects the `Bearer ` prefix even though it names the field
        `accessToken`; sending the bare token is a silent 401."""
        return f"Bearer {self.access_token}"

    def __repr__(self) -> str:  # pragma: no cover - defensive, not logic
        return (
            f"SessionToken(access_token=<redacted>, "
            f"expires_in={max(0, self.expires_at - time.time()):.0f}s)"
        )


class SessionManager:
    def __init__(
        self,
        config: AbhaConfig,
        transport: Transport,
        *,
        clock: "callable[[], float]" = time.time,
    ) -> None:
        self._config = config
        self._transport = transport
        # Injectable so expiry and the refresh race can be tested in
        # microseconds instead of by sleeping for twenty minutes.
        self._clock = clock
        self._lock = threading.Lock()
        self._token: SessionToken | None = None
        self.fetch_count = 0

    @property
    def token(self) -> SessionToken | None:
        return self._token

    def access_token(self) -> str:
        return self._current().access_token

    def authorization(self) -> str:
        return self._current().authorization

    def invalidate(self) -> None:
        """Force the next call to re-authenticate.

        Called when ABDM returns 401 against a token this class still believes
        is valid -- clock skew, or a revocation on their side.
        """
        with self._lock:
            self._token = None

    def _current(self) -> SessionToken:
        leeway = self._config.token_leeway_seconds
        now = self._clock()
        token = self._token
        if token is not None and token.is_valid(leeway_seconds=leeway, now=now):
            return token

        with self._lock:
            # Re-check: another thread may have refreshed while this one
            # waited. Without this the lock would serialise the calls but not
            # prevent them, which defeats the point.
            token = self._token
            if token is not None and token.is_valid(
                leeway_seconds=leeway, now=self._clock()
            ):
                return token
            self._token = self._fetch()
            return self._token

    def _fetch(self) -> SessionToken:
        # No Authorization header here -- this call is what produces one.
        # X-CM-ID is required on the session endpoint specifically.
        headers = base_headers(extra={"X-CM-ID": self._config.cm_id})
        body = {
            "clientId": self._config.client_id,
            "clientSecret": self._config.client_secret,
            "grantType": "client_credentials",
        }
        payload = self._transport.send_json(
            "POST", self._config.session_url, headers=headers, json_body=body
        )
        self.fetch_count += 1

        access = payload.get("accessToken") or payload.get("access_token")
        if not access:
            raise AbhaAuthError(
                "ABDM session response carried no accessToken: "
                f"{sorted(payload)!r}"
            )

        now = self._clock()
        # Default rather than require: a missing expiresIn is survivable, and
        # 1200s is ABDM's documented value. Assuming "never expires" would
        # not be.
        expires_in = int(payload.get("expiresIn") or 1200)
        refresh_in = payload.get("refreshExpiresIn")
        log.debug("acquired ABDM session token, expires in %ss", expires_in)
        return SessionToken(
            access_token=access,
            expires_at=now + expires_in,
            refresh_token=payload.get("refreshToken"),
            refresh_expires_at=now + int(refresh_in) if refresh_in else None,
            token_type=payload.get("tokenType") or "bearer",
            raw=payload,
        )
