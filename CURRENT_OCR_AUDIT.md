# Kravos Setu — Current OCR & Document Intelligence Audit

**Date**: September 2026  
**Auditor**: Antigravity AI Engine / Pair Programmer  
**Repository**: `https://github.com/iamsan06/kravos-setu`  
**Evaluation Target**: Medical Document & Prescription Intelligence Pipeline  

---

## 1. Current Architecture Overview

The Kravos Setu repository currently features an end-to-end 11-stage document processing architecture designed to extract structured clinical facts from medical scans:

```text
Incoming Document (PDF / Image)
      ↓
[1. Acquisition]           (PyMuPDF / PIL Image rasterization to RGB arrays)
      ↓
[2. Quality Assessment]    (Classical CV: Laplacian blur, Hough skew, gray-world color cast, speckle noise)
      ↓
[3. Enhancement]           (Conditional deskew + Restormer/photographic filter)
      ↓
[4. Detection]             (RT-DETR layout bounding boxes / morphological band heuristic)
      ↓
[5. Understanding]         (LayoutLMv3 spatial-topological reading order & section parent linking)
      ↓
[6. Extraction]            (ROIRouter -> PP-OCR / TrOCR / TableExtractor / Fusion)
      ↓
[7. Classification]        (Keyword-based document classifier: prescription, lab_report, discharge, etc.)
      ↓
[8. Structured Extraction] (Typed fields: patient, doctor, meds with schedule, lab tests table rows)
      ↓
[9. Normalization]         (Lexicon CSV matcher for SNOMED/RxNorm clinical entities)
      ↓
[10. Structuring]          (Provenance linking, confidence calibration, JSON assembly)
      ↓
[11. Validation]           (Schema integrity, mandatory section checks, confidence floor)
```

---

## 2. Actual Runtime Path

When a request arrives via `POST /api/ocr/extract` or `POST /api/ocr/analyze`:
1. `backend/ocr_routes.py` receives the multipart upload and forwards it to `PipelineOrchestrator.process_document()`.
2. `PipelineOrchestrator` runs stages 1 through 11 synchronously in memory.
3. In stage 6 (`ExtractionStage`), crops are sent to `ROIRouter.route()`, which uses Sobel gradient orientation entropy to decide whether a crop is printed (routed to `PPOCRAdapter`) or handwritten (routed to `TrOCRAdapter`).
4. In stage 8 (`StructuredExtractor`), the OCR results are normalized via deterministic rules in `ocr/medical_corrector.py`, grouped into table rows or medication schedules, and serialized to JSON.
5. In stage 9 (`NormalizationStage`), clinical entity matches are mapped against `lexicon.csv` and packaged as proposed facts.

---

## 3. Which Models Are Genuinely Active vs. Fallbacks

| Model Name | Intended Architecture | Actual Active Execution | Checkpoint State | Truthful Status |
| :--- | :--- | :--- | :--- | :--- |
| **PP-OCR** | PaddleOCR v4 DBNet + SVTR | **Active Neural Model** (RapidOCR ONNX Runtime on CPU/CUDA) | Bundled in `rapidocr-onnxruntime` package | `ACTIVE_NEURAL_MODEL` |
| **TrOCR** | Transformer OCR (`microsoft/trocr-small-handwritten`) | **Fallback Heuristic** unless HuggingFace cache contains weights; falls back to RapidOCR with reduced confidence | Offline by default unless downloaded | `FALLBACK` (when HF cache empty) / `ACTIVE_NEURAL_MODEL` (when cached) |
| **RT-DETR** | Real-Time Detection Transformer | **Fallback Heuristic** (OpenCV morphological wide horizontal kernel dilation + vertical line subtraction) | No medical layout weights bundled | `FALLBACK` (`morphological_layout_segmentation`) |
| **Restormer** | Transformer for Image Restoration | **Fallback Heuristic** (PIL UnsharpMask + Contrast enhancement + OpenCV bilateral filter) | No PyTorch `.pth` checkpoint bundled | `FALLBACK` (`classical_cv_photographic`) |
| **LayoutLMv3** | Multimodal Document Foundation Model | **Spatial Heuristic** (2-column aware topological sorting and Y-band grouping) | No HuggingFace weights loaded; pure spatial code | `FALLBACK` (`spatial_topological_heuristic`) |

---

## 4. Model Integrity Flaws Identified

1. **Inflated Confidence on Heuristics**:
   - In `models/rtdetr_adapter.py`, heuristic bounding boxes generated from morphological dilation were assigned a static confidence of `0.92`, masquerading as neural object detection confidence.
2. **Ambiguous Status Metadata**:
   - In `models/trocr_adapter.py`, `get_metadata()` returned `"is_neural": True` and `"backend": "transformers-trocr"` even when using the RapidOCR offline fallback.
   - In `models/restormer_adapter.py`, classical unsharp masking was called via the adapter, giving external observers the impression that neural Restormer was running.
3. **No Strict Mode Guard**:
   - If a caller requires real neural inference (e.g. for high-compliance auditing), the system silently fell back to heuristics with no option to enforce neural checkpoint presence.

---

## 5. Current OCR Bottlenecks

1. **CPU Latency on Large Crops**:
   - When large page areas or full pages are routed through TrOCR or un-batched ONNX runs, latency ranges from 12 to 28 seconds on laptop CPUs.
2. **Doctor Cursive & Mixed Script**:
   - High recognition on printed headers and labels (>98%), but heavy doctor cursive handwriting (e.g. `C-MICRODOX 1-0-1 x 5days`) achieves 65–75% word accuracy without specific handwriting fine-tuning.
3. **Lack of Confidence-Driven Reprocessing (Second Pass)**:
   - When a word or bounding box yields low confidence (<0.70), the pipeline currently accepts it directly without trying alternative preprocessing (such as adaptive binarization, contrast stretching, or super-resolution).
4. **Absence of Systematic Multi-Model Fusion**:
   - `ROIRouter.fuse()` is currently a simple rule picking the single highest confidence string between PP-OCR and TrOCR, rather than character/token-level alignment with medical dictionary validation.

---

## 6. Missing Dependencies & Checkpoints

- **Dependencies**:
  - `torch`, `transformers`, `opencv-python`, `rapidocr-onnxruntime` are installed.
  - `ultralytics` (for RT-DETR PyTorch execution) is not installed in the default environment.
- **Checkpoints**:
  - `microsoft/trocr-small-handwritten`: Not pre-downloaded in local cache by default.
  - `microsoft/layoutlmv3-base`: Not pre-downloaded in local cache by default.
  - Document-specific `RT-DETR` medical layout weights (`.pt` / `.onnx`): Missing (needs custom trained weights for clinical layout).
  - Neural `Restormer` document deblur checkpoint (`.pth`): Missing (needs pre-trained weights).

---

## 7. Accuracy-Related Weaknesses

1. **Single-Pass Vulnerability**: If initial contrast or blur causes OCR to drop characters (e.g., `55917` becoming `5S9/7.`), no retry mechanism is invoked.
2. **Numeric Transposition Risks**: Without explicit numeric token boundary enforcement during OCR recognition, decimals can be lost (`13.2` read as `132`).
3. **Layout Grouping Fragility**: Pure horizontal dilation can bridge distant columns on multi-column documents without semantic guidance from LayoutLMv3 or RT-DETR.

---

## 8. Recommended Implementation Changes

1. **Model Truthfulness & Explicit Runtime States**:
   - Add explicit enum states: `ACTIVE_NEURAL_MODEL`, `FALLBACK`, `UNAVAILABLE`, `FAILED`.
   - Never report synthetic confidence as neural confidence.
2. **Strict Mode Support**:
   - Introduce `model_execution.strict: true/false`. Fail explicitly with informative errors when strict mode is active and neural weights are missing.
3. **Adaptive Preprocessing with Profiles**:
   - Implement real quality metric extraction (entropy, shadow score, perspective score, estimated DPI).
   - Dynamically select profiles (`LOW_QUALITY`, `BLURRY`, `SHADOWED`, `HANDWRITING`, `TABLE`, etc.).
4. **Confidence-Driven Second Pass**:
   - For any crop with confidence `< 0.70`, trigger an alternative enhancement pass and re-run OCR, saving candidates.
5. **Dedicated OCR Fusion**:
   - Character/token alignment, medical lexicon weighting, and numeric consistency scoring.
6. **Diagnostic & Availability CLI**:
   - Implement `python -m scripts.check_ocr_models` and update `python -m scripts.test_ocr_pipeline`.
7. **Future Fine-Tuning Infrastructure**:
   - Establish `training/ppocr/`, `training/trocr/`, `training/detection/`, and `training/layout/` dataset specs and error taxonomy.
