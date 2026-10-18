"""Verifying that an inbound M2 callback really came from the ABDM gateway.

Every ``/api/v3/hip/*`` route is reachable by anyone who knows the bridge URL,
and they act on that trust: they store link tokens, and later they decide
which records get discovered and shared. The gateway sends a bearer JWT
signed by the ``central-registry`` realm. That is the same realm that signs
our own session token, and its keys are served by ``config.gateway_jwks_url``.

RS256 only. A token with any other ``alg`` is refused before key lookup.
Accepting ``none`` or HS256 against a public key is the textbook JWT bypass.

A valid signature and issuer are not enough on their own. Every integrator's
*session* token is signed by the same realm (checked live: ours verifies).
Without a further check, any sandbox client could call our callbacks. The
gateway's own tokens carry the ``gateway`` realm role and integrator tokens
do not; ours has ``hip``, ``hiu``, ``bridge`` and so on. The role name follows
ABDM's reference HIP implementation and still needs confirming against a real
gateway callback. A rejection logs the token's client and roles, so the
first refused callback shows whether it is right.

The signature check uses ``cryptography``, which M1 already depends on, so
PyJWT isn't needed for a single algorithm.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Any, Callable

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# Unknown kid triggers a refetch, but not more often than this -- a stream of
# forged tokens with random kids must not turn into a stream of JWKS fetches.
_REFETCH_INTERVAL_SECONDS = 300.0
_CLOCK_LEEWAY_SECONDS = 60
GATEWAY_ROLE = "gateway"


class GatewayAuthError(Exception):
    """The callback's token is missing, malformed, forged or expired."""


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _rsa_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    return rsa.RSAPublicNumbers(e, n).public_key()


def _fetch_jwks(url: str) -> dict[str, Any]:
    response = httpx.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


class GatewayTokenVerifier:
    def __init__(
        self,
        *,
        jwks_url: str,
        issuer: str,
        fetch: Callable[[str], dict[str, Any]] = _fetch_jwks,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._fetch = fetch
        self._clock = clock
        self._keys: dict[str, rsa.RSAPublicKey] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    def _key(self, kid: str) -> rsa.RSAPublicKey:
        with self._lock:
            key = self._keys.get(kid)
            if key is not None:
                return key
            now = self._clock()
            if (
                self._fetched_at is None
                or now - self._fetched_at >= _REFETCH_INTERVAL_SECONDS
            ):
                jwks = self._fetch(self._jwks_url)
                self._keys = {
                    k["kid"]: _rsa_key(k)
                    for k in jwks.get("keys", [])
                    if k.get("kty") == "RSA" and k.get("kid")
                }
                self._fetched_at = now
            key = self._keys.get(kid)
        if key is None:
            raise GatewayAuthError(f"token signed with unknown key {kid!r}")
        return key

    def verify(self, authorization: str | None) -> dict[str, Any]:
        """Return the token's claims, or raise GatewayAuthError."""
        if not authorization or not authorization.strip():
            raise GatewayAuthError("missing Authorization header")
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        parts = token.split(".")
        if len(parts) != 3:
            raise GatewayAuthError("Authorization is not a JWT")
        try:
            header = json.loads(_b64url_decode(parts[0]))
            claims = json.loads(_b64url_decode(parts[1]))
            signature = _b64url_decode(parts[2])
        except (ValueError, UnicodeDecodeError) as exc:
            raise GatewayAuthError("Authorization is not a JWT") from exc

        if header.get("alg") != "RS256":
            raise GatewayAuthError(f"unsupported alg {header.get('alg')!r}")
        kid = header.get("kid")
        if not kid:
            raise GatewayAuthError("token has no kid")

        try:
            self._key(kid).verify(
                signature,
                f"{parts[0]}.{parts[1]}".encode("ascii"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except InvalidSignature as exc:
            raise GatewayAuthError("token signature is invalid") from exc

        now = self._clock()
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or now > exp + _CLOCK_LEEWAY_SECONDS:
            raise GatewayAuthError("token is expired")
        if claims.get("iss") != self._issuer:
            raise GatewayAuthError(f"unexpected issuer {claims.get('iss')!r}")
        roles = (claims.get("realm_access") or {}).get("roles") or []
        if GATEWAY_ROLE not in roles:
            raise GatewayAuthError(
                f"token is not a gateway token (client "
                f"{claims.get('clientId') or claims.get('azp')!r}, roles {roles!r})"
            )
        return claims
