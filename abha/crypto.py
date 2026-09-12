"""RSA encryption of the identity fields ABDM refuses in plaintext.

Every Aadhaar number, mobile number, OTP value, password, ABHA number, email
and account index crossing this boundary is encrypted with ABDM's published
public key. Not TLS-in-addition -- ABDM rejects the plaintext outright, so
this is a correctness requirement before it is a security one.

Two details are easy to get wrong and expensive to debug, because both fail
as an opaque HTTP 400:

**The padding is OAEP with SHA-1, not SHA-256.** ABDM advertises the scheme
in its own certificate response as `RSA/ECB/OAEPWithSHA-1AndMGF1Padding`.
Java's name for that mode specifies the MGF1 digest and leaves the OAEP
digest implicit at SHA-1; a Python port that reads "SHA-1 and MGF1" and
passes `algorithm=SHA256, mgf=MGF1(SHA1)` produces ciphertext ABDM cannot
decrypt, with no error that says so. `_PADDING` below pins both to SHA-1 and
says why. SHA-1 here is ABDM's choice, fixed by their decryption side; it is
not a security decision this package is free to make.

**The ciphertext is Base64, not hex.** Standard alphabet, with padding.

The certificate is fetched from `/v3/profile/public/certificate` and cached.
It is public, unauthenticated, and stable for months, so refetching it on
every enrolment would add a round trip to every flow for nothing. The cache
carries a TTL rather than living forever: ABDM does rotate the key, and a
process that ran for a week against a stale key would fail every encryption
with no path to recovery short of a restart.
"""

from __future__ import annotations

import base64
import threading
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from abha.errors import AbhaError

# Both digests are SHA-1 deliberately. See the module docstring.
_PADDING = padding.OAEP(
    mgf=padding.MGF1(algorithm=hashes.SHA1()),
    algorithm=hashes.SHA1(),
    label=None,
)

_PEM_HEADER = "-----BEGIN PUBLIC KEY-----"
_PEM_FOOTER = "-----END PUBLIC KEY-----"

# One day. Short enough that a key rotation heals without a restart, long
# enough that the fetch is invisible in normal operation.
_CACHE_TTL_SECONDS = 86_400


class AbhaCryptoError(AbhaError):
    """The public key could not be parsed, or a value could not be encrypted."""


def normalize_public_key(raw: str) -> str:
    """Return a PEM block, whatever shape ABDM handed back.

    The certificate endpoint has been observed returning three things: a full
    PEM block, a bare base64 body with no header lines, and a JSON string
    with literal ``\n`` escapes instead of newlines. All three are the same
    key. Rejecting two of them would make this package work only against
    whichever variant happened to be live on the day it was written.
    """
    text = (raw or "").strip()
    if not text:
        raise AbhaCryptoError("ABDM returned an empty public key")

    text = text.replace("\\n", "\n").strip()
    if _PEM_HEADER in text:
        return text

    body = "".join(text.split())
    wrapped = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"{_PEM_HEADER}\n{wrapped}\n{_PEM_FOOTER}"


def load_public_key(raw: str) -> rsa.RSAPublicKey:
    pem = normalize_public_key(raw)
    try:
        key = serialization.load_pem_public_key(pem.encode("ascii"))
    except Exception as exc:  # noqa: BLE001 - cryptography raises several types
        raise AbhaCryptoError(f"could not parse ABDM public key: {exc}") from exc
    if not isinstance(key, rsa.RSAPublicKey):
        raise AbhaCryptoError(
            f"ABDM public key is {type(key).__name__}, expected RSA"
        )
    return key


def encrypt(value: str, public_key: rsa.RSAPublicKey) -> str:
    """Base64 ciphertext for one identity field.

    Empty input is rejected rather than encrypted. An empty Aadhaar field
    encrypts perfectly well and is accepted by this function's type
    signature, but means the caller lost the value somewhere upstream -- and
    ABDM's rejection of the result would point at the wrong layer.
    """
    if not value:
        raise AbhaCryptoError("refusing to encrypt an empty value")
    try:
        ciphertext = public_key.encrypt(value.encode("utf-8"), _PADDING)
    except ValueError as exc:
        # RSA-2048 with OAEP/SHA-1 caps the plaintext at 214 bytes. Nothing
        # ABDM asks to be encrypted comes close, so this means the caller
        # passed the wrong field entirely.
        raise AbhaCryptoError(
            f"value too long for RSA-OAEP encryption ({len(value)} chars): {exc}"
        ) from exc
    return base64.b64encode(ciphertext).decode("ascii")


class PublicKeyCache:
    """Thread-safe, TTL'd holder for the ABDM certificate.

    Deliberately has no opinion about *how* the key is fetched -- it takes a
    zero-argument callable. That keeps this module free of httpx and lets the
    tests exercise expiry and concurrency without a network stub.
    """

    def __init__(self, ttl_seconds: int = _CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._key: rsa.RSAPublicKey | None = None
        self._fetched_at: float = 0.0

    def get(self, fetch: "callable[[], str]") -> rsa.RSAPublicKey:
        now = time.monotonic()
        with self._lock:
            if self._key is not None and (now - self._fetched_at) < self._ttl:
                return self._key
        # Fetched outside the lock: the call is network-bound and holding the
        # lock across it would serialise every concurrent enrolment behind one
        # HTTP request. A racing double-fetch is harmless -- the key is
        # idempotent and both writers store the same value.
        raw = fetch()
        key = load_public_key(raw)
        with self._lock:
            self._key = key
            self._fetched_at = time.monotonic()
        return key

    def invalidate(self) -> None:
        """Drop the cached key so the next `get` refetches.

        Called when ABDM rejects a ciphertext it should have accepted, which
        is the observable symptom of a rotation that outran the TTL.
        """
        with self._lock:
            self._key = None
            self._fetched_at = 0.0
