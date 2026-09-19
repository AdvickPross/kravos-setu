"""Clinical vocabulary normalization stage mapping OCR text to RxNorm, SNOMED CT, LOINC, and ICD-10."""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ocr.extract import SECTION_HEADINGS, _DOSE, _HEADING, _NOISE, clean_ocr_text, _pick_code
from pipeline.schemas.canonical import BoundingBox, ClinicalEntity, OCRResult, Provenance
from retrieval.lexicon import Lexicon
from retrieval.normalize import normalize_query_text

log = logging.getLogger(__name__)


@dataclass
class NormalizationResult:
    entities: list[ClinicalEntity]
    sections_seen: list[str]
    unmatched_lines: list[str]
    ambiguous_lines: list[str]
    low_confidence_lines: list[str]


class NormalizationStage:
    """Normalizes extracted text phrases against the curated clinical lexicon."""

    def __init__(self, lexicon: Lexicon | None = None) -> None:
        self.lexicon = lexicon or Lexicon()

    def process(
        self,
        ocr_results: list[OCRResult],
        document_id: str,
        patient_id: str,
        pipeline_version: str = "2.0.0",
        document_date: datetime | None = None,
    ) -> NormalizationResult:
        """Map OCR lines into coded clinical entities."""
        date = document_date or datetime.now(timezone.utc)
        entities: list[ClinicalEntity] = []
        sections_seen: list[str] = []
        unmatched_lines: list[str] = []
        ambiguous_lines: list[str] = []
        low_confidence_lines: list[str] = []

        current_category: str | None = None

        for ocr_res in ocr_results:
            raw_text = ocr_res.text.strip()
            if not raw_text or _NOISE.match(raw_text):
                continue

            if not ocr_res.is_reliable:
                low_confidence_lines.append(raw_text)
                continue

            # Heading detection
            heading_match = _HEADING.match(raw_text)
            if heading_match:
                label = normalize_query_text(clean_ocr_text(heading_match.group(1)))
                category_hit = None
                for cat, aliases in SECTION_HEADINGS.items():
                    if label in aliases:
                        category_hit = cat
                        break
                if category_hit:
                    current_category = category_hit
                    if category_hit not in sections_seen:
                        sections_seen.append(category_hit)
                    remainder = heading_match.group(2).strip()
                    if not remainder:
                        continue
                    body = remainder
                else:
                    body = raw_text
            else:
                body = raw_text

            # Split multiple items on one line
            items = [p.strip(" .-•") for p in re.split(r"[,;]|\s{3,}|•", body) if p.strip(" .-•")]
            matched_any = False

            for item in items:
                cleaned = clean_ocr_text(item)
                dose_match = _DOSE.search(cleaned)
                phrase = _DOSE.sub("", cleaned).strip(" .-")

                if len(phrase) < 3:
                    continue

                norm_phrase = normalize_query_text(phrase)
                hits = self.lexicon.lookup(norm_phrase)
                if not hits:
                    continue

                if current_category is None:
                    ambiguous_lines.append(raw_text)
                    matched_any = True
                    continue

                in_category = [h for h in hits if h.entry.category == current_category]
                if not in_category:
                    ambiguous_lines.append(raw_text)
                    matched_any = True
                    continue

                entry, alternates = _pick_code(in_category)

                # Deterministic fact ID: sha1(patient_id|doc_id|page|line_no|code)
                fact_id = hashlib.sha1(
                    f"{patient_id}|{document_id}|{ocr_res.page}|{ocr_res.line_no}|{entry.code}".encode()
                ).hexdigest()[:16]

                provenance = Provenance(
                    document_id=document_id,
                    page=ocr_res.page,
                    region_id=ocr_res.region_id,
                    bbox=ocr_res.bbox.to_tuple(),
                    source_model=ocr_res.model,
                    model_version=ocr_res.model_version,
                    pipeline_version=pipeline_version,
                )

                # Minimum-across-stages confidence rule applied in structuring stage,
                # here we record the OCR line confidence as baseline.
                entity = ClinicalEntity(
                    fact_id=fact_id,
                    patient_id=patient_id,
                    category=current_category,
                    safety_class="SAFETY" if current_category == "ALLERGY" else "CLINICAL",
                    text=f"{current_category.title()}: {entry.display} (scanned, OCR {ocr_res.confidence:.0%})",
                    display=entry.display,
                    code=entry.code,
                    code_system=entry.code_system,
                    value=dose_match.group(1) if dose_match else None,
                    unit=dose_match.group(2).lower() if dose_match else None,
                    date=date,
                    confidence=ocr_res.confidence,
                    provenance=provenance,
                    line_no=ocr_res.line_no,
                    line_text=ocr_res.text,
                    status="active",
                    also_in=alternates,
                )
                entities.append(entity)
                matched_any = True

            if not matched_any and len(body) > 2:
                unmatched_lines.append(body)

        return NormalizationResult(
            entities=entities,
            sections_seen=sections_seen,
            unmatched_lines=unmatched_lines,
            ambiguous_lines=ambiguous_lines,
            low_confidence_lines=low_confidence_lines,
        )
