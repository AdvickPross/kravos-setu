"""
FHIR R4 bundle -> flat fact table with provenance.
Design invariants:
  1. Every fact carries source_resource_type + source_resource_id + date
     from the moment it is created. Provenance is never added later.
  2. Facts are classified into a safety_class. SAFETY facts (allergies,
     active meds) are pulled deterministically and must never depend on
     a retriever deciding they are "relevant".
  3. Extraction is dumb and lossless-ish. No LLM here. No judgement here.
"""
import json, base64, hashlib, pathlib
from dataclasses import dataclass, asdict
from typing import Optional, List
# --------------------------------------------------------------------------
# fact model
# --------------------------------------------------------------------------
@dataclass
class Fact:
    fact_id: str
    patient_id: str
    category: str          # ALLERGY | MEDICATION | CONDITION | LAB | VITAL | PROCEDURE | IMMUNIZATION | NOTE | REPORT
    safety_class: str      # SAFETY | CLINICAL | CONTEXT
    text: str              # human-readable, this is what gets embedded / shown
    code_system: Optional[str]
    code: Optional[str]
    display: Optional[str]
    value: Optional[str]
    unit: Optional[str]
    date: Optional[str]    # ISO, may be None
    status: Optional[str]
    source_resource_type: str
    source_resource_id: str
    encounter_id: Optional[str]
    also_in: List[str] = None
    def __post_init__(self):
        if self.also_in is None:
            self.also_in = []
def _fid(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]
# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _cc(concept):
    """CodeableConcept -> (system, code, display)."""
    if not concept:
        return (None, None, None)
    for c in concept.get("coding", []):
        return (c.get("system"), c.get("code"), c.get("display") or concept.get("text"))
    return (None, None, concept.get("text"))
def _ref_id(node, key="reference"):
    r = (node or {}).get(key)
    if not r:
        return None
    return r.split(":")[-1].split("/")[-1]
def _date(r):
    for k in ("onsetDateTime", "authoredOn", "effectiveDateTime", "occurrenceDateTime",
              "recordedDate", "issued", "date"):
        if r.get(k):
            return r[k]
    for k in ("performedPeriod", "effectivePeriod", "period"):
        if r.get(k, {}).get("start"):
            return r[k]["start"]
    if r.get("effectiveInstant"):
        return r["effectiveInstant"]
    return None
def _med_concept(r, index=None):
    """Synthea uses medicationReference -> a sibling Medication resource in the
    same bundle (urn:uuid:...), NOT medicationCodeableConcept and NOT contained.
    Resolve through the bundle index or every drug name is silently lost."""
    if r.get("medicationCodeableConcept"):
        return r["medicationCodeableConcept"]
    ref = _ref_id(r.get("medicationReference"))
    if ref:
        # contained first
        for c in r.get("contained", []):
            if c.get("id") == ref and c.get("code"):
                return c["code"]
        # then bundle-wide
        if index and ref in index:
            return index[ref].get("code")
    return None
# --------------------------------------------------------------------------
# extractors, one per resource type
# --------------------------------------------------------------------------
def _ex_allergy(r, pid, index=None):
    sys_, code, disp = _cc(r.get("code"))
    crit = r.get("criticality")
    reactions = []
    for rx in r.get("reaction", []):
        for m in rx.get("manifestation", []):
            _, _, d = _cc(m)
            if d:
                reactions.append(d)
    txt = f"Allergy: {disp}"
    if crit:
        txt += f" (criticality {crit})"
    if reactions:
        txt += f", reaction: {', '.join(reactions)}"
    return [Fact(_fid(r["id"], "allergy"), pid, "ALLERGY", "SAFETY", txt,
                 sys_, code, disp, crit, None, _date(r),
                 r.get("clinicalStatus", {}).get("coding", [{}])[0].get("code"),
                 "AllergyIntolerance", r["id"], None)]
def _ex_medication(r, pid, index=None):
    sys_, code, disp = _cc(_med_concept(r, index))
    status = r.get("status")
    rt = r["resourceType"]
    txt = f"Medication: {disp}" if disp else "Medication (uncoded)"
    if status:
        txt += f" [{status}]"
    return [Fact(_fid(r["id"], "med"), pid, "MEDICATION", "SAFETY", txt,
                 sys_, code, disp, None, None, _date(r), status,
                 rt, r["id"], _ref_id(r.get("encounter") or r.get("context")))]
def _ex_condition(r, pid, index=None):
    sys_, code, disp = _cc(r.get("code"))
    status = r.get("clinicalStatus", {}).get("coding", [{}])[0].get("code")
    txt = f"Condition: {disp}"
    if status:
        txt += f" [{status}]"
    if r.get("abatementDateTime"):
        txt += f" (resolved {r['abatementDateTime'][:10]})"
    return [Fact(_fid(r["id"], "cond"), pid, "CONDITION", "CLINICAL", txt,
                 sys_, code, disp, None, None, _date(r), status,
                 "Condition", r["id"], _ref_id(r.get("encounter")))]
VITAL_CODES = {"8302-2", "29463-7", "39156-5", "8480-6", "8462-4", "8867-4",
               "9279-1", "8310-5", "2708-6", "59408-5", "72514-3"}
def _ex_observation(r, pid, index=None):
    sys_, code, disp = _cc(r.get("code"))
    cat = ""
    for c in r.get("category", []):
        _, _, cd = _cc(c)
        cat = (cd or "").lower()
    is_vital = code in VITAL_CODES or "vital" in cat
    category = "VITAL" if is_vital else "LAB"
    out = []
    def emit(d, val, unit, suffix=""):
        t = f"{d}: {val}"
        if unit:
            t += f" {unit}"
        out.append(Fact(_fid(r["id"], "obs", suffix or d), pid, category,
                        "CLINICAL", t, sys_, code, d, str(val) if val is not None else None,
                        unit, _date(r), r.get("status"), "Observation", r["id"],
                        _ref_id(r.get("encounter"))))
    if "valueQuantity" in r:
        v = r["valueQuantity"]
        emit(disp, v.get("value"), v.get("unit"))
    elif "valueCodeableConcept" in r:
        _, _, vd = _cc(r["valueCodeableConcept"])
        emit(disp, vd, None)
    elif "valueString" in r:
        emit(disp, r["valueString"], None)
    elif "component" in r:
        # e.g. blood pressure -> systolic + diastolic as separate facts
        for i, comp in enumerate(r["component"]):
            _, ccode, cdisp = _cc(comp.get("code"))
            v = comp.get("valueQuantity", {})
            out.append(Fact(_fid(r["id"], "obs", ccode or i), pid, category, "CLINICAL",
                            f"{cdisp}: {v.get('value')} {v.get('unit','')}".strip(),
                            sys_, ccode, cdisp,
                            str(v.get("value")) if v.get("value") is not None else None,
                            v.get("unit"), _date(r), r.get("status"),
                            "Observation", r["id"], _ref_id(r.get("encounter"))))
    return out
def _ex_procedure(r, pid, index=None):
    sys_, code, disp = _cc(r.get("code"))
    return [Fact(_fid(r["id"], "proc"), pid, "PROCEDURE", "CLINICAL",
                 f"Procedure: {disp}", sys_, code, disp, None, None,
                 _date(r), r.get("status"), "Procedure", r["id"],
                 _ref_id(r.get("encounter")))]
def _ex_immunization(r, pid, index=None):
    sys_, code, disp = _cc(r.get("vaccineCode"))
    return [Fact(_fid(r["id"], "imm"), pid, "IMMUNIZATION", "CONTEXT",
                 f"Immunisation: {disp}", sys_, code, disp, None, None,
                 _date(r), r.get("status"), "Immunization", r["id"],
                 _ref_id(r.get("encounter")))]
def _ex_docref(r, pid, index=None):
    out = []
    for i, c in enumerate(r.get("content", [])):
        att = c.get("attachment", {})
        data = att.get("data")
        if not data:
            continue
        try:
            txt = base64.b64decode(data).decode("utf-8", "replace")
        except Exception:
            continue
        _, _, disp = _cc(r.get("type"))
        out.append(Fact(_fid(r["id"], "doc", i), pid, "NOTE", "CLINICAL",
                        txt.strip(), None, None, disp, None, None,
                        _date(r), r.get("status"), "DocumentReference", r["id"],
                        _ref_id((r.get("context", {}).get("encounter") or [{}])[0])))
    return out
def _ex_diagnosticreport(r, pid, index=None):
    sys_, code, disp = _cc(r.get("code"))
    out = []
    for i, c in enumerate(r.get("presentedForm", [])):
        if not c.get("data"):
            continue
        try:
            txt = base64.b64decode(c["data"]).decode("utf-8", "replace")
        except Exception:
            continue
        out.append(Fact(_fid(r["id"], "rep", i), pid, "REPORT", "CLINICAL",
                        txt.strip(), sys_, code, disp, None, None, _date(r),
                        r.get("status"), "DiagnosticReport", r["id"],
                        _ref_id(r.get("encounter"))))
    return out
EXTRACTORS = {
    "AllergyIntolerance":    _ex_allergy,
    "MedicationRequest":     _ex_medication,
    "MedicationAdministration": _ex_medication,
    "MedicationStatement":   _ex_medication,
    "Condition":             _ex_condition,
    "Observation":           _ex_observation,
    "Procedure":             _ex_procedure,
    "Immunization":          _ex_immunization,
    "DocumentReference":     _ex_docref,
    "DiagnosticReport":      _ex_diagnosticreport,
}
# resources deliberately dropped: billing and infrastructure noise
IGNORE = {"Claim", "ExplanationOfBenefit", "Coverage", "Organization",
          "Practitioner", "PractitionerRole", "Location", "Provenance",
          "SupplyDelivery", "Device", "CareTeam", "Medication", "ImagingStudy",
          "Encounter", "Patient", "CarePlan"}
# --------------------------------------------------------------------------
# bundle level
# --------------------------------------------------------------------------
def patient_meta(bundle):
    for e in bundle.get("entry", []):
        r = e["resource"]
        if r["resourceType"] == "Patient":
            name = ""
            if r.get("name"):
                n = r["name"][0]
                name = " ".join(n.get("given", []) + [n.get("family", "")]).strip()
            return {"patient_id": r["id"], "name": name,
                    "birth_date": r.get("birthDate"), "gender": r.get("gender"),
                    "deceased": r.get("deceasedDateTime")}
    return None
def build_index(bundle):
    """id -> resource, for resolving urn:uuid references across the bundle."""
    idx = {}
    for e in bundle.get("entry", []):
        r = e["resource"]
        if r.get("id"):
            idx[r["id"]] = r
        fu = e.get("fullUrl", "")
        if fu:
            idx[fu.split(":")[-1]] = r
    return idx
def extract_bundle(path) -> tuple:
    bundle = json.load(open(path))
    meta = patient_meta(bundle)
    if not meta:
        return None, [], set()
    pid = meta["patient_id"]
    index = build_index(bundle)
    facts, unhandled = [], set()
    for e in bundle.get("entry", []):
        r = e["resource"]
        rt = r["resourceType"]
        fn = EXTRACTORS.get(rt)
        if fn:
            try:
                facts.extend(fn(r, pid, index))
            except Exception as ex:
                unhandled.add(f"{rt}:ERR:{ex}")
        elif rt not in IGNORE:
            unhandled.add(rt)
    # Synthea writes the same note into DocumentReference AND DiagnosticReport.
    # Keep the first, drop byte-identical repeats, but record the dropped
    # source so provenance survives deduplication.
    seen, deduped = {}, []
    for f in facts:
        if f.category in ("NOTE", "REPORT"):
            h = hashlib.sha1(f.text.encode()).hexdigest()
            if h in seen:
                seen[h].also_in.append(f"{f.source_resource_type}/{f.source_resource_id}")
                continue
            seen[h] = f
        deduped.append(f)
    facts = deduped
    facts.sort(key=lambda f: (f.date or "0000"))
    return meta, facts, unhandled
# --------------------------------------------------------------------------
# the deterministic safety pull -- REQ-2 / SAF territory
# --------------------------------------------------------------------------
def safety_block(facts: List[Fact]) -> List[Fact]:
    """Facts that are ALWAYS surfaced, regardless of what any retriever thinks.
    Semantic relevance decides what is interesting. It must never decide
    whether an allergy appears."""
    out = [f for f in facts if f.category == "ALLERGY"]
    meds = [f for f in facts if f.category == "MEDICATION"
            and f.status in (None, "active", "completed", "in-progress")]
    meds.sort(key=lambda f: f.date or "", reverse=True)
    seen, dedup = set(), []
    for m in meds:
        if m.code in seen:
            continue
        seen.add(m.code)
        dedup.append(m)
    return out + dedup
if __name__ == "__main__":
    import sys, collections
    p = sys.argv[1]
    meta, facts, unhandled = extract_bundle(p)
    print("PATIENT:", meta)
    print("FACTS:", len(facts))
    c = collections.Counter(f.category for f in facts)
    for k, v in c.most_common():
        print(f"  {v:4d}  {k}")
    if unhandled:
        print("UNHANDLED:", unhandled)
    print("\nSAFETY BLOCK (always surfaced):")
    for f in safety_block(facts):
        print(f"  [{f.source_resource_type}/{f.source_resource_id[:8]} {f.date[:10] if f.date else '?'}] {f.text}")
    print("\nSAMPLE FACTS WITH PROVENANCE:")
    for f in facts[:3] + facts[-3:]:
        print(f"  {f.fact_id} {f.category:12s} {(f.date or '?')[:10]}  {f.text[:70]!r}")
        print(f"       <- {f.source_resource_type}/{f.source_resource_id}")
