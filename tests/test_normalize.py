"""Strip-rule tests for retrieval/normalize.py.

Every display string used here is taken verbatim from lexicon.csv.
"""

from __future__ import annotations

import json

import pytest

from retrieval.normalize import (
    CVX,
    LOINC,
    RXNORM,
    SNOMED,
    NormalizationRules,
    normalize_cvx,
    normalize_display,
    normalize_loinc,
    normalize_query_text,
    normalize_rxnorm,
    normalize_snomed,
    load_rules,
)


@pytest.fixture(scope="module")
def rules() -> NormalizationRules:
    return load_rules()


# ---------------------------------------------------------------- LOINC ----


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        # the three cases named in the spec
        ("Creatinine [Mass/volume] in Serum or Plasma", "creatinine"),
        ("Cholesterol in HDL [Mass/volume] in Serum or Plasma", "cholesterol in hdl"),
        ("Body Height", "body height"),
        # bracket cut also removes the method clause behind it
        (
            "Cholesterol in LDL [Mass/volume] in Serum or Plasma by Direct assay",
            "cholesterol in ldl",
        ),
        # no bracket: trailing "in <specimen> by <method>" still goes
        ("Monocytes/Leukocytes in Blood by Automated count", "monocytes/leukocytes"),
        ("Bacteria identified in Urine by Culture", "bacteria identified"),
        ("Anion gap in Serum or Plasma by calculation", "anion gap"),
        ("Oxygen saturation in Arterial blood", "oxygen saturation"),
        ("Hemoglobin A1c/Hemoglobin.total in Blood", "hemoglobin a1c/hemoglobin.total"),
        # nested clauses: "by Probe" only becomes trailing after the
        # specimen clause is peeled, so the peel has to loop
        (
            "Gram positive blood culture panel by Probe in Positive blood culture",
            "gram positive blood culture panel",
        ),
        # "--" residue
        ("Weight difference [Mass difference] --pre dialysis - post dialysis", "weight difference"),
    ],
)
def test_loinc_strip(display: str, expected: str, rules: NormalizationRules) -> None:
    assert normalize_loinc(display, rules.for_system(LOINC)) == expected


@pytest.mark.parametrize(
    "display",
    [
        # questionnaire items contain " in " and " by " that are not
        # specimen/method clauses and must survive verbatim
        "Recurrent opioid use in situations in which it is physically hazardous",
        "Opioids are often taken in larger amounts or over a longer period than was intended",
        "Are you covered by health insurance or some other kind of health care plan",
    ],
)
def test_loinc_leaves_questionnaire_text_alone(
    display: str, rules: NormalizationRules
) -> None:
    assert normalize_loinc(display, rules.for_system(LOINC)) == display.lower()


def test_loinc_percentage_area_keeps_its_by_clause(rules: NormalizationRules) -> None:
    # "by eczema Trunk" is not a method, so it stays
    assert (
        normalize_loinc("Percentage area affected by eczema Trunk [PhenX]", rules.for_system(LOINC))
        == "percentage area affected by eczema trunk"
    )


# ------------------------------------------------------------- stoplist ----


def test_total_score_is_suppressed_but_full_display_survives(
    rules: NormalizationRules,
) -> None:
    """`total score` collapses 4 distinct codes, so it must not be a
    surface form -- but the row still has to be findable by its display."""
    for display in ("Total score [MMSE]", "Total score [AUDIT-C]", "Total score [HARK]"):
        result = normalize_display(LOINC, display, rules)
        assert result.primary is None
        assert "total score" in result.suppressed
        assert result.forms == (display.lower(),)


def test_two_char_residue_is_suppressed(rules: NormalizationRules) -> None:
    result = normalize_display(LOINC, "pH of Urine by Test strip", rules)
    assert "ph of urine" in result.forms  # the trailing method went, the name stayed
    result = normalize_display(LOINC, "pH of Arterial blood", rules)
    assert result.forms == ("ph of arterial blood",)


def test_three_char_analytes_are_kept(rules: NormalizationRules) -> None:
    """inr / mcv / mch are real analytes; the min-length guard must not eat them."""
    assert "inr" in normalize_display(
        LOINC, "INR in Platelet poor plasma by Coagulation assay", rules
    ).forms
    assert "mcv" in normalize_display(
        LOINC, "MCV [Entitic mean volume] in Red Blood Cells", rules
    ).forms
    assert "mch" in normalize_display(LOINC, "MCH [Entitic mass] by Automated count", rules).forms


# --------------------------------------------------------------- SNOMED ----


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("Pneumonia (disorder)", "pneumonia"),
        ("Medication review due (situation)", "medication review due"),
        ("Homeless (finding)", "homeless"),
        ("Plain X-ray of knee region (procedure)", "plain x-ray of knee region"),
        ("Physiotherapy care plan (regime/therapy)", "physiotherapy care plan"),
        ("Fracture subluxation of wrist (morphologic abnormality)", "fracture subluxation of wrist"),
        ("Left (qualifier value)", "left"),
        ("Latex (substance)", "latex"),
        ("House dust mite (organism)", "house dust mite"),
    ],
)
def test_snomed_strips_semantic_tag(
    display: str, expected: str, rules: NormalizationRules
) -> None:
    assert normalize_snomed(display, rules.for_system(SNOMED)) == expected


def test_snomed_keeps_meaningful_parenthetical(rules: NormalizationRules) -> None:
    # not a semantic tag -> untouched
    assert (
        normalize_snomed("Eggs (edible) (substance)", rules.for_system(SNOMED))
        == "eggs (edible)"
    )
    assert normalize_snomed("Aspirin", rules.for_system(SNOMED)) == "aspirin"


# --------------------------------------------------------------- RxNorm ----


def test_rxnorm_combination_product(rules: NormalizationRules) -> None:
    parts = normalize_rxnorm(
        "Acetaminophen 325 MG / Oxycodone Hydrochloride 5 MG Oral Tablet [Percocet]",
        rules.for_system(RXNORM),
    )
    assert parts.ingredients == ("acetaminophen", "oxycodone")
    assert parts.brand == "percocet"


def test_rxnorm_indexes_ingredients_and_brand_and_full_display(
    rules: NormalizationRules,
) -> None:
    display = "Acetaminophen 325 MG / Oxycodone Hydrochloride 5 MG Oral Tablet [Percocet]"
    forms = normalize_display(RXNORM, display, rules).forms
    assert set(forms) >= {display.lower(), "acetaminophen", "oxycodone", "percocet"}


@pytest.mark.parametrize(
    ("display", "expected"),
    [
        ("1 ML medroxyPROGESTERone acetate 150 MG/ML Injection", ("medroxyprogesterone",)),
        ("24 HR tacrolimus 5 MG Extended Release Oral Capsule [Astagraf]", ("tacrolimus",)),
        ("168 HR Ethinyl Estradiol 0.00146 MG/HR / norelgestromin 0.00625 MG/HR Transdermal System",
         ("ethinyl estradiol", "norelgestromin")),
        ("21 DAY ethinyl estradiol 0.000625 MG/HR / etonogestrel 0.005 MG/HR Vaginal System [NuvaRing]",
         ("ethinyl estradiol", "etonogestrel")),
        ("Abuse-Deterrent 12 HR Oxycodone Hydrochloride 15 MG Extended Release Oral Tablet",
         ("oxycodone",)),
        ("NDA020800 0.3 ML Epinephrine 1 MG/ML Auto-Injector", ("epinephrine",)),
        ("{28 (norethindrone 0.35 MG Oral Tablet) } Pack [Camila 28 Day]", ("norethindrone",)),
        ("heparin sodium, porcine 100 UNT/ML Injectable Solution", ("heparin",)),
        ("doxycycline hyclate 100 MG", ("doxycycline",)),
        ("Doxycycline Monohydrate 100 MG Oral Tablet", ("doxycycline",)),
        ("Mestranol / Norethynodrel", ("mestranol", "norethynodrel")),
        ("cycloSPORINE, modified 100 MG Oral Capsule", ("cyclosporine",)),
        ("sacubitril 97 MG / valsartan 103 MG Oral Tablet [Entresto]", ("sacubitril", "valsartan")),
    ],
)
def test_rxnorm_ingredients(
    display: str, expected: tuple[str, ...], rules: NormalizationRules
) -> None:
    assert normalize_rxnorm(display, rules.for_system(RXNORM)).ingredients == expected


def test_rxnorm_salt_is_not_stripped_into_nothing(rules: NormalizationRules) -> None:
    """"chloride" is not a salt suffix, so "Sodium Chloride" keeps both tokens."""
    parts = normalize_rxnorm("Sodium Chloride 9 MG/ML Injectable Solution", rules.for_system(RXNORM))
    assert parts.ingredients == ("sodium chloride",)


def test_rxnorm_keeps_salted_form_alongside_ingredient(rules: NormalizationRules) -> None:
    parts = normalize_rxnorm("ferrous sulfate 325 MG Oral Tablet", rules.for_system(RXNORM))
    assert parts.ingredients == ("ferrous",)
    assert parts.salt_forms == ("ferrous sulfate",)


def test_rxnorm_digit_inside_a_token_is_not_a_cut_point(rules: NormalizationRules) -> None:
    parts = normalize_rxnorm("Vitamin B12 5 MG/ML Injectable Solution", rules.for_system(RXNORM))
    assert parts.ingredients == ("vitamin b12",)


def test_rxnorm_brand_drops_pack_duration(rules: NormalizationRules) -> None:
    parts = normalize_rxnorm(
        "{28 (norethindrone 0.35 MG Oral Tablet) } Pack [Jolivette 28 Day]",
        rules.for_system(RXNORM),
    )
    assert parts.brand == "jolivette"


def test_rxnorm_casing_is_normalized(rules: NormalizationRules) -> None:
    """Synthea casing is inconsistent (medroxyPROGESTERone vs medroxyprogesterone)."""
    a = normalize_rxnorm("1 ML medroxyPROGESTERone acetate 150 MG/ML Injection", rules.for_system(RXNORM))
    b = normalize_rxnorm("1 ML medroxyprogesterone acetate 150 MG/ML Injection", rules.for_system(RXNORM))
    assert a == b


# ------------------------------------------------------------------ CVX ----


def test_cvx_lowercases_only(rules: NormalizationRules) -> None:
    assert normalize_cvx("Influenza, split virus, trivalent, PF") == (
        "influenza, split virus, trivalent, pf"
    )


def test_cvx_short_display_still_indexed(rules: NormalizationRules) -> None:
    """MMR / IPV are 3 chars; the full display is always indexed."""
    assert normalize_display(CVX, "MMR", rules).forms == ("mmr",)
    assert normalize_display(CVX, "IPV", rules).forms == ("ipv",)


# --------------------------------------------------------- query text ------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Any drug allergies before I prescribe amoxicillin?", "any drug allergies before i prescribe amoxicillin"),
        ("what's the K+ trend?", "whats the k+ trend"),
        ("FEV1/FVC ratio", "fev1/fvc ratio"),
        ("HbA1c,  glucose  ", "hba1c glucose"),
        ("bilirubin.total last year", "bilirubin.total last year"),
    ],
)
def test_normalize_query_text(raw: str, expected: str) -> None:
    assert normalize_query_text(raw) == expected


# ----------------------------------------------------------- rule file -----


def test_rules_path_is_overridable(tmp_path) -> None:
    """Nothing may hardcode the rule file: the corpus vocabulary changes."""
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps({"snomed": {"semantic_tags": ["widget"], "min_form_length": 4}}),
        encoding="utf-8",
    )
    custom = NormalizationRules.from_file(path)
    assert normalize_snomed("Sprocket (widget)", custom.for_system(SNOMED)) == "sprocket"
    # the default tag list is not in play for this file
    assert normalize_snomed("Pneumonia (disorder)", custom.for_system(SNOMED)) == "pneumonia (disorder)"
