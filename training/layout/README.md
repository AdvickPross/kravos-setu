# LayoutLMv3 Document Understanding Dataset Specification

## Objective
Train multimodal Transformer for document entity linking and reading order understanding on complex multi-column prescriptions and hospital reports.

## Dataset Mapping
`image + OCR tokens + bounding boxes + labels -> entity relationships`

## Format
HuggingFace / FUNSD format `layout_dataset.json`:

```json
{
  "id": "doc_001",
  "tokens": ["Tab.", "Amoxicillin", "500mg", "1-0-1", "x", "5", "days"],
  "bboxes": [
    [100, 200, 150, 230],
    [160, 200, 320, 230],
    [330, 200, 420, 230],
    [450, 200, 520, 230],
    [530, 200, 550, 230],
    [560, 200, 580, 230],
    [590, 200, 650, 230]
  ],
  "ner_tags": ["B-MED_FORM", "B-MED_NAME", "B-DOSAGE", "B-SCHEDULE", "O", "B-DURATION", "I-DURATION"]
}
```
