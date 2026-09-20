# Kravos Setu — Medical Document OCR & Intelligence Pipeline Architecture

## 1. Executive Summary & Flow

Kravos Setu provides a 5-model, 11-stage document intelligence pipeline designed for medical records (prescriptions, lab reports, discharge summaries, insurance forms, bills). It extracts both coded clinical entities (mapped to RxNorm, SNOMED CT, LOINC, ICD-10) and structured document fields (patient demographics, provider info, prescription line-items with schedules).

The system runs on local hardware (standard CPU laptop first) without external cloud OCR or LLM dependencies.

```text
+-----------------------------------------------------------------------------------------+
|                                    Input Document                                       |
|                    (PDF or Image: .png, .jpg, .jpeg, .tif, .tiff, .bmp, .webp)          |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 1. Acquisition: PyMuPDF rasterization (configurable DPI, e.g. 250) or PIL image load    |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 2. Quality Assessment: Laplacian blur, Hough skew, brightness, contrast, speckle density|
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 3. Enhancement: Conditional Deskew + Restormer (or classical OpenCV restoration)        |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 4. Region Detection: RT-DETR (or morphological layout heuristic segmentation)           |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 5. Layout Understanding: LayoutLMv3 (or topological 2D spatial sort + hierarchy linking)|
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 6. Region-Adaptive Extraction:                                                          |
|    - Printed Paragraphs/Headers -> PP-OCR (RapidOCR ONNX)                               |
|    - Handwritten Text/ROI -> TrOCR (HuggingFace transformer or RapidOCR fallback)       |
|    - Tables -> TableExtractor (OpenCV morphological grid segmentation -> Cell OCR)     |
|    - Ambiguous Typography -> Fusion (Confidence + Alphanumeric scoring)                 |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 7. Document Classification: Heuristic keyword signals -> prescription/lab/discharge/etc.|
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 8. Structured Extraction: MedicalOCRCorrector -> Fields (Patient, Meds, Labs, Schedule) |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 9. Clinical Normalization: Lexicon mapping -> RxNorm, SNOMED, LOINC, ICD-10             |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 10. Structuring: Min-Across-Stages Confidence Propagation & Provenance Assembly         |
+-----------------------------------------------------------------------------------------+
                                             |
                                             v
+-----------------------------------------------------------------------------------------+
| 11. Validation: Policy rules (confidence floor, empty scan check, mandatory sections)   |
+-----------------------------------------------------------------------------------------+
```

---

## 2. The 5 Core Models & Adapters

All models are managed by `models.manager.ModelManager` with LRU eviction and automatic CUDA-to-CPU fallback:

1. **Restormer (`RestormerAdapter`)**: Neural photographic restoration. Falls back to OpenCV bilateral filter + PIL contrast/sharpness when neural weights are unprovided.
2. **RT-DETR (`RTDetrAdapter`)**: Document layout object detection. Segments headers, tables, paragraphs, signatures, ROIs. Falls back to morphological contour segmentation.
3. **LayoutLMv3 (`LayoutLMv3Adapter`)**: Reading order sequencing and heading-to-child hierarchy linking. Falls back to 2D topological spatial sorting.
4. **PP-OCR (`PPOCRAdapter`)**: High-accuracy printed text OCR using lightweight RapidOCR ONNX models (DBNet + SVTR/CRNN).
5. **TrOCR (`TrOCRAdapter`)**: Transformer handwriting recognition. Loads `microsoft/trocr-small-handwritten` from local cache, or gracefully uses RapidOCR fallback with confidence penalties.

---

## 3. Data Contracts & Canonical Schemas

- **`BoundingBox`**: Coordinates `(x0, y0, x1, y1)` in original image pixel space with IoU and normalized coordinate support.
- **`DocumentRegion`**: Segments with `region_id`, `class_name`, `confidence`, `reading_order`, `parent_id`.
- **`OCRResult`**: Text line with `confidence`, `bbox`, `model`, `is_handwritten`, `is_reliable` (confidence >= 0.50).
- **`ClinicalEntity`**: Standardized medical fact with `fact_id`, `category`, `code`, `display`, `value`, `unit`, `provenance`.
- **`StructuredDocument`**: Structured extraction payload holding typed `ExtractedField` items, `MedicationEntry` (with morning/noon/night schedule), and `LabTestEntry`.
- **`PipelineResult`**: Complete output encapsulating quality reports, regions, lines, facts, structured data, stage timings, and metadata.

---

## 4. API Endpoints (`backend/ocr_routes.py`)

- `POST /api/ocr/extract`: Extracts OCR lines and proposes lexicon-coded facts (dry run).
- `POST /api/ocr/commit`: Re-reads and writes approved `factIds` to Postgres `facts` table.
- `POST /api/ocr/analyze`: Full structured JSON extraction (patient, doctor, medications with schedules, lab tests, quality).
- `POST /api/ocr/quality`: Fast pre-screening assessing image blur, skew, brightness, and contrast without running OCR.
- `GET /api/ocr/models`: Status, execution device, precision, and version of all model adapters.
- `GET /api/ocr/health`: Health status and model readiness.

---

## 5. Diagnostic & Benchmarking Tools

- **Diagnostic CLI**:
  ```bash
  py -3 -m scripts.test_ocr_pipeline <path_to_image_or_pdf> [--verbose] [--json] [--save]
  ```
- **Benchmark Evaluator**: `tests/ocr_benchmark/evaluator.py` provides CER, WER, and field accuracy metrics.
- **Unit & Regression Suite**:
  ```bash
  py -3 -m pytest tests/test_pipeline_models.py tests/test_medical_corrector.py tests/test_table_extractor.py tests/test_ocr_benchmark.py -v
  ```
