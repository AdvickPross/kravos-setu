"""Login, the scope table, and multi-account selection.

The scope table is the specification for section 7, so these tests read it
back against the guide one credential at a time. The multi-account tests are
the ones that matter operationally: single-account logins work whatever the
code does, and a test Aadhaar almost always has exactly one ABHA -- so the
branch that breaks for real patients is the branch a happy-path test never
reaches.
"""

from __future__ import annotations

import pytest
from abha_support import FakeAbdm

from abha.errors import AbhaFlowError
from abha.login import LoginMethod, find_abha, request_login_otp, select_account, verify_login_otp
from abha.models import AbhaAccount

OTP_PATH = "/v3/profile/login/request/otp"
VERIFY_PATH = "/v3/profile/login/verify"
SELECT_PATH = "/v3/profile/login/verify/user"
SEARCH_PATH = "/v3/profile/account/abha/search"


@pytest.fixture
def abdm() -> FakeAbdm:
    return FakeAbdm(
        {
            OTP_PATH: {"txnId": "txn-9", "message": "OTP sent to XXXXXX8769"},
            VERIFY_PATH: {
                "txnId": "txn-9",
                "token": "x-tok",
                "refreshToken": "r-tok",
                "accounts": [{"ABHANumber": "91-1111", "name": "Only Account"}],
            },
            SELECT_PATH: {"token": "x-tok-2", "refreshToken": "r-tok-2"},
            SEARCH_PATH: {
                "accounts": [
                    {"ABHANumber": "91-1111", "preferredAbhaAddress": "a@sbx"}
                ]
            },
        }
    )


# --------------------------------------------------------------------------
# the scope table
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,login_hint,otp_system,scope",
    [
        (LoginMethod.AADHAAR, "aadhaar", "aadhaar", ["abha-login", "aadhaar-verify"]),
        (LoginMethod.MOBILE, "mobile", "abdm", ["abha-login", "mobile-verify"]),
        (
            LoginMethod.ABHA_NUMBER,
            "abha-number",
            "aadhaar",
            ["abha-login", "aadhaar-verify"],
        ),
        (
            LoginMethod.ABHA_ADDRESS,
            "abha-address",
            "abdm",
            ["abha-address-login", "mobile-verify"],
        ),
    ],
)
def test_each_credential_sends_its_own_three_fields(
    abdm: FakeAbdm, method, login_hint, otp_system, scope
):
    """Reads the table back against the guide. Getting one of the three
    wrong produces a 400 that names none of them."""
    request_login_otp(abdm.client(), identifier="999999999999", method=method)
    body = abdm.body(OTP_PATH)
    assert body["loginHint"] == login_hint
    assert body["otpSystem"] == otp_system
    assert body["scope"] == scope


def test_abha_number_login_still_uses_the_uidai_otp_system(abdm: FakeAbdm):
    """The non-obvious combination the table exists for: the identifier is an
    ABHA number, but the OTP originates at UIDAI, not ABDM."""
    request_login_otp(
        abdm.client(), identifier="91-1111-2222-3333", method=LoginMethod.ABHA_NUMBER
    )
    assert abdm.body(OTP_PATH)["otpSystem"] == "aadhaar"


def test_every_identifier_is_encrypted(abdm: FakeAbdm):
    """All four are PII. None may travel in the clear."""
    for method, identifier in [
        (LoginMethod.AADHAAR, "999999999999"),
        (LoginMethod.MOBILE, "9999999999"),
        (LoginMethod.ABHA_NUMBER, "91-1111-2222-3333"),
        (LoginMethod.ABHA_ADDRESS, "patient@sbx"),
    ]:
        fake = FakeAbdm({OTP_PATH: {"txnId": "t"}})
        request_login_otp(fake.client(), identifier=identifier, method=method)
        body = fake.body(OTP_PATH)
        assert fake.decrypt(body["loginId"]) == identifier
        assert identifier not in fake.last(OTP_PATH).content.decode()


def test_verify_repeats_the_scope_from_the_otp_request(abdm: FakeAbdm):
    """ABDM requires the scope to match across the two calls for one txnId.
    Passing a `method` through both is what guarantees it."""
    client = abdm.client()
    request_login_otp(client, identifier="9999999999", method=LoginMethod.MOBILE)
    verify_login_otp(client, txn_id="txn-9", otp="123456", method=LoginMethod.MOBILE)
    assert abdm.body(VERIFY_PATH)["scope"] == abdm.body(OTP_PATH)["scope"]


def test_verify_encrypts_the_otp(abdm: FakeAbdm):
    verify_login_otp(
        abdm.client(), txn_id="txn-9", otp="123456", method=LoginMethod.AADHAAR
    )
    otp_block = abdm.body(VERIFY_PATH)["authData"]["otp"]
    assert abdm.decrypt(otp_block["otpValue"]) == "123456"
    assert otp_block["txnId"] == "txn-9"


# --------------------------------------------------------------------------
# multi-account
# --------------------------------------------------------------------------


def test_single_account_login_finishes_in_one_step(abdm: FakeAbdm):
    result = verify_login_otp(
        abdm.client(), txn_id="txn-9", otp="123456", method=LoginMethod.AADHAAR
    )
    assert result.token == "x-tok"
    assert result.needs_account_selection is False


def test_multi_account_login_returns_a_choice_not_a_session():
    """ABDM permits six ABHA numbers per mobile. Code that assumes login is
    single-step passes every test written against a one-ABHA test Aadhaar."""
    abdm = FakeAbdm(
        {
            VERIFY_PATH: {
                "txnId": "txn-9",
                "tToken": "t-tok",
                "accounts": [
                    {"ABHANumber": "91-1111", "name": "First", "index": "0"},
                    {"ABHANumber": "91-2222", "name": "Second", "index": "1"},
                ],
            }
        }
    )
    result = verify_login_otp(
        abdm.client(), txn_id="txn-9", otp="123456", method=LoginMethod.MOBILE
    )
    assert result.needs_account_selection
    assert result.token is None
    assert [a.name for a in result.accounts] == ["First", "Second"]


def test_selection_is_discriminated_by_the_t_token_not_the_account_count(abdm: FakeAbdm):
    """A single-account response still lists that account, so branching on
    `len(accounts)` reports a finished login as needing a choice."""
    result = verify_login_otp(
        abdm.client(), txn_id="txn-9", otp="123456", method=LoginMethod.AADHAAR
    )
    assert len(result.accounts) == 1
    assert result.needs_account_selection is False


def test_select_account_sends_the_t_token_and_encrypts_the_index(abdm: FakeAbdm):
    """The T-token is the only credential that authorises this call, and the
    index identifies an ABHA account -- PII like the rest of the family."""
    account = AbhaAccount.from_payload({"ABHANumber": "91-2222", "index": "1"})
    result = select_account(
        abdm.client(), txn_id="txn-9", account=account, t_token="t-tok"
    )
    assert result.token == "x-tok-2"
    assert abdm.headers(SELECT_PATH)["T-token"] == "Bearer t-tok"
    assert abdm.decrypt(abdm.body(SELECT_PATH)["ABHANumber"]) == "1"


def test_select_account_accepts_a_bare_index(abdm: FakeAbdm):
    """A UI sends back what it was given, not a reconstructed model."""
    select_account(abdm.client(), txn_id="txn-9", account="1", t_token="t-tok")
    assert abdm.decrypt(abdm.body(SELECT_PATH)["ABHANumber"]) == "1"


def test_selecting_an_account_with_no_index_fails_locally(abdm: FakeAbdm):
    """ABDM populates `index` only on multi-account responses, so an account
    without one did not come from a selection prompt."""
    account = AbhaAccount.from_payload({"ABHANumber": "91-1111"})
    with pytest.raises(AbhaFlowError):
        select_account(abdm.client(), txn_id="txn-9", account=account, t_token="t")


def test_missing_t_token_fails_before_the_request(abdm: FakeAbdm):
    with pytest.raises(AbhaFlowError):
        select_account(abdm.client(), txn_id="txn-9", account="1", t_token="")
    assert not abdm.sent_to(SELECT_PATH)


# --------------------------------------------------------------------------
# Find ABHA
# --------------------------------------------------------------------------


def test_find_abha_returns_accounts(abdm: FakeAbdm):
    accounts = find_abha(abdm.client(), abha_address="a@sbx")
    assert [a.abha_number for a in accounts] == ["91-1111"]


def test_find_abha_tolerates_a_bare_object_for_a_single_hit():
    """This endpoint returns an object rather than a one-element array when
    exactly one account matches."""
    abdm = FakeAbdm({SEARCH_PATH: {"account": {"ABHANumber": "91-3333"}}})
    assert [a.abha_number for a in find_abha(abdm.client(), abha_address="a@sbx")] == [
        "91-3333"
    ]


def test_no_such_address_is_an_empty_list_not_an_exception():
    """"No such address" is a legitimate answer. Raising would force every
    caller into a try/except to handle a normal outcome."""
    abdm = FakeAbdm({SEARCH_PATH: {"accounts": []}})
    assert find_abha(abdm.client(), abha_address="nobody@sbx") == []
