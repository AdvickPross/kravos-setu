/**
 * NHA's M1 test cases, as data.
 *
 * Transcribed from `M1_ABHA_CREATION_AND_VERIFICATION_WITH_APIS_UPDATED_V1_1`.
 * Only the rows a private integrator must demonstrate through a UI are here —
 * the Government-only sections (3, demo auth) and the PHR-app section (17)
 * are excluded, and `M1_COVERAGE.md` says why.
 *
 * The point of holding these as data rather than as prose in the JSX: the
 * console can show the reviewer *which row* a screen is evidence for, and the
 * tick-off state can key on the same id. A checklist whose ids live only in a
 * spreadsheet gets tested out of sync with the spreadsheet.
 */

export type Mandate = "mandatory" | "optional";

export type M1Case = {
  id: string;
  title: string;
  mandate: Mandate;
  /** What the tester has to see happen for this row to pass. */
  expect: string;
};

export type M1Group = {
  key: "create" | "verify" | "address" | "tag";
  section: string;
  title: string;
  blurb: string;
  cases: M1Case[];
};

export const M1_GROUPS: M1Group[] = [
  {
    key: "create",
    section: "§1",
    title: "Create ABHA — Aadhaar OTP",
    blurb:
      "The mandatory creation path. Every step below spends one of the sandbox's 100 ABHA creations only at the final verify; everything before it is free.",
    cases: [
      {
        id: "CRT_ABHA_102",
        title: "Consent collection",
        mandate: "mandatory",
        expect:
          "Consent text is shown and explicitly agreed to before Aadhaar is collected.",
      },
      {
        id: "CRT_ABHA_104",
        title: "Aadhaar entry and error message",
        mandate: "mandatory",
        expect:
          "A non-12-digit Aadhaar is refused with a clear message, and no request is sent.",
      },
      {
        id: "CRT_ABHA_105",
        title: "Aadhaar OTP collection",
        mandate: "mandatory",
        expect: "OTP field accepts 6 digits; the masked destination is shown.",
      },
      {
        id: "CRT_ABHA_106",
        title: "Resend OTP — max 2×, 60s apart",
        mandate: "mandatory",
        expect:
          "Resend is blocked for 60s, allowed twice, then refused with a message telling the user to start again.",
      },
      {
        id: "CRT_ABHA_107",
        title: "OTP verification",
        mandate: "mandatory",
        expect: "A wrong OTP shows an error; a correct one proceeds.",
      },
      {
        id: "CRT_ABHA_109",
        title: "Communication mobile verification",
        mandate: "mandatory",
        expect:
          "A mobile different from the Aadhaar-linked one is verified by its own OTP — and if it is saved as null, the console says so.",
      },
      {
        id: "CRT_ABHA_112",
        title: "ABHA address — suggestions and rules",
        mandate: "mandatory",
        expect:
          "At least 3 suggestions offered; a custom address is validated against all five rules.",
      },
      {
        id: "CRT_ABHA_113",
        title: "Display of ABHA number",
        mandate: "mandatory",
        expect: "The 14-digit ABHA number and the ABHA address are both displayed.",
      },
      {
        id: "CRT_ABHA_114",
        title: "View / download ABHA card",
        mandate: "mandatory",
        expect: "The card renders and can be downloaded.",
      },
    ],
  },
  {
    key: "verify",
    section: "§5–8",
    title: "Verify ABHA — number, Aadhaar, mobile",
    blurb:
      "Verification on the ABHA host. Three credentials, each with its own scope and OTP system — the combination is not derivable from the credential, so each is a separate row.",
    cases: [
      {
        id: "VRFY_ABHA_101",
        title: "ABHA number via Aadhaar OTP",
        mandate: "mandatory",
        expect: "Correct OTP fetches the profile; wrong OTP fails with a message.",
      },
      {
        id: "VRFY_ABHA_201",
        title: "ABHA number via mobile OTP",
        mandate: "mandatory",
        expect: "OTP goes to the ABHA-linked communication mobile.",
      },
      {
        id: "VRFY_ABHA_301",
        title: "Fetch ABHA by communication mobile",
        mandate: "mandatory",
        expect: "An invalid mobile shows a relevant error.",
      },
      {
        id: "VRFY_ABHA_302",
        title: "No ABHA for this mobile",
        mandate: "mandatory",
        expect:
          "A clear 'not found' message, and the option to create an ABHA stays available.",
      },
      {
        id: "VRFY_ABHA_303",
        title: "Several ABHAs on one mobile",
        mandate: "mandatory",
        expect:
          "All linked accounts are listed with name, ABHA number and address, and one can be selected.",
      },
      {
        id: "VRFY_ABHA_304",
        title: "Incorrect OTP",
        mandate: "mandatory",
        expect: "Verification fails and the message says why.",
      },
      {
        id: "VRFY_ABHA_401",
        title: "Fetch ABHA by Aadhaar number",
        mandate: "mandatory",
        expect: "A wrong Aadhaar shows a relevant error.",
      },
      {
        id: "VRFY_ABHA_404",
        title: "ABHA exists for this Aadhaar",
        mandate: "mandatory",
        expect: "Profile is displayed and the card can be downloaded.",
      },
    ],
  },
  {
    key: "address",
    section: "§5.2 / 6.1",
    title: "Verify ABHA — by ABHA address",
    blurb:
      "A separate family on the PHR host, not a fourth credential on the one above. Routing an ABHA address through the ABHA-host login endpoints returns 'Invalid Scope'.",
    cases: [
      {
        id: "VRFY_ABHA_102",
        title: "ABHA address via Aadhaar OTP",
        mandate: "mandatory",
        expect: "Profile is fetched after the Aadhaar OTP is verified.",
      },
      {
        id: "VRFY_ABHA_202",
        title: "ABHA address via mobile OTP",
        mandate: "mandatory",
        expect: "Profile is fetched after the communication-mobile OTP is verified.",
      },
    ],
  },
  {
    key: "tag",
    section: "§16",
    title: "One ABHA ↔ one patient ID",
    blurb:
      "The only mandatory row with no ABDM API behind it. The guarantee is the integrator's, and the failure it prevents is one person with two partial histories.",
    cases: [
      {
        id: "TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER",
        title: "ABHA number tagged to a unique patient ID",
        mandate: "mandatory",
        expect:
          "An ABHA already tagged cannot be tagged to a second patient; the check happens before a new record is created.",
      },
    ],
  },
];

export const ALL_CASES: M1Case[] = M1_GROUPS.flatMap((group) => group.cases);

/** The five CRT_ABHA_112 rules, checked client-side so the tester sees which
 *  one they broke before a request goes out. Mirrors
 *  `abha/enrollment.py:validate_abha_address` — the server is the authority
 *  and rejects independently; this is only for immediate feedback. */
export function abhaAddressProblem(value: string): string | null {
  const local = value.trim().split("@")[0];
  if (local.length < 8 || local.length > 18)
    return `Must be 8–18 characters (this is ${local.length}).`;
  if (!/^[A-Za-z0-9._]+$/.test(local))
    return "Only letters, digits, one dot and one underscore are allowed.";
  if (/^[._]|[._]$/.test(local))
    return "A dot or underscore must be between characters, never first or last.";
  if ((local.match(/\./g) ?? []).length > 1) return "At most one dot.";
  if ((local.match(/_/g) ?? []).length > 1) return "At most one underscore.";
  return null;
}
