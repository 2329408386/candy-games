"""Builds sites/teacher/data/units.json — the 习作 / 写话 of each unit of the 统编版 primary-school textbooks — from
docs/chinese/小学语文各册习作与写话要求汇总.md. 布置作文 uses it to fill in the 主题, 文体 and 习作要求 when the
teacher picks 年级 → 上下册 → 单元.

    python tools/units.py           rebuild the JSON
    python tools/units.py --check   fail when the JSON is out of date (run by tests)

Output: {"<grade>": {"上册": [{"unit", "topic", "genre", "requirement"}], "下册": [...]}}; a term without 习作 has
an empty list. genre is one of grading.GENRES (the textbook's own wording is mapped below); requirement is the
summary of the task plus the notes under the term's table that belong to that unit (by 第N单元 or by 第N页).
"""

import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "chinese" / "小学语文各册习作与写话要求汇总.md"
OUT = ROOT / "sites" / "teacher" / "data" / "units.json"
GRADES = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
REQUIREMENT_MAX = 600

# The textbook's 文体 → the workbench's genre (grading.GENRES); first match wins.
GENRE_RULES = (
    ("日记", "日记"),
    ("书信", "书信"),
    ("读后感", "读后感"),
    ("看图", None),  # 看图写话 in grade 2, otherwise by what is written (想象 / 记事)
    ("多图", None),
    ("写景", "写景"),
    ("游记", "写景"),
    ("地点", "写景"),
    ("写人", "写人"),
    ("想象", "想象"),
    ("童话", "想象"),
    ("故事", "想象"),
    ("科幻", "想象"),
    ("续写", "想象"),
    ("改编", "想象"),
    ("状物", "状物"),
    ("观察", "状物"),
    ("动物", "状物"),
    ("事物介绍", "状物"),
    ("说明", "状物"),
    ("叙事", "记事"),
    ("记叙", "记事"),
    ("经历", "记事"),
    ("活动", "记事"),
    ("实验", "记事"),
)


def genre_of(label, grade):
    for key, genre in GENRE_RULES:
        if key in label:
            if genre is None:
                return "看图写话" if grade <= 2 else "想象" if "想象" in label else "记事"
            return genre
    return "其他"


def unit_number(cell):
    cell = cell.strip()
    if cell.isdigit():
        return int(cell)
    m = re.match(r"第(.)单元", cell)
    return NUMBERS.get(m[1]) if m else None


def clip(text, limit):
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(c) for c in "。；！？\n")
    return cut[: end + 1] if end > limit // 2 else cut


def build():
    out, grade, term, rows = {}, None, None, None
    for raw in SOURCE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        m = re.match(r"## (.)年级(上|下)册", line)
        if m:
            grade, term = GRADES[m[1]], m[2] + "册"
            rows = out.setdefault(str(grade), {}).setdefault(term, [])
            continue
        if rows is None or not line:
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 6 or cells[0] in ("单元", "单元／栏目位置") or set(cells[0]) <= set("-:"):
                continue
            unit, topic, label, _, summary, page = cells[:6]
            n = unit_number(unit)
            rows.append({
                "unit": f"第{n}单元",
                "n": n,
                "page": page,
                "topic": topic,
                "genre": genre_of(label, grade),
                "requirement": "\n".join(s.strip() for s in summary.split("<br>") if s.strip()),
            })
            continue
        # A note under the table: belongs to the unit it names, or to the row on the page it names.
        m = re.match(r"第(.)单元", line)
        p = re.search(r"第(\d+)页", line)
        target = None
        if m and NUMBERS.get(m[1]):
            target = next((r for r in rows if r["n"] == NUMBERS[m[1]]), None)
        elif p:
            target = next((r for r in rows if r["page"] == p[1]), None)
        if target:
            note = re.sub(r"^第.单元", "", line) if m else line
            target["requirement"] += "\n" + note.lstrip("：:")
    for terms in out.values():
        for rows in terms.values():
            for r in rows:
                r["requirement"] = clip(r["requirement"], REQUIREMENT_MAX)
                del r["n"], r["page"]
    return out


def render():
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            sys.exit("units.json 过期了：运行 python tools/units.py")
        print("units.json ok")
    else:
        OUT.parent.mkdir(exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
