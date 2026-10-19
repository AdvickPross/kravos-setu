"""ABDM health-data encryption ("Fidelius"), byte-compatible with the
reference implementation (github.com/mgrmtech/fidelius-cli, BouncyCastle).

The scheme (NHA catalogue, "How a health record is encrypted between HIP
and HIU"):

1. ECDH on **BouncyCastle's ``curve25519``**. This is Curve25519 in short
   **Weierstrass** form (``CustomNamedCurves.getByName("curve25519")``),
   not the Montgomery X25519 that ``cryptography`` implements. The points,
   keys and shared secret all differ, so X25519 from a library won't
   interoperate. The curve is implemented here directly. Its parameters
   are *derived* from the Montgomery constant A = 486662 rather than
   transcribed, and the test suite checks them against the reference CLI's
   published key pairs and ciphertext.
2. Salt and IV: XOR the two 32-byte nonces (sender's, then requester's).
   The first 20 bytes are the HKDF salt and the last 12 are the GCM IV.
3. Key: HKDF-SHA256 over the 32-byte big-endian **x coordinate** of the
   shared point, no ``info``, 32 bytes out.
4. AES-256-GCM, no associated data, with the 16-byte tag appended. Base64.

Key encodings, as the CLI emits them:

* public key: the uncompressed point ``04 || X || Y`` (65 bytes, 88 base64
  chars), or X.509 SubjectPublicKeyInfo with *explicit* curve parameters.
  ``keyMaterial.dhPublicKey.keyValue`` in ABDM data flow is usually the X.509
  form, and we answer in whichever form the requester used.
* private key: Java ``BigInteger.toByteArray()`` (two's-complement, minimal).

The arithmetic is plain Python big integers and not constant-time. That's
acceptable because every key here is ephemeral: a fresh key pair per
transfer, used for one ECDH and then discarded.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# -- the curve: Curve25519 mapped to short Weierstrass y^2 = x^3 + a x + b ----------

P = 2**255 - 19
_A_MONT = 486662
_INV3 = pow(3, -1, P)
A = (3 - _A_MONT * _A_MONT) * _INV3 % P
B = (2 * _A_MONT**3 - 9 * _A_MONT) * pow(27, -1, P) % P
N = 2**252 + 27742317777372353535851937790883648493  # prime order of G
GX = (9 + _A_MONT * _INV3) % P  # Montgomery u = 9, shifted by A/3
# y of the base point: the root BouncyCastle uses (the even one). Checked
# against the reference CLI's key pairs in tests/test_fidelius.py.
GY = 0x20AE19A1B8A086B4E01EDD2C7748D14C923D4D7E6D7C61B229E9C5A27ECED3D9

Point = tuple[int, int] | None  # None is the point at infinity


def _on_curve(pt: Point) -> bool:
    if pt is None:
        return False
    x, y = pt
    return 0 <= x < P and 0 <= y < P and (y * y - (x * x * x + A * x + B)) % P == 0


def _add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    return x3, (slope * (x1 - x3) - y1) % P


def _mul(k: int, pt: Point) -> Point:
    result: Point = None
    addend = pt
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


G: Point = (GX, GY)

# X.509 SubjectPublicKeyInfo for this curve with explicit parameters, up to
# and including the BIT STRING header; the 65-byte point follows. Taken from
# the reference CLI's own x509PublicKey output.
_X509_PREFIX = base64.b64decode(
    "MIIBMTCB6gYHKoZIzj0CATCB3gIBATArBgcqhkjOPQEBAiB/////////////////////////////////////////7TBE"
    "BCAqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqYSRShRAQge0Je0Je0Je0Je0Je0Je0Je0Je0Je0Je0JgtenHcQ"
    "yGQEQQQqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq0kWiCuGaG4oIa04B7dLHdI0UySPU1+bXxhsinpxaJ+"
    "ztPZAiAQAAAAAAAAAAAAAAAAAAAAFN753qL3nNZYEmMaXPXT7QIBCANCAA=="
)


class FideliusError(ValueError):
    """Key material that cannot be used: malformed, or not on the curve."""


def _point_bytes(pt: Point) -> bytes:
    assert pt is not None
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


def encode_public_key(pt: Point, *, x509: bool) -> str:
    raw = _point_bytes(pt)
    return base64.b64encode(_X509_PREFIX + raw if x509 else raw).decode("ascii")


def decode_public_key(value: str) -> tuple[Point, bool]:
    """Parse a requester's public key. Returns (point, was_x509).

    The point is validated to lie on the curve. An off-curve point is the
    classic invalid-curve attack on ECDH, and it's never used.
    """
    try:
        data = base64.b64decode(value.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise FideliusError("public key is not base64") from exc
    if len(data) == 65:
        raw, x509 = data, False
    elif len(data) == len(_X509_PREFIX) + 65 and data.startswith(_X509_PREFIX):
        raw, x509 = data[len(_X509_PREFIX):], True
    else:
        raise FideliusError("public key is neither a raw curve25519 point nor its X.509 form")
    if raw[0] != 0x04:
        raise FideliusError("only uncompressed points are supported")
    pt = (int.from_bytes(raw[1:33], "big"), int.from_bytes(raw[33:], "big"))
    if not _on_curve(pt):
        raise FideliusError("public key is not on curve25519")
    return pt, x509


def decode_private_key(value: str) -> int:
    """Java ``BigInteger(byte[])``: two's-complement, big-endian."""
    d = int.from_bytes(base64.b64decode(value), "big", signed=True)
    if not 0 < d < N:
        raise FideliusError("private key out of range")
    return d


def encode_private_key(d: int) -> str:
    """Java ``BigInteger.toByteArray()``: minimal two's-complement."""
    return base64.b64encode(d.to_bytes(d.bit_length() // 8 + 1, "big", signed=True)).decode("ascii")


@dataclass(frozen=True)
class KeyMaterial:
    private_key: int
    public_point: Point
    nonce: bytes  # 32 bytes

    @property
    def nonce_b64(self) -> str:
        return base64.b64encode(self.nonce).decode("ascii")

    def public_key(self, *, x509: bool) -> str:
        return encode_public_key(self.public_point, x509=x509)


def generate_key_material() -> KeyMaterial:
    d = secrets.randbelow(N - 1) + 1
    return KeyMaterial(private_key=d, public_point=_mul(d, G), nonce=secrets.token_bytes(32))


def public_point_for(private_key: int) -> Point:
    return _mul(private_key, G)


def _shared_x(private_key: int, peer: Point) -> bytes:
    shared = _mul(private_key, peer)
    if shared is None:
        raise FideliusError("ECDH produced the point at infinity")
    return shared[0].to_bytes(32, "big")


def _salt_iv(sender_nonce: bytes, requester_nonce: bytes) -> tuple[bytes, bytes]:
    if len(sender_nonce) != 32 or len(requester_nonce) != 32:
        raise FideliusError("nonces must be 32 bytes")
    xor = bytes(a ^ b for a, b in zip(sender_nonce, requester_nonce))
    return xor[:20], xor[-12:]


def _aes_key(private_key: int, peer: Point, salt: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=None).derive(
        _shared_x(private_key, peer)
    )


def encrypt(
    plaintext: bytes,
    *,
    sender_private_key: int,
    sender_nonce: bytes,
    requester_public_key: str,
    requester_nonce: bytes,
) -> str:
    """Encrypt for the requester (the HIU). Returns base64(ciphertext || tag)."""
    peer, _ = decode_public_key(requester_public_key)
    salt, iv = _salt_iv(sender_nonce, requester_nonce)
    key = _aes_key(sender_private_key, peer, salt)
    return base64.b64encode(AESGCM(key).encrypt(iv, plaintext, None)).decode("ascii")


def decrypt(
    encrypted: str,
    *,
    requester_private_key: int,
    requester_nonce: bytes,
    sender_public_key: str,
    sender_nonce: bytes,
) -> bytes:
    """The HIU's side. Here for round-trip tests and for a future HIU (M3)."""
    peer, _ = decode_public_key(sender_public_key)
    salt, iv = _salt_iv(sender_nonce, requester_nonce)
    key = _aes_key(requester_private_key, peer, salt)
    return AESGCM(key).decrypt(iv, base64.b64decode(encrypted), None)
