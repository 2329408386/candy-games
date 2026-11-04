"""Builds sites/teacher/data/units.json — the 习作 / 写话 of each unit of the 统编版 primary-school textbooks — from
docs/chinese/习作单元.md. 布置作文 uses it to fill in the 主题, 文体, 引言 and 习作重点 when the teacher picks
年级 → 上下册 → 单元.

    python tools/units.py           rebuild the JSON
    python tools/units.py --check   fail when the JSON is out of date or the source is malformed (run by tests)

Source: "## <N>年级<上|下>册", then per unit "### 第<N>单元" followed by the lines 主题：…, 文体：… (several joined
with 、, or empty for 不指定), 引言：… and 习作重点： with its numbered lines under it.
Output: {"<grade>": {"上册": [{"unit", "topic", "genres", "intro", "requirement"}], "下册": [...]}}; a term
without 习作 has an empty list. genres are taken from grading.CHOICES.
"""

import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "chinese" / "习作单元.md"
OUT = ROOT / "sites" / "teacher" / "data" / "units.json"
GENRES = ROOT / "sites" / "teacher" / "prompts" / "genres.json"
GRADES = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6}
INTRO_MAX, REQUIREMENT_MAX = 300, 800  # core/essay INTRO_MAX / REQUIREMENT_MAX


def build():
    choices = [g for g in json.loads(GENRES.read_text(encoding="utf-8")) if g != "其他"]
    out = {str(g): {"上册": [], "下册": []} for g in range(1, 7)}
    rows, unit, field = None, None, None
    for n, raw in enumerate(SOURCE.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        m = re.fullmatch(r"## (.)年级(上|下)册", line)
        if m:
            rows, unit = out[str(GRADES[m[1]])][m[2] + "册"], None
            continue
        m = re.fullmatch(r"### (第.单元)", line)
        if m and rows is not None:
            unit = {"unit": m[1], "topic": "", "genres": [], "intro": "", "requirement": ""}
            rows.append(unit)
            field = None
            continue
        if unit is None or not line:
            continue
        m = re.match(r"(主题|文体|引言|习作重点)：(.*)", line)
        if m:
            key, value = m[1], m[2].strip()
            if key == "主题":
                unit["topic"] = value
            elif key == "文体":
                genres = [g for g in re.split(r"[、，,\s]+", value) if g]
                bad = [g for g in genres if g not in choices]
                if bad:
                    sys.exit(f"{SOURCE.name}:{n}: 不认识的文体 {bad}，可选：{'、'.join(choices)}")
                unit["genres"] = genres
            elif key == "引言":
                unit["intro"] = value
            field = key
            if key == "习作重点" and value:
                unit["requirement"] = value
            continue
        if field == "习作重点":
            unit["requirement"] += ("\n" if unit["requirement"] else "") + line
        else:
            sys.exit(f"{SOURCE.name}:{n}: 这一行不属于任何一项：{line}")
    for terms in out.values():
        for units in terms.values():
            for u in units:
                where = f"{u['unit']}《{u['topic']}》"
                if not (u["topic"] and u["intro"] and u["requirement"]):
                    sys.exit(f"{where} 缺少主题、引言或习作重点")
                if len(u["topic"]) > 40 or len(u["intro"]) > INTRO_MAX or len(u["requirement"]) > REQUIREMENT_MAX:
                    sys.exit(f"{where} 太长：主题 ≤40、引言 ≤{INTRO_MAX}、习作重点 ≤{REQUIREMENT_MAX} 字")
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
