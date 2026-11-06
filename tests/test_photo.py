"""Line boxes moved onto the handwriting (core/essay/photo.py), on drawn pages."""

import io

import pytest
from PIL import Image, ImageDraw

from core.essay import photo, worker

W, H = 1000, 1600
LEFT, RIGHT = 80, 920


def page(rows, title=None):
    """A sheet with a row of dark blocks (like characters) per row: rows is [(y centre, x start, x end)]."""
    img = Image.new("RGB", (W, H), (245, 243, 235))
    pen = ImageDraw.Draw(img)
    for y, x1, x2 in ([title] if title else []) + rows:
        for x in range(x1, x2, 60):
            pen.rectangle([x + 8, y - 20, x + 48, y + 20], outline=(25, 25, 25), width=4)
            pen.line([x + 8, y, x + 48, y], fill=(25, 25, 25), width=4)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=92)
    return buf.getvalue()


def lines_at(rows):
    return [{"text": "字" * ((x2 - x1) // 60), "box": [x1 * 1000 // W, (y - 25) * 1000 // H, x2 * 1000 // W, (y + 25) * 1000 // H]} for y, x1, x2 in rows]


def row_off(line, row):
    box = line["box"]
    return abs((box[1] + box[3]) / 2 * H / 1000 - row[0])


# Rows spreading towards the bottom, as in a photo taken from above the top of the page, with short lines and indents.
SPREADING = [(y, LEFT + (120 if k % 5 == 0 else 0), RIGHT - (420 if k % 5 == 4 else 0)) for k, y in enumerate(150 + 70 * k + 1.5 * k * k for k in range(16))]
SPREADING = [(round(y), a, b) for y, a, b in SPREADING]


def test_boxes_a_row_too_high_at_the_bottom_land_on_their_rows():
    evenly = [(150 + 85 * k, a, b) for k, (_, a, b) in enumerate(SPREADING)]
    assert abs(evenly[-1][0] - SPREADING[-1][0]) > 100  # the model's last box is about a row off
    new, stats = photo.calibrate(page(SPREADING), lines_at(evenly))
    assert stats["moved"] == len(SPREADING)
    assert all(row_off(line, row) < 20 for line, row in zip(new, SPREADING))


def test_right_boxes_do_not_climb_onto_the_title():
    rows = [(200 + 75 * k, a, b) for k, (_, a, b) in enumerate(SPREADING[:14])]
    new, _ = photo.calibrate(page(rows, title=(120, 300, 700)), lines_at(rows))
    assert all(row_off(line, row) < 20 for line, row in zip(new, rows))


def test_nothing_to_go_on_keeps_the_boxes():
    rows = SPREADING[:6]
    blank = io.BytesIO()
    Image.new("RGB", (W, H), (245, 243, 235)).save(blank, "JPEG")
    lines = lines_at(rows)
    assert photo.calibrate(blank.getvalue(), lines)[0] == lines  # no ink
    assert photo.calibrate(page(rows), lines[:2])[0] == lines[:2]  # too few lines
    boxless = [{"text": "一行", "box": None}] * 5
    assert photo.calibrate(page(rows), boxless)[0] == boxless


def test_a_fit_making_the_line_ends_worse_keeps_the_boxes():
    rows = SPREADING[:12]
    lines = lines_at(rows)
    # Ends the ink cannot support: every line claims the whole width while the rows are short.
    short = [(y, 300, 540) for y, _, _ in rows]
    wide = [{**line, "box": [50, line["box"][1] + 12, 950, line["box"][3] + 12]} for line in lines]
    assert photo.calibrate(page(short), wide)[0] == wide


def test_faint_first_character_stays_inside_the_box():
    mask = (__import__("numpy").zeros((400, 1000), dtype="uint8"))
    mask[100:140, 200:800] = 1  # the ink begins a character after where the model says the line begins
    import cv2, numpy as np

    boxes = np.array([[120.0, 100, 800, 140], [120, 200, 800, 240], [120, 300, 800, 340]])
    column = photo._Column(mask, cv2.integral(mask).astype(np.int64), boxes)
    assert column.ends(0, 100, 140, "字")[0] == 120.0


def test_two_pages_on_one_photo_are_fitted_apart():
    lines = [{"text": "字", "box": [100, y, 400, y + 30]} for y in (100, 150, 200, 250)] + [{"text": "字", "box": [600, y, 900, y + 30]} for y in (100, 150, 200)]
    assert photo._columns(lines) == [[0, 1, 2, 3], [4, 5, 6]]


def test_a_failed_calibration_keeps_the_page(monkeypatch, tmp_path):
    import core.essay as essay

    target = tmp_path / "1.jpg"
    target.write_bytes(b"not a photo")
    monkeypatch.setattr(essay, "image_path", lambda iid: target)
    sheet = {"lines": lines_at(SPREADING)}
    assert worker.calibrated({"id": 1}, sheet) is sheet
    with pytest.raises(ValueError):
        photo.calibrate(b"not a photo", sheet["lines"])
