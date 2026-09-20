# Kravos Setu — Model Fine-Tuning Infrastructure & Error Taxonomy

This directory contains specifications and dataset formatting templates for future offline fine-tuning of the 5 document intelligence models.

> [!IMPORTANT]
> **Rule of Medical OCR Engineering**: Never fine-tune models blindly before systematic error analysis. Use the benchmarking suite (`tests/ocr_benchmark/`) to classify and measure error distribution across documents before initiating training runs.

---

## 1. Error Taxonomy

Every OCR discrepancy in Kravos Setu is systematically categorized into one of eight distinct failure modes:

| Error Category | Manifestation | Responsible Subsystem | Corrective Action |
| :--- | :--- | :--- | :--- |
| **`DETECTION_ERROR`** | Missing lines, cut-off words, truncated bounding boxes | RT-DETR / Layout Segmenter | Fine-tune RT-DETR on clinical prescription layouts (`training/detection/`) |
| **`RECOGNITION_ERROR`** | Character confusion on printed machine typography | PP-OCR / RapidOCR | Fine-tune PP-OCR SVTR recognizer on Indian hospital font styles (`training/ppocr/`) |
| **`HANDWRITING_ERROR`** | Misread cursive doctor handwriting | TrOCR | Fine-tune TrOCR with LoRA on region-specific clinical prescriptions (`training/trocr/`) |
| **`PREPROCESSING_ERROR`**| Over-filtering, thresholding wash-out, distortion | Quality / Adaptive Preprocessing | Adjust quality thresholds and selective enhancement profiles |
| **`LAYOUT_ERROR`** | Multi-column words interleaved in wrong reading order | LayoutLMv3 / Spatial Router | Fine-tune LayoutLMv3 on multi-column lab reports & hospital bills (`training/layout/`) |
| **`TABLE_ERROR`** | Cell boundaries misaligned, row-spanning values merged | TableExtractor | Calibrate morphological kernel sizes and ruling line detection |
| **`NUMERIC_ERROR`** | Dropped decimal points (`13.2` -> `132`), misread dosage units | MedicalOCRCorrector & Fusion | Tighten numeric plausibility ranges and fusion penalty weights |
| **`NORMALIZATION_ERROR`**| Legitimate drug/test name rejected by lexicon lookup | Lexicon / Normalizer | Expand standard vocabulary (`lexicon.csv` & SNOMED/RxNorm maps) |

---

## 2. Directory Structure

- **`training/ppocr/`**: Dataset specification and format for printed text recognizer fine-tuning.
- **`training/trocr/`**: Dataset specification for doctor handwriting recognition fine-tuning.
- **`training/detection/`**: Dataset specification (COCO / YOLO format) for medical document region detection.
- **`training/layout/`**: Dataset specification for multimodal document understanding and token classification.
