"""Face auth: the QR, the poll loop, and the ways it can hang.

This is the only M1 flow with no callback and a human in the middle, so the
failures are timing-shaped rather than payload-shaped: a loop that never
exits, a loop that exits too early, or a loop that treats a dead transaction
as a live one.
"""

from __future__ import annotations

import pytest
from abha_support import FakeAbdm, collecting_sleep

from abha.errors import AbhaFlowError
from abha.faceauth import (
    QR_VALIDITY_SECONDS,
    await_capture,
    complete_face_enrolment,
    poll_once,
    start_face_auth,
)
from abha.models import FaceAuthStatus

INIT_PATH = "/v3/enrollment/enrol/auth/init"
PID_PATH = "/v3/enrollment/enrol/capturePID"
AADHAAR = "999999999999"


def _abdm(pid_statuses: list[str] | None = None) -> FakeAbdm:
    routes = {INIT_PATH: {"txnId": "face-txn"}}
    if pid_statuses is not None:
        routes[PID_PATH] = [{"status": s} for s in pid_statuses]
    return FakeAbdm(routes)


# --------------------------------------------------------------------------
# starting
# --------------------------------------------------------------------------


def test_qr_points_at_the_phr_host_not_the_abha_host():
    """The QR is opened by the patient's phone, not by this service, and the
    landing page lives on a different host from the API."""
    abdm = _abdm()
    session = start_face_auth(abdm.client(), aadhaar=AADHAAR)
    assert session.qr_url.endswith("/face-auth?txnId=face-txn")
    assert "phr" in session.qr_url


def test_qr_validity_is_carried_so_a_ui_can_count_down():
    session = start_face_auth(_abdm().client(), aadhaar=AADHAAR)
    assert session.expires_in_seconds == QR_VALIDITY_SECONDS == 600


def test_enrolment_and_login_send_different_scopes():
    """Same endpoint, different scope. Sending the enrolment scope on a login
    attempt fails at the final step, not at this one."""
    enrol = _abdm()
    start_face_auth(enrol.client(), aadhaar=AADHAAR, for_login=False)
    assert enrol.body(INIT_PATH)["scope"] == ["abha-enrol", "face-auth"]

    login = _abdm()
    start_face_auth(login.client(), aadhaar=AADHAAR, for_login=True)
    assert login.body(INIT_PATH)["scope"] == ["abha-login", "face-verify"]


def test_init_encrypts_the_aadhaar():
    abdm = _abdm()
    start_face_auth(abdm.client(), aadhaar=AADHAAR)
    assert abdm.decrypt(abdm.body(INIT_PATH)["loginId"]) == AADHAAR


def test_missing_txn_id_in_the_init_response_is_caught_immediately():
    """Without it there is nothing to poll and nothing to encode in a QR;
    failing here beats failing in the poll loop."""
    abdm = FakeAbdm({INIT_PATH: {"message": "ok"}})
    with pytest.raises(AbhaFlowError):
        start_face_auth(abdm.client(), aadhaar=AADHAAR)


def test_malformed_aadhaar_is_rejected_before_any_call():
    abdm = _abdm()
    with pytest.raises(AbhaFlowError):
        start_face_auth(abdm.client(), aadhaar="123")
    assert not abdm.sent_to(INIT_PATH)


# --------------------------------------------------------------------------
# polling
# --------------------------------------------------------------------------


def test_loop_exits_on_the_first_terminal_status():
    abdm = _abdm(["PENDING", "PENDING", "VERIFIED"])
    sleep, slept = collecting_sleep()
    status = await_capture(abdm.client(), txn_id="face-txn", sleep=sleep)
    assert status is FaceAuthStatus.VERIFIED
    assert len(abdm.sent_to(PID_PATH)) == 3
    assert slept == [5.0, 5.0], "poll interval must stay inside ABDM's 5-10s band"


def test_complete_and_verified_are_both_success():
    """ABDM uses them inconsistently between the enrolment and login variants
    of this endpoint. Treating only one as terminal hangs against the other."""
    for terminal in ("COMPLETE", "VERIFIED"):
        abdm = _abdm([terminal])
        sleep, _ = collecting_sleep()
        status = await_capture(abdm.client(), txn_id="face-txn", sleep=sleep)
        assert status.is_success, terminal


def test_failure_is_returned_not_raised():
    """A patient who declined or mismatched is a normal outcome a UI has to
    render, not an exception."""
    abdm = _abdm(["PENDING", "FAILED"])
    sleep, _ = collecting_sleep()
    assert (
        await_capture(abdm.client(), txn_id="face-txn", sleep=sleep)
        is FaceAuthStatus.FAILED
    )


def test_unknown_status_ends_the_loop_instead_of_spinning():
    """The dangerous default. Mapping an unrecognised status to PENDING would
    poll a dead transaction until the ten-minute timeout."""
    abdm = _abdm(["SOMETHING_NEW"])
    sleep, slept = collecting_sleep()
    status = await_capture(abdm.client(), txn_id="face-txn", sleep=sleep)
    assert status is FaceAuthStatus.FAILED
    assert slept == []


def test_timeout_raises_and_says_the_transaction_is_dead():
    """The QR expires after ten minutes; polling past that learns nothing.
    A timeout is the integration's problem, so unlike a FAILED it raises."""
    abdm = FakeAbdm({INIT_PATH: {"txnId": "t"}, PID_PATH: {"status": "PENDING"}})
    now = [0.0]

    def fake_sleep(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(AbhaFlowError) as exc:
        await_capture(
            abdm.client(),
            txn_id="face-txn",
            sleep=fake_sleep,
            now=lambda: now[0],
        )
    assert "new one must be started" in str(exc.value)
    # 600s deadline at 5s per poll, plus the poll that observes the deadline.
    assert len(abdm.sent_to(PID_PATH)) == 121


def test_poll_once_does_not_sleep_or_retry():
    """The HTTP route uses this; a blocking wait would hold a worker for up
    to ten minutes per patient."""
    abdm = _abdm(["PENDING"])
    assert poll_once(abdm.client(), txn_id="face-txn") is FaceAuthStatus.PENDING
    assert len(abdm.sent_to(PID_PATH)) == 1


def test_status_is_read_from_any_of_the_three_field_names():
    for field in ("status", "authResult", "result"):
        abdm = FakeAbdm({PID_PATH: {field: "VERIFIED"}})
        assert poll_once(abdm.client(), txn_id="t").is_success, field


# --------------------------------------------------------------------------
# completing
# --------------------------------------------------------------------------


def test_completion_names_face_and_sends_no_otp_block():
    """Same endpoint as OTP enrolment, which is why the two are separate
    functions: the OTP block is mandatory there and must be absent here."""
    abdm = FakeAbdm(
        {
            "/v3/enrollment/enrol/byAadhaar": {
                "ABHAProfile": {"ABHANumber": "91-1111", "mobile": "9999999999"}
            }
        }
    )
    complete_face_enrolment(abdm.client(), txn_id="face-txn", mobile="9999999999")
    auth_data = abdm.body("/v3/enrollment/enrol/byAadhaar")["authData"]

    assert auth_data["authMethods"] == ["face"]
    assert "otp" not in auth_data
    assert auth_data["face"]["txnId"] == "face-txn"


def test_null_mobile_trap_applies_to_the_face_path_too(caplog):
    """Identical behaviour to OTP enrolment: HTTP 200, mobile silently null."""
    import logging

    abdm = FakeAbdm(
        {"/v3/enrollment/enrol/byAadhaar": {"ABHAProfile": {"ABHANumber": "91-1"}}}
    )
    with caplog.at_level(logging.WARNING, logger="abha.faceauth"):
        result = complete_face_enrolment(
            abdm.client(), txn_id="face-txn", mobile="8888888888"
        )
    assert result.mobile_is_linked is False
    assert "null" in caplog.text
