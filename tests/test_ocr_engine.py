"""The pixel layer.

Slow by nature -- RapidOCR loads three ONNX models and then actually reads an
image -- so this is deliberately thin. It asserts the contract every layer
above depends on, and one thing that cannot be checked any other way: that
the demo scan is hard enough to be worth running OCR against.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ocr.engine import MIN_LINE_CONFIDENCE, OcrLine, read_document

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_SCAN = REPO_ROOT / "demo" / "scans" / "discharge_summary.png"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def test_confidence_threshold_is_a_property_of_the_line():
    assert OcrLine("x", MIN_LINE_CONFIDENCE, 1, 1).is_reliable
    assert not OcrLine("x", MIN_LINE_CONFIDENCE - 0.01, 1, 1).is_reliable


def test_an_unsupported_file_type_says_so():
    with pytest.raises(ValueError, match="unsupported file type"):
        read_document(REPO_ROOT / "README.md")


def test_a_missing_file_raises_before_the_engine_loads():
    with pytest.raises(FileNotFoundError):
        read_document(REPO_ROOT / "demo" / "scans" / "nope.png")


@pytest.mark.skipif(not DEMO_SCAN.exists(), reason="run scripts/make_demo_scan.py")
def test_the_demo_scan_reads_but_not_perfectly():
    """Both halves matter.

    It has to read -- the clinical headings must survive, or nothing
    downstream has a section to work with. And it has to read *imperfectly*:
    a crisp render proves only that the renderer works, so if OCR ever
    returns clean text here the degradation in `make_demo_scan.py` has
    stopped doing its job and the pipeline is no longer being tested against
    anything realistic.
    """
    document = read_document(DEMO_SCAN)

    assert document.lines, "no text recognised at all"
    assert 0.5 < document.mean_confidence < 1.0

    text = document.text.upper()
    assert "ALLERGIES" in text
    assert "DIAGNOSIS" in text

    # The noise, rotation and blur should leave visible scars. Exact equality
    # with the source would mean the degradation is no longer applied.
    assert "PENICILLIN" in text
    assert any(
        not line.text.replace(" ", "").isalnum() for line in document.lines
    ), "OCR returned suspiciously clean text -- is the scan still degraded?"


@pytest.mark.skipif(not DEMO_SCAN.exists(), reason="run scripts/make_demo_scan.py")
def test_every_line_carries_page_and_position():
    document = read_document(DEMO_SCAN)
    for line in document.lines:
        assert line.page == 1
        assert line.box is not None
        x0, y0, x1, y1 = line.box
        assert x1 > x0 and y1 > y0
