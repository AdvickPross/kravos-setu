# M1 coverage against NHA's checklist

Every row of `M1_ABHA_CREATION_AND_VERIFICATION_WITH_APIS_UPDATED_V1_1_f656c93440.xlsx`,
mapped to what exists in this repo.

**Integrator class assumed: private.** It changes the answer. Section 3
(demo/offline auth) is Government-only and section 4 (Driving Licence / PAN)
is *Mandatory for Government, Optional for Private*. If Kravos Setu is
certified as a Government integrator, both move from "not built" to
"required", and the two rows are marked below.

Status values:

| | |
|---|---|
| **done** | implemented and tested |
| **partial** | the API side is done; something outside the API layer is not |
| **not built** | no implementation |
| **n/a** | does not apply to a private HIP/HIU integrator |

Verification levels, which are not the same thing as status:

- **live** — the request was sent to `abhasbx.abdm.gov.in` and ABDM's reply
  confirms the shape. Non-existent identifiers were used throughout, so
  validation passes and the lookup fails: *"User not found"* is the pass
  signal. No OTP was sent to anybody and no sandbox ABHA-creation quota was
  spent.
- **mock** — pinned in the test suite against a scripted ABDM
  (`tests/abha_support.py`), which proves the request this code builds, not
  that ABDM accepts it.

---

## Session and encryption

| Test case | Requirement | Status | Verified |
|---|---|---|---|
| — | `POST /api/hiecm/gateway/v3/sessions` | done | **live** — token acquired |
| — | `GET /v3/profile/public/certificate`, RSA-OAEP-SHA1 | done | **live** — key fetched, value encrypted |

## 1 · ABHA creation via Aadhaar OTP — *Mandatory*

| Test case | Requirement | Status | Notes |
|---|---|---|---|
| CRT_ABHA_101 | Create-ABHA option; 8 APIs | done | all 8 exist and accept our auth (**live**) |
| CRT_ABHA_102 | Consent collection | **partial** | the `{code, version}` artefact is sent on every enrolment. The **consent language must be displayed to the patient**, and this repo does not ship NHA's approved text — see *Outstanding* below |
| CRT_ABHA_103 | Multilingual consent | not built | Optional |
| CRT_ABHA_104 | Aadhaar entry + error message | done | 12-digit check before any call; 422 with a message |
| CRT_ABHA_105 | Aadhaar OTP entry | done | `masked_identifier` surfaced so the UI can say where it went |
| CRT_ABHA_106 | Resend max 2×, 60s apart | done | `abha/otp_policy.py`, enforced server-side |
| CRT_ABHA_107 | OTP verification | done | `enrol_by_aadhaar` |
| CRT_ABHA_108 | Skip OTP when comms mobile == Aadhaar mobile | not built | Optional; `checkAndGenerateMobileOTP` not implemented |
| CRT_ABHA_109 | Verify a different communication mobile | done | `request_mobile_otp` / `verify_mobile_otp`, **plus** detection of the null-mobile trap |
| CRT_ABHA_112 | ≥3 address suggestions + 5 validation rules | done | `suggest_abha_addresses`; the rules are enforced in `validate_abha_address`, which the checklist requires "at the API level" |
| CRT_ABHA_113 | Display ABHA number | **partial** | returned by the API; no UI screen renders it yet |
| CRT_ABHA_114 | View / download ABHA card | done | `GET /api/abha/card` returns PNG bytes |
| CRT_ABHA_115 | Alternative to a card | n/a | CRT_ABHA_114 is implemented instead |

## 2 · ABHA creation via Aadhaar biometric — *Optional*

| Test case | Requirement | Status | Notes |
|---|---|---|---|
| CRT_ABHA_201–208 | Fingerprint enrolment | **partial** | the `wadh` digest and PID options are built and tested (`abha/biometrics.py`) — the part that is reliably wrong elsewhere — but no fingerprint enrolment call is wired |
| — | Face auth | done | `abha/faceauth.py`: init → QR → `capturePID` poll → enrol |
| CRT_ABHA_209 | Card (Mandatory for private) | done | same endpoint as CRT_ABHA_114 |

## 3 · Demo auth / offline — *Government only*

| Test case | Status |
|---|---|
| CRT_ABHA_301–309 | **n/a** for a private integrator. Not built. Becomes Mandatory if certified as Government. |

## 4 · Driving Licence / PAN — *Optional (private) · Mandatory (government)*

| Test case | Requirement | Status |
|---|---|---|
| CRT_ABHA_401–411 | `enrol/byDocument`, upload, manual verification | **not built**. The path is declared in `abha/endpoints.py` and confirmed live; no flow. Blocks Government certification. |

## 5 · Verification via Aadhaar OTP — *Mandatory*

| Test case | Requirement | Status | Verified |
|---|---|---|---|
| VRFY_ABHA_101 | ABHA **number** via Aadhaar OTP | done | **live** — reaches UIDAI (ABDM-1204) |
| VRFY_ABHA_102 | ABHA **address** via Aadhaar OTP | done | **live** — PHR host, scope accepted |

## 6 · Verification via mobile OTP — *Mandatory*

| Test case | Requirement | Status | Verified |
|---|---|---|---|
| VRFY_ABHA_201 | ABHA **number** via mobile OTP | done | **live** — reaches ABDM lookup (ABDM-1115) |
| VRFY_ABHA_202 | ABHA **address** via mobile OTP | done | **live** — PHR host, scope accepted |

## 7 · Fetch ABHA by mobile — *Mandatory*

| Test case | Requirement | Status |
|---|---|---|
| VRFY_ABHA_301 | Retrieval by communication mobile | done |
| VRFY_ABHA_302 | "No ABHA for this mobile" + offer creation | done — ABDM-1115 passes through with its code so the UI can branch |
| VRFY_ABHA_303 | Multi-account list and selection | done — `login/verify/user` with the `T-token`; see `select_account` |
| VRFY_ABHA_304 | Incorrect OTP | done — ABDM's code preserved |
| VRFY_ABHA_305 | Resend limit | done |

## 8 · Fetch ABHA by Aadhaar number — *Mandatory*

| Test case | Status | Verified |
|---|---|---|
| VRFY_ABHA_401–405 | done | **live** — reaches UIDAI |

## 9 · Biometric verification — *Optional, Government*

| Test case | Status |
|---|---|
| VRFY_ABHA_501/502 | **partial** — `wadh` built; no verification call wired |

## 10 · Read ABHA via QR — *Optional*

| Test case | Requirement | Status |
|---|---|---|
| VRFY_ABHA_501 (QR) | Scan a QR to read ABHA info | **partial** — `GET /api/abha/qr` produces the payload. Scanning is client-side and the checklist itself says "No APIs"; no scanner in the UI. |

## 11 · Profile update — *all Optional*

| Test case | Requirement | Status |
|---|---|---|
| PROF_ABHA_601 | Mobile update | not built |
| PROF_ABHA_602 | Photo update | not built |
| PROF_ABHA_603 | Email update | not built |
| PROF_ABHA_604 | Re-KYC | not built |
| PROF_ABHA_605 | Delete / deactivate / reactivate | not built |

All five hang off `/v3/profile/account/request/otp` + `/verify`, both confirmed
live to exist. Every row is Optional, so none blocks M1.

## 16 · One ABHA ↔ one patient ID — *Mandatory*

| Test case | Requirement | Status |
|---|---|---|
| TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER | Tag one ABHA number to one unique patient ID; check before creating a new record | done — `backend/abha_link.py`, `/api/abha/link*` |

The only Mandatory item with **no ABDM API behind it** — the checklist puts the
guarantee on the integrator. Refuses in both directions and normalises
formatting, because `91-1234-5678-9012` and `91123456789012` would otherwise
pass the uniqueness check as two different identifiers. The map is gitignored:
ABHA numbers are health identifiers.

## 17 · Share profile — *PHR apps only*

| Test case | Status |
|---|---|
| SHARE_PATIENT_PROFILE_701 | **n/a** — Kravos Setu is an HIP/HIU, not a PHR app |

---

## Summary

**Every Mandatory item applicable to a private integrator is implemented, and
the creation and verification paths have been walked end to end against the
live sandbox by hand.** One real gap remains, and it is not an endpoint.

### Proven by hand, not only by test

A real ABHA was created through the product UI and then used to sign in:

| | |
|---|---|
| ABHA number | `91-7366-1108-3337` |
| ABHA address | `91736611083337@sbx` |
| Tagged to | patient `01607992-…` (6 allergy facts, 57 medications) |

That single run is evidence for CRT_ABHA_104, 105, 106, 107, 112, 113;
VRFY_ABHA_101 and 401/404; and TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER,
whose duplicate case was confirmed separately (a second patient against the
same ABHA is refused with HTTP 409).

## Outstanding

1. **Consent language (CRT_ABHA_102) — Mandatory, and the one real gap.**
   The consent *artefact* is sent correctly and the sign-up flow shows the
   consent as its own step before Aadhaar is collected, which is the
   behaviour the row requires. What is missing is the **wording**: the screen
   carries a placeholder, explicitly labelled as such. NHA's approved text is
   not in this repo, and writing an approximation would be worse than leaving
   it out — consent wording is exactly what this row audits, and a paraphrase
   would pass review by looking right. Obtain the approved language and drop
   it into `SignUpFlow`.

2. **ABHA card (CRT_ABHA_114) — blocked on ABDM, not on us.** The sandbox
   answers `/v3/profile/account/abha-card` with **501**, so the card cannot
   render there. The code path is built and the profile fields the row also
   asks for do come through. Re-test against production, or ask ABDM
   integration support whether the sandbox implements it at all.

3. **Driving Licence / PAN (section 4)** and **demo auth (section 3)** are
   unbuilt. Both are Optional for a private integrator and Mandatory for a
   Government one — worth settling which class applies before scheduling
   certification.

Everything the previous revision listed as "no UI yet" is now built: sign-in,
sign-up, profile and tagging all have product screens, and `/m1` keeps the
raw request/response view for a tester.

## Reproducing the live checks

```pwsh
python scripts/abha_smoke.py            # session + certificate + encryption
python scripts/abha_probe.py            # every M1 endpoint: exists? auth accepted?
python scripts/abha_probe.py --contract # scope/otpSystem matrices, live
```

`scripts/abha_probe.py` sends only non-existent identifiers. It spends no ABHA
creation quota and sends no OTP to anyone.
