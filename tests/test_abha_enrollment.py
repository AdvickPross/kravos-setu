"""Enrolment by Aadhaar: what actually goes on the wire.

These assertions are about the request, not the response, for the reason set
out in `tests/abha_support.py`: the response is ABDM's and cannot be tested
without ABDM, but every realistic failure in this flow is a property of what
this code sends.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from abha_support import FakeAbdm, error

from abha.endpoints import ENROLMENT_CONSENT
from abha.enrollment import (
    enrol_by_aadhaar,
    request_aadhaar_otp,
    request_mobile_otp,
    set_abha_address,
    suggest_abha_addresses,
    verify_mobile_otp,
)
from abha.errors import AbhaApiError, AbhaFlowError

AADHAAR = "999999999999"
MOBILE = "9999999999"


@pytest.fixture
def abdm() -> FakeAbdm:
    return FakeAbdm(
        {
            "/v3/enrollment/request/otp": {"txnId": "txn-1", "message": "OTP sent"},
            "/v3/enrollment/enrol/byAadhaar": {
                "txnId": "txn-1",
                "ABHAProfile": {
                    "ABHANumber": "91-1234-5678-9012",
                    "preferredAbhaAddress": "test@sbx",
                    "name": "Test Patient",
                    "mobile": MOBILE,
                },
                "tokens": {"token": "x-tok", "refreshToken": "r-tok"},
                "new": True,
            },
            "/v3/enrollment/auth/byAbdm": {"txnId": "txn-1", "message": "verified"},
            "/v3/enrollment/enrol/suggestion": {
                "txnId": "txn-1",
                "abhaAddressList": ["testuser1@sbx", "testuser2@sbx"],
            },
            "/v3/enrollment/enrol/abha-address": {
                "txnId": "txn-1",
                "preferredAbhaAddress": "testuser1@sbx",
            },
        }
    )


# --------------------------------------------------------------------------
# request shape
# --------------------------------------------------------------------------


def test_aadhaar_is_encrypted_and_never_sent_in_the_clear(abdm: FakeAbdm):
    """The assertion that justifies the whole fixture: decrypt what was sent
    and check ABDM would have read the right number -- and that the number
    appears nowhere in the raw bytes."""
    request_aadhaar_otp(abdm.client(), AADHAAR)
    body = abdm.body("/v3/enrollment/request/otp")

    assert abdm.decrypt(body["loginId"]) == AADHAAR
    assert AADHAAR not in abdm.last("/v3/enrollment/request/otp").content.decode()


def test_first_otp_request_sends_an_empty_txn_id(abdm: FakeAbdm):
    """ABDM mints the txnId here. Sending a stale one from a previous flow is
    rejected, so this field is not a parameter anyone can thread through."""
    request_aadhaar_otp(abdm.client(), AADHAAR)
    assert abdm.body("/v3/enrollment/request/otp")["txnId"] == ""


def test_enrolment_otp_request_uses_the_aadhaar_otp_system(abdm: FakeAbdm):
    """`aadhaar` means UIDAI sends the OTP. `abdm` would mean ABDM does, and
    is what the mobile-attach call uses -- the two are not interchangeable."""
    request_aadhaar_otp(abdm.client(), AADHAAR)
    body = abdm.body("/v3/enrollment/request/otp")
    assert body["otpSystem"] == "aadhaar"
    assert body["loginHint"] == "aadhaar"
    assert body["scope"] == ["abha-enrol"]


def test_aadhaar_formatting_is_tolerated(abdm: FakeAbdm):
    """Patients type spaces and hyphens; ABDM rejects both."""
    request_aadhaar_otp(abdm.client(), "9999 9999 9999")
    assert abdm.decrypt(abdm.body("/v3/enrollment/request/otp")["loginId"]) == AADHAAR


@pytest.mark.parametrize("bad", ["12345", "99999999999a", ""])
def test_malformed_aadhaar_is_rejected_locally(abdm: FakeAbdm, bad: str):
    """ABDM's rejection of a malformed Aadhaar is indistinguishable from its
    rejection of a valid but unenrolled one, and the two need different
    responses -- so this one is caught here."""
    with pytest.raises(AbhaFlowError):
        request_aadhaar_otp(abdm.client(), bad)
    assert not abdm.sent_to("/v3/enrollment/request/otp")


def test_otp_is_encrypted_but_mobile_is_not(abdm: FakeAbdm):
    """The asymmetry is ABDM's and is not guessable: `otpValue` must be
    encrypted, `mobile` in the same block must not be. Encrypting the mobile
    "for safety" is rejected."""
    enrol_by_aadhaar(abdm.client(), txn_id="txn-1", otp="123456", mobile=MOBILE)
    otp_block = abdm.body("/v3/enrollment/enrol/byAadhaar")["authData"]["otp"]

    assert abdm.decrypt(otp_block["otpValue"]) == "123456"
    assert otp_block["mobile"] == MOBILE


def test_enrolment_carries_the_consent_artefact(abdm: FakeAbdm):
    """The version is the consent *text* version, not the API version, and
    ABDM rejects a mismatch."""
    enrol_by_aadhaar(abdm.client(), txn_id="txn-1", otp="123456")
    assert abdm.body("/v3/enrollment/enrol/byAadhaar")["consent"] == ENROLMENT_CONSENT
    assert ENROLMENT_CONSENT == {"code": "abha-enrollment", "version": "1.4"}


def test_otp_block_timestamp_is_not_iso8601(abdm: FakeAbdm):
    """Two different time formats in one request. The header is ISO-8601 with
    a Z; the auth block wants 'YYYY-MM-DD HH:MM:SS'. Sending the header
    format inside the block is rejected."""
    enrol_by_aadhaar(abdm.client(), txn_id="txn-1", otp="123456")
    stamp = abdm.body("/v3/enrollment/enrol/byAadhaar")["authData"]["otp"]["timeStamp"]
    assert "T" not in stamp and "Z" not in stamp
    assert stamp.count("-") == 2 and stamp.count(":") == 2
    assert abdm.headers("/v3/enrollment/enrol/byAadhaar")["TIMESTAMP"].endswith("Z")


def test_every_call_carries_the_mandatory_headers(abdm: FakeAbdm):
    enrol_by_aadhaar(abdm.client(), txn_id="txn-1", otp="123456")
    for request in abdm.requests:
        assert request.headers.get("REQUEST-ID"), request.url.path
        assert request.headers.get("TIMESTAMP"), request.url.path


def test_session_token_is_fetched_once_for_a_whole_flow(abdm: FakeAbdm):
    """Three business calls must not mean three session calls."""
    client = abdm.client()
    request_aadhaar_otp(client, AADHAAR)
    enrol_by_aadhaar(client, txn_id="txn-1", otp="123456")
    suggest_abha_addresses(client, txn_id="txn-1")
    assert len(abdm.sent_to("/gateway/v3/sessions")) == 1
    assert len(abdm.sent_to("/v3/profile/public/certificate")) == 1


# --------------------------------------------------------------------------
# the null-mobile trap
# --------------------------------------------------------------------------


def test_mobile_saved_as_null_is_surfaced_not_swallowed(caplog):
    """ABDM returns 200 with a complete-looking profile when the supplied
    mobile is not the Aadhaar-linked one, and saves `mobile` as null. The
    ABHA is real but has no contact route, so every later OTP login fails.
    Nothing in the response says so."""
    abdm = FakeAbdm(
        {
            "/v3/enrollment/enrol/byAadhaar": {
                "ABHAProfile": {"ABHANumber": "91-1111", "mobile": None},
                "tokens": {"token": "x-tok"},
            }
        }
    )
    with caplog.at_level(logging.WARNING, logger="abha.enrollment"):
        result = enrol_by_aadhaar(
            abdm.client(), txn_id="txn-1", otp="123456", mobile="8888888888"
        )

    assert result.mobile_is_linked is False
    assert "null" in caplog.text


def test_mobile_attach_flips_to_the_abdm_otp_system(abdm: FakeAbdm):
    """The repair path for the trap above. `otpSystem` is `abdm` here: the
    OTP goes to the number being attached, not to the one UIDAI has."""
    request_mobile_otp(abdm.client(), txn_id="txn-1", mobile=MOBILE, token="x-tok")
    body = abdm.body("/v3/enrollment/request/otp")

    assert body["otpSystem"] == "abdm"
    assert body["loginHint"] == "mobile"
    assert body["scope"] == ["abha-enrol", "mobile-verify"]
    assert body["txnId"] == "txn-1", "the txnId must chain from enrolment"
    assert abdm.decrypt(body["loginId"]) == MOBILE


def test_mobile_attach_sends_the_transaction_token_as_x_token(abdm: FakeAbdm):
    """Not the session token -- that identifies the integrator, not the
    patient. Both headers are present and they are different values."""
    request_mobile_otp(abdm.client(), txn_id="txn-1", mobile=MOBILE, token="x-tok")
    headers = abdm.headers("/v3/enrollment/request/otp")

    assert headers["X-token"] == "Bearer x-tok"
    assert headers["Authorization"] == "Bearer session-token-1"


def test_already_prefixed_token_is_not_double_prefixed(abdm: FakeAbdm):
    request_mobile_otp(
        abdm.client(), txn_id="txn-1", mobile=MOBILE, token="Bearer x-tok"
    )
    assert abdm.headers("/v3/enrollment/request/otp")["X-token"] == "Bearer x-tok"


def test_mobile_verify_encrypts_the_otp_and_keeps_the_scope(abdm: FakeAbdm):
    verify_mobile_otp(abdm.client(), txn_id="txn-1", otp="654321", token="x-tok")
    body = abdm.body("/v3/enrollment/auth/byAbdm")
    assert body["scope"] == ["abha-enrol", "mobile-verify"]
    assert abdm.decrypt(body["authData"]["otp"]["otpValue"]) == "654321"


@pytest.mark.parametrize("bad", ["99999", "abcdefghij"])
def test_malformed_mobile_is_rejected_locally(abdm: FakeAbdm, bad: str):
    with pytest.raises(AbhaFlowError):
        request_mobile_otp(abdm.client(), txn_id="t", mobile=bad, token="x")


# --------------------------------------------------------------------------
# ABHA address
# --------------------------------------------------------------------------


def test_suggestion_sends_the_txn_id_in_a_header_not_the_body(abdm: FakeAbdm):
    """This one call takes the txnId as `Transaction_Id`; every other takes
    it in the body. The inconsistency is ABDM's."""
    assert suggest_abha_addresses(abdm.client(), txn_id="txn-1") == [
        "testuser1@sbx",
        "testuser2@sbx",
    ]
    request = abdm.last("/v3/enrollment/enrol/suggestion")
    assert request.headers["Transaction_Id"] == "txn-1"
    assert not request.content


def test_preferred_flag_is_sent_as_an_int_not_a_bool(abdm: FakeAbdm):
    """ABDM wants 1/0 here; a JSON `true` is rejected."""
    set_abha_address(
        abdm.client(), txn_id="txn-1", abha_address="testuser1@sbx", preferred=True
    )
    body = abdm.body("/v3/enrollment/enrol/abha-address")
    assert body["preferred"] == 1
    assert not isinstance(body["preferred"], bool)


# --------------------------------------------------------------------------
# failure handling
# --------------------------------------------------------------------------


def test_expired_otp_keeps_its_abdm_code_for_the_caller_to_branch_on():
    """"OTP expired" and "already enrolled" both arrive as 400 and need
    different responses; a caller that only sees the status cannot tell."""
    abdm = FakeAbdm(
        {"/v3/enrollment/enrol/byAadhaar": error(400, "ABDM-1044", "OTP expired")}
    )
    with pytest.raises(AbhaApiError) as exc:
        enrol_by_aadhaar(abdm.client(), txn_id="txn-1", otp="000000")
    assert exc.value.code == "ABDM-1044"
    assert exc.value.status_code == 400


def test_a_401_triggers_exactly_one_re_authentication():
    """A stale session heals silently; a retry *loop* against an auth failure
    is how a client id gets rate-limited."""
    abdm = FakeAbdm(
        {
            "/gateway/v3/sessions": [
                {"accessToken": "stale", "expiresIn": 1200},
                {"accessToken": "fresh", "expiresIn": 1200},
            ],
            "/v3/enrollment/enrol/abha-address": [
                httpx.Response(401, json={"message": "token expired"}),
                {"preferredAbhaAddress": "testuser1@sbx"},
            ],
        }
    )
    client = abdm.client()
    set_abha_address(client, txn_id="t", abha_address="testuser1@sbx")

    assert len(abdm.sent_to("/gateway/v3/sessions")) == 2
    assert len(abdm.sent_to("/v3/enrollment/enrol/abha-address")) == 2
    assert abdm.headers("/v3/enrollment/enrol/abha-address")["Authorization"] == (
        "Bearer fresh"
    )


def test_a_401_that_persists_is_not_retried_forever():
    abdm = FakeAbdm(
        {"/v3/enrollment/enrol/abha-address": httpx.Response(401, json={"m": "no"})}
    )
    with pytest.raises(AbhaApiError):
        set_abha_address(abdm.client(), txn_id="t", abha_address="testuser1@sbx")
    assert len(abdm.sent_to("/v3/enrollment/enrol/abha-address")) == 2


def test_a_decryption_failure_drops_the_key_so_the_next_attempt_recovers():
    """A rotated certificate surfaces as a 400 blaming the Aadhaar.

    The call is deliberately *not* replayed -- the body was encrypted before
    it reached the client, so a replay would resend the same stale
    ciphertext. What is asserted is the recovery that does work: the cached
    key is discarded, the error says so, and the caller's next attempt
    fetches a fresh certificate and succeeds.
    """
    abdm = FakeAbdm(
        {
            "/v3/enrollment/request/otp": [
                error(400, "ABDM-1", "unable to decrypt loginId"),
                {"txnId": "txn-1"},
            ]
        }
    )
    client = abdm.client()

    with pytest.raises(AbhaApiError) as exc:
        request_aadhaar_otp(client, AADHAAR)
    assert "discarded" in exc.value.message
    assert len(abdm.sent_to("/v3/enrollment/request/otp")) == 1

    assert request_aadhaar_otp(client, AADHAAR).txn_id == "txn-1"
    assert len(abdm.sent_to("/v3/profile/public/certificate")) == 2


def test_an_ordinary_400_does_not_drop_the_key():
    """A broad version of the heuristic above would turn every typo into an
    extra certificate fetch, and hide real bad-input bugs."""
    abdm = FakeAbdm(
        {"/v3/enrollment/request/otp": error(400, "ABDM-9", "aadhaar not enrolled")}
    )
    client = abdm.client()
    with pytest.raises(AbhaApiError):
        request_aadhaar_otp(client, AADHAAR)
    with pytest.raises(AbhaApiError):
        request_aadhaar_otp(client, AADHAAR)
    assert len(abdm.sent_to("/v3/profile/public/certificate")) == 1
    assert len(abdm.sent_to("/v3/enrollment/request/otp")) == 2


# --------------------------------------------------------------------------
# ABHA address rules (CRT_ABHA_112)
# --------------------------------------------------------------------------


def test_valid_addresses_are_accepted():
    from abha.enrollment import validate_abha_address

    for good in ("animesh.reddy", "ok_name12", "abcdefgh", "a" * 18):
        assert validate_abha_address(good) == good


def test_the_sbx_suffix_is_tolerated():
    """Patients and UIs both paste the full address; the rules apply to the
    local part, which is what the API takes."""
    from abha.enrollment import validate_abha_address

    assert validate_abha_address("valid.name@sbx") == "valid.name@sbx"


@pytest.mark.parametrize(
    "bad,because",
    [
        ("ab", "8-18 characters"),
        ("a" * 19, "8-18 characters"),
        ("has space1", "only letters"),
        ("has-dash12", "only letters"),
        ("_leading123", "never first or last"),
        ("trailing12_", "never first or last"),
        (".leading123", "never first or last"),
        ("a.b.c12345", "at most one dot"),
        ("a_b_c12345", "at most one underscore"),
    ],
)
def test_each_broken_rule_names_itself(bad: str, because: str):
    """A generic "invalid ABHA address" sends the patient guessing between
    five rules."""
    from abha.enrollment import validate_abha_address

    with pytest.raises(AbhaFlowError) as exc:
        validate_abha_address(bad)
    assert because in str(exc.value)


def test_a_malformed_address_never_reaches_abdm(abdm: FakeAbdm):
    with pytest.raises(AbhaFlowError):
        set_abha_address(abdm.client(), txn_id="txn-1", abha_address="ab")
    assert not abdm.sent_to("/v3/enrollment/enrol/abha-address")
