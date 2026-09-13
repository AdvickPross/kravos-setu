import type { AbhaCall } from "./abha";

/**
 * Turn an ABDM or UIDAI refusal into something a person can act on.
 *
 * The API layer is deliberately faithful: it passes ABDM's status and code
 * straight through, so a "user not found" arrives as a real 404 and a UIDAI
 * lockout as a real 422. That is right for an API and useless at a keyboard —
 * "UIDAI Error code : 403 : Maximum number of attempts for OTP match is
 * exceeded" tells you what happened and nothing about what to do, and the same
 * screen offers the same disabled buttons either way.
 *
 * Each case below pairs the plain-language cause with the one action that
 * actually resolves it. The recovery is the point; the wording is secondary.
 *
 * Matching is on message text as well as code because ABDM's codes are not
 * distinct enough — ABDM-1204 covers every UIDAI-side refusal, from a locked
 * Aadhaar to a wrong OTP to one that was never generated, and those need three
 * different answers.
 */

export type Recovery =
  /** Nothing to do but try the same thing again — a wrong OTP, typically. */
  | "retry"
  /** The transaction is dead; the flow must begin from the credential again. */
  | "restart"
  /** No ABHA exists for this credential — offer to create one. */
  | "create"
  /** A wait is genuinely required before anything will work. */
  | "wait";

export type Explained = {
  title: string;
  detail: string;
  recovery: Recovery;
};

const has = (haystack: string, ...needles: string[]) =>
  needles.some((n) => haystack.includes(n));

export function explain(call: AbhaCall): Explained {
  const text = `${call.abdmCode ?? ""} ${call.message ?? ""} ${
    call.fields?.map((f) => `${f.attribute} ${f.message}`).join(" ") ?? ""
  }`.toLowerCase();

  // UIDAI locks an Aadhaar after repeated OTP failures. Nothing on our side
  // clears it and no amount of retrying helps, so this must not look like the
  // wrong-OTP case that it otherwise resembles.
  // Matched on the phrase, not on a bare "403" -- three digits are far too
  // common in identifiers and transaction ids to key a lockout on.
  if (
    has(text, "maximum number of attempts", "uidai error code : 403", "number of attempts for otp")
  ) {
    return {
      title: "Too many OTP attempts",
      detail:
        "UIDAI has temporarily blocked verification for this Aadhaar. Wait a few minutes, then request a fresh OTP — entering another code now will keep failing.",
      recovery: "wait",
    };
  }

  if (has(text, "otp is not generated", "otp not generated")) {
    return {
      title: "No OTP was issued",
      detail: "That verification never started or has since expired. Request a new OTP.",
      recovery: "restart",
    };
  }

  if (has(text, "invalid transaction id", "transaction id")) {
    return {
      title: "This verification expired",
      detail:
        "ABDM closes a verification after a few minutes. Start again and enter the OTP as soon as it arrives.",
      recovery: "restart",
    };
  }

  if (has(text, "invalid otp", "otp is invalid", "incorrect otp", "wrong otp")) {
    return {
      title: "Incorrect OTP",
      detail: "Check the code and try again. Two resends are available.",
      recovery: "retry",
    };
  }

  // ABDM-1114 (ABHA number), ABDM-1115 (mobile). NHA's VRFY_ABHA_302 requires
  // this to lead to ABHA creation rather than to a dead end.
  if (
    call.status === 404 ||
    has(text, "abdm-1114", "abdm-1115", "user not found", "does not match", "no user")
  ) {
    return {
      title: "No ABHA found",
      detail:
        "Nothing is registered against what you entered. Check it, or create an ABHA — it takes about a minute with your Aadhaar.",
      recovery: "create",
    };
  }

  if (has(text, "aadhaar number is incorrect", "invalid loginid", "invalid aadhaar")) {
    return {
      title: "Aadhaar not recognised",
      detail: "UIDAI did not accept that number. Check the 12 digits and try again.",
      recovery: "retry",
    };
  }

  if (has(text, "invalid mobile")) {
    return {
      title: "Mobile number not accepted",
      detail:
        "ABDM rejects numbers that are not plausible Indian mobiles — repeated digits like 9999999999 among them.",
      recovery: "retry",
    };
  }

  // ABDM's sandbox throws this intermittently and it clears on its own.
  if (has(text, "runtime error", "303001", "unknown exception")) {
    return {
      title: "ABDM had a problem",
      detail: "The sandbox returned an internal error. This usually clears — try again.",
      recovery: "retry",
    };
  }

  if (call.status === 0) {
    return {
      title: "Cannot reach the server",
      detail: "The Kravos API is not responding. Check that it is running on port 8000.",
      recovery: "retry",
    };
  }

  return {
    title: "That did not work",
    detail: call.message ?? "ABDM refused the request.",
    recovery: "retry",
  };
}
