"""班级作文报: a teacher picks good essays of one assignment and lays them out as a class newspaper.

One paper per assignment (ess_papers), its essays in ess_paper_items in order. The editor
lists every student's newest graded essay with a few recommended (recommend()); the teacher ticks any of them and
may edit the printed text, title and comment. Saving takes a snapshot — the printed text starts as the essay with
its errors corrected (clean_text()) and 好词好句 (goods) are kept to be underlined — so later changes to a review do
not change a paper already made. Published papers are listed on the class's student pages; students of other
classes never see them.

The paper is one A3 landscape sheet: a watercolor background for its topic, a 标题, 本期主题, a one-line tagline and a
卷首语, then the essays in six columns, each with its 老师点评, and 妙笔集锦 at the end. The background and the four texts
were written in advance for every textbook unit (sites/teacher/data/paper_themes.json, pictures built by
tools/paper_art.py into static/paper-art/); theme_for() finds the unit of an assignment by its title, else falls
back to its genre, and the teacher may change all of them.
"""

import json, re, time
from pathlib import Path
from flask import abort, jsonify, redirect, render_template, url_for
from core.app import db
from . import (
    BEIJING, SUBMISSION_SQL, grading, own_assignment, payload, review_of, student, teacher,
    teacher_api, teacher_profile,
)

MAX_ITEMS = 12
MAX_TEXT = grading.MAX_TEXT
NAME_MAX, INTRO_MAX, TITLE_MAX, COMMENT_MAX = 30, 300, 30, 150
RECOMMEND_MIN, RECOMMEND_MAX, RECOMMEND_SHARE, RECOMMEND_SCORE = 3, 6, 0.2, 18
FIXED_TYPES = set(grading.ERROR_TYPES) | {grading.SOFT}  # corrections applied to the printed text
GOOD = "好词好句"
QUOTES_MAX = 10  # 妙笔集锦
HEAD_MAX, SUBJECT_MAX, TAGLINE_MAX = 12, 20, 40
THEMES = json.loads((Path(grading.PROMPTS).parent / "data" / "paper_themes.json").read_text(encoding="utf-8"))
ART_NAMES = {
    "autumn": "秋日", "spring": "春天", "festival": "节庆", "classmates": "同学校园", "plants": "植物", "animals": "小动物",
    "panda": "熊猫竹林", "fairy": "童话星空", "science": "科学太空", "landscape": "山水风景", "books": "书香",
    "letters": "书信", "play": "活动游戏", "toys": "玩具", "growth": "成长阳光", "colorful": "缤纷",
}
ACCENT = {  # the title color of each background
    "autumn": "#9a5b1e", "spring": "#3f7a4a", "festival": "#b0322a", "classmates": "#2f5f8a", "plants": "#3f7a3a",
    "animals": "#8a5a2a", "panda": "#2f6a3a", "fairy": "#6a4a9a", "science": "#2f5f8a", "landscape": "#2f6a5a",
    "books": "#7a4a2a", "letters": "#a5483c", "play": "#c0503a", "toys": "#b0603a", "growth": "#b06a1a", "colorful": "#7a4a8a",
}
KEYWORDS = (  # topic words of an assignment not taken from a unit → background set
    ("熊猫", "panda"), ("节", "festival"), ("年", "festival"), ("俗", "festival"), ("秋", "autumn"), ("春", "spring"),
    ("植物", "plants"), ("花", "plants"), ("树", "plants"), ("动物", "animals"), ("猫", "animals"), ("狗", "animals"),
    ("童话", "fairy"), ("想象", "fairy"), ("假如", "fairy"), ("科学", "science"), ("实验", "science"), ("未来", "science"),
    ("信", "letters"), ("读", "books"), ("书", "books"), ("游戏", "play"), ("活动", "play"), ("比赛", "play"),
    ("玩具", "toys"), ("景", "landscape"), ("游", "landscape"), ("家乡", "landscape"), ("同学", "classmates"),
    ("朋友", "classmates"), ("老师", "classmates"), ("妈妈", "growth"), ("爸爸", "growth"), ("成长", "growth"),
)


def _units():
    from . import UNITS

    return UNITS


def art_sets():
    """{art: the first theme key using it} — the 背景图案 a teacher may switch to."""
    out = {}
    for key, v in THEMES.items():
        out.setdefault(v["art"], key)
    return out


def theme_for(assignment):
    """(theme key, theme) for an assignment: its unit (title = 单元主题, the class's grade first), else a background
    from a topic word with the texts of its genre, else its genre, else default."""
    units = _units()
    title = (assignment["title"] or "").strip()
    grades = [str(assignment["grade"])] + [g for g in units if g != str(assignment["grade"])]
    for g in grades:
        for term, rows in units.get(g, {}).items():
            for u in rows:
                if u["topic"] == title:
                    key = f"{g}-{term}-{u['unit']}"
                    if key in THEMES:
                        return key, THEMES[key]
    genres = [x for x in re.split(r"[、，,\s]+", assignment["genre"] or "") if x]
    base_key = next((f"genre:{x}" for x in genres if f"genre:{x}" in THEMES), "default")
    for word, art in KEYWORDS:
        if word in title:
            key = art_sets()[art]
            theme = dict(THEMES[base_key], art=art)
            theme["title"] = title[:HEAD_MAX]
            return key, theme
    theme = dict(THEMES[base_key])
    if base_key != "default" or title:
        theme["title"] = title[:HEAD_MAX] or theme["title"]
    return base_key, theme


ART_DIR = Path(grading.PROMPTS).parent / "static" / "paper-art"
# 3-上册-第一单元 → its file name, which carries a content hash (tools/paper_art.py, tools/assets.py).
ART_FILES = {p.name.rsplit(".", 2)[0]: p.name for p in ART_DIR.glob("*.svg")}


def art_file(key):
    return "paper-art/" + ART_FILES[key.replace(":", "-")]


def clean_text(text, marks):
    """The essay with its corrections applied (errors, 书写提醒 incl. pinyin) and a new paragraph where 分段 says so.
    Places with 「□」 (unreadable in the photo) are left as written."""
    edits = []
    for m in marks:
        start, end = m.get("start"), m.get("end")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start <= end <= len(text):
            continue
        if m.get("type") == "分段":
            if 0 < start and text[start - 1] != "\n":
                edits.append((start, start, "\n"))
        elif m.get("type") in FIXED_TYPES and m.get("fix") and "□" not in text[start:end] + m["fix"] and end > start:
            edits.append((start, end, m["fix"]))
    out, taken = text, []
    for start, end, fix in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        if any(start < b and a < end or (start == end and a < start < b) for a, b in taken):
            continue  # overlapping corrections: keep the later one only
        out = out[:start] + fix + out[end:]
        taken.append((start, end))
    return "\n".join(line.strip() for line in grading.clean_text(out).split("\n"))


def goods_of(text, marks):
    """好词好句 of a review: [{quote, note}] in reading order."""
    out = []
    for m in sorted(marks, key=lambda m: m.get("start") or 0):
        if m.get("type") == GOOD and isinstance(m.get("start"), int) and isinstance(m.get("end"), int):
            quote = text[m["start"]:m["end"]].strip()
            if quote and quote not in [g["quote"] for g in out]:
                out.append({"quote": quote, "note": (m.get("note") or "")[:40]})
    return out


def first_sentences(summary, low=14):
    """The opening of the 总评 (its praise comes first), as the default 老师点评."""
    parts = re.findall(r"[^。！？!?]*[。！？!?]+|[^。！？!?]+$", summary or "")
    out = ""
    for p in parts:
        out += p.strip()
        if len(out) >= low:
            break
    return out[:COMMENT_MAX]


def candidates(assignment):
    """Every student of the class with their newest graded essay (as 批量导出 takes it), best first; ungraded
    students last with sid None."""
    d = db()
    students = d.execute(
        "SELECT * FROM ess_students WHERE class_id=? AND removed_at IS NULL ORDER BY id", (assignment["class_id"],)
    ).fetchall()
    rows, waiting = [], []
    for st in students:
        sub = d.execute(
            SUBMISSION_SQL + " WHERE s.assignment_id=? AND s.student_id=? AND s.status IN ('review','published')"
            " ORDER BY s.attempt DESC LIMIT 1",
            (assignment["id"], st["id"]),
        ).fetchone()
        review = sub and review_of(sub["id"])
        final = review and (review["final"] or review["ai"])
        if not final:
            waiting.append({"sid": None, "student": st["name"]})
            continue
        marks = final.get("marks") or []
        goods = goods_of(sub["text"], marks)
        rows.append({
            "sid": sub["id"],
            "student": st["name"],
            "total": final.get("total") or 0,
            "level": grading.level_of(final.get("total") or 0),
            "goods": len(goods),
            "chars": len(sub["text"]),
            "title": (sub["title"] or assignment["title"])[:TITLE_MAX],
            "text": clean_text(sub["text"], marks)[:MAX_TEXT],
            "comment": first_sentences(final.get("summary")),
        })
    rows.sort(key=lambda r: (-r["total"], -r["goods"], -r["chars"]))
    return rows, waiting


def recommend(rows, class_size):
    """The sids to suggest: the best 20 % of the class (3 to 6) of those at 良 or better; when nobody is, the best 3."""
    k = min(RECOMMEND_MAX, max(RECOMMEND_MIN, round(class_size * RECOMMEND_SHARE)))
    good = [r for r in rows if r["total"] >= RECOMMEND_SCORE][:k]
    return [r["sid"] for r in (good or rows[:RECOMMEND_MIN])]


def reason(row):
    parts = [row["level"]]
    if row["goods"]:
        parts.append(f"好词好句 {row['goods']} 处")
    return " · ".join(parts)


def paper_of_assignment(aid):
    return db().execute("SELECT * FROM ess_papers WHERE assignment_id=?", (aid,)).fetchone()


def items_of(pid):
    return db().execute(
        "SELECT i.*,st.name student,s.student_id FROM ess_paper_items i JOIN ess_submissions s ON s.id=i.submission_id"
        " JOIN ess_students st ON st.id=s.student_id WHERE i.paper_id=? ORDER BY i.position",
        (pid,),
    ).fetchall()


def next_issue(class_id, aid):
    row = db().execute(
        "SELECT max(p.issue) FROM ess_papers p JOIN ess_assignments a ON a.id=p.assignment_id"
        " WHERE a.class_id=? AND a.deleted_at IS NULL AND p.assignment_id!=?",
        (class_id, aid),
    ).fetchone()
    return (row[0] or 0) + 1


def class_papers(class_id, published_only=False):
    return db().execute(
        "SELECT p.*,a.title assignment_title,(SELECT count(*) FROM ess_paper_items i WHERE i.paper_id=p.id) items"
        " FROM ess_papers p JOIN ess_assignments a ON a.id=p.assignment_id WHERE a.class_id=? AND a.deleted_at IS NULL"
        + (" AND p.published_at IS NOT NULL" if published_only else "")
        + " ORDER BY p.issue DESC,p.id DESC",
        (class_id,),
    ).fetchall()


def multiline(data, key, limit, label, required=False):
    """A text with paragraphs (the printed essay, the 卷首语): printable characters, at most one blank line."""
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{label}格式不正确")
    value = "".join(c for c in value.replace("\r\n", "\n").replace("\r", "\n") if c == "\n" or c.isprintable())
    value = re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in value.split("\n"))).strip()
    if required and not value:
        raise ValueError(f"请填写{label}")
    if len(value) > limit:
        raise ValueError(f"{label}最多 {limit} 个字")
    return value


def oneline(value, limit, label, required=False):
    if not isinstance(value, str):
        raise ValueError(f"{label}格式不正确")
    value = " ".join("".join(c for c in value if c.isprintable()).split())
    if required and not value:
        raise ValueError(f"请填写{label}")
    if len(value) > limit:
        raise ValueError(f"{label}最多 {limit} 个字")
    return value


def paragraphs(text, goods):
    """The printed essay as paragraphs of runs [(text, note or None)]: each 好词好句 found in the text is a run with
    its note (underlined); one the teacher edited away is simply not marked."""
    spans = []
    for g in goods:
        at = text.find(g["quote"])
        while at >= 0 and any(at < b and a < at + len(g["quote"]) for a, b, _ in spans):
            at = text.find(g["quote"], at + 1)
        if at >= 0:
            spans.append((at, at + len(g["quote"]), g.get("note") or ""))
    spans.sort()
    out, para, pos = [], [], 0

    def push(chunk, note):
        nonlocal para
        pieces = chunk.split("\n")
        for i, piece in enumerate(pieces):
            if i:
                if para:
                    out.append(para)
                para = []
            if piece:
                para.append((piece, note))

    for a, b, note in spans:
        push(text[pos:a], None)
        push(text[a:b], note)
        pos = b
    push(text[pos:], None)
    if para:
        out.append(para)
    return out


def month_label(ts):
    t = time.gmtime(ts + BEIJING)
    return f"{t.tm_year}年{t.tm_mon}月"


def render_paper(paper, assignment, for_teacher):
    items = []
    quotes = []
    for r in items_of(paper["id"]):
        goods = json.loads(r["goods"] or "[]")
        review = review_of(r["submission_id"])
        final = review and (review["final"] or review["ai"])
        excellent = bool(final) and grading.level_of(final.get("total") or 0) == "优"
        items.append({
            "title": r["title"],
            "student": r["student"],
            "paragraphs": paragraphs(r["text"], goods),
            "comment": r["comment"],
            "excellent": excellent,
            "long": len(r["text"]) > 700,
        })
        for g in goods:
            if 4 <= len(g["quote"]) <= 60 and g["quote"] in r["text"]:
                quotes.append({"quote": g["quote"], "student": r["student"]})
    # 妙笔集锦: one or two per student, in paper order, so as many children as possible are quoted
    picked, per = [], {}
    for rnd in (1, 2):
        for q in quotes:
            if len(picked) < QUOTES_MAX and per.get(q["student"], 0) < rnd and q not in picked:
                picked.append(q)
                per[q["student"]] = per.get(q["student"], 0) + 1
    profile = teacher_profile(assignment["license_id"])
    key, theme = theme_for(assignment)
    art = paper["art"] if paper["art"] in THEMES else key
    return render_template(
        "paper.html",
        paper=paper,
        assignment=assignment,
        items=items,
        quotes=picked,
        head={
            "title": paper["title"] or theme["title"],
            "subject": paper["subject"] or theme["subject"],
            "tagline": paper["tagline"] or theme["tagline"],
            "intro": paper["intro"] or theme["intro"],
            "art": art_file(art),
            "accent": ACCENT[THEMES[art]["art"]],
        },
        authors=[it["student"] for it in items],
        editor=profile["nickname"] or "",
        day=month_label(paper["published_at"] or paper["updated_at"]),
        for_teacher=for_teacher,
    )


def register(app):
    @app.get("/teacher/essay/assignments/<int:aid>/paper")
    def paper_edit(aid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        assignment = own_assignment(aid, row["id"])
        rows, waiting = candidates(assignment)
        class_size = len(rows) + len(waiting)
        recommended = recommend(rows, class_size)
        for r in rows:
            r["recommended"] = r["sid"] in recommended
            r["reason"] = reason(r)
        paper = paper_of_assignment(aid)
        key, theme = theme_for(assignment)
        sets = art_sets()
        arts = [{"key": key, "label": f"本单元专属（{ART_NAMES[THEMES[key]['art']]}）"}] + [
            {"key": k, "label": ART_NAMES[a]} for a, k in sets.items() if k != key
        ]
        for a in arts:
            a["src"] = url_for("static", filename=art_file(a["key"]))
        selected = []
        if paper:
            for it in items_of(paper["id"]):
                selected.append({"sid": it["submission_id"], "title": it["title"], "text": it["text"], "comment": it["comment"]})
                if not any(r["sid"] == it["submission_id"] for r in rows):  # an older attempt kept in the paper
                    rows.append({"sid": it["submission_id"], "student": it["student"], "total": None, "level": "", "goods": 0,
                                 "chars": len(it["text"]), "title": it["title"], "text": it["text"], "comment": it["comment"],
                                 "recommended": False, "reason": "已刊登的旧稿"})
        else:
            selected = [{"sid": r["sid"], "title": r["title"], "text": r["text"], "comment": r["comment"]} for r in rows if r["recommended"]]
        return render_template(
            "paper_edit.html",
            assignment=assignment,
            paper=paper,
            page_data={
                "rows": rows,
                "waiting": [w["student"] for w in waiting],
                "selected": selected,
                "name": paper["name"] if paper else f"{assignment['class_name']}作文报",
                "issue": paper["issue"] if paper else next_issue(assignment["class_id"], aid),
                "head": {
                    "title": (paper and paper["title"]) or theme["title"],
                    "subject": (paper and paper["subject"]) or theme["subject"],
                    "tagline": (paper and paper["tagline"]) or theme["tagline"],
                    "intro": (paper and paper["intro"]) or theme["intro"],
                    "art": paper["art"] if paper and paper["art"] in THEMES else key,
                },
                "arts": arts,
                "max": MAX_ITEMS,
                "limits": {"name": NAME_MAX, "intro": INTRO_MAX, "title": TITLE_MAX, "comment": COMMENT_MAX, "text": MAX_TEXT,
                           "head": HEAD_MAX, "subject": SUBJECT_MAX, "tagline": TAGLINE_MAX},
                "save": url_for("paper_save", aid=aid),
            },
        )

    @app.post("/api/assignments/<int:aid>/paper")
    @teacher_api
    def paper_save(row, aid):
        assignment = own_assignment(aid, row["id"])
        data = payload()
        name = oneline(data.get("name", ""), NAME_MAX, "报名", required=True)
        issue = data.get("issue")
        if isinstance(issue, str) and issue.strip().isdigit():
            issue = int(issue)
        if not isinstance(issue, int) or isinstance(issue, bool) or not 1 <= issue <= 999:
            raise ValueError("期号要在 1—999 之间")
        intro = multiline(data, "intro", INTRO_MAX, "卷首语")
        head = oneline(data.get("title", ""), HEAD_MAX, "标题", required=True)
        subject = oneline(data.get("subject", ""), SUBJECT_MAX, "本期主题")
        tagline = oneline(data.get("tagline", ""), TAGLINE_MAX, "点题的一句话")
        art = data.get("art")
        if art not in THEMES:
            raise ValueError("背景图案不对，请刷新页面再试")
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("请至少选一篇作文")
        if len(items) > MAX_ITEMS:
            raise ValueError(f"一期最多刊登 {MAX_ITEMS} 篇")
        d = db()
        clean, students = [], set()
        for it in items:
            if not isinstance(it, dict) or not isinstance(it.get("sid"), int) or isinstance(it.get("sid"), bool):
                raise ValueError("作文列表不对，请刷新页面再试")
            sub = d.execute(
                "SELECT * FROM ess_submissions WHERE id=? AND assignment_id=? AND status IN ('review','published')",
                (it["sid"], aid),
            ).fetchone()
            review = sub and review_of(sub["id"])
            final = review and (review["final"] or review["ai"])
            if not final:
                raise ValueError("有一篇作文不是这次作业的，或者还没批改完，请刷新页面再试")
            if sub["student_id"] in students:
                raise ValueError("同一位同学只能刊登一篇")
            students.add(sub["student_id"])
            clean.append((
                sub["id"],
                oneline(it.get("title", ""), TITLE_MAX, "题目", required=True),
                multiline(it, "text", MAX_TEXT, "刊登文字", required=True),
                multiline(it, "comment", COMMENT_MAX, "老师点评"),
                json.dumps(goods_of(sub["text"], final.get("marks") or []), ensure_ascii=False),
            ))
        now = int(time.time())
        paper = paper_of_assignment(aid)
        if paper:
            pid = paper["id"]
            d.execute(
                "UPDATE ess_papers SET name=?,issue=?,intro=?,title=?,subject=?,tagline=?,art=?,updated_at=? WHERE id=?",
                (name, issue, intro, head, subject, tagline, art, now, pid),
            )
            d.execute("DELETE FROM ess_paper_items WHERE paper_id=?", (pid,))
        else:
            pid = d.execute(
                "INSERT INTO ess_papers(assignment_id,name,issue,intro,title,subject,tagline,art,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (aid, name, issue, intro, head, subject, tagline, art, now, now),
            ).lastrowid
        d.executemany(
            "INSERT INTO ess_paper_items(paper_id,submission_id,position,title,text,comment,goods) VALUES(?,?,?,?,?,?,?)",
            [(pid, sid, k, title, text, comment, goods) for k, (sid, title, text, comment, goods) in enumerate(clean)],
        )
        return jsonify(ok=True, url=url_for("paper_view", pid=pid))

    def own_paper(pid, lid):
        paper = db().execute("SELECT * FROM ess_papers WHERE id=?", (pid,)).fetchone()
        if not paper:
            abort(404)
        return paper, own_assignment(paper["assignment_id"], lid)

    @app.get("/teacher/essay/papers/<int:pid>")
    def paper_view(pid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        paper, assignment = own_paper(pid, row["id"])
        return render_paper(paper, dict(assignment, license_id=row["id"]), for_teacher=True)

    @app.post("/api/papers/<int:pid>/<any(publish,unpublish):action>")
    @teacher_api
    def paper_publish(row, pid, action):
        own_paper(pid, row["id"])
        db().execute("UPDATE ess_papers SET published_at=? WHERE id=?", (int(time.time()) if action == "publish" else None, pid))
        return jsonify(ok=True)

    @app.post("/api/papers/<int:pid>/delete")
    @teacher_api
    def paper_delete(row, pid):
        paper, _ = own_paper(pid, row["id"])
        db().execute("DELETE FROM ess_paper_items WHERE paper_id=?", (pid,))
        db().execute("DELETE FROM ess_papers WHERE id=?", (pid,))
        return jsonify(ok=True, url=url_for("assignment_page", aid=paper["assignment_id"]))

    @app.get("/student/papers/<int:pid>")
    def student_paper(pid):
        me = student()
        if not me:
            return redirect(url_for("student_home"))
        paper = db().execute(
            "SELECT p.* FROM ess_papers p JOIN ess_assignments a ON a.id=p.assignment_id"
            " WHERE p.id=? AND a.class_id=? AND a.deleted_at IS NULL AND p.published_at IS NOT NULL",
            (pid, me["class_id"]),
        ).fetchone()
        if not paper:
            abort(404)
        assignment = db().execute(
            "SELECT a.*,c.name class_name,c.grade,c.license_id FROM ess_assignments a JOIN ess_classes c ON c.id=a.class_id WHERE a.id=?",
            (paper["assignment_id"],),
        ).fetchone()
        return render_paper(paper, assignment, for_teacher=False)
