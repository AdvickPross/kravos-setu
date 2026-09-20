# TrOCR Fine-Tuning Dataset Specification

## Objective
Adapt `microsoft/trocr-small-handwritten` or `microsoft/trocr-base-handwritten` to cursive doctor handwriting and prescription dosage notations (e.g. `1-0-1 x 5days`, `30mts before food`).

## Dataset Mapping
`handwriting_crop -> transcription`

## Format
JSON Lines file `metadata.jsonl`:

```json
{"file_name": "crops/hw_001.png", "text": "C-MICRODOX LBX 100mg 1-0-1 x 5days"}
{"file_name": "crops/hw_002.png", "text": "then 0-0-1 x 40days (After food)"}
{"file_name": "crops/hw_003.png", "text": "T. PANTOP 40mg 1-0-1 x 5days"}
{"file_name": "crops/hw_004.png", "text": "PEROBAR 2.5% soap for apply"}
```

## Recommended Training Recipe
- LoRA (Low-Rank Adaptation) on the VisionEncoderDecoder cross-attention layers.
- Rank: 16, Alpha: 32.
- Learning rate: 5e-5 with linear warmup.
