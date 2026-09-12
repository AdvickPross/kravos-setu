"""Every ABDM path M1 touches, in one table.

Deliberately a flat module of constants rather than strings inline at each
call site. The paths come from the ABHA V3 Integrator Guide v1.4 and ABDM
does move them between minor revisions; when one changes, the fix should be a
one-line edit here with no search through flow logic, and a diff on this file
should read as a complete answer to "what does this service call?".

Section numbers refer to the v1.4 guide. They are the fastest way to check a
path against the source when a call starts failing.

All paths are relative. `AbhaConfig.abha_url` / `.phr_url` supply the host,
because the host differs by environment *and* by which of the two surfaces a
path belongs to -- which is exactly the mistake the `PHR_` prefix is here to
prevent.
"""

from __future__ import annotations

# -- §1 session, §2 encryption ---------------------------------------------
# The session URL is not here: it lives on a third host and is built by
# AbhaConfig.session_url.
PUBLIC_CERTIFICATE = "/v3/profile/public/certificate"

# -- §3 create ABHA via Aadhaar --------------------------------------------
ENROL_REQUEST_OTP = "/v3/enrollment/request/otp"
ENROL_BY_AADHAAR = "/v3/enrollment/enrol/byAadhaar"
# Used to attach a mobile that differs from the Aadhaar-linked one. Not
# optional in practice -- see abha/enrollment.py on the null-mobile trap.
ENROL_AUTH_BY_ABDM = "/v3/enrollment/auth/byAbdm"
ENROL_ADDRESS_SUGGESTION = "/v3/enrollment/enrol/suggestion"
ENROL_SET_ADDRESS = "/v3/enrollment/enrol/abha-address"

# -- §4 driving licence, §5 demographic auth -------------------------------
ENROL_BY_DOCUMENT = "/v3/enrollment/enrol/byDocument"
ENROL_BY_DEMOGRAPHIC = "/v3/enrollment/enrol/byDemographic"

# -- §6 biometrics and face auth -------------------------------------------
ENROL_AUTH_INIT = "/v3/enrollment/enrol/auth/init"
ENROL_CAPTURE_PID = "/v3/enrollment/enrol/capturePID"

# -- §7 verification / login, Find ABHA ------------------------------------
LOGIN_REQUEST_OTP = "/v3/profile/login/request/otp"
LOGIN_VERIFY = "/v3/profile/login/verify"
# Second leg when one credential resolves to several ABHA accounts; carries
# the T-token issued by LOGIN_VERIFY rather than a session token.
LOGIN_VERIFY_USER = "/v3/profile/login/verify/user"
SEARCH_ABHA = "/v3/profile/account/abha/search"

# -- §8 update profile, §9 read profile ------------------------------------
PROFILE_ACCOUNT = "/v3/profile/account"

# -- §10 QR, §11 card ------------------------------------------------------
PROFILE_QR_CODE = "/v3/profile/account/qrCode"
PROFILE_ABHA_CARD = "/v3/profile/account/abha-card"

# -- §14 ABHA address flow (PHR host, not the ABHA host) -------------------
PHR_PROFILE = "/login/profile/abha-profile"
PHR_CARD = "/login/profile/abha/phr-card"

# -- consent artefact ------------------------------------------------------
# Sent verbatim in the enrolment body. The version is the *consent text*
# version, not the API version, and ABDM rejects a mismatch.
ENROLMENT_CONSENT = {"code": "abha-enrollment", "version": "1.4"}
