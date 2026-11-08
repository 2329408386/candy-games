"""班级作文报: recommendations, the corrected printed text, saving, publishing and who may see it."""

import json, re, time
import pytest
from conftest import TEACHER2_KEY, SiteClient
from core.essay import paper
from test_essay import jpost, student_client, teacher_client

TEXT = "今天是中秋节，我们一家人坐在院子里赏月。月亮圆圆的，像一个大玉盘。\n\n我吃了月饼，心里高心极了。"


def marks_for(text):
    def at(q):
        i = text.index(q)
        return i, i + len(q)

    s1, e1 = at("高心")
    s2, e2 = at("像一个大玉盘")
    return [
        {"type": "错别字", "start": s1, "end": e1, "fix": "高兴", "note": ""},
        {"type": "好词好句", "start": s2, "end": e2, "fix": "", "note": "比喻真形象"},
    ]


@pytest.fixture
def paper_class(db):
    """A class of 8 students with graded essays scoring 23, 21, 20, 19, 17, 16, 15 and one not graded yet."""
    db.execute("UPDATE ess_classes SET archived_at=1 WHERE license_id=1 AND archived_at IS NULL")
    db.execute("DELETE FROM limits WHERE bucket LIKE '%:activate:%'")
    db.commit()
    t = teacher_client()
    cid = jpost(t, "/api/classes", {"name": "三年级一班", "grade": 3}).get_json()["id"]
    names = ["甲", "乙", "丙", "丁", "戊", "己", "庚", "辛"]
    jpost(t, f"/api/classes/{cid}/students", {"names": "\n".join(names)})
    aid = jpost(t, f"/api/classes/{cid}/assignments", {"title": "中华传统节日", "genre": "记事"}).get_json()["id"]
    students = {r["name"]: r["id"] for r in db.execute("SELECT id,name FROM ess_students WHERE class_id=?", (cid,))}
    sids = {}
    now = int(time.time())
    for name, total in zip(names, (23, 21, 20, 19, 17, 16, 15, None)):
        status = "queued" if total is None else "published"
        sid = db.execute(
            "INSERT INTO ess_submissions(assignment_id,student_id,attempt,source,title,text,status,created_at) VALUES(?,?,1,'text',?,?,?,?)",
            (aid, students[name], f"{name}的中秋", TEXT, status, now),
        ).lastrowid
        sids[name] = sid
        if total is not None:
            review = {"total": total, "marks": marks_for(TEXT), "summary": "你把赏月写得很美。下次多写写心情。"}
            db.execute(
                "INSERT INTO ess_reviews(submission_id,ai,model,prompt_version,created_at) VALUES(?,?,'m','v',?)",
                (sid, json.dumps(review, ensure_ascii=False), now),
            )
    db.commit()
    return t, cid, aid, sids


def test_clean_text_and_goods():
    marks = marks_for(TEXT) + [{"type": "标点", "start": 0, "end": 2, "fix": "□天", "note": ""}]
    out = paper.clean_text(TEXT, marks)
    assert "高兴极了" in out and "高心" not in out and out.startswith("今天"), "□ is never written in"
    assert paper.goods_of(TEXT, marks) == [{"quote": "像一个大玉盘", "note": "比喻真形象"}]
    paras = paper.paragraphs(out, paper.goods_of(TEXT, marks))
    assert len(paras) == 2 and ("像一个大玉盘", "比喻真形象") in paras[0]
    split = paper.clean_text("我们去公园。公园里花开了。", [{"type": "分段", "start": 6, "end": 8, "fix": ""}])
    assert split == "我们去公园。\n公园里花开了。"


def test_recommend():
    rows = [{"sid": i, "total": t} for i, t in enumerate((23, 21, 20, 19, 17))]
    assert paper.recommend(rows, 10) == [0, 1, 2], "at least three"
    assert paper.recommend(rows, 40) == [0, 1, 2, 3], "only 良 or better"
    assert paper.recommend([{"sid": i, "total": 15} for i in range(5)], 5) == [0, 1, 2], "nobody at 良: the best three"
    assert len(paper.recommend([{"sid": i, "total": 22} for i in range(50)], 50)) == 6


def edit_data(client, aid):
    html = client.get(f"/teacher/essay/assignments/{aid}/paper").get_data(as_text=True)
    return json.loads(re.search(r"window.PAPER=(.*?);</script>", html).group(1))


def test_make_publish_and_read(paper_class, db):
    t, cid, aid, sids = paper_class
    assert "做作文报" in t.get(f"/teacher/essay/assignments/{aid}").get_data(as_text=True)
    data = edit_data(t, aid)
    top3 = [sids["甲"], sids["乙"], sids["丙"]]  # 8 students: 20 % rounds to 2, at least 3
    assert [x["sid"] for x in data["selected"]] == top3
    assert [r["sid"] for r in data["rows"] if r["recommended"]] == top3
    assert [r["student"] for r in data["rows"]] == ["甲", "乙", "丙", "丁", "戊", "己", "庚"]
    assert data["waiting"] == ["辛"] and data["issue"] == 1 and data["name"] == "三年级一班作文报"
    first = data["selected"][0]
    assert "高兴" in first["text"] and first["comment"] == "你把赏月写得很美。下次多写写心情。", "a short first sentence takes the next one"

    items = [dict(first, comment="写得真好！"), {**next(r for r in data["rows"] if r["sid"] == sids["庚"])}]
    r = jpost(t, f"/api/assignments/{aid}/paper", {"name": "小荷作文报", "issue": 3, "intro": "第一段\n\n第二段", "items": items})
    assert r.status_code == 200, r.get_json()
    pid = int(r.get_json()["url"].rsplit("/", 1)[1])
    page = t.get(f"/teacher/essay/papers/{pid}").get_data(as_text=True)
    assert "小荷作文报" in page and "头条" in page and "写得真好！" in page and "妙笔集锦" in page and "★ 优秀习作" in page
    assert '<span class="good" title="比喻真形象">像一个大玉盘</span>' in page
    assert edit_data(t, aid)["selected"][0]["comment"] == "写得真好！", "the saved paper is loaded again"

    # Bad saves: an essay of another assignment, the same student twice, nothing at all.
    other = jpost(t, f"/api/classes/{cid}/assignments", {"title": "别的作业", "genre": "记事"}).get_json()["id"]
    assert jpost(t, f"/api/assignments/{other}/paper", {"name": "x", "issue": 1, "items": [first]}).status_code == 400
    assert jpost(t, f"/api/assignments/{aid}/paper", {"name": "x", "issue": 1, "items": [first, first]}).status_code == 400
    assert jpost(t, f"/api/assignments/{aid}/paper", {"name": "x", "issue": 1, "items": []}).status_code == 400
    waiting = {"sid": sids["辛"], "title": "x", "text": "x", "comment": ""}
    assert jpost(t, f"/api/assignments/{aid}/paper", {"name": "x", "issue": 1, "items": [waiting]}).status_code == 400

    # Students see it only once it is published, and only in their own class.
    s, _ = student_client(db, cid, "乙")
    assert s.get(f"/student/papers/{pid}").status_code == 404
    assert "班级作文报" not in s.get("/student/").get_data(as_text=True)
    assert jpost(t, f"/api/papers/{pid}/publish").status_code == 200
    home = s.get("/student/").get_data(as_text=True)
    assert "班级作文报" in home and "小荷作文报" in home
    view = s.get(f"/student/papers/{pid}").get_data(as_text=True)
    assert "甲" in view and "老师点评" in view and "AI" not in view and "发布给学生" not in view
    assert "班级作文报" in t.get(f"/teacher/classes/{cid}").get_data(as_text=True)

    # Another teacher cannot open or change it; another class's student cannot read it.
    t2 = teacher_client(TEACHER2_KEY)
    assert t2.get(f"/teacher/essay/papers/{pid}").status_code == 404
    assert jpost(t2, f"/api/papers/{pid}/unpublish").status_code == 404
    db.execute("UPDATE ess_classes SET archived_at=1 WHERE license_id=2 AND archived_at IS NULL")
    db.commit()
    other_cid = jpost(t2, "/api/classes", {"name": "三年级二班", "grade": 3}).get_json()["id"]
    jpost(t2, f"/api/classes/{other_cid}/students", {"names": "外班"})
    outsider, _ = student_client(db, other_cid, "外班")
    assert outsider.get(f"/student/papers/{pid}").status_code == 404
    assert SiteClient("teacher").get(f"/student/papers/{pid}").status_code == 302

    # Unpublish hides it again; delete removes it and the assignment offers a new one.
    assert jpost(t, f"/api/papers/{pid}/unpublish").status_code == 200
    assert s.get(f"/student/papers/{pid}").status_code == 404
    assert jpost(t, f"/api/papers/{pid}/delete").status_code == 200
    assert not db.execute("SELECT 1 FROM ess_paper_items WHERE paper_id=?", (pid,)).fetchone()
    assert "做作文报" in t.get(f"/teacher/essay/assignments/{aid}").get_data(as_text=True)


def test_units_has_third_grade_unit_four():
    from core.essay import UNITS

    unit = next(u for u in UNITS["3"]["下册"] if u["unit"] == "第四单元")
    assert unit["topic"] == "中华传统节日" and unit["genres"] == ["记事"] and "传统节日" in unit["intro"]
