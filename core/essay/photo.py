"""Looking at the pixels of an essay photo: what the vision model's rough coordinates cannot give.

  rectify(data)            finds the sheet of paper among whatever else is in the picture and flattens it. None when
                           it is not sure (the photo is then used as it is: a wrong cut would lose the child's writing).
  snap_lines(data, lines)  moves the model's line boxes onto the real rows of handwriting.
  fit, crop_char, glyph, stitch_lines   small picture helpers for the ocr and photo-check steps.

OpenCV is imported inside the functions: only the essay worker needs it, and the site's processes stay small.
"""

import glob, io, statistics
from pathlib import Path

STORED_SIDE = 2400  # longest side of a photo kept on disk
OCR_SIDE = 2000  # longest side of what the ocr step's model is sent

# Switches in ess_meta (value "1" = on, 模型设置 page). All off: photos are handled as they were before this module.
FLAGS = {
    "photo_rectify": "自动裁正作文纸",
    "photo_snap": "按字迹校准行位置",
    "shape_scan": "放大筛查写错笔画的字",
}

_DETECT_SIDE = 800  # the paper is looked for on a picture this big
_MIN_AREA, _MAX_AREA = 0.2, 0.93  # of the picture: smaller is not the essay, bigger needs no cutting
_MIN_BRIGHT = 0.7  # of the quadrilateral must be as light as paper
_MARGIN = 0.015  # kept around the paper, so writing near its edge is never cut
_SNAP_SIDE = 1600  # handwriting rows are looked for on a picture this big
_MIN_MATCHED = 0.6  # of the lines must land on a row, or the page keeps the model's boxes


def _cv():
    import cv2

    cv2.setNumThreads(1)  # a small server, shared with the site
    return cv2


def _decode(data):
    import numpy as np

    cv2 = _cv()
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not an image")
    return img


def _scaled(img, side):
    cv2 = _cv()
    h, w = img.shape[:2]
    scale = min(1.0, side / max(h, w))
    return (cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img), scale


# ---- the sheet of paper ----
def _order(points):
    """Four corners clockwise from the top-left."""
    import numpy as np

    points = np.asarray(points, dtype=np.float32)
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))]
    first = int(np.argmin(points.sum(axis=1)))
    return np.roll(points, -first, axis=0)


def _sides(q):
    import numpy as np

    return [float(np.linalg.norm(q[(k + 1) % 4] - q[k])) for k in range(4)]


def _sound(q):
    """Whether four corners can be a photographed sheet: nearly square corners, opposite sides alike, paper-shaped."""
    import numpy as np

    sides = _sides(q)
    if min(sides) < 1:
        return False
    for k in range(4):
        a, b = q[k - 1] - q[k], q[(k + 1) % 4] - q[k]
        angle = np.degrees(np.arccos(np.clip(float(a @ b) / (np.linalg.norm(a) * np.linalg.norm(b)), -1, 1)))
        if not 65 <= angle <= 115:
            return False
    if min(sides[0], sides[2]) / max(sides[0], sides[2]) < 0.6 or min(sides[1], sides[3]) / max(sides[1], sides[3]) < 0.6:
        return False
    width, height = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    return max(width, height) / min(width, height) <= 2.4


def _quads(mask):
    """Quadrilaterals around the biggest shapes of a mask."""
    import numpy as np

    cv2 = _cv()
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        hull = cv2.convexHull(contour)
        perimeter = cv2.arcLength(hull, True)
        found = None
        for eps in (0.02, 0.03, 0.05, 0.08):
            poly = cv2.approxPolyDP(hull, eps * perimeter, True)
            if len(poly) == 4:
                found = poly.reshape(4, 2)
                break
        if found is None:
            rect = cv2.minAreaRect(contour)
            if cv2.contourArea(contour) < 0.85 * rect[1][0] * rect[1][1]:
                continue  # not a solid block: no rectangle to speak of
            found = cv2.boxPoints(rect)
        yield _order(np.asarray(found, dtype=np.float32))


def find_paper(img):
    """The corners of the sheet in img's own pixels (clockwise from the top-left), or None when not sure."""
    import numpy as np

    cv2 = _cv()
    small, scale = _scaled(img, _DETECT_SIDE)
    h, w = small.shape[:2]
    light = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2LAB)[:, :, 0], (5, 5), 0)
    size = max(5, round(max(h, w) * 0.025)) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    _, bright = cv2.threshold(light, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Closing fills the handwriting and the printed grid, opening cuts thin bridges to other light things.
    bright = cv2.morphologyEx(cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel), cv2.MORPH_OPEN, kernel)
    edges = cv2.morphologyEx(cv2.Canny(light, 50, 150), cv2.MORPH_CLOSE, kernel)
    best, best_score = None, 0
    for mask in (bright, edges):
        for q in _quads(mask):
            area = cv2.contourArea(q) / (w * h)
            if not _MIN_AREA <= area <= _MAX_AREA or not _sound(q):
                continue
            inside = np.zeros((h, w), np.uint8)
            cv2.fillConvexPoly(inside, q.astype(np.int32), 255)
            share = cv2.countNonZero(cv2.bitwise_and(bright, inside)) / max(1, cv2.countNonZero(inside))
            if share < _MIN_BRIGHT:
                continue
            outside = cv2.mean(light, cv2.bitwise_not(inside))[0]
            if cv2.mean(light, inside)[0] < outside + 10:
                continue  # as light as its surroundings: no real edge of a sheet (white paper on a white table)
            score = area * share * share
            if score > best_score:
                best, best_score = q, score
    return None if best is None else best / scale


def rectify(data):
    """JPEG bytes → (the flattened sheet as JPEG bytes, width, height), or None when the photo should stay as it is."""
    import numpy as np

    cv2 = _cv()
    img = _decode(data)
    q = find_paper(img)
    if q is None:
        return None
    h, w = img.shape[:2]
    center = q.mean(axis=0)
    q = center + (q - center) * (1 + 2 * _MARGIN)
    q[:, 0] = np.clip(q[:, 0], 0, w - 1)
    q[:, 1] = np.clip(q[:, 1], 0, h - 1)
    sides = _sides(q)
    width, height = (sides[0] + sides[2]) / 2, (sides[1] + sides[3]) / 2
    scale = min(1.0, STORED_SIDE / max(width, height))
    width, height = max(1, round(width * scale)), max(1, round(height * scale))
    target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
    flat = cv2.warpPerspective(img, cv2.getPerspectiveTransform(q.astype(np.float32), target), (width, height), flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    ok, out = cv2.imencode(".jpg", flat, [cv2.IMWRITE_JPEG_QUALITY, 90])
    return (out.tobytes(), width, height) if ok else None


# ---- rows of handwriting ----
def ink_mask(gray):
    """Handwriting as a 0/255 mask: dark strokes on the local paper colour, without long printed rules and grids."""
    cv2 = _cv()
    h, w = gray.shape[:2]
    block = max(15, w // 30) | 1
    mask = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 15)
    for size in ((max(20, w // 12), 1), (1, max(20, h // 25))):
        rules = cv2.dilate(cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size)), cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
        mask = cv2.subtract(mask, rules)
    return cv2.medianBlur(mask, 3)


def _rows(profile, pitch):
    """Rows of writing from the ink per pixel row: [(top, bottom)]. Rows that touch are split at their thinnest."""
    import numpy as np

    window = max(1, round(pitch / 8))
    smooth = np.convolve(profile, np.ones(window) / window, mode="same")
    filled = smooth[smooth > 0]
    if not len(filled):
        return []
    on = smooth >= 0.25 * np.percentile(filled, 90)
    rows, start, gap = [], None, 0
    for y, value in enumerate(on):
        if value:
            start, gap = y if start is None else start, 0
        elif start is not None:
            gap += 1
            if gap > 0.15 * pitch:
                rows.append((start, y - gap + 1))
                start, gap = None, 0
    if start is not None:
        rows.append((start, len(on)))
    out = []
    for top, bottom in rows:
        count = round((bottom - top) / pitch)
        while count > 1 and bottom - top > 1.5 * pitch:
            at = top + round((bottom - top) / count)
            reach = max(1, round(pitch / 4))
            lo, hi = max(top + 1, at - reach), min(bottom - 1, at + reach)
            cut = lo + int(np.argmin(smooth[lo:hi + 1]))
            out.append((top, cut))
            top, count = cut, count - 1
        out.append((top, bottom))
    return [r for r in out if r[1] - r[0] >= 0.3 * pitch]


def _align(centers, rows, pitch):
    """Lines to rows, in order: {line index: row index} with the smallest total distance, and that total. A line may
    stay without a row (it costs about one line pitch) and a row without a line (the title, a stain) costs little."""
    n, m = len(centers), len(rows)
    skip_line, skip_row = 0.9 * pitch, 0.05 * pitch
    cost = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        cost[i][m] = cost[i + 1][m] + skip_line
    for j in range(m - 1, -1, -1):
        cost[n][j] = cost[n][j + 1] + skip_row
    middle = [(a + b) / 2 for a, b in rows]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            cost[i][j] = min(cost[i + 1][j] + skip_line, cost[i][j + 1] + skip_row, cost[i + 1][j + 1] + abs(centers[i] - middle[j]))
    pairs, i, j = {}, 0, 0
    while i < n and j < m:
        if cost[i][j] == cost[i + 1][j + 1] + abs(centers[i] - middle[j]):
            pairs[i] = j
            i, j = i + 1, j + 1
        elif cost[i][j] == cost[i][j + 1] + skip_row:
            j += 1
        else:
            i += 1
    return pairs, cost[0][0]


def _blurred(mask, rows, left, right, pitch):
    """Whether the rows found are not to be trusted: a tilted page has no clean blank between its rows, and each row
    sits higher at one end than at the other. The rows are then only slices through the writing."""
    import numpy as np

    span = rows[-1][1] - rows[0][0]
    if len(rows) >= 2 and sum(b - a for a, b in rows) > 0.92 * span:
        return True
    third = max(1, (right - left) // 3)
    slopes = []
    for top, bottom in rows:
        ends = []
        for lo, hi in ((left, left + third), (right - third, right)):
            ys = np.flatnonzero((mask[top:bottom, lo:hi] > 0).sum(axis=1))
            if not len(ys):
                break
            ends.append(float(ys.mean()))
        if len(ends) == 2:
            slopes.append(abs(ends[0] - ends[1]))
    return bool(slopes) and statistics.median(slopes) > 0.2 * pitch


def snap_lines(data, lines):
    """Lines from the ocr step ([{"box": [x1, y1, x2, y2] in 0–1000 or None, …}]) with their boxes moved onto the
    rows of handwriting → (new lines, {"lines", "matched", "shift" (median, in 0–1000), "before", "after"}).
    before/after: the share of the ink near the writing that the boxes cover. The boxes stay when too few lines find
    a row (a tilted page, for one), or when the new ones would cover less ink than the old."""
    import numpy as np

    cv2 = _cv()
    boxed = [k for k, line in enumerate(lines) if line.get("box")]
    stats = {"lines": len(boxed), "matched": 0, "shift": 0, "before": 0.0, "after": 0.0}
    if len(boxed) < 2:
        return lines, stats
    small, _ = _scaled(_decode(data), _SNAP_SIDE)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    mask = ink_mask(gray)
    boxes = [[v / 1000 * (w if n % 2 == 0 else h) for n, v in enumerate(lines[k]["box"])] for k in boxed]
    centers = [(b[1] + b[3]) / 2 for b in boxes]
    gaps = [b - a for a, b in zip(centers, centers[1:]) if b > a]
    pitch = statistics.median(gaps) if gaps else statistics.median(b[3] - b[1] for b in boxes) * 1.5
    pitch = max(pitch, 8.0)
    # Only where the writing can be: clutter beside the paper must not count as rows.
    left = max(0, round(min(b[0] for b in boxes) - 0.04 * w))
    right = min(w, round(max(b[2] for b in boxes) + 0.04 * w))
    top = max(0, round(min(b[1] for b in boxes) - 2 * pitch))
    bottom = min(h, round(max(b[3] for b in boxes) + 2 * pitch))
    if right <= left or bottom <= top:
        return lines, stats
    area = np.zeros_like(mask)
    area[top:bottom, left:right] = mask[top:bottom, left:right]
    stats["before"] = _cover(area, boxes)
    rows = _rows((area[:, left:right] > 0).sum(axis=1).astype(np.float64), pitch)
    if not rows or _blurred(area, rows, left, right, pitch):
        return lines, stats
    # The model's boxes are often all off by about the same amount: try the page shifted before aligning.
    offsets = [pitch * k / 4 for k in range(-8, 9)]
    offset = min(offsets, key=lambda o: _align([c + o for c in centers], rows, pitch)[1] + abs(o) * 0.01)
    pairs, _ = _align([c + offset for c in centers], rows, pitch)
    pairs = {i: j for i, j in pairs.items() if abs(centers[i] + offset - sum(rows[j]) / 2) <= 0.75 * pitch}
    stats["matched"] = len(pairs)
    if len(pairs) < _MIN_MATCHED * len(boxed):
        return lines, stats
    shift = statistics.median(sum(rows[j]) / 2 - centers[i] for i, j in pairs.items())
    new = []
    for i, b in enumerate(boxes):
        if i not in pairs:
            new.append([b[0], b[1] + shift, b[2], b[3] + shift])
            continue
        y1, y2 = rows[pairs[i]]
        pad = max(1, round((y2 - y1) * 0.08))
        y1, y2 = max(0, y1 - pad), min(h, y2 + pad)
        reach = 0.06 * w
        lo, hi = max(left, round(b[0] - reach)), min(right, round(b[2] + reach))
        columns = np.flatnonzero((area[y1:y2, lo:hi] > 0).sum(axis=0) >= 2)
        x1, x2 = b[0], b[2]
        if len(columns):
            ink_left, ink_right = lo + int(columns[0]), lo + int(columns[-1]) + 1
            if 0.5 * (b[2] - b[0]) <= ink_right - ink_left <= 1.4 * (b[2] - b[0]):
                x1, x2 = ink_left, ink_right
        new.append([x1, y1, x2, y2])
    after = _cover(area, new)
    stats["after"] = after
    stats["shift"] = round(shift / h * 1000)
    if after < stats["before"]:
        stats["matched"] = 0
        return lines, stats
    out = [dict(line) for line in lines]
    for k, b in zip(boxed, new):
        x1, y1 = max(0, min(999, round(b[0] / w * 1000))), max(0, min(999, round(b[1] / h * 1000)))
        out[k]["box"] = [x1, y1, max(x1 + 1, min(1000, round(b[2] / w * 1000))), max(y1 + 1, min(1000, round(b[3] / h * 1000)))]
    return out, stats


def _cover(mask, boxes):
    """The share of the mask's ink inside the boxes (each pixel once)."""
    import numpy as np

    total = int((mask > 0).sum())
    if not total:
        return 0.0
    inside = np.zeros_like(mask)
    for x1, y1, x2, y2 in boxes:
        inside[max(0, round(y1)) : max(0, round(y2)), max(0, round(x1)) : max(0, round(x2))] = 1
    return round(int(((mask > 0) & (inside > 0)).sum()) / total, 3)


# ---- small pictures for the models (Pillow only) ----
def _jpeg(img, quality=90):
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def fit(data, side):
    """A JPEG with its longest side at most side px → (bytes, width, height); the same bytes when it already fits."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as img:
        if max(img.size) <= side:
            return data, img.width, img.height
        small = img.convert("RGB")
        small.thumbnail((side, side), Image.Resampling.LANCZOS)
    return _jpeg(small), small.width, small.height


def char_weight(c):
    """How wide a character is against a 汉字: the same numbers as weight() in sites/teacher/static/redpen.js."""
    return 0.55 if " " <= c <= "~" else 1.0


def crop_char(data, box, line, nth):
    """Character number nth (from 1) of a line, with its neighbours, enlarged: JPEG bytes. Its place in the line is
    estimated from the characters' widths, so a neighbour on each side is kept in the picture."""
    from PIL import Image

    chars = list(line)
    widths = [char_weight(c) for c in chars]
    total = sum(widths) or 1
    start = sum(widths[: max(0, nth - 2)]) / total
    end = sum(widths[: min(len(chars), nth + 1)]) / total
    with Image.open(io.BytesIO(data)) as img:
        w, h = img.size
        x1, y1, x2, y2 = box
        span = (x2 - x1) / 1000 * w
        left, right = x1 / 1000 * w + span * start, x1 / 1000 * w + span * end
        pad_x, pad_y = (right - left) * 0.1, (y2 - y1) / 1000 * h * 0.3
        part = img.convert("RGB").crop((
            max(0, int(left - pad_x)), max(0, int(y1 / 1000 * h - pad_y)),
            min(w, max(int(left - pad_x) + 1, int(right + pad_x))), min(h, max(int(y1 / 1000 * h - pad_y) + 1, int(y2 / 1000 * h + pad_y))),
        ))
    if part.height < 320:
        scale = 320 / part.height
        part = part.resize((max(1, round(part.width * scale)), 320), Image.Resampling.LANCZOS)
    return _jpeg(part)


def _font(size):
    from PIL import ImageFont

    root = Path(__file__).resolve().parent.parent.parent / "sites" / "teacher" / "static"
    found = glob.glob(str(root / "redpen-font.*.woff2"))
    return ImageFont.truetype(found[0], size) if found else None


def glyph(char):
    """The character in a standard hand (霞鹜文楷) as JPEG bytes, or None when the font does not have it: the red-pen
    font is a subset, and a blank picture would mislead the model."""
    from PIL import Image, ImageChops, ImageDraw

    try:
        font = _font(240)
    except OSError:
        font = None
    if font is None:
        return None

    def draw(c):
        img = Image.new("L", (320, 320), 255)
        ImageDraw.Draw(img).text((160, 160), c, fill=0, font=font, anchor="mm")
        return img

    picture = draw(char)
    if picture.point(lambda v: 255 - v).getbbox() is None or ImageChops.difference(picture, draw("\U000f0000")).getbbox() is None:
        return None
    return _jpeg(picture.convert("RGB"))


def stitch_lines(crops, numbers, width=2400):
    """Crops of lines (JPEG bytes) one under another, each numbered in red at its left: JPEG bytes."""
    from PIL import Image, ImageDraw, ImageFont

    gutter, gap = 96, 12
    rows = []
    for data in crops:
        with Image.open(io.BytesIO(data)) as img:
            row = img.convert("RGB")
        if row.width > width - gutter:
            row = row.resize((width - gutter, max(1, round(row.height * (width - gutter) / row.width))), Image.Resampling.LANCZOS)
        rows.append(row)
    sheet = Image.new("RGB", (gutter + max(r.width for r in rows), sum(r.height + gap for r in rows)), "white")
    pen = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(44)
    except (OSError, TypeError):
        font = None
    y = 0
    for row, number in zip(rows, numbers):
        sheet.paste(row, (gutter, y))
        pen.text((gutter - 12, y + row.height / 2), str(number), fill=(210, 20, 20), font=font, anchor="rm" if font else None)
        y += row.height + gap
    return _jpeg(sheet)
