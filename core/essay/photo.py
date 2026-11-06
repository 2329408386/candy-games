"""Moves the OCR model's line boxes onto the handwriting. The model reads the words well but guesses where they are:
its rows are often evenly spaced while a photo taken at an angle spreads the real rows towards the bottom, so a box
can end up a whole row too high by the end of the page and the red pen lands on the line above.

The boxes of a column are treated as one template and stretched to fit the ink (y' = a + b*y + c*y², plus a tilt), then
each line may move a little and its ends follow the ink. Short lines and indents tell a page from the same page one
row off. When the fit is no better than the model's own boxes, would move them implausibly far, or makes the lines'
ends agree less with the ink, they stay.
Only the worker imports this module (OpenCV and numpy are not loaded in the web processes)."""

import cv2
import numpy as np

SIDE = 1600  # ink is looked for on a picture this big
MIN_LINES = 3  # fewer lines than this in a column cannot be fitted
MAX_SHIFT = 2.5  # rows: further than this and the fit is not believed
MIN_GAIN = 0.005  # how much better (share of the ink covered minus share of the area) a fit must be than the model
MAX_ENDS_OFF = 0.2  # beyond this the ink says too little about the lines (very faint writing, a steep slant): no move
ENDS_SLACK = 0.01  # the lines' ends may agree this much less with the ink than the model's own boxes did
NEAR = (0.35, 0.7, 1.1)  # rows: the best fit moving at most this far is also tried
CLOSE = 0.02  # the smallest move wins among fits scoring within this of the best
PUNCTUATION = set("，。、；：？！…—”’）》,.;:?!)")


def _decode(data):
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR | cv2.IMREAD_IGNORE_ORIENTATION)
    if img is None:
        raise ValueError("cannot decode photo")
    h, w = img.shape[:2]
    scale = SIDE / max(h, w)
    return cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else img


def ink_mask(gray):
    """Handwriting as 0/1: dark strokes on the local paper colour, without long printed rules and grids."""
    h, w = gray.shape[:2]
    block = max(15, w // 30) | 1
    mask = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (3, 3), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, block, 15)
    for size in ((max(20, w // 12), 1), (1, max(20, h // 25))):
        rules = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, size))
        mask = cv2.subtract(mask, cv2.dilate(rules, cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))))
    return (cv2.medianBlur(mask, 3) > 0).astype(np.uint8)


class _Column:
    """The boxes of one column of writing on the ink."""

    PIECES = 6  # a slanted row is summed over this many upright pieces

    def __init__(self, mask, integral, boxes):
        self.mask, self.integral, self.boxes = mask, integral, boxes
        self.h, self.w = mask.shape
        self.ys = (boxes[:, 1] + boxes[:, 3]) / 2
        gaps = np.diff(self.ys)
        self.pitch = float(np.median(gaps[gaps > 0]))
        self.top, self.span, self.xc = self.ys[0], max(1.0, self.ys[-1] - self.ys[0]), self.w / 2
        edges = np.linspace(boxes[:, 0], boxes[:, 2], self.PIECES + 1, axis=1)
        self.x1, self.x2 = np.clip(edges[:, :-1].astype(int), 0, self.w), np.clip(edges[:, 1:].astype(int), 0, self.w)
        self.mid = (edges[:, :-1] + edges[:, 1:]) / 2 - self.xc
        left = max(0, int(boxes[:, 0].min() - 0.05 * self.w))
        right = min(self.w, int(boxes[:, 2].max() + 0.05 * self.w))
        upper = max(0, int(self.ys[0] - 1.5 * self.pitch))
        lower = min(self.h, int(self.ys[-1] + 3.5 * self.pitch))
        self.area = (right - left) * (lower - upper)
        self.total = float(integral[lower, right] - integral[upper, right] - integral[lower, left] + integral[upper, left])

    def warp(self, p):
        a, b, c, _ = p
        t = (self.ys - self.top) / self.span
        return self.top + a + (self.ys - self.top) * b + c * self.span * t * t

    def covered(self, y, half, s):
        """Ink and area inside the rows centred at y (half: half a row's height, per line), slanted by s."""
        centre = y[:, None] + s * self.mid
        half = np.broadcast_to(np.asarray(half)[:, None] if np.ndim(half) else half, centre.shape)
        y1, y2 = np.clip((centre - half).astype(int), 0, self.h), np.clip((centre + half).astype(int), 0, self.h)
        i = self.integral
        ink = i[y2, self.x2] - i[y1, self.x2] - i[y2, self.x1] + i[y1, self.x1]
        return ink.astype(float), ((self.x2 - self.x1) * (y2 - y1)).astype(float)

    def score(self, p):
        y = self.warp(p)
        if np.any(np.diff(y) < 0.5 * self.pitch):
            return -9.0
        ink, area = self.covered(y, np.maximum(4, 0.36 * np.gradient(y)), p[3])
        return float(ink.sum() / self.total - area.sum() / self.area)

    def shift(self, p):
        """How far these parameters move the worst line, in rows."""
        return float(np.abs(self.warp(p) - self.ys).max() / self.pitch)

    def refine(self, p):
        top = self.score(p)
        for size in (0.5, 0.25, 0.1):
            steps = (0.1 * self.pitch * size, 0.01 * size, 0.01 * size, 0.01 * size)
            moved = True
            while moved:
                moved = False
                for k, step in enumerate(steps):
                    for sign in (1, -1):
                        q = list(p)
                        q[k] += sign * step
                        value = self.score(q)
                        if value > top + 1e-6:
                            p, top, moved = tuple(q), value, True
        return p, top

    def fit(self):
        """The fit with the smallest move among those about as good as the best: stretching the boxes can also score by
        covering the title, a page one row off, so the model's own positions decide between close fits."""
        found = []
        for a in np.arange(-1, 1.01, 0.125) * self.pitch:
            for b in np.arange(0.9, 1.2, 0.02):
                for c in np.arange(-0.04, 0.2, 0.02):
                    p = (float(a), float(b), float(c), 0.0)
                    found.append((self.score(p), self.shift(p), p))
        fits = [self.refine(max(found, key=lambda f: f[0])[2])]
        for limit in NEAR:
            near = [f for f in found if f[1] <= limit]
            if near:
                fits.append(self.refine(max(near, key=lambda f: f[0])[2]))
        top = max(value for _, value in fits)
        return min((f for f in fits if f[1] >= top - CLOSE), key=lambda f: self.shift(f[0]))

    def ends_off(self, y, s):
        """How far the ink on the rows at y starts and ends from where the model says each line does (share of the page
        width, each line at most 0.5; a row without ink counts 0.5). A page one row off fits the rows as well as the
        right one, but its short lines and indents land on the wrong rows, which only this shows."""
        heights = np.gradient(y)
        left = max(0, int(self.boxes[:, 0].min() - 0.05 * self.w))
        right = min(self.w, int(self.boxes[:, 2].max() + 0.05 * self.w))
        total = 0.0
        for k, box in enumerate(self.boxes):
            mid = y[k] + s * ((box[0] + box[2]) / 2 - self.xc)
            half = max(4, 0.36 * heights[k])
            y1, y2 = int(np.clip(mid - half, 0, self.h)), int(np.clip(mid + half, 0, self.h))
            dense = self._dense(self.mask[y1:y2, left:right], y2 - y1) if y2 > y1 else []
            total += min(0.5, (abs(left + dense[0] - box[0]) + abs(left + dense[-1] - box[2])) / self.w) if len(dense) else 0.5
        return total / len(self.boxes)

    @staticmethod
    def _dense(rows, height):
        """Columns of this slice with a character's worth of ink around them, rather than a thin rule."""
        window = max(3, height // 2)
        return np.flatnonzero(np.convolve(rows.sum(axis=0).astype(float), np.ones(window) / window, mode="same") / height >= 0.10)

    def nudged(self, y, s):
        """Each row a little up or down, wherever it holds the most ink."""
        heights = np.gradient(y)
        offsets = np.arange(-0.22, 0.2201, 0.02)
        out = y.copy()
        for k in range(len(y)):
            values = []
            for offset in offsets:
                one = y.copy()
                one[k] += offset * heights[k]
                ink, area = self.covered(one, np.maximum(4, 0.36 * heights), s)
                values.append(ink[k].sum() / (area[k].sum() + 1) - 0.05 * abs(offset))
            out[k] = y[k] + offsets[int(np.argmax(values))] * heights[k]
        return out

    def ends(self, k, y1, y2, text):
        """Where line k starts and ends on its row, from the ink: a character's worth of ink rather than a thin rule,
        and on to a closing punctuation mark, which is too small to count as one."""
        box = self.boxes[k]
        width = box[2] - box[0]
        reach = 0.07 * self.w
        lo, hi = max(0, int(box[0] - reach)), min(self.w, int(box[2] + reach))
        if hi <= lo or y2 <= y1:
            return box[0], box[2]
        column = self.mask[y1:y2, lo:hi].sum(axis=0).astype(float)
        window = max(3, (y2 - y1) // 2)  # as in _dense
        dense = self._dense(self.mask[y1:y2, lo:hi], y2 - y1)
        if not len(dense):
            return box[0], box[2]
        strokes = np.flatnonzero(column >= 2)
        start = strokes[strokes >= dense[0] - window // 2]
        stop = strokes[strokes <= dense[-1] + window // 2]
        if not len(start) or not len(stop):
            return box[0], box[2]
        x1, x2 = lo + int(start[0]), lo + int(stop[-1]) + 1
        if text and text[-1] in PUNCTUATION:
            more = strokes[(strokes > x2 - lo) & (strokes <= x2 - lo + (y2 - y1))]
            if len(more):
                x2 = lo + int(more[-1]) + 1
        pad = max(1, (y2 - y1) // 12)
        x1, x2 = max(0, x1 - pad), min(self.w, x2 + pad)
        # The model is roughly right about the ends: a far-off end is a stray mark or a slanted row leaving the slice.
        # Moving in is held back more: faint first or last characters must not fall outside the box.
        outward, inward = 1.5 * (y2 - y1), 0.75 * (y2 - y1)
        x1 = x1 if -outward <= x1 - box[0] <= inward else box[0]
        x2 = x2 if -inward <= x2 - box[2] <= outward else box[2]
        return (x1, x2) if x1 < x2 else (box[0], box[2])


def _columns(lines):
    """Line indexes per column, in reading order: a spread of two pages restarts near the top."""
    groups, current = [], []
    for k, line in enumerate(lines):
        if not line.get("box"):
            continue
        y = (line["box"][1] + line["box"][3]) / 2
        if current and y < (lines[current[-1]]["box"][1] + lines[current[-1]]["box"][3]) / 2:
            groups.append(current)
            current = []
        current.append(k)
    return groups + [current] if current else groups


def calibrate(data, lines):
    """Lines from the ocr step ([{"text", "box": [x1, y1, x2, y2] in 0–1000 or None, …}]) with their boxes moved onto
    the writing → (new lines, {"lines", "moved", "shift" (largest, in rows)}). Lines whose boxes could not be
    improved keep them. Raises ValueError when the photo cannot be decoded."""
    out = [dict(line) for line in lines]
    stats = {"lines": sum(1 for line in lines if line.get("box")), "moved": 0, "shift": 0.0}
    if stats["lines"] < MIN_LINES:
        return out, stats
    gray = cv2.cvtColor(_decode(data), cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    mask = ink_mask(gray)
    integral = cv2.integral(mask).astype(np.int64)
    scale = np.array([w, h, w, h]) / 1000
    for group in _columns(lines):
        if len(group) < MIN_LINES:
            continue
        boxes = np.array([lines[k]["box"] for k in group], dtype=float) * scale
        if not np.any(np.diff((boxes[:, 1] + boxes[:, 3]) / 2) > 0):
            continue
        column = _Column(mask, integral, boxes)
        if column.total <= 0:
            continue
        p, value = column.fit()
        base = column.score((0.0, 1.0, 0.0, 0.0))
        y = column.warp(p)
        s = p[3]
        if value < base + MIN_GAIN or np.abs(y - column.ys).max() > MAX_SHIFT * column.pitch:
            continue
        off = column.ends_off(y, s)
        if off > MAX_ENDS_OFF or off > column.ends_off(column.ys, 0.0) + ENDS_SLACK:
            continue
        y = column.nudged(y, s)
        heights = np.gradient(y)
        for row, k in enumerate(group):
            half = max(6.0, 0.45 * heights[row])
            # The line's own place on the slope; a slanted row needs a little more room in an upright box.
            box = boxes[row]
            mid = y[row] + s * ((box[0] + box[2]) / 2 - column.xc)
            half += 0.25 * abs(s) * (box[2] - box[0])
            y1, y2 = int(np.clip(mid - half, 0, h)), int(np.clip(mid + half, 0, h))
            x1, x2 = column.ends(row, y1, y2, lines[k].get("text", ""))
            new = [round(x1 / w * 1000), round(y1 / h * 1000), round(x2 / w * 1000), round(y2 / h * 1000)]
            if new[0] < new[2] and new[1] < new[3]:
                out[k]["box"] = new
                stats["moved"] += 1
        shift = float(np.abs(y - column.ys).max() / column.pitch)
        stats["shift"] = round(max(stats["shift"], shift), 2)
    return out, stats
