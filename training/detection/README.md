# RT-DETR Medical Document Region Detection Dataset Specification

## Objective
Train Real-Time DEtection TRansformer (RT-DETR) to localize clinical layout regions (headers, prescription tables, patient metadata blocks, doctor stamps, and signatures).

## Dataset Mapping
`document_image -> region bounding boxes + class`

## Format
Standard COCO format `annotations.json`:

```json
{
  "images": [{"id": 1, "file_name": "presc_001.png", "width": 1200, "height": 1600}],
  "categories": [
    {"id": 1, "name": "header"},
    {"id": 2, "name": "footer"},
    {"id": 3, "name": "paragraph"},
    {"id": 4, "name": "table"},
    {"id": 5, "name": "signature"},
    {"id": 6, "name": "stamp"},
    {"id": 7, "name": "barcode"},
    {"id": 8, "name": "roi"}
  ],
  "annotations": [
    {"id": 1, "image_id": 1, "category_id": 1, "bbox": [50, 40, 1100, 150], "area": 165000, "iscrowd": 0},
    {"id": 2, "image_id": 1, "category_id": 4, "bbox": [50, 250, 1100, 600], "area": 660000, "iscrowd": 0}
  ]
}
```
