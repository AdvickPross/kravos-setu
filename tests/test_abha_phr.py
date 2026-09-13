"""ABHA-address verification on the PHR host. NHA M1 VRFY_ABHA_102/202.

Every shape asserted here was confirmed against the live sandbox before it
was written down, using non-existent identifiers so validation would pass and
the lookup would fail. The tests then pin it so a later refactor cannot
quietly move the flow back onto the ABHA host, which is where it does not
work and where it looks like it should.
"""

from __future__ import annotations

import pytest
from abha_support import FakeAbdm

from abha.errors import AbhaFlowError
from abha.login import LoginMethod, _METHODS
from abha.phr import (
    AddressOtpChannel,
    request_address_otp,
    search_abha_address,
    verify_address_otp,
)

SEARCH = "/v3/phr/web/login/abha/search"
OTP = "/v3/phr/web/login/abha/request/otp"
VERIFY = "/v3/phr/web/login/abha/verify"
ADDRESS = "patient@sbx"


@pytest.fixture
def abdm() -> FakeAbdm:
    return FakeAbdm(
        {
            SEARCH: {"ABHANumber": "91-1111-2222-3333", "name": "Test Patient"},
            OTP: {"txnId": "phr-txn", "message": "OTP sent to XXXXXX8769"},
            VERIFY: {"token": "phr-x-tok", "refreshToken": "phr-r-tok"},
        }
    )


# --------------------------------------------------------------------------
# the reason this module exists
# --------------------------------------------------------------------------


def test_abha_address_is_not_a_login_method_on_the_abha_host():
    """The regression guard for the actual bug.

    An ABHA address looks like a fourth credential alongside the other three,
    and the first implementation added it to that table. The sandbox rejects
    it: `{"scope": "Invalid Scope", "loginHint": "Invalid Login Hint"}`.
    """
    assert "abha-address" not in {m.value for m in LoginMethod}
    assert all(
        spec.login_hint != "abha-address" for spec in _METHODS.values()
    ), "ABHA address is back on the ABHA host; it belongs in abha/phr.py"


def test_every_call_goes_to_the_phr_host(abdm: FakeAbdm):
    client = abdm.client()
    search_abha_address(client, abha_address=ADDRESS)
    request_address_otp(client, abha_address=ADDRESS)
    verify_address_otp(client, txn_id="phr-txn", otp="123456")

    business = [r for r in abdm.requests if "phr" in r.url.path or "login/abha" in r.url.path]
    assert len(business) == 3
    for request in business:
        assert "/phr/web/" in request.url.path, request.url.path


# --------------------------------------------------------------------------
# scope and otpSystem are coupled
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "channel,scope,otp_system",
    [
        (
            AddressOtpChannel.AADHAAR,
            ["abha-address-login", "aadhaar-verify"],
            "aadhaar",
        ),
        (
            AddressOtpChannel.MOBILE,
            ["abha-address-login", "mobile-verify"],
            "abdm",
        ),
    ],
)
def test_channel_picks_both_scope_and_otp_system(
    abdm: FakeAbdm, channel, scope, otp_system
):
    """Sandbox-verified pairing. Crossing them returns "Invalid Otp System",
    which is why the channel enum sets both and neither is a free argument."""
    request_address_otp(abdm.client(), abha_address=ADDRESS, channel=channel)
    body = abdm.body(OTP)
    assert body["scope"] == scope
    assert body["otpSystem"] == otp_system
    assert body["loginHint"] == "abha-address"


def test_the_channel_enum_is_the_only_way_to_set_the_pair():
    """A caller cannot mismatch scope and otpSystem, because there is no
    argument that lets them try."""
    import inspect

    params = set(inspect.signature(request_address_otp).parameters)
    assert "scope" not in params and "otp_system" not in params


def test_verify_repeats_the_channel_scope(abdm: FakeAbdm):
    verify_address_otp(
        abdm.client(), txn_id="phr-txn", otp="123456", channel=AddressOtpChannel.MOBILE
    )
    assert abdm.body(VERIFY)["scope"] == ["abha-address-login", "mobile-verify"]


# --------------------------------------------------------------------------
# payload shapes
# --------------------------------------------------------------------------


def test_search_uses_abhaAddress_not_healthId(abdm: FakeAbdm):
    """`healthId` is the v1 spelling and is still widely copied; the sandbox
    rejects it with "Invalid ABHA Address"."""
    search_abha_address(abdm.client(), abha_address=ADDRESS)
    body = abdm.body(SEARCH)
    assert body == {"abhaAddress": ADDRESS}


def test_address_is_encrypted_on_the_otp_request(abdm: FakeAbdm):
    request_address_otp(abdm.client(), abha_address=ADDRESS)
    assert abdm.decrypt(abdm.body(OTP)["loginId"]) == ADDRESS
    assert ADDRESS not in abdm.last(OTP).content.decode()


def test_verify_encrypts_the_otp_and_carries_the_txn_id(abdm: FakeAbdm):
    verify_address_otp(abdm.client(), txn_id="phr-txn", otp="123456")
    otp_block = abdm.body(VERIFY)["authData"]["otp"]
    assert otp_block["txnId"] == "phr-txn"
    assert abdm.decrypt(otp_block["otpValue"]) == "123456"
    assert abdm.body(VERIFY)["authData"]["authMethods"] == ["otp"]


def test_verify_returns_a_token(abdm: FakeAbdm):
    result = verify_address_otp(abdm.client(), txn_id="phr-txn", otp="123456")
    assert result.token == "phr-x-tok"


# --------------------------------------------------------------------------
# the not-found branch
# --------------------------------------------------------------------------


def test_unknown_address_returns_none_not_an_exception():
    """The checklist requires the UI to answer "no such address" by offering
    ABHA creation (VRFY_ABHA_302). That is a branch, not an error path."""
    abdm = FakeAbdm({SEARCH: {"message": "User not found"}})
    assert search_abha_address(abdm.client(), abha_address="nobody@sbx") is None


def test_found_address_returns_the_account(abdm: FakeAbdm):
    account = search_abha_address(abdm.client(), abha_address=ADDRESS)
    assert account is not None
    assert account.abha_number == "91-1111-2222-3333"


@pytest.mark.parametrize("bad", ["", "   "])
def test_empty_address_fails_before_any_call(abdm: FakeAbdm, bad: str):
    with pytest.raises(AbhaFlowError):
        search_abha_address(abdm.client(), abha_address=bad)
    assert not abdm.sent_to(SEARCH)


def test_not_found_arrives_as_an_http_400_and_still_returns_none():
    """ABDM reports absence as a 400, not an empty 200.

    Left alone, the ordinary answer "nobody has that address" arrives as an
    exception, the route turns it into a client error, and the UI shows a
    failure where VRFY_ABHA_302 requires it to offer ABHA creation. Caught
    live against the sandbox, not in review.
    """
    from abha_support import error

    abdm = FakeAbdm({SEARCH: error(400, "ABDM-9999", "User not found")})
    assert search_abha_address(abdm.client(), abha_address="nobody@sbx") is None


def test_a_genuinely_bad_request_still_raises():
    """The mirror risk: treating every 400 as absence would swallow a
    malformed request and report it to the user as "no such patient"."""
    from abha_support import error

    from abha.errors import AbhaApiError

    abdm = FakeAbdm({SEARCH: error(400, "ABDM-9999", "Invalid ABHA Address")})
    with pytest.raises(AbhaApiError):
        search_abha_address(abdm.client(), abha_address="bogus@@sbx")
