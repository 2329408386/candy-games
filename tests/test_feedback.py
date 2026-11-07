"""改进建议: only licensed users write, the workbench's back office sees every site and answers."""

import pytest
from conftest import TEACHER2_KEY, SiteClient
from test_referral import activate, make_teacher

PATH = {"chinese": "/feedback", "math": "/feedback", "english": "/feedback", "teacher": "/teacher/feedback"}


@pytest.fixture(autouse=True)
def clean(db):
    db.execute("DELETE FROM feedback")
    db.execute("DELETE FROM devices WHERE site!='teacher' AND license_id=1")  # the seed licenses allow three devices
    db.execute("DELETE FROM limits WHERE bucket LIKE '%:activate:%'")
    db.commit()


def signed_in(site):
    c = SiteClient(site)
    assert c.activate().status_code == 200
    return c


def send(c, site, content):
    return c.post(PATH[site], json={"content": content})


def admin():
    a = SiteClient("teacher")
    assert a.login().status_code in (200, 302)
    return a


@pytest.mark.parametrize("site", list(PATH))
def test_licensed_user_writes_on_every_site(site, db):
    c = signed_in(site)
    assert c.get(PATH[site]).status_code == 200
    assert send(c, site, " 希望多一些题目 ").status_code == 200
    row = db.execute("SELECT * FROM feedback").fetchone()
    assert (row["site"], row["license_id"], row["content"]) == (site, 1, "希望多一些题目")
    page = c.get(PATH[site]).get_data(as_text=True)
    assert "希望多一些题目" in page and "收到了，谢谢你。" in page


@pytest.mark.parametrize("site", list(PATH))
def test_without_license_no_way_in(site, db):
    c = SiteClient(site)
    assert c.get(PATH[site]).status_code == 302
    assert send(c, site, "x").status_code == 403
    assert not db.execute("SELECT 1 FROM feedback").fetchone()


def test_trial_teacher_cannot_write(db):
    _, key = make_teacher(db, days=7, trial=True)
    c, _ = activate(key)
    assert c.get("/teacher/feedback").status_code == 302
    assert send(c, "teacher", "x").status_code == 403
    assert "提改进建议" not in c.get("/teacher/").get_data(as_text=True)


def test_student_cannot_write_or_see_the_way_in(db, models_free_klass):
    s = models_free_klass
    assert s.get("/teacher/feedback").status_code == 302
    assert send(s, "teacher", "x").status_code == 403
    assert "提改进建议" not in s.get("/student/").get_data(as_text=True)


@pytest.fixture
def models_free_klass(db):
    from test_essay import jpost, student_client

    db.execute("UPDATE ess_classes SET archived_at=1 WHERE license_id=1 AND archived_at IS NULL")
    db.commit()
    t = signed_in("teacher")
    cid = jpost(t, "/api/classes", {"name": "建议班", "grade": 3}).get_json()["id"]
    jpost(t, f"/api/classes/{cid}/students", {"names": "王小明"})
    return student_client(db, cid)[0]


def test_ways_in_are_quiet():
    teacher = signed_in("teacher").get("/teacher/").get_data(as_text=True)
    assert 'href="/workspace/chinese/teacher/feedback"' in teacher
    game = signed_in("chinese")
    assert "/feedback" not in game.get("/").get_data(as_text=True)  # only at the bottom of 我的
    assert 'href="/feedback"' in game.get("/me").get_data(as_text=True)


def test_length_and_daily_limit(db):
    c = signed_in("chinese")
    for bad in ("", "   ", "字" * 1001, 5):
        assert c.post("/feedback", json={"content": bad}).status_code == 400
    assert c.post("/feedback", json={"content": "字" * 1000}).status_code == 200  # fits the game site's small body limit
    for _ in range(4):
        assert send(c, "chinese", "再说一条").status_code == 200
    r = send(c, "chinese", "第六条")
    assert r.status_code == 429 and "谢谢你" in r.get_json()["error"]
    assert send(signed_in("math"), "math", "另一个站不受影响").status_code == 200


def test_content_is_escaped_for_user_and_admin():
    c = signed_in("chinese")
    evil = "<script>alert(1)</script>"
    send(c, "chinese", evil)
    a = admin()
    for page in (c.get("/feedback"), a.get("/admin/feedback")):
        words = page.get_data(as_text=True)
        assert evil not in words and "&lt;script&gt;alert(1)&lt;/script&gt;" in words


def test_admin_sees_every_site_and_replies(db):
    assert SiteClient("teacher").get("/admin/feedback").status_code == 302
    assert SiteClient("teacher").post("/admin/feedback/1/reply", {"reply": "x"}).status_code == 302
    c = signed_in("math")
    send(c, "math", "数学想要口算")
    t = signed_in("teacher")
    send(t, "teacher", "想要导出成绩")
    a = admin()
    page = a.get("/admin/feedback").get_data(as_text=True)
    assert "数学想要口算" in page and "想要导出成绩" in page
    assert 'href="/math/admin/licenses/1"' in page and 'href="/workspace/chinese/admin/licenses/1"' in page
    assert ">用户建议 <span class=\"tag\">2</span>" in a.get("/admin").get_data(as_text=True)
    assert a.post("/admin/feedback/999999/reply", {"reply": "x"}).status_code == 404
    fid = db.execute("SELECT id FROM feedback WHERE site='math'").fetchone()[0]
    a.post(f"/admin/feedback/{fid}/reply", {"reply": " "})
    assert db.execute("SELECT reply FROM feedback WHERE id=?", (fid,)).fetchone()[0] is None

    # The answer: a dot on the avatar and on 我的 until the page is opened.
    assert a.post(f"/admin/feedback/{fid}/reply", {"reply": "已经在做了\n下周上线"}).status_code == 302
    assert "fb-dot" in c.get("/me").get_data(as_text=True)
    first = c.get("/feedback").get_data(as_text=True)
    assert "已经在做了\n下周上线" in first and "fb-reply new" in first
    assert "fb-dot" not in first
    assert "fb-dot" not in c.get("/me").get_data(as_text=True)
    assert "fb-reply new" not in c.get("/feedback").get_data(as_text=True)
    # Sorted out of 待回复, and an audit that is not filed under another site's license id.
    assert "数学想要口算" not in a.get("/admin/feedback").get_data(as_text=True)
    assert "数学想要口算" in a.get("/admin/feedback?view=replied").get_data(as_text=True)
    assert db.execute("SELECT license_id FROM audit WHERE action='feedback_reply'").fetchone()[0] is None
    # Changing the answer tells them again.
    a.post(f"/admin/feedback/{fid}/reply", {"reply": "上线了"})
    assert "fb-dot" in c.get("/me").get_data(as_text=True)


def test_teacher_dot_in_menu(db):
    t = signed_in("teacher")
    send(t, "teacher", "想要导出成绩")
    fid = db.execute("SELECT id FROM feedback").fetchone()[0]
    admin().post(f"/admin/feedback/{fid}/reply", {"reply": "好的"})
    assert "fb-dot" in t.get("/teacher/").get_data(as_text=True)
    assert "好的" in t.get("/teacher/feedback").get_data(as_text=True)
    assert "fb-dot" not in t.get("/teacher/").get_data(as_text=True)


def test_other_users_replies_stay_private(db):
    t1 = signed_in("teacher")
    send(t1, "teacher", "只有我能看到")
    other = SiteClient("teacher")
    assert other.activate(TEACHER2_KEY).status_code == 200
    assert "只有我能看到" not in other.get("/teacher/feedback").get_data(as_text=True)
