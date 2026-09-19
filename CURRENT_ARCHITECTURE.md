# Kravos Setu — Current Document & OCR Architecture

## 1. Overview & Current Document Processing Flow

The repository currently provides an OCR-based scanned document ingestion flow designed for clinical documents (such as discharge summaries and OPD slips) to extract coded clinical facts.

```
+-----------------------------------------------------------------------------+
|                               Input Document                                |
|           (PDF or Image: .png, .jpg, .jpeg, .tif, .tiff, .bmp, .webp)        |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                                Preprocessing                                |
|  - PDF: Rasterized via PyMuPDF (fitz) at 200 DPI into 3-channel RGB numpy   |
|  - Image: Loaded via PIL, converted to RGB numpy array                      |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                         OCR Engine (RapidOCR ONNX)                          |
|  - Single singleton instance loaded via @lru_cache(maxsize=1) _reader()     |
|  - 3 ONNX models: DBNet (detection), angle classifier, SVTR/CRNN (recog)   |
|  - Yields: raw polygons [[x,y],...], text strings, and confidence scores    |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                          OcrDocument & OcrLine                              |
|  - Boxes flattened to (x0, y0, x1, y1) in original page pixel coordinates   |
|  - Lines tagged with page index (1-indexed), line_no, and confidence float  |
|  - is_reliable flag: confidence >= MIN_LINE_CONFIDENCE (0.50)               |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                        Clinical Fact Extraction                             |
|  - Regex cleans OCR punctuation artifacts (clean_ocr_text)                  |
|  - Matches Section Headings: ALLERGY, MEDICATION, CONDITION, LAB, etc.      |
|  - Extracts dosages via regex: (\d+(\.\d+)?)\s*(mg|mcg|g|ml|units?|iu)      |
|  - Resolves phrases against retrieval.lexicon.Lexicon (synonyms/RxNorm/etc) |
|  - Generates deterministic fact_id: sha1(patient_id|source|page|line|code)  |
|  - Collects unmatched, ambiguous (outside known section), low_confidence    |
+-----------------------------------------------------------------------------+
                                       |
                                       v
+-----------------------------------------------------------------------------+
|                    FastAPI Endpoints (Two-Stage Review)                     |
|  - POST /api/ocr/extract: Upload -> temp dir -> OCR -> facts (writes 0)     |
|  - POST /api/ocr/commit: Re-reads upload -> inserts approved fact IDs into  |
|    Postgres 'facts' table (ON CONFLICT DO NOTHING)                          |
|  - Next.js UI interacts via frontend/src/lib/ocr.ts                         |
+-----------------------------------------------------------------------------+
```

---

## 2. The Current OCR Contract

### 2.1 Inputs
- **File formats**: PDF (`.pdf`) or images (`.png`, `.jpg`, `.jpeg`, `.tif`, `.tiff`, `.bmp`, `.webp`).
- **File constraints**: Maximum upload size `25 MB` (`MAX_UPLOAD_BYTES`), non-empty payloads.
- **Entry points**:
  - `ocr.engine.read_document(path: str | Path) -> OcrDocument`
  - `ocr.engine.read_pdf_text_layer(path: str | Path) -> OcrDocument` (for born-digital PDFs)

### 2.2 Outputs & Data Representations
- **`OcrLine`** (`ocr/engine.py`):
  ```python
  @dataclass(frozen=True)
  class OcrLine:
      text: str
      confidence: float                       # Scalar 0.0 - 1.0 from RapidOCR
      page: int                               # 1-indexed page number
      line_no: int                            # 1-indexed line number on the page
      box: tuple[float, float, float, float]  # (x0, y0, x1, y1) in original page pixel coordinates
  ```
  - Reliability property: `is_reliable = confidence >= 0.50`.
- **`OcrDocument`** (`ocr/engine.py`):
  ```python
  @dataclass(frozen=True)
  class OcrDocument:
      source: Path
      lines: list[OcrLine]
      engine: str                             # "rapidocr-onnxruntime" or "pdf-text-layer"
  ```
- **`OcrFact`** (`ocr/extract.py`):
  ```python
  @dataclass(frozen=True)
  class OcrFact:
      fact_id: str                            # 16-char sha1 hash
      patient_id: str
      category: str                           # "ALLERGY", "MEDICATION", "CONDITION", etc.
      safety_class: str                       # "SAFETY" for ALLERGY, "CLINICAL" otherwise
      text: str
      code_system: str | None
      code: str | None
      display: str | None
      value: str | None
      unit: str | None
      date: datetime | None
      status: str                             # "active"
      source_resource_id: str                 # "{file}#p{page}L{line_no}"
      confidence: float                       # Carried from OcrLine.confidence
      page: int
      line_no: int
      line_text: str
      also_in: list[str]                      # Alternate code mappings
  ```

### 2.3 Error Behavior
- `OcrUnavailable`: Raised if `rapidocr_onnxruntime` is missing. Surfaced as HTTP 503 at the API.
- `ValueError`: Raised on unsupported file extensions. Surfaced as HTTP 415.
- `FileNotFoundError`: Raised if the target path does not exist on disk.
- Empty images / pages with no text: RapidOCR returns `None` or `[]`; `OcrDocument` produces `lines = []` and `mean_confidence = 0.0`.

---

## 3. Downstream Consumers

1. **Frontend Review Client** (`frontend/src/lib/ocr.ts`):
   - Calls `POST /api/ocr/extract` to get lines, proposed facts, and unextracted lines.
   - Shows reviewed checkboxes for clinicians to approve facts.
   - Calls `POST /api/ocr/commit` with approved `factIds`.
2. **Postgres Storage & Retrieval** (`retrieval/db.py`, `backend/data_layer.py`):
   - Approved facts are committed to the `facts` table with `source_resource_type = "ScannedDocument"`.
   - Coded queries via `/api/query` and conversational RAG via `/api/chat` retrieve these facts identically to FHIR-derived facts.
3. **CLI Ingestion Script** (`scripts/ocr_ingest.py`):
   - Executes `read_document` and `extract_facts`, producing human-reviewable `.facts.json` artifacts in `demo/scans/`.
