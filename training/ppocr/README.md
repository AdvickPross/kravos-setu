# PP-OCR Fine-Tuning Dataset Specification

## Objective
Fine-tune PaddleOCR / RapidOCR recognition head on printed clinical fonts, hospital header formats, and specialized medical abbreviations.

## Dataset Mapping
`image_crop -> ground_truth_text`

## Format
Text file `train_list.txt` formatted with tab-separated image path and transcription:

```text
train/crop_0001.png	Tab. Amoxicillin 500mg
train/crop_0002.png	Dr. N. Sanjira, MDS (OMFS)
train/crop_0003.png	Ref By Doctor: Dr. Rajiv Mehta, MD
train/crop_0004.png	Fast Blood Glucose: 112 mg/dL
```

## Input Image Requirements
- Height: Normalized to 48 pixels (variable width, maintaining aspect ratio).
- Color: RGB or Grayscale.
- Padding: 2-pixel margin around text.
