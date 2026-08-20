export function ageFromBirthDate(birthDate: string | null | undefined, asOf = new Date()): number | null {
  if (!birthDate) return null;
  const dob = new Date(birthDate);
  let age = asOf.getFullYear() - dob.getFullYear();
  const hadBirthdayThisYear =
    asOf.getMonth() > dob.getMonth() ||
    (asOf.getMonth() === dob.getMonth() && asOf.getDate() >= dob.getDate());
  if (!hadBirthdayThisYear) age -= 1;
  return Math.max(age, 0);
}

/** Age today, or age at death. Mirrors `PatientSummary.age_years` on the
 *  backend, which reports age *at death* for a deceased patient -- showing a
 *  deceased 1950-born patient as 76 would be wrong in both places. */
export function displayAge(
  birthDate: string | null | undefined,
  deceased: string | null | undefined,
): number | null {
  if (!birthDate) return null;
  return ageFromBirthDate(birthDate, deceased ? new Date(deceased) : new Date());
}

export function formatDate(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.slice(0, 10);
}

/** "3 mo ago", "12 y ago". Synthea timelines run deep into the past, so a bare
 *  ISO date makes a 1978 result look much like a 2025 one at a glance --
 *  which is exactly the distinction a pre-op reader needs fastest. */
export function relativeAge(value: string | null | undefined, now = new Date()): string | null {
  if (!value) return null;
  const then = new Date(value);
  if (Number.isNaN(then.getTime())) return null;
  const days = Math.floor((now.getTime() - then.getTime()) / 86_400_000);
  if (days < 0) return "future";
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 31) return `${days} d ago`;
  const months = Math.floor(days / 30.44);
  if (months < 24) return `${months} mo ago`;
  return `${Math.floor(days / 365.25)} y ago`;
}

export function formatMs(value: number | null | undefined): string {
  if (value == null) return "—";
  return value < 1 ? "<1 ms" : `${Math.round(value)} ms`;
}

/** `status` is clinically load-bearing -- perioperative guidance turns on
 *  continue-vs-hold -- so it is never filtered away, only styled. */
export type StatusTone = "active" | "historical" | "neutral";

export function statusTone(status: string | null | undefined): StatusTone {
  if (!status) return "neutral";
  const value = status.toLowerCase();
  if (value === "active" || value === "in-progress") return "active";
  if (value === "completed" || value === "resolved" || value === "final") return "historical";
  return "neutral";
}

const CATEGORY_LABELS: Record<string, string> = {
  ALLERGY: "Allergies",
  MEDICATION: "Medications",
  CONDITION: "Conditions",
  LAB: "Labs",
  VITAL: "Vitals",
  PROCEDURE: "Procedures",
  IMMUNIZATION: "Immunisations",
  REPORT: "Reports",
};

export function categoryLabel(category: string | null | undefined): string {
  if (!category) return "Other";
  return CATEGORY_LABELS[category] ?? category.charAt(0) + category.slice(1).toLowerCase();
}

/** Singular, for prose like "every allergy record". The plural labels above
 *  read as "every allergies record", which is wrong. */
const CATEGORY_NOUNS: Record<string, string> = {
  ALLERGY: "allergy",
  MEDICATION: "medication",
  CONDITION: "condition",
  LAB: "lab",
  VITAL: "vital sign",
  PROCEDURE: "procedure",
  IMMUNIZATION: "immunisation",
  REPORT: "report",
};

export function categoryNoun(category: string | null | undefined): string {
  if (!category) return "record";
  return CATEGORY_NOUNS[category] ?? category.toLowerCase();
}

/** AllergyIntolerance.criticality, which `extract.py` stores in `Fact.value`
 *  for ALLERGY rows. Shown labelled rather than as a bare "low", which next to
 *  an allergen reads as an anonymous measurement. Only `low` occurs in this
 *  corpus (880/880), but a `high` must be impossible to skim past if real
 *  data ever carries one. */
export function criticalityTone(value: string | null | undefined): "danger" | "neutral" {
  const v = (value ?? "").toLowerCase();
  return v === "high" || v === "unable-to-assess" ? "danger" : "neutral";
}
