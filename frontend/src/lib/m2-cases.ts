/**
 * NHA's M2 ("Building HIP") test cases, as data.
 *
 * Transcribed from `M2_BUILDING_HIP_WITH_APIS_UPDATED_22_Aug_871c2f7fcd.xlsx`.
 * The optional HIP-initiated methods (Mobile OTP, Aadhaar OTP, Direct Auth;
 * §2–4) are left out: the sheet requires any one HIP-initiated method, and
 * Demographic Auth is the mandatory one. `M2_COVERAGE.md` says why for every
 * row.
 */

import type { M1Case } from "./m1-cases";

/** Same shape as an M1 case, so `StepCard` renders both. */
export type M2Case = M1Case;

export type M2Group = {
  key: "setup" | "records" | "hip" | "user" | "sms" | "share";
  section: string;
  title: string;
  blurb: string;
  cases: M2Case[];
};

export const M2_GROUPS: M2Group[] = [
  {
    key: "setup",
    section: "§0",
    title: "Sandbox setup",
    blurb:
      "Before any row can be tested live: ABDM credentials, a HIP ID linked to our bridge, and a bridge URL pointing at this server. Nothing on this screen is a test case except the facility being findable.",
    cases: [
      {
        id: "USER_INIT_LINK_602",
        title: "Facility searchable in the PHR app",
        mandate: "mandatory",
        expect: "Searching the facility in the PHR app finds it — needs the HIP registered and linked to the bridge.",
      },
    ],
  },
  {
    key: "records",
    section: "§1",
    title: "Health record creation",
    blurb:
      "Every visit is shared as an ABDM FHIR document (OPConsultRecord). This works today with no ABDM involvement — preview any visit's bundle.",
    cases: [
      {
        id: "Health_RECORD_CREATION_101",
        title: "Digital health records in FHIR",
        mandate: "mandatory",
        expect: "A digital health record is generated in ABDM's FHIR format.",
      },
    ],
  },
  {
    key: "hip",
    section: "§8a",
    title: "HIP-initiated linking (demographic)",
    blurb:
      "We link a patient's records to their ABHA without the patient doing anything: ABDM checks our demographics against the ABHA, issues a link token, and we link every visit from the backend — no click needed after the first.",
    cases: [
      {
        id: "HIP_INTI_LINK_501",
        title: "Link records via demographic auth",
        mandate: "mandatory",
        expect: "The system can link a patient's health records with their ABHA address.",
      },
      {
        id: "HIP_INTI_LINK_502",
        title: "Demographics held by the HIP",
        mandate: "mandatory",
        expect: "Name, date of birth, gender and mobile are present for the patient.",
      },
      {
        id: "HIP_INTI_LINK_503",
        title: "Demographics validated by the gateway",
        mandate: "mandatory",
        expect: "ABDM validates the demographics and generates a linking token.",
      },
      {
        id: "HIP_INTI_LINK_504",
        title: "New linking token created",
        mandate: "mandatory",
        expect: "A new link token is received and stored.",
      },
      {
        id: "HIP_INTI_LINK_505",
        title: "Link records — backend batch",
        mandate: "mandatory",
        expect: "Linking is pushed from the backend as a batch process; records become linked.",
      },
      {
        id: "HIP_INTI_LINK_506",
        title: "Pull records in the PHR app",
        mandate: "mandatory",
        expect: "The linked records can be pulled in the PHR app with correct HI types, dates and demographics.",
      },
    ],
  },
  {
    key: "user",
    section: "§6",
    title: "User-initiated linking (PHR app)",
    blurb:
      "The patient drives this from the ABDM PHR app. Our server answers ABDM in the background — discovery, OTP, confirm — so this screen shows readiness and the live exchange log.",
    cases: [
      { id: "USER_INIT_LINK_601", title: "Log into the PHR app", mandate: "optional", expect: "The user can log into the sandbox PHR app." },
      { id: "USER_INIT_LINK_603", title: "Patient identified by the HIP", mandate: "mandatory", expect: "We match the patient — ABHA address first, then mobile with a demographic match." },
      { id: "USER_INIT_LINK_604", title: "Records listed in the PHR app", mandate: "mandatory", expect: "The PHR app lists the patient's unlinked records at this facility." },
      { id: "USER_INIT_LINK_605", title: "OTP consent to link", mandate: "mandatory", expect: "An OTP reaches the patient; the correct OTP links the selected records, a wrong one is refused." },
      { id: "USER_INIT_LINK_606", title: "Consent manager validates", mandate: "mandatory", expect: "ABDM validates the request (gateway side)." },
      { id: "USER_INIT_LINK_607", title: "Pull records in the PHR app", mandate: "mandatory", expect: "The linked records can be pulled in the PHR app." },
    ],
  },
  {
    key: "sms",
    section: "§7",
    title: "Deep-link SMS",
    blurb:
      "For a patient with a mobile on file but no ABHA address: ABDM texts them a link to a PHR app, from which they discover their records here.",
    cases: [
      {
        id: "HIP_INIT_NOTIFY_HIECM",
        title: "SMS with deep link",
        mandate: "mandatory",
        expect: "ABDM sends the patient an SMS with a deep link; after installing a PHR app they can discover the record.",
      },
    ],
  },
  {
    key: "share",
    section: "§8b",
    title: "Consent & data transfer",
    blurb:
      "An HIU requests data under a consent the patient granted. We verify the consent, encrypt each visit for the HIU and push it, then report the outcome. Revoked or expired consents are deleted and nothing more is shared.",
    cases: [
      { id: "HIP_INIT_GRANT_CONSENT_", title: "Granted consent saved", mandate: "optional", expect: "A granted consent appears in the HIP's system." },
      { id: "HIP_INIT_REVOKE_CONSENT", title: "Revoked consent deleted", mandate: "optional", expect: "A revoked consent disappears from the HIP's system." },
      { id: "HIP_INIT_EXPIRE_CONSENT", title: "Expired consent deleted", mandate: "optional", expect: "An expired consent disappears from the HIP's system." },
      { id: "HIP_INIT_SHARE_CARECONTEXT", title: "Share records on request", mandate: "mandatory", expect: "Records arrive in the PHR app, decryptable, in proper FHIR, within 2 hours." },
    ],
  },
];

export const ALL_M2_CASES: M2Case[] = M2_GROUPS.flatMap((g) => g.cases);
