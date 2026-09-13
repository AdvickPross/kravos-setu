"""The HTTP surface.

Two things are being protected: that the rest of Kravos Setu keeps working
with no ABDM credentials configured, and that the error mapping stays
useful -- a UI needs to tell "wrong OTP" from "ABDM is down" from "you called
these in the wrong order", and a single 500 flattens all three.
"""

from __future__ import annotations

import httpx
import pytest
from abha_support import FakeAbdm, error
from fastapi.testclient import TestClient

import backend.abha_routes as routes
from backend.main import app


@pytest.fixture(autouse=True)
def isolated_client(monkeypatch):
    """Every test starts with no cached client and no credentials in env."""
    routes.reset_client()
    monkeypatch.delenv("ABDM_CLIENT_ID", raising=False)
    monkeypatch.delenv("ABDM_CLIENT_SECRET", raising=False)
    yield
    routes.reset_client()


@pytest.fixture
def api() -> TestClient:
    return TestClient(app)


def _wire(monkeypatch, abdm: FakeAbdm) -> None:
    """Point the route module's singleton at the fake ABDM."""
    monkeypatch.setenv("ABDM_CLIENT_ID", "cid")
    monkeypatch.setenv("ABDM_CLIENT_SECRET", "secret")
    routes.reset_client()
    monkeypatch.setattr(routes, "_client_instance", abdm.client(), raising=False)


# --------------------------------------------------------------------------
# absence of credentials
# --------------------------------------------------------------------------


def test_status_reports_unconfigured_without_calling_abdm(api: TestClient):
    """The frontend asks this on load to decide whether to offer the flow;
    that decision must not depend on the sandbox being up."""
    body = api.get("/api/abha/status").json()
    assert body == {"configured": False, "environment": None}


def test_unconfigured_flows_answer_503_not_404(api: TestClient):
    """404 would tell the frontend the feature does not exist rather than
    that it is not switched on."""
    response = api.post("/api/abha/enrol/aadhaar/otp", json={"aadhaar": "9" * 12})
    assert response.status_code == 503
    assert ".env" in response.json()["detail"]


def test_the_rest_of_the_app_is_unaffected_by_missing_credentials(api: TestClient):
    """The corpus demo must keep working with no ABDM credentials at all."""
    assert api.get("/api/patients").status_code == 200


def test_status_reports_the_environment_once_configured(api: TestClient, monkeypatch):
    monkeypatch.setenv("ABDM_CLIENT_ID", "cid")
    monkeypatch.setenv("ABDM_CLIENT_SECRET", "secret")
    body = api.get("/api/abha/status").json()
    assert body == {"configured": True, "environment": "sandbox"}


# --------------------------------------------------------------------------
# token placement
# --------------------------------------------------------------------------


def test_profile_requires_the_x_token_header(api: TestClient, monkeypatch):
    _wire(monkeypatch, FakeAbdm({"/v3/profile/account": {"ABHANumber": "91-1"}}))
    assert api.get("/api/abha/profile").status_code == 401


def test_transaction_tokens_travel_in_headers_never_in_bodies(
    api: TestClient, monkeypatch
):
    """X-token is a patient credential, and request bodies reach access logs,
    error trackers and devtools far more readily than headers do."""
    abdm = FakeAbdm({"/v3/enrollment/request/otp": {"txnId": "t"}})
    _wire(monkeypatch, abdm)

    response = api.post(
        "/api/abha/enrol/mobile/otp",
        json={"txnId": "t", "mobile": "9999999999"},
        headers={"X-token": "x-tok"},
    )
    assert response.status_code == 200
    assert "x-tok" not in abdm.last("/v3/enrollment/request/otp").content.decode()
    assert abdm.headers("/v3/enrollment/request/otp")["X-token"] == "Bearer x-tok"


def test_account_selection_requires_the_t_token_header(api: TestClient, monkeypatch):
    _wire(monkeypatch, FakeAbdm({"/v3/profile/login/verify/user": {"token": "x"}}))
    response = api.post("/api/abha/login/select", json={"txnId": "t", "index": "1"})
    assert response.status_code == 401
    assert "T-token" in response.json()["detail"]


# --------------------------------------------------------------------------
# error mapping
# --------------------------------------------------------------------------


def test_abdm_4xx_passes_through_with_its_code(api: TestClient, monkeypatch):
    """A UI branches on "OTP expired" versus "already enrolled"; both are
    400s and the code is the only thing that separates them."""
    _wire(
        monkeypatch,
        FakeAbdm(
            {"/v3/enrollment/enrol/byAadhaar": error(400, "ABDM-1044", "OTP expired")}
        ),
    )
    response = api.post(
        "/api/abha/enrol/aadhaar/verify", json={"txnId": "t", "otp": "000000"}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "ABDM-1044"
    assert response.json()["detail"]["source"] == "abdm"


def test_abdm_5xx_becomes_502_because_it_is_not_this_service_that_failed(
    api: TestClient, monkeypatch
):
    _wire(
        monkeypatch,
        FakeAbdm({"/v3/enrollment/request/otp": httpx.Response(503, json={"m": "x"})}),
    )
    response = api.post("/api/abha/enrol/aadhaar/otp", json={"aadhaar": "9" * 12})
    assert response.status_code == 502


def test_local_sequencing_mistakes_are_422_not_500(api: TestClient, monkeypatch):
    """A malformed Aadhaar never reaches ABDM, and is the caller's error."""
    abdm = FakeAbdm({"/v3/enrollment/request/otp": {"txnId": "t"}})
    _wire(monkeypatch, abdm)
    response = api.post("/api/abha/enrol/aadhaar/otp", json={"aadhaar": "99999999999a"})
    assert response.status_code == 422
    assert not abdm.sent_to("/v3/enrollment/request/otp")


def test_unreachable_abdm_becomes_502(api: TestClient, monkeypatch):
    def dead(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    abdm = FakeAbdm()
    abdm.handler = dead  # type: ignore[method-assign]
    _wire(monkeypatch, abdm)
    response = api.post("/api/abha/enrol/aadhaar/otp", json={"aadhaar": "9" * 12})
    assert response.status_code == 502


# --------------------------------------------------------------------------
# happy paths
# --------------------------------------------------------------------------


def test_enrolment_reports_whether_the_mobile_was_actually_linked(
    api: TestClient, monkeypatch
):
    """The null-mobile trap has to survive the serialisation boundary --
    a UI cannot warn about what the JSON does not carry."""
    _wire(
        monkeypatch,
        FakeAbdm(
            {
                "/v3/enrollment/enrol/byAadhaar": {
                    "ABHAProfile": {"ABHANumber": "91-1111", "mobile": None},
                    "tokens": {"token": "x-tok"},
                }
            }
        ),
    )
    body = api.post(
        "/api/abha/enrol/aadhaar/verify",
        json={"txnId": "t", "otp": "123456", "mobile": "8888888888"},
    ).json()
    assert body["account"]["abha_number"] == "91-1111"
    assert body["account"]["mobile"] is None


def test_face_status_is_one_poll_not_a_blocking_wait(api: TestClient, monkeypatch):
    """`await_capture` would hold a worker for up to ten minutes per patient;
    the browser polls this instead."""
    abdm = FakeAbdm({"/v3/enrollment/enrol/capturePID": {"status": "PENDING"}})
    _wire(monkeypatch, abdm)
    body = api.get("/api/abha/face/status", params={"txnId": "t"}).json()
    assert body == {"status": "PENDING", "terminal": "false"}
    assert len(abdm.sent_to("/v3/enrollment/enrol/capturePID")) == 1


def test_card_is_returned_as_png_bytes(api: TestClient, monkeypatch):
    """This endpoint answers with an image; JSON-parsing it would raise on a
    successful response."""
    png = b"\x89PNG\r\n\x1a\n-fake"
    _wire(
        monkeypatch,
        FakeAbdm(
            {
                "/v3/profile/account/abha-card": httpx.Response(
                    200, content=png, headers={"Content-Type": "image/png"}
                )
            }
        ),
    )
    response = api.get("/api/abha/card", headers={"X-token": "x-tok"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == png


def test_abha_address_login_reads_the_profile_from_the_phr_host(
    api: TestClient, monkeypatch
):
    """The section-14 split: a patient verified by ABHA address must be read
    from the PHR host, and a valid token against the wrong host returns 401
    rather than a redirect."""
    abdm = FakeAbdm({"/login/profile/abha-profile": {"ABHANumber": "91-1"}})
    _wire(monkeypatch, abdm)
    response = api.get(
        "/api/abha/profile",
        params={"viaAbhaAddress": True},
        headers={"X-token": "x-tok"},
    )
    assert response.status_code == 200
    assert "phr" in abdm.last("/login/profile/abha-profile").url.path


def test_openapi_still_generates(api: TestClient):
    """The frontend's TypeScript types are generated from openapi.json, so a
    route that breaks the schema breaks the build rather than one page."""
    schema = api.get("/openapi.json").json()
    assert "/api/abha/enrol/aadhaar/otp" in schema["paths"]
    assert "/api/query" in schema["paths"]


# --------------------------------------------------------------------------
# ABHA address verification (PHR host)
# --------------------------------------------------------------------------


def test_address_search_returns_null_for_an_unknown_address(
    api: TestClient, monkeypatch
):
    """`null`, not 404: the checklist requires the UI to answer this by
    offering ABHA creation, which is a branch rather than an error."""
    _wire(monkeypatch, FakeAbdm({"/login/abha/search": {"message": "User not found"}}))
    response = api.post("/api/abha/address/search", json={"abhaAddress": "nobody@sbx"})
    assert response.status_code == 200
    assert response.json() is None


def test_address_otp_goes_to_the_phr_host(api: TestClient, monkeypatch):
    """Routing an ABHA address through the ABHA-host login endpoints is
    rejected with "Invalid Scope"; this pins the correct host at the HTTP
    boundary as well as inside the flow module."""
    abdm = FakeAbdm({"/login/abha/request/otp": {"txnId": "phr-txn"}})
    _wire(monkeypatch, abdm)
    response = api.post(
        "/api/abha/address/otp",
        json={"abhaAddress": "patient@sbx", "channel": "mobile"},
    )
    assert response.status_code == 200
    assert "/phr/web/" in abdm.last("/login/abha/request/otp").url.path
    assert abdm.body("/login/abha/request/otp")["otpSystem"] == "abdm"


# --------------------------------------------------------------------------
# resend policy
# --------------------------------------------------------------------------


def test_first_otp_request_is_not_a_resend(api: TestClient, monkeypatch):
    """No txnId means no prior OTP, so the budget is untouched."""
    from abha.otp_policy import default_policy

    abdm = FakeAbdm({"/v3/enrollment/request/otp": {"txnId": "t"}})
    _wire(monkeypatch, abdm)
    api.post("/api/abha/enrol/aadhaar/otp", json={"aadhaar": "9" * 12})
    assert default_policy().state_for("t").resends_used == 0


def test_a_third_resend_is_refused_with_422(api: TestClient, monkeypatch):
    """CRT_ABHA_106: max two resends. Enforced server-side, because a
    disabled button does nothing about a second tab."""
    from abha.otp_policy import ResendPolicy
    import abha.otp_policy as otp_policy

    clock = [0.0]
    monkeypatch.setattr(
        otp_policy, "_default_policy", ResendPolicy(now=lambda: clock[0])
    )
    abdm = FakeAbdm({"/v3/enrollment/request/otp": {"txnId": "t"}})
    _wire(monkeypatch, abdm)

    body = {"aadhaar": "9" * 12, "txnId": "t"}
    assert api.post("/api/abha/enrol/aadhaar/otp", json=body).status_code == 200
    clock[0] += 60
    assert api.post("/api/abha/enrol/aadhaar/otp", json=body).status_code == 200
    clock[0] += 60
    third = api.post("/api/abha/enrol/aadhaar/otp", json=body)
    assert third.status_code == 422
    assert "limit reached" in third.json()["detail"]
    assert len(abdm.sent_to("/v3/enrollment/request/otp")) == 2, (
        "the refused resend must not reach ABDM"
    )


def test_otp_state_is_readable_for_the_ui(api: TestClient, monkeypatch):
    from abha.otp_policy import ResendPolicy
    import abha.otp_policy as otp_policy

    monkeypatch.setattr(otp_policy, "_default_policy", ResendPolicy())
    body = api.get("/api/abha/otp/state", params={"txnId": "fresh"}).json()
    assert body["resends_used"] == 0
    assert body["resends_remaining"] == 2


# --------------------------------------------------------------------------
# ABHA <-> patient tagging
# --------------------------------------------------------------------------


@pytest.fixture
def link_store(tmp_path, monkeypatch):
    import backend.abha_link as abha_link

    store = abha_link.AbhaLinkStore(tmp_path / "links.json")
    monkeypatch.setattr(abha_link, "_default_store", store)
    return store


def _a_real_patient_id() -> str:
    from backend.data_layer import list_patients

    patients = list_patients()
    if not patients:
        pytest.skip("no patient corpus available")
    return patients[0].patient_id


def test_tagging_works_without_abdm_credentials(api: TestClient, link_store):
    """The only M1 item with no ABDM API behind it, so unlike every other
    route it must work with nothing configured."""
    patient_id = _a_real_patient_id()
    response = api.post(
        "/api/abha/link",
        json={"abhaNumber": "91-1234-5678-9012", "patientId": patient_id},
    )
    assert response.status_code == 200
    assert response.json()["patient_id"] == patient_id


def test_tagging_an_unknown_patient_is_404(api: TestClient, link_store):
    """A tag pointing at a patient that does not exist reads as a verified
    identity and resolves to nothing."""
    response = api.post(
        "/api/abha/link",
        json={"abhaNumber": "91-1234-5678-9012", "patientId": "no-such-patient"},
    )
    assert response.status_code == 404


def test_a_second_patient_for_one_abha_is_409(api: TestClient, link_store):
    """409, not 400: the request is well-formed and the refusal is about
    state the caller can inspect and resolve."""
    patient_id = _a_real_patient_id()
    link_store.link(abha_number="91-1234-5678-9012", patient_id=patient_id)

    from backend.data_layer import list_patients

    others = [p.patient_id for p in list_patients() if p.patient_id != patient_id]
    if not others:
        pytest.skip("need two patients")

    response = api.post(
        "/api/abha/link",
        json={"abhaNumber": "91-1234-5678-9012", "patientId": others[0]},
    )
    assert response.status_code == 409
    assert "already tagged" in response.json()["detail"]


def test_lookup_by_abha_before_creating_a_record(api: TestClient, link_store):
    patient_id = _a_real_patient_id()
    body = api.get("/api/abha/link/by-abha/91-1234-5678-9012").json()
    assert body["patient_id"] is None

    link_store.link(abha_number="91-1234-5678-9012", patient_id=patient_id)
    body = api.get("/api/abha/link/by-abha/91-1234-5678-9012").json()
    assert body["patient_id"] == patient_id


def test_a_masked_abha_number_is_422(api: TestClient, link_store):
    response = api.get("/api/abha/link/by-abha/91-****-****-9012")
    assert response.status_code == 422


# --------------------------------------------------------------------------
# response shapes ABDM changes under us
# --------------------------------------------------------------------------


def test_an_address_returned_as_an_array_does_not_500(api: TestClient, monkeypatch):
    """ABDM returns `phrAddress` as a list from enrolment -- an ABHA number can
    carry six addresses -- even though the guide shows a bare string.

    Assigning that to a `str | None` field raised inside pydantic, escaped the
    route, and reached the browser as a CORS error, because Starlette's
    server-error handler sits outside CORSMiddleware. A successfully created
    ABHA looked like a network failure.
    """
    _wire(
        monkeypatch,
        FakeAbdm(
            {
                "/v3/enrollment/enrol/byAadhaar": {
                    "ABHAProfile": {
                        "ABHANumber": "91-7366-1108-3337",
                        "phrAddress": ["91736611083337@sbx"],
                        "mobile": None,
                    },
                    "tokens": {"token": "x-tok"},
                    "new": True,
                }
            }
        ),
    )
    response = api.post(
        "/api/abha/enrol/aadhaar/verify", json={"txnId": "t", "otp": "123456"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["account"]["abha_address"] == "91736611083337@sbx"
    assert body["account"]["abha_number"] == "91-7366-1108-3337"


def test_an_unforeseen_error_stays_an_http_exception(api: TestClient, monkeypatch):
    """The systemic half. Whatever ABDM sends, the failure must leave the app
    through the middleware stack so the response carries CORS headers and the
    cause is readable -- not as a bare 500 the browser blames on origins."""
    import backend.abha_routes as routes

    def explode():
        raise ValueError("something nobody predicted")

    monkeypatch.setattr(routes, "request_aadhaar_otp", lambda *a, **k: explode())
    _wire(monkeypatch, FakeAbdm({"/v3/enrollment/request/otp": {"txnId": "t"}}))

    response = api.post("/api/abha/enrol/aadhaar/otp", json={"aadhaar": "9" * 12})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["code"] == "KRAVOS-UNHANDLED"
    assert "something nobody predicted" in detail["message"]
