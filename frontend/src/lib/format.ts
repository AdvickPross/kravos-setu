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

export function formatDate(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.slice(0, 10);
}

export function formatMs(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(1)}ms`;
}
