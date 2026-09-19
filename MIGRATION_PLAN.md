# Kravos Setu — OCR Pipeline Migration Plan

## 1. Component Migration Boundary Matrix

| Existing Component | Current Responsibility | Action | Replacement | Reason |
| :--- | :--- | :--- | :--- | :--- |
| `ocr/engine.py: _reader()` | Loads singleton `RapidOCR` instance with no memory management | **Replace** | `models/manager.py` (`ModelManager`) + `models/ppocr_adapter.py` | Need unified model lifecycle management, lazy loading, memory-aware execution and unloading for Jetson Orin Nano (8GB unified memory). |
| `ocr/engine.py: read_document()` | Monolithic page-by-page OCR returning `OcrDocument` | **Modify** (Keep for backwards compatibility, wrap orchestrator) | `pipeline/orchestrator.py` (`PipelineOrchestrator.process_document()`) | Decouple pixel reading from full multi-model stages (Quality -> Restormer -> RT-DETR -> LayoutLMv3 -> PP-OCR/TrOCR). |
| `ocr/engine.py: read_pdf_text_layer()` | Fast path for born-digital PDFs | **Keep / Integrate** | `pipeline/acquisition.py` / orchestrator fast-path | Essential utility for born-digital PDFs; preserved to avoid unnecessary rasterization when clean text layers exist. |
| `ocr/engine.py: OcrLine, OcrDocument` | Data contracts for OCR lines and documents | **Modify** (Keep compatibility aliases) | `pipeline/schemas/canonical.py` (`OCRResult`, `DocumentRegion`, `PipelineResult`) | Canonical schemas need normalized bboxes, region associations, model versions, and multi-model provenance. |
| `ocr/extract.py: clean_ocr_text()` | Punctuation / whitespace cleanup of OCR text | **Keep & Reuse** | `pipeline/normalization.py` | Proven utility that prevents OCR noise from breaking clinical lexicon lookups. |
| `ocr/extract.py: extract_facts()` | Section-based heuristic extraction with Lexicon | **Modify / Enhance** | `pipeline/extraction.py` + `pipeline/structuring.py` | Enhance to consume canonical document regions, reading order, and multi-model OCR text with confidence propagation. |
| `backend/ocr_routes.py: api_extract()` | Handles `POST /api/ocr/extract` | **Modify** | Wraps `PipelineOrchestrator` | Preserves exact HTTP contract for the Next.js frontend while driving the modular 5-model pipeline underneath. |
| `backend/ocr_routes.py: api_commit()` | Handles `POST /api/ocr/commit` | **Modify** | Wraps `PipelineOrchestrator` re-read / commit | Retains deterministic fact_id generation and idempotent Postgres insertion. |
| `scripts/ocr_ingest.py` | CLI tool for scanning and committing | **Modify** | Uses `PipelineOrchestrator` | Preserves CLI interface and reviewable `.facts.json` artifact generation with new metrics and stage versions. |
| `demo/scans/make_demo_scan.py` | Synthetic scan generator with noise/blur | **Keep / Extend** | Extended with multi-document-type generation | Key development and testing utility for degraded document evaluation. |

---

## 2. New Architectural Components

1. **`pipeline/schemas/canonical.py`**:
   Canonical schemas for `BoundingBox`, `DocumentRegion`, `QualityReport`, `OCRResult`, `ClinicalEntity`, `Provenance`, and `PipelineResult`.
2. **`config/pipeline.json` & `config/policy.json`**:
   Config-driven stage ordering, quality thresholds, routing thresholds, and memory limits.
3. **`models/base.py`, `models/registry.py`, `models/manager.py`**:
   Uniform model abstraction, registry, and memory-managed lifecycle handling (Jetson 8GB budget, idle unloader).
4. **Model Adapters (`models/*_adapter.py`)**:
   - `restormer_adapter.py` (conditional image restoration)
   - `rtdetr_adapter.py` (region & object detection)
   - `layoutlmv3_adapter.py` (reading order & structural understanding)
   - `ppocr_adapter.py` (printed text OCR via RapidOCR / PaddleOCR ONNX)
   - `trocr_adapter.py` (handwritten text OCR via HuggingFace TrOCR)
5. **Pipeline Stages (`pipeline/*.py`)**:
   - `quality.py` (fast classical CV quality assessment)
   - `enhancement.py` (conditional neural enhancement)
   - `detection.py` (region detection & normalization)
   - `understanding.py` (structural reading-order analysis)
   - `router.py` (printed vs. handwritten ROI routing & low-confidence fusion)
   - `extraction.py` (routed OCR text extraction)
   - `normalization.py` (clinical vocabulary mapping and confidence weighting)
   - `structuring.py` (minimum-across-stages confidence propagation & provenance assembly)
   - `validation.py` (clinical consistency & mandatory field rules)
   - `orchestrator.py` (pipeline runner, structured JSON-line logging, error handling)
   - `offline.py` (edge queue & cloud sync logic)
6. **Evaluation & Verification (`eval/ocr_eval.py`)**:
   - CER/WER calculation on clean printed, degraded printed, and handwritten subsets.
   - Region detection IoU/mAP evaluation and reading order accuracy metrics.
