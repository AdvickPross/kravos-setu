"""
Batch-run the FHIR extractor over a whole Synthea output folder.

Usage:
    python batch.py output

Writes two files next to wherever you run it:
    facts.jsonl     one fact per line (all patients), ready to index
    patients.jsonl  one patient per line (meta + fact count + unhandled types)

Skips Synthea's hospitalInformation*/practitionerInformation* files, which
have no Patient resource and would otherwise crash the extractor.
"""
import sys, json, pathlib
from dataclasses import asdict
from extract import extract_bundle

SKIP_PREFIXES = ("hospitalInformation", "practitionerInformation")


def main():
    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "output")
    files = [p for p in root.rglob("*.json")
             if not p.name.startswith(SKIP_PREFIXES)]
    print(f"Found {len(files)} candidate patient files under {root}")

    n_ok = n_skip = n_err = total_facts = 0
    with open("facts.jsonl", "w", encoding="utf-8") as ff, \
         open("patients.jsonl", "w", encoding="utf-8") as pf:
        for i, path in enumerate(files, 1):
            try:
                meta, facts, unhandled = extract_bundle(path)
            except Exception as ex:
                n_err += 1
                print(f"  ERR  {path.name}: {ex}")
                continue
            if not meta:                      # no Patient resource -> skip quietly
                n_skip += 1
                continue
            meta["_source_file"] = path.name
            meta["_unhandled"] = sorted(unhandled)
            meta["_n_facts"] = len(facts)
            pf.write(json.dumps(meta, ensure_ascii=False) + "\n")
            for f in facts:
                ff.write(json.dumps(asdict(f), ensure_ascii=False) + "\n")
            total_facts += len(facts)
            n_ok += 1
            if i % 100 == 0:
                print(f"  ...{i}/{len(files)} processed")

    print(f"\nDone. patients={n_ok}  skipped_no_patient={n_skip}  errors={n_err}")
    print(f"total facts written = {total_facts}")
    print("output -> facts.jsonl, patients.jsonl")


if __name__ == "__main__":
    main()
