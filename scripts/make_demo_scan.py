"""Render a discharge summary and degrade it into something scan-like.

There are no scanned records in this repo and there should not be: a real
one is a real person's health record. So the demo input is synthesised, and
synthesising it honestly matters — an image of crisp anti-aliased text is not
a scan, and OCR against it proves nothing except that the renderer works.

`--degrade` therefore applies what a desk scanner actually does: a fractional
rotation from paper never sitting square, a slight blur from the optics,
sensor noise, and JPEG-style contrast loss. The result is a page RapidOCR has
to genuinely read, and it does not read it perfectly — which is the point,
because the pipeline downstream has to cope with imperfect input and the demo
should show that rather than hide it.

    pwsh> python scripts/make_demo_scan.py
    pwsh> python scripts/make_demo_scan.py --clean --out demo/scans/clean.png

The content is fictional. The drug and allergy names are real concepts drawn
from `lexicon.csv` so the extraction has something to resolve; the patient is
not.
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

DEFAULT_OUT = REPO_ROOT / "demo" / "scans" / "discharge_summary.png"

# Deliberately shaped like an Indian discharge summary: headings in caps with
# a colon, one item per line, doses in the medication block.
DOCUMENT = """\
SUNRISE MULTISPECIALITY HOSPITAL
Bengaluru, Karnataka

DISCHARGE SUMMARY

Patient Name : Ramesh Kumar
Age / Sex    : 58 / M
IP Number    : IP-2026-04417
Admitted     : 02-03-2026
Discharged   : 09-03-2026

DIAGNOSIS:
Type 2 diabetes mellitus
Essential hypertension
Chronic kidney disease

ALLERGIES:
Penicillin
Aspirin

DISCHARGE MEDICATIONS:
Metformin 500 mg
Amlodipine 5 mg
Atorvastatin 20 mg
Furosemide 40 mg

INVESTIGATIONS:
Hemoglobin
Creatinine
Potassium

PROCEDURES:
Renal dialysis

Advised review after two weeks.
Dr. S. Iyer, MD
"""


def render(text: str, width: int = 1240, height: int = 1754):
    """A4 at ~150 DPI, black on white, in a monospace face."""
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    font = None
    for candidate in ("consola.ttf", "cour.ttf", "DejaVuSansMono.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, 26)
            break
        except OSError:
            continue
    if font is None:  # pragma: no cover - depends on installed fonts
        font = ImageFont.load_default()

    y = 90
    for line in text.splitlines():
        draw.text((90, y), line, fill=(15, 15, 15), font=font)
        y += 38
    return image


def degrade(image, seed: int = 7):
    """Make it look like it came off a scanner rather than a renderer."""
    from PIL import Image, ImageEnhance, ImageFilter

    rng = random.Random(seed)

    # Paper is never square on the glass.
    image = image.rotate(
        rng.uniform(-0.7, 0.7), resample=Image.BICUBIC, fillcolor="white"
    )
    # Scanner optics are not perfectly sharp.
    image = image.filter(ImageFilter.GaussianBlur(radius=0.6))
    # Toner and sensor noise.
    pixels = image.load()
    for _ in range(int(image.width * image.height * 0.012)):
        x = rng.randrange(image.width)
        y = rng.randrange(image.height)
        shade = rng.randint(90, 190)
        pixels[x, y] = (shade, shade, shade)
    # Scanned pages lose contrast and pick up a grey cast.
    image = ImageEnhance.Contrast(image).enhance(0.82)
    image = ImageEnhance.Brightness(image).enhance(0.97)
    return image


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="skip the degradation -- renders crisp text, which is not a scan "
        "and will flatter the OCR",
    )
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    image = render(DOCUMENT)
    if not args.clean:
        image = degrade(image, seed=args.seed)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.out, quality=72)
    size_kb = args.out.stat().st_size / 1024
    print(f"wrote {args.out}  ({image.width}x{image.height}, {size_kb:.0f} KB)")
    print("clean render" if args.clean else "degraded: rotation, blur, noise, contrast loss")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
