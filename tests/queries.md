# Probe queries

**Generated** — edit `data/probe_queries.txt`, then rerun:

```pwsh
python scripts/run_probes.py
```

Run against `lexicon.csv` + `data/synonyms.csv`, with today = 2026-08-19.

This is a **debugging instrument, not an evaluation set**. It records what resolved and what didn't so that a change in the normalizer or the synonym file shows up as a diff. No accuracy number is claimed or implied; the gold eval set (80–100 queries with gold fact IDs) is a separate artifact.

---

### 1. `what meds is she on right now`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `meds` | unresolved | — | — | — |  |

### 2. `HTN and T2DM meds?`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `HTN` | synonym | CONDITION | `59621000` | Essential hypertension (disorder) |  |
| `T2DM` | synonym | CONDITION | `44054006` | Diabetes mellitus type 2 (disorder) |  |
| `meds` | unresolved | — | — | — |  |

### 3. `is he on warfarin or clopidogrel before surgery`

*date window:* … → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `warfarin` | exact | MEDICATION | `855332` | Warfarin Sodium 5 MG Oral Tablet |  |
| `clopidogrel` | exact | MEDICATION | `309362`, `749196` | clopidogrel |  |

### 4. `does she take metformin and what dose`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `metformin` | exact | MEDICATION | `860975` | 24 HR Metformin hydrochloride 500 MG Extended Release Oral Tablet |  |
| `dose` | unresolved | — | — | — |  |

### 5. `stopped aspirin before the procedure?`

*date window:* … → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `aspirin` | exact | ALLERGY | `1191` | Aspirin |  |
| `aspirin` | exact | MEDICATION | `212033`, `243670`, `2563431` | aspirin |  |

### 6. `any beta blockers or ACE inhibitors on the list`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `beta blockers` | unresolved | — | — | — |  |
| `ACE inhibitors` | unresolved | — | — | — |  |

### 7. `on paracetamol and salbutamol?`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `paracetamol` | synonym | MEDICATION | `1043400`, `1049221`, `1049625`, `198440` +6 | paracetamol |  |
| `salbutamol` | synonym | MEDICATION | `2123111`, `245314`, `351136`, `351137` +3 | salbutamol |  |

### 8. `any drug allergies before I prescribe amoxicillin`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `drug allergies` | synonym | ALLERGY | `609328004` | Allergic disposition (finding) |  |
| `amoxicillin` | exact | MEDICATION | `308182`, `308192`, `562251`, `562508` +1 | amoxicillin |  |

### 9. `is he allergic to penicillin`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `allergic` | synonym | ALLERGY | `609328004` | Allergic disposition (finding) |  |
| `penicillin` | synonym | ALLERGY | `7984` | Penicillin V |  |
| `penicillin` | synonym | MEDICATION | `105078`, `834061`, `834102` | penicillin |  |

### 10. `any patient with no known allergies`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `no known allergies` | synonym | ALLERGY | `609328004` | Allergic disposition (finding) | yes |

### 11. `NKDA documented?`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `NKDA` | synonym | ALLERGY | `609328004` | Allergic disposition (finding) | yes |

### 12. `amoxicilin allergy`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `amoxicilin` | fuzzy | MEDICATION | `308182`, `308192`, `562251`, `562508` +1 | amoxicillin |  |
| `allergy` | synonym | ALLERGY | `609328004` | Allergic disposition (finding) |  |

### 13. `sulfa allergy or reaction to cotrimoxazole`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `sulfa` | synonym | ALLERGY | `10831` | Sulfamethoxazole / Trimethoprim |  |
| `sulfa` | synonym | MEDICATION | `198335` | sulfamethoxazole 800 MG / trimethoprim 160 MG Oral Tablet |  |
| `allergy` | synonym | ALLERGY | `609328004` | Allergic disposition (finding) |  |
| `reaction` | unresolved | — | — | — |  |
| `cotrimoxazole` | synonym | ALLERGY | `10831` | Sulfamethoxazole / Trimethoprim |  |
| `cotrimoxazole` | synonym | MEDICATION | `198335` | sulfamethoxazole 800 MG / trimethoprim 160 MG Oral Tablet |  |

### 14. `hba1c trend over the last 2 years`

*date window:* 2024-08-19 → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `hba1c` | synonym | LAB | `4548-4` | Hemoglobin A1c/Hemoglobin.total in Blood |  |

### 15. `glucose over the last year`

*date window:* 2025-08-19 → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `glucose` | exact | LAB | `2339-0`, `2345-7`, `25428-4`, `5792-7` | glucose |  |

### 16. `latest creatinine and eGFR`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `creatinine` | exact | LAB | `2160-0`, `38483-4` | creatinine |  |
| `eGFR` | synonym | LAB | `33914-3` | Glomerular filtration rate [Volume Rate/Area] in Serum or Plasma by Creatinine-based formula (MDRD)/1.73 sq M |  |

### 17. `K+ and Na levels since 2019`

*date window:* 2019-01-01 → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `K+` | synonym | LAB | `2823-3`, `6298-4` | potassium |  |
| `Na` | synonym | LAB | `2947-0`, `2951-2` | sodium |  |

### 18. `hb and hct before surgery`

*date window:* … → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `hb` | synonym | LAB | `718-7` | Hemoglobin [Mass/volume] in Blood |  |
| `hct` | synonym | LAB | `4544-3`, `20570-8` | hematocrit |  |

### 19. `LFT and RFT results this year`

*date window:* 2026-01-01 → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `LFT` | synonym | LAB | `1742-6`, `1920-8`, `1975-2`, `6768-6` +1 | liver function tests |  |
| `RFT` | synonym | LAB | `2160-0`, `38483-4`, `3094-0`, `6299-2` +1 | renal function tests |  |

### 20. `lipid profile - LDL and HDL`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `lipid profile` | synonym | LAB | `2093-3`, `18262-6`, `2085-9`, `2571-8` | total cholesterol |  |
| `LDL` | synonym | LAB | `18262-6` | Cholesterol in LDL [Mass/volume] in Serum or Plasma by Direct assay |  |
| `HDL` | synonym | LAB | `2085-9` | Cholesterol in HDL [Mass/volume] in Serum or Plasma |  |

### 21. `INR and platelet count`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `INR` | exact | LAB | `6301-6` | INR in Platelet poor plasma by Coagulation assay |  |
| `platelet count` | synonym | LAB | `26515-7`, `777-3` | platelets |  |

### 22. `what was the last troponin`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `troponin` | synonym | LAB | `89579-7` | Troponin I.cardiac [Mass/volume] in Serum or Plasma by High sensitivity method |  |

### 23. `BP and heart rate readings in 2024`

*date window:* 2024-01-01 → 2024-12-31

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `BP` | synonym | VITAL | `8480-6`, `8462-4`, `8478-0` | blood pressure |  |
| `heart rate` | exact | VITAL | `8867-4` | Heart rate |  |

### 24. `SpO2 and respiratory rate recent`

*date window:* 2026-02-20 → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `SpO2` | synonym | VITAL | `2708-6` | Oxygen saturation in Arterial blood |  |
| `respiratory rate` | exact | VITAL | `9279-1` | Respiratory rate |  |

### 25. `BMI and weight trend`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `BMI` | synonym | VITAL | `39156-5`, `59576-9` | body mass index |  |
| `weight` | synonym | VITAL | `29463-7` | Body Weight |  |

### 26. `no history of MI but has CKD stage 3`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `MI` | synonym | CONDITION | `22298006`, `401303003`, `401314000`, `399211009` | myocardial infarction | yes |
| `CKD` | synonym | CONDITION | `431855005`, `431856006`, `433144002`, `431857002` +1 | chronic kidney disease |  |
| `stage` | unresolved | — | — | — |  |

### 27. `does he have COPD or asthma`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `COPD` | synonym | CONDITION | `185086009`, `87433001` | copd |  |
| `asthma` | exact | CONDITION | `195967001`, `233678006` | asthma |  |

### 28. `SOB on exertion and wheezing`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `SOB` | synonym | CONDITION | `267036007` | Dyspnea (finding) |  |
| `exertion` | unresolved | — | — | — |  |
| `wheezing` | exact | CONDITION | `56018004` | Wheezing (finding) |  |

### 29. `give me a full pre-op brief for this patient`

*date window:* … → 2026-08-19

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `full` | unresolved | — | — | — |  |

### 30. `does the patient have a PICC line and when was the last dressing change`

*date window:* none

| span | quality | category | codes | display | neg |
| --- | --- | --- | --- | --- | --- |
| `PICC line` | unresolved | — | — | — |  |
| `dressing change` | unresolved | — | — | — |  |

---

## What resolved

| quality | entities |
| --- | --- |
| exact | 13 |
| synonym | 37 |
| fuzzy | 1 |
| unresolved | 11 |

## What did not resolve

- `meds` — in *what meds is she on right now*
- `meds` — in *HTN and T2DM meds?*
- `dose` — in *does she take metformin and what dose*
- `beta blockers` — in *any beta blockers or ACE inhibitors on the list*
- `ACE inhibitors` — in *any beta blockers or ACE inhibitors on the list*
- `reaction` — in *sulfa allergy or reaction to cotrimoxazole*
- `stage` — in *no history of MI but has CKD stage 3*
- `exertion` — in *SOB on exertion and wheezing*
- `full` — in *give me a full pre-op brief for this patient*
- `PICC line` — in *does the patient have a PICC line and when was the last dressing change*
- `dressing change` — in *does the patient have a PICC line and when was the last dressing change*
