"""core/essay/photo.py on synthetic photos: a sheet with rows of "handwriting", among clutter."""

import io

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from core.essay import photo  # noqa: E402


def encode(img):
    ok, out = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return out.tobytes()


def sheet(w=1000, h=1400, rows=10, ruled="", ink=True):
    """A white page with rows of dark strokes. Returns (page, [(top, bottom, left, right) per row])."""
    rng = np.random.default_rng(7)
    page = np.full((h, w, 3), 240, np.uint8)
    pitch = (h - 200) // rows
    spots = []
    for r in range(rows):
        top = 100 + r * pitch + pitch // 4
        bottom = top + pitch // 2
        left, right = 90, 910 - (120 if r % 3 == 2 else 0)
        spots.append((top, bottom, left, right))
        if ruled == "lines":
            cv2.line(page, (40, bottom + 8), (w - 40, bottom + 8), (150, 190, 150), 2)
        if ruled == "grid":
            cv2.rectangle(page, (40, top - 8), (w - 40, bottom + 8), (150, 190, 150), 2)
            for x in range(40, w - 40, pitch // 2 + 8):
                cv2.line(page, (x, top - 8), (x, bottom + 8), (150, 190, 150), 1)
        if ink:
            x = left
            while x < right - 30:
                for _ in range(4):
                    a = (int(x + rng.integers(0, 40)), int(rng.integers(top, bottom)))
                    b = (int(x + rng.integers(0, 40)), int(rng.integers(top, bottom)))
                    cv2.line(page, a, b, (35, 30, 30), 3)
                x += 56
    return page, spots


def on_desk(page, corners, size=(1800, 2400)):
    """The page photographed at corners (top-left clockwise) on a dark desk with light clutter."""
    w, h = size
    desk = np.full((h, w, 3), (70, 80, 95), np.uint8)
    desk = cv2.add(desk, np.random.default_rng(3).integers(0, 12, desk.shape, dtype=np.uint8))
    ph, pw = page.shape[:2]
    transform = cv2.getPerspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]), np.float32(corners))
    warped = cv2.warpPerspective(page, transform, (w, h))
    cover = cv2.warpPerspective(np.full((ph, pw), 255, np.uint8), transform, (w, h))
    desk[cover > 0] = warped[cover > 0]
    cv2.rectangle(desk, (30, 30), (260, 170), (225, 225, 235), -1)  # a light box in the corner
    cv2.circle(desk, (w - 120, h - 140), 90, (40, 40, 200), -1)  # a red mug
    cv2.line(desk, (40, h - 40), (420, h - 180), (200, 200, 60), 18)  # a pen
    return desk


CORNERS = [[420, 330], [1450, 410], [1520, 2120], [330, 2150]]


def test_finds_the_sheet_among_clutter_and_flattens_it():
    page, _ = sheet()
    img = on_desk(page, CORNERS)
    found = photo.find_paper(img)
    assert found is not None and np.abs(found - np.float32(CORNERS)).max() < 30
    data, w, h = photo.rectify(encode(img))
    assert 1.2 < h / w < 1.6 and max(w, h) <= photo.STORED_SIDE
    flat = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    # Nothing of the desk is left inside, apart from the thin margin kept around the page.
    inner = flat[int(h * 0.04) : int(h * 0.96), int(w * 0.04) : int(w * 0.96)]
    assert np.median(inner) > 190
    # The rows of writing came out level: each has a clear gap above and below across the whole width.
    profile = (photo.ink_mask(flat) > 0).sum(axis=1)
    assert (profile < 0.02 * w).sum() > 0.2 * h


@pytest.mark.parametrize("make", [
    lambda: sheet(1500, 2000)[0],  # the page fills the photo
    lambda: np.full((1600, 1200, 3), 245, np.uint8),  # only white
    lambda: np.random.default_rng(1).integers(40, 120, (1600, 1200, 3), dtype=np.uint8),  # no paper
])
def test_leaves_the_photo_alone_when_not_sure(make):
    assert photo.rectify(encode(make())) is None


def test_white_sheet_on_a_white_table_is_left_alone():
    page, _ = sheet()
    img = np.full((2400, 1800, 3), 238, np.uint8)
    img[500:1900, 400:1400] = page
    assert photo.rectify(encode(img)) is None


def test_rectified_again_stays_as_it_is():
    data, _, _ = photo.rectify(encode(on_desk(sheet()[0], CORNERS)))
    assert photo.rectify(data) is None


def model_lines(spots, w, h, dy=0.0, dx=0.0):
    """Line boxes as a model gives them: rough, off by (dx, dy) pixels."""
    return [
        {"text": "字" * 10, "box": [round((l + dx) / w * 1000), round((t + dy) / h * 1000), round((r + dx) / w * 1000), round((b + dy) / h * 1000)]}
        for t, b, l, r in spots
    ]


@pytest.mark.parametrize("ruled", ["", "lines", "grid"])
@pytest.mark.parametrize("dy,dx", [(-45, 0), (30, 25), (-70, -30)])
def test_boxes_snap_onto_the_rows(ruled, dy, dx):
    page, spots = sheet(ruled=ruled)
    h, w = page.shape[:2]
    lines, stats = photo.snap_lines(encode(page), model_lines(spots, w, h, dy, dx))
    assert stats["matched"] == len(spots) and stats["after"] >= stats["before"]
    for line, (top, bottom, left, right) in zip(lines, spots):
        x1, y1, x2, y2 = line["box"]
        assert abs((y1 + y2) / 2 / 1000 * h - (top + bottom) / 2) < 16
        assert abs(x1 / 1000 * w - left) < 25 and abs(x2 / 1000 * w - right) < 80
        assert line["text"] == "字" * 10


@pytest.mark.parametrize("dy", [0, -35, 35])
def test_a_title_above_does_not_put_the_page_one_row_off(dy):
    """Rows are evenly spaced, so one row off looks just as good by height alone, and the boxes still cover ink."""
    page, spots = sheet(rows=10)
    h, w = page.shape[:2]
    # The first row is the title: written, but not one of the lines. Lines end at different places.
    title, body = spots[0], spots[1:]
    page[title[0] - 6 : title[1] + 6] = 240
    cv2.rectangle(page, (380, title[0]), (620, title[1]), (35, 30, 30), 3)
    ends = [900, 880, 420, 890, 600, 870, 300, 895, 650]
    placed = []
    for (top, bottom, left, _), end in zip(body, ends):
        page[top - 6 : bottom + 6, end:] = 240
        placed.append((top, bottom, left, end))
    lines, stats = photo.snap_lines(encode(page), model_lines(placed, w, h, dy))
    assert stats["matched"] == len(placed) and stats["ends"] < 40
    for line, (top, bottom, _, _) in zip(lines, placed):
        assert abs((line["box"][1] + line["box"][3]) / 2 / 1000 * h - (top + bottom) / 2) < 16


def test_outside_paper_counts_what_the_cut_would_lose():
    page, spots = sheet()
    img = on_desk(page, CORNERS)
    h, w = img.shape[:2]
    inside = {"box": [450, 300, 550, 330]}
    beyond = {"box": [20, 900, 120, 930]}
    assert photo.outside_paper(encode(img), [inside]) == 0
    assert photo.outside_paper(encode(img), [inside, beyond]) == 0.5
    assert photo.outside_paper(encode(page), [beyond]) == 0  # not cut at all


def test_a_line_without_ink_moves_with_the_page():
    page, spots = sheet()
    h, w = page.shape[:2]
    top, bottom, _, _ = spots[5]
    page[top - 4 : bottom + 4] = 240  # the model read a line here, but there is no ink (too faint, say)
    original = model_lines(spots, w, h, -40)
    lines, stats = photo.snap_lines(encode(page), original)
    assert stats["matched"] == len(spots) - 1
    moved = lines[5]["box"][1] - original[5]["box"][1]
    assert 15 <= moved <= 45  # the page's shift, 40 px of 1400 = about 29


def test_boxes_stay_when_the_rows_cannot_be_found():
    page, spots = sheet(ink=False)
    h, w = page.shape[:2]
    original = model_lines(spots, w, h, -40)
    assert photo.snap_lines(encode(page), original)[0] == original
    # A strongly tilted page has no rows of its own.
    page, spots = sheet()
    original = model_lines(spots, w, h)
    for angle in (14, 5):
        turned = cv2.warpAffine(page, cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1), (w, h), borderValue=(240, 240, 240))
        assert photo.snap_lines(encode(turned), original)[0] == original, angle
    # A slight tilt still finds the right rows, each box just a little taller.
    turned = cv2.warpAffine(page, cv2.getRotationMatrix2D((w / 2, h / 2), 2, 1), (w, h), borderValue=(240, 240, 240))
    lines, stats = photo.snap_lines(encode(turned), original)
    assert stats["matched"] == len(spots)
    for line, (top, bottom, _, _) in zip(lines, spots):
        assert abs((line["box"][1] + line["box"][3]) / 2 / 1000 * h - (top + bottom) / 2) < 25
    # One line alone, or no boxes: nothing to do.
    one = model_lines(spots, w, h)[:1]
    assert photo.snap_lines(encode(page), one)[0] == one
    assert photo.snap_lines(encode(page), [{"text": "字", "box": None}])[0] == [{"text": "字", "box": None}]


def test_small_pictures():
    from PIL import Image

    page, spots = sheet()
    data = encode(page)
    assert photo.fit(data, 2000) == (data, 1000, 1400)
    small, w, h = photo.fit(data, 700)
    assert (w, h) == (500, 700) and small != data
    top, bottom, left, right = spots[0]
    box = [round(left / 10), round(top / 14), round(right / 10), round(bottom / 14)]
    with Image.open(io.BytesIO(photo.crop_char(data, box, "今天是星期天我和妈妈", 4))) as img:
        assert img.height >= 320 and img.width < 1500  # about three characters, not the whole line
    assert photo.glyph("暖") is not None
    assert photo.glyph("\U000f0001") is None  # not in the font
    with Image.open(io.BytesIO(photo.stitch_lines([small, small], [1, 2], width=400))) as img:
        assert img.width <= 400 and img.height > 400
