"""Grading an essay: prompt building, the model's JSON, and the checks that keep made-up feedback out.

Pipeline (run by the worker, core/essay/worker.py):
  0. ocr    — photos only: the ocr step's vision model copies each page line by line, with each line's box on the
              photo (prompts/ocr.md); assemble() joins the lines into the essay text and keeps where every line is,
              so the result page can draw the marks on the photo in red pen.
  1. grade  — the grade step's model returns dims / marks / summary (one encouraging paragraph) (prompts/grade.md).
  2. clean  — the server keeps only what fits the schema: scores are clamped to each dimension's maximum, the total
              and level are computed here (never taken from the model), and every quote must occur in the essay
              text exactly (whitespace aside), otherwise the mark is dropped. Offsets of the quotes are stored so
              pages can highlight them.
     plausible_fix() also drops corrections that cannot be right (a 错别字 that changes the length, a 标点 fix that
              changes words, anything touching an unreadable □).
  3. check  — the check step's model re-judges every error mark (错别字 / 用词不当 / 病句 / 标点) and every 修改建议
              example sentence, and gives its own level; marks it rejects or skips are dropped, and a level two steps
              away from ours is flagged for the teacher. If the check step is not configured or fails, the marks stay,
              the review is flagged "unchecked" and goes to the teacher instead of being published (worker.py).

Prompts live in sites/teacher/prompts/; PROMPT_VERSION is a hash of those files and is stored with each review.
Anchor essays (teacher-scored examples that calibrate the scale) go in prompts/anchors/<band>.json.
"""

import hashlib, json, re
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent.parent / "sites" / "teacher" / "prompts"
DIMS = {"content": "内容", "structure": "结构", "language": "语言", "norms": "规范"}
ERROR_TYPES = ("错别字", "用词不当", "病句", "标点")
MARK_TYPES = ERROR_TYPES + ("好词好句", "修改建议", "分段")
LEVELS = ("待提高", "及格", "良", "优")
TOTAL = 25  # every band's maxima add up to this (prompts/bands.json)
FLAGS = {
    "off_topic": "可能偏题",
    "too_short": "可能没写完或字数不够",
    "copied": "疑似照抄",
    "unclear": "看不清的字较多",
    "unchecked": "没有经过复核",
    "disagree": "复核等级相差较大，建议细看",
}
MAX_TEXT = 3000
MAX_MARKS = 40
# Marks kept per kind: the review is careful (every real problem is pointed out); long notes go in the page margins.
MARK_LIMITS = {"error": 25, "好词好句": 6, "修改建议": 5, "分段": 3}
NOTE_MAX = 40
SUMMARY_MAX = 200


def _read(name):
    return (PROMPTS / name).read_text(encoding="utf-8")


BANDS = json.loads(_read("bands.json"))
GENRES = json.loads(_read("genres.json"))
CHOICES = [g for g in GENRES if g != "其他"]  # what a teacher may tick (several, or none)
GRADE_PROMPT = _read("grade.md")
CHECK_PROMPT = _read("check.md")
OCR_PROMPT = _read("ocr.md")


def _anchor_files():
    return sorted((PROMPTS / "anchors").glob("*.json")) if (PROMPTS / "anchors").is_dir() else []


PROMPT_VERSION = hashlib.sha256(
    b"".join(p.read_bytes() for p in sorted(PROMPTS.glob("*.*")) + _anchor_files())
).hexdigest()[:8]


def band_of(grade):
    return next(key for key, band in BANDS.items() if grade in band["grades"])


def level_of(total):
    """优 22–25, 良 19–21, 及格 15–18, below that 待提高."""
    return "优" if total >= 22 else "良" if total >= 19 else "及格" if total >= 15 else "待提高"


def clean_text(text):
    """An essay as stored: unified newlines, no trailing spaces, at most one blank line between paragraphs."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("　", "  ")
    text = "".join(c for c in text if c == "\n" or c.isprintable())
    lines = [line.rstrip() for line in text.split("\n")]
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip("\n")
    return text


def char_count(text):
    """Characters as a teacher counts them: everything except whitespace."""
    return sum(1 for c in text if not c.isspace())


# ---- prompts ----
def anchors_text(band):
    path = PROMPTS / "anchors" / f"{band}.json"
    if not path.exists():
        return ""
    parts = ["\n# 参考范文（老师已经评过分，用来对齐打分尺度，不要照搬评语）"]
    for i, a in enumerate(json.loads(path.read_text(encoding="utf-8")), 1):
        parts.append(
            f"## 范文{i}：{a['grade']}年级 · {a.get('genre', '')} · 《{a.get('title', '')}》\n"
            f"{a['text']}\n老师给分：{a['score']} 分（{level_of(a['score'])}）。老师的意见：{a.get('comment', '')}"
        )
    return "\n\n".join(parts)


def topic_of(essay):
    """The assignment's 作文主题 (trial essays have none)."""
    topic = essay["topic"] if "topic" in essay.keys() else ""
    return "" if topic == "试用" else topic or ""


def genres_of(essay):
    return [g for g in (essay["genre"] or "").split("、") if g in GENRES and g != "其他"]


def genre_text(essay):
    """The 文体 part of the system prompt: what each chosen genre asks for, or how to go on when none is chosen."""
    chosen = genres_of(essay)
    if not chosen:
        return "老师没有指定文体：先判断这篇作文主要是哪种文体（写人、记事、写景、状物、想象……），再按那种文体的一般要求批改；不要因为文体扣分。"
    head = "" if len(chosen) == 1 else f"老师给了 {len(chosen)} 种文体，学生写其中任意一种都可以，按他实际写的那种来要求：\n"
    return head + "\n".join(f"{g}：{GENRES[g]}" for g in chosen)


def intro_of(essay):
    return essay["intro"] if "intro" in essay.keys() and essay["intro"] else ""


def grade_messages(essay):
    """essay: dict with grade, genre, requirement, min_chars, title, text. The system prompt only depends on the
    band and genre, so it is the same for a whole assignment and providers can cache it."""
    band = band_of(essay["grade"])
    system = (
        GRADE_PROMPT.replace("{band}", BANDS[band]["name"] + "\n" + BANDS[band]["text"])
        .replace("{genre}", genre_text(essay))
        .replace("{anchors}", anchors_text(band))
    )
    user = (
        f"年级：{essay['grade']}年级\n文体：{'、'.join(genres_of(essay)) or '不指定'}\n"
        f"作文主题：{topic_of(essay) or '（老师没有定主题）'}\n"
        + (f"引言（学生看到的题目说明）：{intro_of(essay)}\n" if intro_of(essay) else "")
        + f"习作重点：{essay['requirement'] or '（老师没有写具体要求）'}\n"
        + (f"字数要求：不少于 {essay['min_chars']} 字（这篇约 {char_count(essay['text'])} 字）\n" if essay["min_chars"] else "")
        + f"作文标题：{essay['title'] or '（无标题）'}\n\n作文正文：\n{essay['text']}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def check_messages(essay, marks):
    band = band_of(essay["grade"])
    # Numbered 1, 2, … over checked_marks, in text order: apply_check counts the same way.
    errors = checked_marks(marks)
    items = [{"id": i, "type": m["type"], "quote": m["quote"], "fix": m["fix"], "note": m["note"]} for i, m in enumerate(errors, 1)]
    user = (
        f"学段：{BANDS[band]['name']}\n文体：{'、'.join(genres_of(essay)) or '不指定'}\n作文主题：{topic_of(essay) or '无'}\n习作重点：{essay['requirement'] or '无'}\n\n"
        f"作文正文：\n{essay['text']}\n\n需要复核的标注：\n{json.dumps(items, ensure_ascii=False, indent=1)}"
    )
    return [{"role": "system", "content": CHECK_PROMPT}, {"role": "user", "content": user}]


# ---- cleaning the model's answer ----
def _str(value, limit):
    return re.sub(r"\s+", " ", value).strip()[:limit] if isinstance(value, str) else ""


def locate(text, quote, taken=()):
    """(start, end) of quote in text, ignoring whitespace differences; prefers an occurrence not already marked."""
    if not quote:
        return None
    spans, start = [], text.find(quote)
    while start >= 0:
        spans.append((start, start + len(quote)))
        start = text.find(quote, start + 1)
    if not spans:
        # Retry without whitespace (models often drop the line breaks or spaces inside a quote).
        squeezed = re.sub(r"\s+", "", quote)
        if not squeezed:
            return None
        index = [i for i, c in enumerate(text) if not c.isspace()]
        flat = "".join(text[i] for i in index)
        pos = flat.find(squeezed)
        while pos >= 0:
            spans.append((index[pos], index[pos + len(squeezed) - 1] + 1))
            pos = flat.find(squeezed, pos + 1)
    for span in spans:
        if span not in taken:
            return span
    return spans[0] if spans else None


def clean_review(raw, essay):
    """The model's JSON reduced to what we trust. Raises ValueError when it is unusable (missing dimensions)."""
    band = BANDS[band_of(essay["grade"])]
    text = essay["text"]
    dims_in = raw.get("dims")
    if not isinstance(dims_in, dict):
        raise ValueError("缺少评分维度 dims")
    dims = {}
    for key, label in DIMS.items():
        item = dims_in.get(key)
        if not isinstance(item, dict) or not isinstance(item.get("score"), (int, float)):
            raise ValueError(f"缺少维度 {key} 的分数")
        top = band["max"][key]
        dims[key] = {"score": max(0, min(top, round(item["score"]))), "max": top, "reason": _str(item.get("reason"), 120)}
    total = sum(d["score"] for d in dims.values())
    marks, taken, kinds = [], set(), {}
    for m in raw.get("marks") if isinstance(raw.get("marks"), list) else []:
        if not isinstance(m, dict) or m.get("type") not in MARK_TYPES:
            continue
        kind = "error" if m["type"] in ERROR_TYPES else m["type"]
        if kinds.get(kind, 0) >= MARK_LIMITS[kind]:
            continue
        quote = m.get("quote") if isinstance(m.get("quote"), str) else ""
        span = locate(text, quote.strip(), taken) if len(quote.strip()) <= 80 else None
        if not span:
            continue
        fix = _str(m.get("fix"), 120)
        if m["type"] in ERROR_TYPES and (not fix or fix == text[span[0] : span[1]]):
            continue  # an error with no correction (or the same text) is not actionable
        if not plausible_fix(m["type"], text[span[0] : span[1]], fix):
            continue
        taken.add(span)
        kinds[kind] = kinds.get(kind, 0) + 1
        marks.append(
            {"type": m["type"], "start": span[0], "end": span[1], "quote": text[span[0] : span[1]], "fix": fix, "note": _str(m.get("note"), NOTE_MAX)}
        )
    marks.sort(key=lambda m: m["start"])
    example = raw.get("example") if isinstance(raw.get("example"), dict) else {}
    example_span = locate(text, (example.get("quote") or "").strip()) if isinstance(example.get("quote"), str) else None
    lists = {
        key: [_str(v, 40) for v in raw.get(key) if isinstance(v, str) and v.strip()][:2]
        for key in ("praise", "advice")
        if isinstance(raw.get(key), list)
    }
    return {
        "dims": dims,
        "total": total,
        "level": level_of(total),
        "marks": marks[:MAX_MARKS],
        "praise": lists.get("praise", []),
        "advice": lists.get("advice", []),
        "example": (
            {"quote": text[example_span[0] : example_span[1]], "revised": _str(example.get("revised"), 600), "why": _str(example.get("why"), 200)}
            if example_span and _str(example.get("revised"), 600)
            else None
        ),
        "summary": _str(raw.get("summary"), SUMMARY_MAX),
        "teacher_note": _str(raw.get("teacher_note"), 200),
        "flags": [f for f in dict.fromkeys(raw.get("flags") or []) if f in FLAGS and f not in ("unchecked", "disagree")]
        if isinstance(raw.get("flags"), list)
        else [],
    }


PUNCT = set("，。、；：？！“”‘’「」『』（）《》〈〉【】…—～·,.;:?!\"'()<>[]-~ ")


def plausible_fix(kind, quote, fix):
    """Rules no correct correction breaks, so a wrong one never reaches a child: nothing is corrected where the
    photo could not be read (□); a 错别字 fix swaps one or two characters and keeps the length; a 标点 fix only
    changes punctuation."""
    if kind not in ERROR_TYPES:
        return True
    if "□" in quote or "□" in fix:
        return False
    if kind == "错别字":
        a, b = re.sub(r"\s+", "", quote), re.sub(r"\s+", "", fix)
        changed = sum(1 for x, y in zip(a, b) if x != y)
        return len(a) == len(b) and 1 <= changed <= 2 and not any(c in PUNCT for c in b)
    if kind == "标点":
        words = lambda t: "".join(c for c in t if c not in PUNCT and not c.isspace())
        return words(quote) == words(fix)
    return True


def checked_marks(marks):
    """The marks the checker judges, in text order (numbered 1, 2, … by check_messages and apply_check): every
    error, and every 修改建议 with an example sentence (the child is told to write it that way, so it must be right)."""
    return [m for m in marks if m["type"] in ERROR_TYPES or (m["type"] == "修改建议" and m.get("fix"))]


def parse_check(raw):
    items = raw.get("items")
    if not isinstance(items, list):
        raise ValueError("复核结果缺少 items")
    verdicts = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("ok"), bool):
            verdicts[item["id"]] = (item["ok"], _str(item.get("reason"), 120))
    level = raw.get("level") if raw.get("level") in LEVELS else None
    return verdicts, level


def apply_check(review, verdicts, level):
    """Drops the checked marks (checked_marks) the checker rejected or skipped (kept in review["rejected"] for the
    teacher) and flags a level that disagrees by two steps or more."""
    kept, rejected, n = [], [], 0
    checked = {id(m) for m in checked_marks(review["marks"])}
    for m in review["marks"]:
        if id(m) in checked:
            n += 1
            ok, reason = verdicts.get(n, (False, "复核没有确认"))
            if not ok:
                rejected.append({**m, "reason": reason})
                continue
        kept.append(m)
    review = {**review, "marks": kept, "rejected": rejected, "check_level": level}
    if level and abs(LEVELS.index(level) - LEVELS.index(review["level"])) >= 2:
        review["flags"] = review["flags"] + ["disagree"]
    return review


def error_mark_count(review):
    return sum(1 for m in review["marks"] if m["type"] in ERROR_TYPES)


# ---- what a teacher's edit may contain ----
def clean_final(edit, review, text, band_max):
    """A teacher's edited review (from the review page): same shape as the AI's, validated the same way, except that
    the teacher may set any score within the maximum and add marks anywhere in the text."""
    if not isinstance(edit, dict):
        raise ValueError("格式不正确")
    dims = {}
    for key in DIMS:
        item = (edit.get("dims") or {}).get(key) if isinstance(edit.get("dims"), dict) else None
        score = item.get("score") if isinstance(item, dict) else None
        if not isinstance(score, int) or isinstance(score, bool) or not 0 <= score <= band_max[key]:
            raise ValueError(f"{DIMS[key]}的分数要在 0—{band_max[key]} 之间")
        dims[key] = {"score": score, "max": band_max[key], "reason": review["dims"][key]["reason"]}
    marks = []
    for m in edit.get("marks") if isinstance(edit.get("marks"), list) else []:
        if not isinstance(m, dict) or m.get("type") not in MARK_TYPES:
            raise ValueError("标注类型不正确")
        start, end = m.get("start"), m.get("end")
        if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
            raise ValueError("标注位置不正确")
        marks.append(
            {"type": m["type"], "start": start, "end": end, "quote": text[start:end], "fix": _str(m.get("fix"), 120), "note": _str(m.get("note"), 60)}
        )
    marks.sort(key=lambda m: m["start"])
    total = sum(d["score"] for d in dims.values())
    lists = {
        k: [_str(v, 200) for v in edit.get(k) if isinstance(v, str) and v.strip()][:3] if isinstance(edit.get(k), list) else []
        for k in ("praise", "advice")
    }
    example = review.get("example") if edit.get("keep_example", True) else None
    return {
        **review,
        "dims": dims,
        "total": total,
        "level": level_of(total),
        "marks": marks[:60],
        "praise": lists["praise"],
        "advice": lists["advice"],
        "example": example,
        "summary": _str(edit.get("summary"), 300),
    }


# ---- photos ----
def ocr_messages(image_bytes, page, pages):
    import base64

    url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()
    note = f"这是作文的第 {page} 页，共 {pages} 页。" + ("" if page == 1 else "这一页通常没有题目，title 留空。")
    return [
        {"role": "system", "content": OCR_PROMPT},
        {"role": "user", "content": [{"type": "text", "text": note + "请誊抄这张照片。"}, {"type": "image_url", "image_url": {"url": url}}]},
    ]


def _box(value):
    if not (isinstance(value, list) and len(value) == 4 and all(isinstance(v, (int, float)) for v in value)):
        return None
    x1, y1, x2, y2 = (max(0, min(1000, round(v))) for v in value)
    return [x1, y1, x2, y2] if x1 < x2 and y1 < y2 and y2 - y1 <= 250 else None


def parse_ocr(raw):
    """One page of the ocr step's JSON → {"title", "lines": [{"text", "box" or None, "para"}]}. Raises ValueError."""
    lines = raw.get("lines")
    if not isinstance(lines, list):
        raise ValueError("识别结果缺少 lines")
    out = []
    for line in lines:
        if not isinstance(line, dict) or not isinstance(line.get("text"), str):
            continue
        text = "".join(c for c in line["text"] if c.isprintable()).strip()
        if text:
            out.append({"text": text, "box": _box(line.get("box")), "para": line.get("para") is True})
    if out and sum(1 for line in out if line["box"]) < 0.7 * len(out):
        for line in out:  # too few usable boxes: show the page without drawing on it
            line["box"] = None
    return {"title": _str(raw.get("title"), 40), "lines": out}


def assemble(pages):
    """Pages from parse_ocr → (title, text, [[{"s", "e", "b"}…] per page]): the essay text (a newline between
    paragraphs) and, for every line with a box, where its characters are in the text."""
    title = next((p["title"] for p in pages if p["title"]), "")
    text, placed = "", []
    for page in pages:
        spots = []
        for line in page["lines"]:
            if text and line["para"]:
                text += "\n"
            start = len(text)
            text += line["text"]
            if line["box"]:
                spots.append({"s": start, "e": len(text), "b": line["box"]})
        placed.append(spots)
    return title, text, placed


def remap(old, new, spots):
    """Line positions after a teacher corrected the recognised text: offsets follow the edit."""
    import difflib

    table = [None] * (len(old) + 1)
    for tag, a1, a2, b1, b2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        for k in range(a1, a2 + 1):
            table[k] = b1 + (k - a1 if tag == "equal" else (b2 - b1) * (k - a1) // max(1, a2 - a1))
    table[len(old)] = len(new)
    out = []
    for page in spots:
        lines = []
        for spot in page:
            s, e = table[spot["s"]], table[spot["e"]]
            if s is not None and e is not None and e > s:
                lines.append({**spot, "s": s, "e": e})
        out.append(lines)
    return out
