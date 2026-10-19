"""abha.gateway_auth: only a gateway-signed, unexpired RS256 token passes."""

from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from abha.gateway_auth import GatewayAuthError, GatewayTokenVerifier

ISSUER = "https://dev.abdm.gov.in/auth/realms/central-registry"
NOW = 1_800_000_000.0


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwk(key: rsa.RSAPrivateKey, kid: str, alg: str = "RS256") -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "alg": alg,
        "e": _b64(numbers.e.to_bytes(3, "big")),
        "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
    }


def _token(key: rsa.RSAPrivateKey, *, kid="k1", alg="RS256", **claims) -> str:
    body = {
        "iss": ISSUER,
        "exp": NOW + 600,
        "realm_access": {"roles": ["gateway", "offline_access"]},
        **claims,
    }
    head = _b64(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    payload = _b64(json.dumps(body).encode())
    digest = hashes.SHA512() if alg == "RS512" else hashes.SHA256()
    signature = key.sign(f"{head}.{payload}".encode(), padding.PKCS1v15(), digest)
    return f"Bearer {head}.{payload}.{_b64(signature)}"


@pytest.fixture(scope="module")
def gateway_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def fetches() -> list[str]:
    return []


@pytest.fixture
def verifier(gateway_key, fetches) -> GatewayTokenVerifier:
    def fetch(url: str) -> dict:
        fetches.append(url)
        # Mirrors the real gateway JWKS: one RS256 key and one RS512 key.
        return {"keys": [_jwk(gateway_key, "k1", "RS256"), _jwk(gateway_key, "k2", "RS512")]}

    return GatewayTokenVerifier(
        jwks_url="https://dev.abdm.gov.in/gateway/v0.5/certs",
        issuer=ISSUER,
        fetch=fetch,
        clock=lambda: NOW,
    )


def test_valid_gateway_token_passes(verifier, gateway_key):
    claims = verifier.verify(_token(gateway_key, clientId="gateway"))
    assert claims["clientId"] == "gateway"


def test_token_signed_by_another_key_is_refused(verifier):
    forger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(GatewayAuthError, match="signature"):
        verifier.verify(_token(forger))


def test_rs512_token_on_the_rs512_key_passes(verifier, gateway_key):
    """ABDM's sample gateway token is RS512."""
    assert verifier.verify(_token(gateway_key, kid="k2", alg="RS512"))["iss"] == ISSUER


def test_alg_must_match_the_key_it_names(verifier, gateway_key):
    with pytest.raises(GatewayAuthError, match="does not match"):
        verifier.verify(_token(gateway_key, kid="k1", alg="RS512"))


@pytest.mark.parametrize("alg", ["none", "HS256", "PS256"])
def test_other_algorithms_are_refused(verifier, gateway_key, alg):
    with pytest.raises(GatewayAuthError, match="alg"):
        verifier.verify(_token(gateway_key, alg=alg))


def test_expired_token_is_refused(verifier, gateway_key):
    with pytest.raises(GatewayAuthError, match="expired"):
        verifier.verify(_token(gateway_key, exp=NOW - 3600))


def test_wrong_issuer_is_refused(verifier, gateway_key):
    with pytest.raises(GatewayAuthError, match="issuer"):
        verifier.verify(_token(gateway_key, iss="https://evil.example/realms/x"))


def test_another_integrators_session_token_is_refused(verifier, gateway_key):
    """Same realm, valid signature -- but an integrator, not the gateway.
    Roles copied from a real sandbox session token."""
    integrator_roles = ["hip", "hiu", "bridge", "healthId", "offline_access"]
    with pytest.raises(GatewayAuthError, match="not a gateway token"):
        verifier.verify(
            _token(gateway_key, realm_access={"roles": integrator_roles}, clientId="SBXID_X")
        )


@pytest.mark.parametrize("header", [None, "", "Bearer not-a-jwt", "Bearer a.b"])
def test_missing_or_malformed_header_is_refused(verifier, header):
    with pytest.raises(GatewayAuthError):
        verifier.verify(header)


def test_unknown_kids_do_not_cause_a_fetch_per_request(verifier, gateway_key, fetches):
    verifier.verify(_token(gateway_key))
    for i in range(5):
        with pytest.raises(GatewayAuthError, match="unknown key"):
            verifier.verify(_token(gateway_key, kid=f"random-{i}"))
    assert len(fetches) == 1
