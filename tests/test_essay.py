"""语文老师工作台: accounts, the submit → grade → review → publish flow, model settings and the grading checks.
Model calls are faked (core.essay.llm.post), so nothing here touches the network."""

import io, json, re, time, zipfile
import pytest
from conftest import KEYS, TEACHER2_KEY, SiteClient
from core.essay import grading, llm, models_sync, worker

ESSAY = (
    "今天是星期天，我和妈妈去公园玩。公园里的花开得真美，红的像火，粉的像霞。\n\n"
    "我们在湖边看见一群小鸭子在水里游来游去，我高心极了。妈妈说要爱护小动物，我记住了。\n\n"
    "这真是快乐的一天！"
)


def grade_answer(**over):
    answer = {
        "dims": {
            "content": {"score": 8, "reason": "内容完整"},
            "structure": {"score": 4, "reason": "顺序清楚"},
            "language": {"score": 6, "reason": "有比喻"},
            "norms": {"score": 99, "reason": "超出满分，应被截断"},
        },
        "marks": [
            {"quote": "高心", "type": "错别字", "fix": "高兴", "note": "“兴”字写错了"},
            {"quote": "红的像火，粉的像霞", "type": "好词好句", "fix": "", "note": "比喻很生动"},
            {"quote": "我和妈妈去公园玩", "type": "病句", "fix": "我和妈妈一起去公园玩", "note": "复核会否定这条"},
            {"quote": "原文里没有这句话", "type": "病句", "fix": "随便", "note": "编造的，应被丢掉"},
            {"quote": "游来\n游去", "type": "好词好句", "fix": "", "note": "空白不同也能找到"},
        ],
        "praise": ["比喻写得好"],
        "advice": ["多写一写小鸭子的样子"],
        "example": {"quote": "我高心极了", "revised": "我高兴得拍起手来", "why": "用动作表现心情"},
        "summary": "你写出了公园的美。",
        "teacher_note": "整体不错",
        "flags": ["too_short", "bogus"],
    }
    answer.update(over)
    return answer


# What the ocr step answers for a photo of ESSAY's first two paragraphs (one line has no usable box).
OCR_PAGE = {
    "title": "快乐的星期天",
    "lines": [
        {"text": "今天是星期天，我和妈妈去公园玩。", "box": [80, 100, 920, 150], "para": True},
        {"text": "公园里的花开得真美，红的像火，粉的像霞。", "box": [60, 170, 940, 220], "para": False},
        {"text": "我们在湖边看见一群小鸭子在水里游来游去，", "box": [80, 240, 930, 290], "para": True},
        {"text": "我高心极了。", "box": [60, 310, 400, 360], "para": False},
    ],
}


class FakeModels:
    """Stands in for llm.post: answers grade and check requests, records every call."""

    def __init__(self):
        self.calls = []
        self.grade = grade_answer()
        # Error marks are numbered in text order: 1 = 我和妈妈去公园玩 (病句), 2 = 高心 (错别字).
        self.check = {"items": [{"id": 1, "ok": False, "reason": "原句没有错"}, {"id": 2, "ok": True}], "level": "良"}
        self.fail = set()  # models that raise
        self.ocr = OCR_PAGE
        self.verify = None  # what the photo check of 错别字 answers (None: every character unreadable, marks kept)
        self.verified = []

    def __call__(self, row, body):
        self.calls.append((row["model"], body))
        if row["model"] in self.fail:
            raise llm.LLMError("HTTP 429: rate limited")
        system = body["messages"][0]["content"]
        if system.startswith("你在核对"):
            self.verified.append(body)
            asked = sum(1 for part in body["messages"][1]["content"] if part["type"] == "text" and part["text"].startswith("第 "))
            answer = self.verify if self.verify is not None else {"items": [{"id": n, "seen": "□"} for n in range(1, asked + 1)]}
            return json.dumps(answer, ensure_ascii=False), 300, 0, 50
        if "誊抄" in system[:80]:
            answer = self.ocr.pop(0) if isinstance(self.ocr, list) else self.ocr  # a list: one answer per call
        else:
            answer = None
        answer = answer if answer is not None else self.check if "复核" in system[:60] else self.grade
        return json.dumps(answer, ensure_ascii=False), 3000, 2500, 800


@pytest.fixture
def models(monkeypatch, db):
    fake = FakeModels()
    monkeypatch.setattr(llm, "post", fake)
    db.execute("DELETE FROM ess_steps")
    db.execute("DELETE FROM ess_providers")
    db.execute("INSERT INTO ess_providers(id,name,base_url,key_enc,key_tail,timeout,concurrency,created_at) VALUES(1,'测试','https://x/v1',?,'',30,2,1)", (llm.encrypt_key("sk-test-123456"),))
    db.executemany(
        "INSERT INTO ess_steps(step,slot,provider_id,model,price_in,price_cached,price_out) VALUES(?,?,1,?,?,?,?)",
        [("grade", "main", "big", 4.0, 0.2, 12.0), ("check", "main", "small", 1.0, 0.0, 2.0), ("ocr", "main", "eyes", 1.0, 0.0, 2.0)],
    )
    db.execute("DELETE FROM ess_meta WHERE key='daily_budget'")
    db.execute("INSERT OR REPLACE INTO ess_meta VALUES('models_checked',?)", (str(int(time.time())),))
    db.commit()
    monkeypatch.setattr(models_sync, "fetch_models", lambda provider: [{"id": "big"}, {"id": "small"}, {"id": "spare"}])
    return fake


def teacher_client(key=KEYS["teacher"]):
    c = SiteClient("teacher")
    assert c.activate(key).status_code == 200
    return c


def jpost(c, path, data=None):
    return c.post(path, json=data or {})


@pytest.fixture
def klass(db):
    """A teacher with a fresh class of three students and one assignment (earlier tests' classes archived)."""
    db.execute("UPDATE ess_classes SET archived_at=1 WHERE license_id=1 AND archived_at IS NULL")
    db.commit()
    t = teacher_client()
    r = jpost(t, "/api/classes", {"name": "三年级二班", "grade": 3})
    assert r.status_code == 200, r.get_json()
    cid = r.get_json()["id"]
    assert jpost(t, f"/api/classes/{cid}/students", {"names": "王小明\n李华、张三"}).get_json()["added"] == 3
    aid = jpost(t, f"/api/classes/{cid}/assignments", {"title": "快乐的一天", "genre": "记事", "requirement": "写一件快乐的事", "min_chars": "100"}).get_json()["id"]
    return t, cid, aid


def students_of(db, cid):
    return {r["name"]: r for r in db.execute("SELECT s.*,c.code FROM ess_students s JOIN ess_classes c ON c.id=s.class_id WHERE class_id=?", (cid,))}


def student_client(db, cid, name="王小明"):
    st = students_of(db, cid)[name]
    s = SiteClient("teacher")
    r = jpost(s, "/api/student/login", {"code": st["code"].lower(), "name": name, "pin": st["pin"]})
    assert r.status_code == 200, r.get_json()
    return s, st


def redpen_of(html):
    return json.loads(re.search(r"window.REDPEN=(.*?);</script>", html).group(1))


def run_worker():
    worker.work(worker.connect(), once=True)


def test_landing_and_health(client_factory=None):
    c = SiteClient("teacher")
    html = c.get("/").get_data(as_text=True)
    assert "我是学生" in html and "我是老师" in html
    assert c.get("/health").get_json()["status"] == "ok"


def test_full_flow(models, klass, db):
    t, cid, aid = klass
    s, st = student_client(db, cid)
    assert "快乐的一天" in s.get("/student/").get_data(as_text=True)
    r = jpost(s, f"/api/write/{aid}", {"title": "快乐的星期天", "text": ESSAY})
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["id"]
    assert s.get(f"/api/submissions/{sid}/state").get_json()["status"] == "queued"
    # Resubmitting while it is being graded is refused.
    assert jpost(s, f"/api/write/{aid}", {"text": ESSAY}).status_code == 400

    run_worker()
    row = db.execute("SELECT * FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    assert row["status"] == "review"
    review = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
    # Scores clamped to the band maximum (中年级 norms = 4, out of 25); total and level computed by the server.
    assert review["dims"]["norms"]["score"] == 4 and review["total"] == 8 + 4 + 6 + 4 and review["level"] == "优"
    quotes = [m["quote"] for m in review["marks"]]
    assert "原文里没有这句话" not in quotes  # made-up quote dropped
    assert "游来游去" in quotes  # found despite the whitespace difference
    assert "我和妈妈去公园玩" not in quotes and review["rejected"][0]["reason"] == "原句没有错"  # checker rejected it
    for m in review["marks"]:
        assert ESSAY[m["start"] : m["end"]] == m["quote"]
    assert review["flags"] == ["too_short"] and review["check_level"] == "良"
    assert review["example"]["quote"] == "我高心极了"
    # The check request lists only error marks, numbered in order.
    check_body = [b for m, b in models.calls if m == "small"][0]
    listed = json.loads(check_body["messages"][1]["content"].split("需要复核的标注：\n")[1])
    assert [(i["id"], i["quote"]) for i in listed] == [(1, "我和妈妈去公园玩"), (2, "高心")]
    # Usage and cost: (3000-2500)*4 + 2500*0.2 + 800*12 per million for the grade call.
    usage = db.execute("SELECT * FROM ess_usage WHERE submission_id=? AND step='grade'", (sid,)).fetchone()
    assert usage["ok"] == 1 and usage["cost"] == pytest.approx((500 * 4 + 2500 * 0.2 + 800 * 12) / 1e6)

    # Students cannot see it before the teacher publishes.
    assert s.get(f"/student/essay/results/{sid}").status_code == 404
    page = t.get(f"/teacher/essay/submissions/{sid}").get_data(as_text=True)
    assert "window.REVIEW=" in page and "高心" in page
    data = json.loads(re.search(r"window.REVIEW=(.*?);</script>", page).group(1))
    edit = {
        "dims": {k: {"score": v["score"]} for k, v in data["review"]["dims"].items()},
        "marks": [{"type": m["type"], "start": m["start"], "end": m["end"], "fix": m["fix"], "note": m["note"]} for m in data["review"]["marks"]],
        "summary": "老师改过的评语",
        "praise": ["亮点"],
        "advice": [],
        "keep_example": True,
    }
    edit["dims"]["content"]["score"] = 7
    start = ESSAY.index("这真是")
    edit["marks"].append({"type": "修改建议", "start": start, "end": start + 7, "fix": "", "note": "结尾可以再写写感受"})
    assert jpost(t, f"/api/submissions/{sid}/review", {"review": dict(edit, dims=dict(edit["dims"], norms={"score": 5}))}).status_code == 400
    r = jpost(t, f"/api/submissions/{sid}/review", {"review": edit, "publish": True})
    assert r.status_code == 200 and r.get_json()["level"] == "良"
    html = s.get(f"/student/essay/results/{sid}").get_data(as_text=True)
    pen = redpen_of(html)
    assert pen["summary"] == "老师改过的评语" and any(m["note"] == "结尾可以再写写感受" for m in pen["marks"])
    assert pen["total"] == 21 and pen["max"] == 25 and pen["level"] == "良" and not pen["onPhoto"]
    assert any(m["type"] == "错别字" and m["fix"] == "高兴" for m in pen["marks"])
    assert "重新交一次" not in t.get(f"/teacher/essay/results/{sid}").get_data(as_text=True)
    # 「学生看到的」 opened from the assignment page leads back there; opened from the review page, back to the review.
    assert "返回审核" in t.get(f"/teacher/essay/results/{sid}").get_data(as_text=True)
    back = t.get(f"/teacher/essay/results/{sid}?back=assignment").get_data(as_text=True)
    assert "返回作业" in back and f"/teacher/essay/assignments/{aid}" in back and "返回审核" not in back
    page = t.get(f"/teacher/essay/assignments/{aid}").get_data(as_text=True)
    assert "提交时间" in page and "批改完成" in page and 'value="graded-desc"' in page and "back=assignment" in page

    # After publishing the student may resubmit once more (MAX_ATTEMPTS = 2); the newest one counts. The way back is
    # a small link at the bottom of the result, for a wrong photo or the like.
    assert "重新交一次" in html and f"/student/essay/{aid}" in html
    r = jpost(s, f"/api/write/{aid}", {"text": ESSAY + "我还想再去。"})
    assert r.status_code == 200
    db.execute("UPDATE ess_submissions SET status='published' WHERE assignment_id=?", (aid,))
    db.commit()
    assert "最多交 2 次" in jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["error"]
    assert "重新交一次" not in s.get(f"/student/essay/results/{sid}").get_data(as_text=True)


def test_auto_publish_and_publish_all(models, klass, db):
    t, cid, aid = klass
    aid2 = jpost(t, f"/api/classes/{cid}/assignments", {"title": "自动", "genre": "日记", "auto_publish": True}).get_json()["id"]
    assert 'name="auto_publish" checked' in t.get(f"/teacher/essay/new?class={cid}").get_data(as_text=True)  # on by default
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid2}", {"text": ESSAY}).get_json()["id"]
    s2, _ = student_client(db, cid, "李华")
    sid2 = jpost(s2, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert s.get(f"/student/essay/results/{sid}").status_code == 200
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid2,)).fetchone()[0] == "review"
    assert jpost(t, f"/api/assignments/{aid}/publish_all").get_json()["published"] == 1
    assert s2.get(f"/student/essay/results/{sid2}").status_code == 200


def test_backup_model_and_failure(models, klass, db):
    t, cid, aid = klass
    db.execute("INSERT INTO ess_steps(step,slot,provider_id,model) VALUES('grade','backup',1,'spare')")
    db.commit()
    models.fail = {"big"}
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "review"
    assert "spare" in db.execute("SELECT model FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0]
    # The main model is asked twice (a failure that may pass), then the backup answers.
    assert [r[0] for r in db.execute("SELECT ok FROM ess_usage WHERE submission_id=? AND step='grade' ORDER BY id", (sid,))] == [0, 0, 1]

    # Both fail: retried later (rate limit), then failed once the retries run out; the teacher can regrade.
    models.fail = {"big", "spare"}
    s2, _ = student_client(db, cid, "李华")
    sid2 = jpost(s2, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    for _ in worker.RETRY_DELAYS:
        run_worker()
        db.execute("UPDATE ess_jobs SET run_after=0 WHERE submission_id=? AND status='queued'", (sid2,))
        db.commit()
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid2,)).fetchone()[0] == "failed"
    models.fail = set()
    assert jpost(t, f"/api/submissions/{sid2}/regrade").status_code == 200
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid2,)).fetchone()[0] == "review"


def test_a_one_off_failure_is_retried_on_the_same_model(models, klass, db, monkeypatch):
    t, cid, aid = klass
    calls = []

    def flaky(row, body):
        calls.append(row["model"])
        if len(calls) == 1:  # the provider's own "please retry" answer, a 400
            raise llm.LLMError("HTTP 400: Model output became abnormal ... Please retry", retry=bool(llm.PLEASE_RETRY.search("Please retry")))
        return models(row, body)

    monkeypatch.setattr(llm, "post", flaky)
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert calls[:2] == ["big", "big"]
    assert "big" in db.execute("SELECT model FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0]


def test_check_failure_keeps_marks_and_flags(models, klass, db):
    t, cid, aid = klass
    models.fail = {"small"}
    # Even on an assignment that auto-publishes, an unchecked review waits for the teacher.
    aid2 = jpost(t, f"/api/classes/{cid}/assignments", {"title": "自动", "auto_publish": True}).get_json()["id"]
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid2}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    review = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
    assert "unchecked" in review["flags"] and any(m["quote"] == "我和妈妈去公园玩" for m in review["marks"])
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "review"
    assert sum(1 for m, _ in models.calls if m == "small") == 2  # tried twice
    # Every failed call is logged with its reason, shown in 模型设置.
    failed = db.execute("SELECT error,ms FROM ess_usage WHERE submission_id=? AND ok=0", (sid,)).fetchall()
    assert len(failed) == 2 and all("429" in r["error"] and r["ms"] is not None for r in failed)


def test_holds_when_not_configured_or_over_budget(models, klass, db):
    t, cid, aid = klass
    db.execute("DELETE FROM ess_steps WHERE step='grade'")
    db.commit()
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    state = t.get(f"/api/submissions/{sid}/state").get_json()
    assert state["status"] == "queued" and "暂未开通" in state["hold"]
    # The student only reads that the teacher is marking it.
    assert s.get(f"/api/submissions/{sid}/state").get_json() == {"status": "queued", "label": "老师批改中", "hold": ""}
    db.execute("INSERT INTO ess_steps(step,slot,provider_id,model) VALUES('grade','main',1,'big')")
    db.execute("INSERT INTO ess_meta VALUES('daily_budget','0.0000001')")
    db.execute("INSERT INTO ess_usage(license_id,step,model,tokens_in,tokens_cached,tokens_out,cost,ok,created_at) VALUES(1,'grade','x',0,0,0,1,1,?)", (int(time.time()),))
    db.execute("UPDATE ess_jobs SET run_after=0")
    db.commit()
    run_worker()
    assert "额度" in t.get(f"/api/submissions/{sid}/state").get_json()["hold"]
    db.execute("DELETE FROM ess_meta WHERE key='daily_budget'")
    db.execute("UPDATE ess_jobs SET run_after=0")
    db.commit()
    run_worker()
    assert s.get(f"/api/submissions/{sid}/state").get_json()["status"] == "review"


def test_student_login_lock_and_pin_reset(klass, db):
    t, cid, aid = klass
    st = students_of(db, cid)["张三"]
    s = SiteClient("teacher")
    for _ in range(5):
        assert jpost(s, "/api/student/login", {"code": st["code"], "name": "张三", "pin": "x"}).status_code == 403
    assert jpost(s, "/api/student/login", {"code": st["code"], "name": "张三", "pin": st["pin"]}).status_code == 429
    pin = jpost(t, f"/api/students/{st['id']}/pin").get_json()["pin"]  # resetting the 口令 also unlocks
    assert jpost(s, "/api/student/login", {"code": st["code"], "name": "张三", "pin": pin}).status_code == 200
    assert "我的作业" in s.get("/student/").get_data(as_text=True)
    jpost(t, f"/api/students/{st['id']}/pin")  # a new reset signs the student out
    assert 'id="student-login"' in s.get("/student/").get_data(as_text=True)


def test_login_slips_export_as_excel(models, klass, db):
    t, cid, aid = klass
    r = t.get(f"/teacher/classes/{cid}/slips.xlsx")
    assert r.status_code == 200 and r.mimetype == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert "filename*=UTF-8''" in r.headers["Content-Disposition"]
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        strings = z.read("xl/sharedStrings.xml").decode()
    for name, st in students_of(db, cid).items():
        assert name in strings and st["pin"] in strings and st["code"] in strings
    assert strings.count("/workspace/chinese/student") == 1  # the 网址 once at the top, not on every row
    assert f"/teacher/classes/{cid}/slips.xlsx" in t.get(f"/teacher/classes/{cid}").get_data(as_text=True)
    assert teacher_client(TEACHER2_KEY).get(f"/teacher/classes/{cid}/slips.xlsx").status_code == 404


def test_isolation_between_teachers_and_students(models, klass, db):
    t, cid, aid = klass
    other = teacher_client(TEACHER2_KEY)
    assert other.get(f"/teacher/classes/{cid}").status_code == 404
    assert other.get(f"/teacher/essay/assignments/{aid}").status_code == 404
    assert jpost(other, f"/api/classes/{cid}/students", {"names": "坏人"}).status_code == 404
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert other.get(f"/teacher/essay/submissions/{sid}").status_code == 404
    assert jpost(other, f"/api/submissions/{sid}/review", {"review": {}}).status_code == 404
    s2, _ = student_client(db, cid, "李华")
    assert s2.get(f"/api/submissions/{sid}/state").status_code == 404
    # Students cannot call teacher endpoints.
    assert jpost(s, f"/api/classes/{cid}/students", {"names": "x"}).status_code == 403


def test_quotas(models, klass, db):
    t, cid, aid = klass
    used = db.execute(
        "SELECT count(*) FROM ess_students s JOIN ess_classes c ON c.id=s.class_id WHERE c.license_id=1 AND c.archived_at IS NULL AND s.removed_at IS NULL"
    ).fetchone()[0]
    month = db.execute("SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id WHERE c.license_id=1").fetchone()[0]
    db.execute("INSERT OR REPLACE INTO ess_quota VALUES(1,?,?)", (used + 1, month + 1))
    db.commit()
    assert "名额不够" in jpost(t, f"/api/classes/{cid}/students", {"names": "甲\n乙"}).get_json()["error"]
    assert jpost(t, f"/api/classes/{cid}/students", {"names": "甲"}).status_code == 200
    s, _ = student_client(db, cid)
    assert jpost(s, f"/api/write/{aid}", {"text": ESSAY}).status_code == 200
    s2, _ = student_client(db, cid, "李华")
    assert "篇数已经用完" in jpost(s2, f"/api/write/{aid}", {"text": ESSAY}).get_json()["error"]
    db.execute("DELETE FROM ess_quota")
    db.commit()


def test_admin_model_settings(db):
    a = SiteClient("teacher")
    assert a.login().status_code == 302
    assert a.get("/admin/models").status_code == 200
    r = a.post("/admin/models", {"action": "provider_add", "name": "百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/", "api_key": "sk-secret-ABCD1234", "timeout": "60", "concurrency": "2"})
    assert r.status_code == 302
    row = db.execute("SELECT * FROM ess_providers WHERE name='百炼'").fetchone()
    assert row["base_url"].endswith("/v1") and row["key_tail"] == "1234" and "sk-secret" not in row["key_enc"]
    assert llm.decrypt_key(row["key_enc"]) == "sk-secret-ABCD1234"
    # Anthropic-style endpoints are refused; a pasted …/chat/completions is trimmed to the base.
    a.post("/admin/models", {"action": "provider_add", "name": "错的", "base_url": "https://opencode.ai/inference/anthropic/v1/messages", "api_key": "sk-x-12345678", "timeout": "60", "concurrency": "2"})
    assert not db.execute("SELECT 1 FROM ess_providers WHERE name='错的'").fetchone()
    a.post("/admin/models", {"action": "provider_add", "name": "全地址", "base_url": "https://api.deepseek.com/v1/chat/completions", "api_key": "sk-x-12345678", "timeout": "60", "concurrency": "2"})
    assert db.execute("SELECT base_url FROM ess_providers WHERE name='全地址'").fetchone()[0] == "https://api.deepseek.com/v1"
    db.execute("DELETE FROM ess_providers WHERE name='全地址'")
    db.commit()
    # Leaving the key empty keeps it.
    a.post("/admin/models", {"action": "provider_edit", "id": row["id"], "name": "百炼2", "base_url": "https://a.b/v1", "api_key": "", "timeout": "60", "concurrency": "2"})
    assert llm.decrypt_key(db.execute("SELECT key_enc FROM ess_providers WHERE id=?", (row["id"],)).fetchone()[0]) == "sk-secret-ABCD1234"
    a.post("/admin/models", {"action": "step", "step": "grade", "slot": "main", "provider_id": row["id"], "model": "deepseek-v4-pro", "temperature": "0.3", "extra": '{"enable_thinking": false, "response_format": null}', "price_in": "4.7", "price_out": "14", "peak_hours": "9-12,14-18", "peak_factor": "2"})
    step = db.execute("SELECT * FROM ess_steps WHERE step='grade' AND slot='main'").fetchone()
    assert step["model"] == "deepseek-v4-pro" and step["temperature"] == 0.3 and step["peak_factor"] == 2
    body = llm.request_body(dict(step), [{"role": "user", "content": "hi"}], True)
    assert body["enable_thinking"] is False and "response_format" not in body and body["temperature"] == 0.3
    # Bad input is refused; a provider in use cannot be deleted.
    a.post("/admin/models", {"action": "step", "step": "grade", "slot": "backup", "provider_id": row["id"], "model": "m", "extra": "[1]"})
    assert not db.execute("SELECT 1 FROM ess_steps WHERE slot='backup' AND step='grade'").fetchone()
    a.post("/admin/models", {"action": "provider_delete", "id": row["id"]})
    assert db.execute("SELECT 1 FROM ess_providers WHERE id=?", (row["id"],)).fetchone()
    audit = db.execute("SELECT details FROM audit WHERE site='teacher' AND action LIKE 'essay_%'").fetchall()
    assert audit and all("sk-secret" not in r[0] for r in audit)
    # Not reachable without the admin sign-in.
    assert SiteClient("teacher").get("/admin/models").status_code == 302
    db.execute("DELETE FROM ess_steps")
    db.execute("DELETE FROM ess_providers")
    db.commit()


def test_use_hours_pick_the_model_by_time_of_day(models, db):
    beijing = lambda hour: 86400 * 10 + (hour - 8) * 3600  # a timestamp at that Beijing hour
    assert llm.in_hours("18-8", beijing(20)) and llm.in_hours("18-8", beijing(3)) and not llm.in_hours("18-8", beijing(12))
    assert llm.in_hours("0-8,18-24", beijing(19)) and not llm.in_hours("0-8,18-24", beijing(9)) and not llm.in_hours("", beijing(9))
    assert llm.valid_use_hours("18-8") and llm.valid_use_hours("0-8, 18-24") and llm.valid_use_hours("")
    assert not llm.valid_use_hours("8-8") and not llm.valid_use_hours("25-3") and not llm.valid_use_hours("晚上")
    # grade: main "big" all day, backup "spare" first from 18:00 to 08:00.
    db.execute("INSERT INTO ess_steps(step,slot,provider_id,model,use_hours) VALUES('grade','backup',1,'spare','18-8')")
    db.commit()
    order = lambda hour: [r["model"] for r in llm.step_rows(db, "grade", now=beijing(hour))]
    assert order(20) == ["spare", "big"] and order(7) == ["spare", "big"] and order(12) == ["big", "spare"]
    a = SiteClient("teacher")
    a.login()
    form = {"action": "step", "step": "grade", "slot": "backup", "provider_id": "1", "model": "spare"}
    a.post("/admin/models", dict(form, use_hours="晚上"))
    assert db.execute("SELECT use_hours FROM ess_steps WHERE step='grade' AND slot='backup'").fetchone()[0] == "18-8"
    a.post("/admin/models", dict(form, use_hours="19-7"))
    assert db.execute("SELECT use_hours FROM ess_steps WHERE step='grade' AND slot='backup'").fetchone()[0] == "19-7"
    assert "现在先用" in a.get("/admin/models").get_data(as_text=True)


def test_student_pages_never_show_ai_status(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid, "李华")
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    # One browser signed in as the teacher and as a student (a teacher trying the student pages).
    assert s.activate(KEYS["teacher"]).status_code == 200
    page = s.get(f"/student/essay/{aid}").get_data(as_text=True)
    assert "as=student" in page
    assert s.get(f"/api/submissions/{sid}/state?as=student").get_json()["label"] == "老师批改中"
    assert s.get(f"/api/submissions/{sid}/state").get_json()["label"] == "排队批改中"  # the teacher side


def test_admin_quota_on_license_detail(db):
    a = SiteClient("teacher")
    a.login()
    assert "学生名额和批改篇数" in a.get("/admin/licenses/2").get_data(as_text=True)
    a.post("/admin/licenses/2/essay_quota", {"students": "45", "monthly": "200"})
    assert tuple(db.execute("SELECT students,monthly FROM ess_quota WHERE license_id=2").fetchone()) == (45, 200)
    db.execute("DELETE FROM ess_quota")
    db.commit()


def test_worker_threads_beat_and_grade(models, klass, db):
    import threading

    t, cid, aid = klass
    db.execute("DELETE FROM ess_meta WHERE key='worker_seen'")
    db.commit()
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    stop = threading.Event()
    threads = [threading.Thread(target=worker.thread_main, args=(stop,)) for _ in range(worker.THREADS)]
    for th in threads:
        th.start()
    deadline = time.time() + 10
    while time.time() < deadline and s.get(f"/api/submissions/{sid}/state").get_json()["status"] != "review":
        time.sleep(0.1)
    stop.set()
    for th in threads:
        th.join(5)
    assert s.get(f"/api/submissions/{sid}/state").get_json()["status"] == "review"
    assert db.execute("SELECT value FROM ess_meta WHERE key='worker_seen'").fetchone()
    assert SiteClient("teacher").get("/health").get_json()["worker_seen"]


def test_grading_helpers():
    text = "第一段。\n\n第二 段有空格。"
    assert grading.locate(text, "第二段有空格") == (text.index("第二"), len(text) - 1)
    assert grading.locate(text, "没有") is None
    assert [grading.level_of(n) for n in (25, 22, 21, 19, 18, 17, 15, 14, 0)] == ["优", "优", "良", "良", "良", "及格", "及格", "待提高", "待提高"]
    assert all(sum(b["max"].values()) == grading.TOTAL == 25 for b in grading.BANDS.values())
    assert grading.clean_text("  a  \r\n\r\n\r\n\r\nb　") == "  a\n\nb"
    essay = {"grade": 1, "genre": "看图写话", "requirement": "", "min_chars": 0, "title": "", "text": ESSAY}
    with pytest.raises(ValueError):
        grading.clean_review({"dims": {}}, essay)
    # A careful review keeps many marks (up to 20 errors), the 分段 type, and a summary of a whole paragraph.
    dims = {k: {"score": 3} for k in grading.DIMS}
    many = [{"quote": ESSAY[i : i + 2], "type": "病句", "fix": "X", "note": "要改"} for i in range(0, 70, 2)]
    long_summary = "你" * 150
    cleaned = grading.clean_review(
        {"dims": dims, "marks": many + [{"quote": ESSAY[60:64], "type": "分段", "fix": "", "note": "这里另起一段"}], "summary": long_summary},
        essay,
    )
    assert sum(m["type"] == "病句" for m in cleaned["marks"]) == grading.MARK_LIMITS["error"] == 25
    assert [m["type"] for m in cleaned["marks"] if m["type"] == "分段"] == ["分段"]
    # A 分段 mark where the child already began a paragraph (after a line break, indent or not) is dropped.
    paras = [{"quote": q, "type": "分段", "fix": "", "note": "这里另起一段"} for q in ("我们在湖边", "这真是快乐", "妈妈说要爱护")]
    para_only = grading.clean_review({"dims": dims, "marks": paras}, {**essay, "text": ESSAY.replace("\n\n这真", "\n\u3000\u3000这真")})
    assert [m["quote"] for m in para_only["marks"]] == ["妈妈说要爱护"]
    assert grading.para_start("  开头", 2) and not grading.para_start("一句。二句", 3)
    assert cleaned["summary"] == long_summary and cleaned["praise"] == [] and cleaned["advice"] == []
    msgs = grading.grade_messages(essay)
    assert "最短" in msgs[0]["content"] and "总评" in msgs[0]["content"]
    assert "第一学段" in msgs[0]["content"] and "拼音" in msgs[0]["content"] and ESSAY in msgs[1]["content"]
    assert "{band}" not in msgs[0]["content"] and "{anchors}" not in msgs[0]["content"]
    assert llm.parse_json('好的\n```json\n{"a": 1}\n```') == {"a": 1}
    # Peak prices: Monday 2026-11-02 10:00 Beijing is peak, Saturday is not.
    monday10 = 1793584800  # 2026-11-02 02:00 UTC
    assert llm.in_peak("9-12,14-18", monday10) and not llm.in_peak("9-12,14-18", monday10 + 5 * 86400)
    row = {"price_in": 2.0, "price_cached": 0.5, "price_out": 8.0, "peak_factor": 2.0, "peak_hours": "9-12"}
    assert llm.cost(row, 1000, 400, 100, monday10) == pytest.approx(2 * (600 * 2 + 400 * 0.5 + 100 * 8) / 1e6)


# ---- 版本策略: models_sync ----
OPENCODE = [
    {"id": "deepseek-v4.1-flash", "type": "chat", "release_date": "2026-09-10", "capabilities": {"structured_output": True}},
    {"id": "deepseek-v4-pro", "type": "chat"},
    {"id": "glm-5.3", "type": "chat"},
    {"id": "glm-5.3-flash", "type": "chat"},
    {"id": "glm-5.4-flash-preview", "type": "chat"},
    {"id": "hy4-preview", "type": "chat"},
    {"id": "muse-spark-1.3-contributor", "type": "chat"},
]


def test_series_and_version():
    assert models_sync.series("deepseek-v4.1-flash") == "deepseek-#-flash" == models_sync.series("DeepSeek-V5-Flash")
    assert models_sync.series("glm-5.3-flash") == "glm-#-flash" != models_sync.series("glm-5.3")
    assert models_sync.series("qwen3.8-flash") == "qwen#-flash"
    assert models_sync.version("deepseek-v4.1-flash") == (4, 1) < models_sync.version("deepseek-v4.10-flash") == (4, 10)
    slot = {"model": "glm-5.3-flash", "series": "", "auto_mode": "latest", "step": "check"}
    assert models_sync.pick(slot, OPENCODE) is None  # already newest; the preview is another series
    newer = OPENCODE + [{"id": "glm-5.4-flash", "type": "chat"}, {"id": "glm-6", "type": "chat"}]
    assert models_sync.pick(slot, newer)["id"] == "glm-5.4-flash"
    assert models_sync.pick(dict(slot, auto_mode="replace"), newer) is None  # still listed
    gone = [m for m in newer if m["id"] != "glm-5.3-flash"]
    assert models_sync.pick(dict(slot, auto_mode="replace"), gone)["id"] == "glm-5.4-flash"
    assert models_sync.pick(dict(slot, auto_mode="fixed"), gone) is None
    # Capabilities: no JSON for grading, no images for OCR.
    assert models_sync.pick(slot, OPENCODE + [{"id": "glm-5.9-flash", "capabilities": {"structured_output": False}}]) is None
    ocr = {"model": "qwen-vl-ocr-1", "series": "", "auto_mode": "latest", "step": "ocr"}
    assert models_sync.pick(ocr, [{"id": "qwen-vl-ocr-2", "modalities": {"input": ["text"]}}]) is None


def test_price_from_cost():
    cost = {
        "text": {"input": {"price": 0.15, "cache": {"read": 0.003}}, "output": {"price": 0.6}},
        "multipliers": [{"when": {"utc_days": {"in": ["mon", "tue", "wed", "thu", "fri"]}, "any": [
            {"all": [{"utc_hours": {"gte": "01:00"}}, {"utc_hours": {"lt": "04:00"}}]},
            {"all": [{"utc_hours": {"gte": "06:00"}}, {"utc_hours": {"lt": "10:00"}}]}]}, "factor": 2}],
    }
    p = models_sync.price_from_cost(cost, 7.0)
    assert (p["price_in"], p["price_cached"], p["price_out"]) == (1.05, 0.021, 4.2)
    assert p["peak_hours"] == "9-12,14-18" and p["peak_factor"] == 2
    assert models_sync.price_from_cost({"weird": 1}, 7) is None


def test_sync_switches_after_smoke_test_and_reverts(models, db, monkeypatch):
    listing = [{"id": "big"}, {"id": "small"}, {"id": "small-2", "cost": {"text": {"input": {"price": 1}, "output": {"price": 2}}}}]
    monkeypatch.setattr(models_sync, "fetch_models", lambda provider: listing)
    db.execute("UPDATE ess_steps SET model='small-1' WHERE step='check'")
    db.execute("UPDATE ess_meta SET value='0' WHERE key='models_checked'")
    db.commit()
    models.fail = {"small-2"}  # the test request fails: no switch
    assert models_sync.sync(db) == {}
    assert db.execute("SELECT model FROM ess_steps WHERE step='check'").fetchone()[0] == "small-1"
    models.fail = set()
    assert models_sync.sync(db) == {}  # checked a moment ago: not due
    db.execute("UPDATE ess_meta SET value='0' WHERE key='models_checked'")
    db.commit()
    assert models_sync.sync(db) == {("check", "main"): "small-2"}
    row = db.execute("SELECT * FROM ess_steps WHERE step='check'").fetchone()
    assert row["prev_model"] == "small-1" and "small-2" in row["auto_note"] and row["price_in"] == 7.1 and row["price_out"] == 14.2
    assert db.execute("SELECT 1 FROM audit WHERE action='essay_auto_model'").fetchone()
    a = SiteClient("teacher")
    a.login()
    a.post("/admin/models", {"action": "revert", "step": "check", "slot": "main"})
    row = db.execute("SELECT * FROM ess_steps WHERE step='check'").fetchone()
    assert row["model"] == "small-1" and row["auto_mode"] == "fixed"
    page = a.get("/admin/models").get_data(as_text=True)
    assert "版本策略" in page and "立即检查更新" in page


def test_missing_model_switches_and_retries(models, klass, db, monkeypatch):
    t, cid, aid = klass
    monkeypatch.setattr(models_sync, "fetch_models", lambda provider: [{"id": "big-v2"}, {"id": "small"}])
    db.execute("UPDATE ess_steps SET model='big-v1' WHERE step='grade'")
    db.commit()
    real = models.__call__

    def post(row, body):
        if row["model"] == "big-v1" and "复核" not in body["messages"][0]["content"][:60]:
            e = llm.LLMError("HTTP 404: The model `big-v1` does not exist", retry=False)
            e.model_missing = bool(llm.MODEL_MISSING.search("The model `big-v1` does not exist"))
            raise e
        return real(row, body)

    monkeypatch.setattr(llm, "post", post)
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "review"
    assert db.execute("SELECT model FROM ess_steps WHERE step='grade' AND slot='main'").fetchone()[0] == "big-v2"
    assert llm.MODEL_MISSING.search("Model not found: x") and not llm.MODEL_MISSING.search("rate limit exceeded")


def test_fetch_models_retries_without_key(monkeypatch):
    import io, urllib.error, urllib.request

    seen = []

    def urlopen(req, timeout):
        seen.append(req.get_header("Authorization"))
        if req.get_header("Authorization"):
            raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b""))
        return io.BytesIO(b'{"data": [{"id": "glm-5.3-flash"}]}')

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    provider = {"base_url": "https://opencode.ai/inference/v1/", "key_enc": llm.encrypt_key("sk-abc-123456")}
    assert models_sync.fetch_models(provider) == [{"id": "glm-5.3-flash"}]
    assert seen == ["Bearer sk-abc-123456", None]


def test_successful_check_clears_old_warnings(models, db):
    db.execute("UPDATE ess_steps SET auto_note='检查新版本失败：模型列表 HTTP 404',auto_alert=0 WHERE step='grade'")
    db.execute("UPDATE ess_meta SET value='0' WHERE key='models_checked'")
    db.commit()
    models_sync.sync(db)
    assert db.execute("SELECT auto_note FROM ess_steps WHERE step='grade'").fetchone()[0] == ""


# ---- the workbench around the essays ----
def photo_bytes(color=(250, 250, 245), size=(1200, 1600)):
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def upload(c, path, photos, **fields):
    import io

    data = dict(fields, photos=[(io.BytesIO(b), f"p{i}.jpg") for i, b in enumerate(photos)], csrf=c.token())
    return c.c.post(c.prefix + path, data=data, headers={"X-CSRF-Token": data["csrf"]}, content_type="multipart/form-data")


def test_routes_entry_and_old_addresses():
    c = SiteClient("teacher")
    html = c.get("/").get_data(as_text=True)
    assert "我是老师" in html and "我是学生" in html and "/workspace/chinese/teacher/" in html
    assert 'id="teacher-activate"' in c.get("/teacher/").get_data(as_text=True)
    assert 'id="student-login"' in c.get("/student/").get_data(as_text=True)
    from werkzeug.test import Client
    import wsgi

    raw = Client(wsgi.app)
    for old, new in (
        ("/teacher/", "/workspace/chinese/"),
        ("/teacher/submissions/3", "/workspace/chinese/"),
        ("/teacher/admin/models?x=1", "/workspace/chinese/admin/models?x=1"),
        ("/workspace", "/workspace/chinese/"),
        ("/workspace/", "/workspace/chinese/"),
    ):
        r = raw.get(old)
        assert r.status_code == 301 and r.headers["Location"].endswith(new), (old, r.headers.get("Location"))


def test_sign_ins_last_and_sides_are_separate(klass, db):
    t, cid, aid = klass
    st = students_of(db, cid)["王小明"]
    c = SiteClient("teacher")
    r = c.activate()
    assert "Expires=" in r.headers["Set-Cookie"]
    r = jpost(c, "/api/student/login", {"code": st["code"], "name": "王小明", "pin": st["pin"]})
    assert r.status_code == 200 and "Expires=" in r.headers["Set-Cookie"]
    # Both stay signed in on one browser: each side shows its own home.
    assert "我的功能" in c.get("/teacher/").get_data(as_text=True)
    student_page = c.get("/student/").get_data(as_text=True)
    assert "我的作业" in student_page and "王小明" in student_page and "修改名字和头像" not in student_page


def test_teacher_profile_name_and_avatar(db):
    import io
    from PIL import Image

    t = teacher_client(TEACHER2_KEY)
    assert "退出登录" in t.get("/teacher/").get_data(as_text=True)
    assert jpost(t, "/api/me/name", {"name": "李老师"}).status_code == 200
    assert "李老师" in t.get("/teacher/").get_data(as_text=True)
    buf = io.BytesIO()
    Image.new("RGB", (300, 200), (200, 60, 60)).save(buf, "PNG")
    r = t.c.post(t.prefix + "/api/me/avatar", data=buf.getvalue(), headers={"X-CSRF-Token": t.token()})
    assert r.status_code == 200, r.get_json()
    url = r.get_json()["avatar"]
    assert url in t.get("/teacher/").get_data(as_text=True)
    img = t.c.get(url)
    assert img.status_code == 200 and img.mimetype == "image/webp"
    assert SiteClient("teacher").c.get(url).status_code == 404  # only the teacher sees it
    db.execute("DELETE FROM profiles WHERE site='teacher'")
    db.commit()


def test_feature_expiry_and_admin_extension(models, klass, db):
    from core.essay import features

    t, cid, aid = klass
    row = db.execute("SELECT * FROM ws_entitlements WHERE license_id=1 AND feature='essay'").fetchone()
    assert row["starts_at"] and row["expires_at"] > time.time() + 360 * 86400  # the clock started on activation
    assert "剩余 <strong>" in t.get("/teacher/").get_data(as_text=True)
    s, _ = student_client(db, cid)
    db.execute("UPDATE ws_entitlements SET expires_at=? WHERE license_id=1", (int(time.time()) - 3 * 86400,))
    db.commit()
    assert "已于" in t.get("/teacher/").get_data(as_text=True)
    assert "到期" in jpost(t, f"/api/classes/{cid}/assignments", {"title": "x", "genre": "记事"}).get_json()["error"]
    assert "到期" in jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["error"]
    assert t.get(f"/teacher/classes/{cid}").status_code == 200  # still readable
    a = SiteClient("teacher")
    a.login()
    page = a.get("/admin/licenses/1").get_data(as_text=True)
    assert "功能和有效期" in page and "已到期" in page
    a.post("/admin/licenses/1/feature", {"feature": "essay", "action": "extend", "days": "365"})
    info = features.describe(db.execute("SELECT * FROM ws_entitlements WHERE license_id=1").fetchone())
    assert info["state"] == "active" and info["days"] == 364  # counted from today (today itself is day one)
    a.post("/admin/licenses/1/feature", {"feature": "essay", "action": "expiry", "date": "2030-01-31"})
    expires = db.execute("SELECT expires_at FROM ws_entitlements WHERE license_id=1").fetchone()[0]
    assert time.strftime("%Y-%m-%d %H:%M", time.gmtime(expires + 8 * 3600)) == "2030-01-31 23:59"
    assert jpost(s, f"/api/write/{aid}", {"text": ESSAY}).status_code == 200


def test_generated_codes_carry_features_from_activation(db):
    a = SiteClient("teacher")
    a.login()
    page = a.get("/admin/licenses/new").get_data(as_text=True)
    assert "功能和有效期" in page and 'name="days_essay"' in page
    nonce = re.search(r'name="nonce" value="([^"]+)"', page).group(1)
    r = a.post("/admin/licenses/new", {"nonce": nonce, "quantity": "1", "device_limit": "3", "feature_essay": "1", "days_essay": "730"})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    lid = db.execute("SELECT max(id) FROM licenses WHERE site='teacher'").fetchone()[0]
    row = db.execute("SELECT * FROM ws_entitlements WHERE license_id=?", (lid,)).fetchone()
    assert row["days"] == 730 and row["starts_at"] is None
    code = db.execute("SELECT code FROM license_keys WHERE site='teacher' AND license_id=?", (lid,)).fetchone()[0]
    teacher_client(code)
    row = db.execute("SELECT * FROM ws_entitlements WHERE license_id=?", (lid,)).fetchone()
    from core.essay import features

    assert features.describe(row)["days"] == 730
    nonce = re.search(r'name="nonce" value="([^"]+)"', a.get("/admin/licenses/new").get_data(as_text=True)).group(1)
    assert a.post("/admin/licenses/new", {"nonce": nonce, "quantity": "1", "device_limit": "3"}).status_code == 400


def test_photo_essay_is_read_graded_and_drawn_on_the_photo(models, klass, db):
    t, cid, aid = klass
    s, st = student_client(db, cid)
    assert upload(s, f"/api/write/{aid}", []).status_code == 400  # neither photos nor text
    r = upload(s, f"/api/write/{aid}", [photo_bytes(), photo_bytes(size=(900, 1200))])
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["id"]
    images = db.execute("SELECT * FROM ess_images WHERE submission_id=? ORDER BY page", (sid,)).fetchall()
    assert [(i["page"], i["w"], i["h"]) for i in images] == [(1, 1200, 1600), (2, 900, 1200)]
    models.grade = grade_answer(marks=[{"quote": "高心", "type": "错别字", "fix": "高兴", "note": ""}])
    run_worker()
    sub = db.execute("SELECT * FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    assert sub["status"] == "published" or sub["status"] == "review"
    # Two pages, the same answer each: the text is both pages' lines; the student's own title is kept empty → OCR's.
    assert sub["text"].startswith("今天是星期天，我和妈妈去公园玩。公园里的花开得真美") and sub["title"] == "快乐的星期天"
    assert "\n我们在湖边" in sub["text"]
    lines = json.loads(db.execute("SELECT lines FROM ess_images WHERE id=?", (images[0]["id"],)).fetchone()[0])
    assert lines[0] == {"s": 0, "e": 16, "b": [80, 100, 920, 150]}
    assert all(sub["text"][l["s"] : l["e"]] for l in lines)
    ocr_calls = [b for m, b in models.calls if "誊抄" in b["messages"][0]["content"][:80]]
    assert len(ocr_calls) == 2 and ocr_calls[0]["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    db.execute("UPDATE ess_submissions SET status='published',published_at=1 WHERE id=?", (sid,))
    db.execute("UPDATE ess_reviews SET final=ai WHERE submission_id=?", (sid,))
    db.commit()
    pen = redpen_of(s.get(f"/student/essay/results/{sid}").get_data(as_text=True))
    assert pen["onPhoto"] and len(pen["pages"]) == 2 and pen["pages"][0]["lines"][0]["b"] == [80, 100, 920, 150]
    # The photo is the student's and their teacher's only.
    src = pen["pages"][0]["src"]
    assert s.c.get(src).status_code == 200 and t.c.get(src).status_code == 200
    s2, _ = student_client(db, cid, "李华")
    assert s2.c.get(src).status_code == 404 and teacher_client(TEACHER2_KEY).c.get(src).status_code == 404


def test_sideways_photo_is_turned_upright_and_read_again(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid, "李华")
    # The first answer says the photo must turn 90° clockwise (boxes useless); the second reads the upright photo.
    sideways = {**OCR_PAGE, "rotate": 90, "writing": "工整", "lines": [{**l, "box": None} for l in OCR_PAGE["lines"]]}
    models.ocr = [sideways, {**OCR_PAGE, "rotate": 0, "writing": "凌乱"}]
    sid = upload(s, f"/api/write/{aid}", [photo_bytes(size=(1600, 1200))]).get_json()["id"]
    old = db.execute("SELECT id FROM ess_images WHERE submission_id=?", (sid,)).fetchone()[0]
    run_worker()
    img = db.execute("SELECT * FROM ess_images WHERE submission_id=?", (sid,)).fetchone()
    assert img["id"] != old and (img["w"], img["h"]) == (1200, 1600) and json.loads(img["lines"])
    ocr_calls = [b for m, b in models.calls if "誊抄" in b["messages"][0]["content"][:80]]
    assert len(ocr_calls) == 2
    sub = db.execute("SELECT * FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    assert sub["writing"] == "凌乱" and sub["status"] in ("review", "published")
    grade_call = [b for m, b in models.calls if m == "big"][-1]
    assert "书写：凌乱" in grade_call["messages"][1]["content"]
    # The admin's essay-reread grades it again from the photos.
    import manage, wsgi

    models.ocr = {**OCR_PAGE, "rotate": 0, "writing": "工整"}
    with wsgi.teacher.app_context():
        manage.essay_reread(sid)
    sub = db.execute("SELECT * FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    assert sub["status"] == "queued" and sub["text"] == ""
    run_worker()
    assert db.execute("SELECT writing FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "工整"


def test_jobs_cut_off_by_a_restart_resume_at_once(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid, "李华")
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    # The process was stopped while the job ran (status left 'running' / 'grading', locked just now).
    db.execute("UPDATE ess_jobs SET status='running',locked_at=? WHERE submission_id=?", (int(time.time()), sid))
    db.execute("UPDATE ess_submissions SET status='grading' WHERE id=?", (sid,))
    db.commit()
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "grading"
    worker.resume_interrupted(worker.connect())
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "queued"
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] in ("review", "published")


def test_handwriting_and_ocr_extras():
    page = grading.parse_ocr({"title": "", "lines": [{"text": "一行", "box": [1, 1, 9, 9]}], "rotate": 270, "writing": "一般"})
    assert page["rotate"] == 270 and page["writing"] == "一般"
    assert grading.parse_ocr({"lines": [], "rotate": 45, "writing": "好"})["rotate"] == 0
    assert grading.worst_writing([{"writing": "工整"}, {"writing": "一般"}, {"writing": ""}]) == "一般"
    assert grading.worst_writing([{"writing": ""}]) == ""
    essay = {"grade": 3, "genre": "", "requirement": "", "min_chars": 0, "title": "", "text": ESSAY}
    assert "书写：打字提交" in grading.grade_messages(essay)[1]["content"]
    assert "书写：工整" in grading.grade_messages({**essay, "source": "photo", "writing": "工整"})[1]["content"]


def test_teacher_corrects_recognised_text(models, klass, db):
    t, cid, aid = klass
    st = students_of(db, cid)["张三"]
    r = upload(t, f"/api/assignments/{aid}/submit_for", [photo_bytes()], student_id=str(st["id"]))
    assert r.status_code == 200, r.get_json()
    sid = r.get_json()["id"]
    run_worker()
    sub = db.execute("SELECT * FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    assert sub["source"] == "photo" and sub["status"] == "review"
    assert "照片里的字认错了" in t.get(f"/teacher/essay/submissions/{sid}").get_data(as_text=True)
    fixed = sub["text"].replace("星期天", "星期日")
    assert jpost(t, f"/api/submissions/{sid}/text", {"text": fixed}).status_code == 200
    lines = json.loads(db.execute("SELECT lines FROM ess_images WHERE submission_id=?", (sid,)).fetchone()[0])
    assert fixed[lines[1]["s"] : lines[1]["e"]] == "公园里的花开得真美，红的像火，粉的像霞。"
    run_worker()
    assert db.execute("SELECT text FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == fixed


def test_unreadable_photo_fails_without_retries(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid, "李华")
    models.ocr = {"title": "", "lines": [], "error": "不是作文"}
    sid = upload(s, f"/api/write/{aid}", [photo_bytes()]).get_json()["id"]
    run_worker()
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "failed"


def test_photo_waits_until_ocr_is_configured(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid)
    db.execute("DELETE FROM ess_steps WHERE step='ocr'")
    db.commit()
    sid = upload(s, f"/api/write/{aid}", [photo_bytes()]).get_json()["id"]
    run_worker()
    assert db.execute("SELECT hold FROM ess_submissions WHERE id=?", (sid,)).fetchone()[0] == "not_configured"


def test_trial_runs_one_whole_assignment(models, db):
    from core import essay

    c = SiteClient("teacher")
    assert "/teacher/trial/essay" in c.get("/teacher/").get_data(as_text=True)
    assert "开始试用" in c.get("/teacher/trial/essay").get_data(as_text=True)
    r = jpost(c, "/api/trial/essay/start")
    assert r.status_code == 200, r.get_json()
    lid = db.execute("SELECT max(license_id) FROM ess_trials").fetchone()[0]
    lic = db.execute("SELECT * FROM licenses WHERE site='teacher' AND id=?", (lid,)).fetchone()
    assert lic["buyer"].startswith("免费试用") and lic["device_limit"] == 1
    assert tuple(db.execute("SELECT students,monthly FROM ess_quota WHERE license_id=?", (lid,)).fetchone()) == (essay.TRIAL_STUDENTS, essay.TRIAL_ESSAYS)
    home = c.get("/teacher/").get_data(as_text=True)
    assert "trial-banner" in home and "还剩 7 天" in home
    # The normal flow: a class, students, one assignment; a second one is refused.
    cid = jpost(c, "/api/classes", {"name": "试用班", "grade": 3}).get_json()["id"]
    assert jpost(c, f"/api/classes/{cid}/students", {"names": "小红\n小蓝"}).get_json()["added"] == 2
    aid = jpost(c, f"/api/classes/{cid}/assignments", {"title": "快乐的一天", "auto_publish": True}).get_json()["id"]
    assert "只能布置 1 次" in jpost(c, f"/api/classes/{cid}/assignments", {"title": "再来"}).get_json()["error"]
    jpost(c, f"/api/assignments/{aid}/delete")
    assert "只能布置 1 次" in jpost(c, f"/api/classes/{cid}/assignments", {"title": "删了再来"}).get_json()["error"]
    db.execute("UPDATE ess_assignments SET deleted_at=NULL WHERE id=?", (aid,))
    db.commit()
    s, _ = student_client(db, cid, "小红")
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert s.get(f"/student/essay/results/{sid}").status_code == 200
    # The essay limit counts the whole trial, not the month.
    db.execute("UPDATE ess_quota SET monthly=1 WHERE license_id=?", (lid,))
    db.execute("UPDATE ess_submissions SET created_at=1 WHERE id=?", (sid,))
    db.commit()
    s2, _ = student_client(db, cid, "小蓝")
    assert "试用的批改篇数已经用完" in jpost(s2, f"/api/write/{aid}", {"text": ESSAY}).get_json()["error"]
    # Signed out, the same browser gets back into its trial; it cannot open a second one.
    jpost(c, "/api/logout")
    assert "回到我的试用" in c.get("/teacher/trial/essay").get_data(as_text=True)
    assert jpost(c, "/api/trial/essay/start").status_code == 200
    assert db.execute("SELECT count(*) FROM ess_trials WHERE license_id>=?", (lid,)).fetchone()[0] == 1
    assert "trial-banner" in c.get("/teacher/").get_data(as_text=True)
    # Another browser: the daily cap across the site.
    db.execute("INSERT OR REPLACE INTO ess_meta VALUES('trial_daily','1')")
    db.commit()
    assert "名额" in jpost(SiteClient("teacher"), "/api/trial/essay/start").get_json()["error"]
    db.execute("DELETE FROM ess_meta WHERE key='trial_daily'")
    db.commit()


def test_admin_converts_a_trial_and_keeps_its_data(models, db):
    c = SiteClient("teacher")
    assert jpost(c, "/api/trial/essay/start").status_code == 200
    lid = db.execute("SELECT max(license_id) FROM ess_trials").fetchone()[0]
    assert f"试用编号 {lid}" in c.get("/teacher/").get_data(as_text=True)
    cid = jpost(c, "/api/classes", {"name": "试用班", "grade": 3}).get_json()["id"]
    jpost(c, f"/api/classes/{cid}/students", {"names": "小红"})
    jpost(c, f"/api/classes/{cid}/assignments", {"title": "快乐的一天"})
    a = SiteClient("teacher")
    a.login()
    assert "转为正式" in a.get(f"/admin/licenses/{lid}").get_data(as_text=True)
    assert "转为正式" not in a.get("/admin/licenses/2").get_data(as_text=True)
    assert a.post("/admin/licenses/2/trial_convert", {"buyer": "x", "days": "365", "students": "60", "monthly": "300", "device_limit": "2"}).status_code == 404
    form = {"buyer": "王老师 订单 123", "days": "365", "students": "50", "monthly": "200", "device_limit": "3"}
    a.post(f"/admin/licenses/{lid}/trial_convert", dict(form, buyer=" "))
    assert db.execute("SELECT 1 FROM ess_trials WHERE license_id=?", (lid,)).fetchone()
    a.post(f"/admin/licenses/{lid}/trial_convert", form)
    assert not db.execute("SELECT 1 FROM ess_trials WHERE license_id=?", (lid,)).fetchone()
    lic = db.execute("SELECT * FROM licenses WHERE site='teacher' AND id=?", (lid,)).fetchone()
    assert lic["buyer"] == "王老师 订单 123" and lic["device_limit"] == 3
    assert tuple(db.execute("SELECT students,monthly FROM ess_quota WHERE license_id=?", (lid,)).fetchone()) == (50, 200)
    expires = db.execute("SELECT expires_at FROM ws_entitlements WHERE license_id=? AND feature='essay'", (lid,)).fetchone()[0]
    assert 364 * 86400 < expires - time.time() < 366 * 86400
    assert db.execute("SELECT 1 FROM audit WHERE action='trial_convert' AND license_id=?", (lid,)).fetchone()
    # The teacher carries on in the same browser: no banner, the class is still there, more assignments are allowed.
    home = c.get("/teacher/").get_data(as_text=True)
    assert "trial-banner" not in home
    assert [r[0] for r in db.execute("SELECT name FROM ess_students WHERE class_id=?", (cid,))] == ["小红"]
    assert jpost(c, f"/api/classes/{cid}/assignments", {"title": "第二次"}).get_json().get("id")
    # This browser still cannot open another trial.
    jpost(c, "/api/logout")
    assert "已经试用过" in jpost(c, "/api/trial/essay/start").get_json()["error"]


def test_corrections_that_cannot_be_right_are_dropped():
    essay = {"grade": 3, "genre": "", "requirement": "", "min_chars": 0, "title": "", "text": ESSAY + "我看见□只鸟。"}
    dims = {k: {"score": 3} for k in grading.DIMS}
    marks = [
        {"quote": "高心", "type": "错别字", "fix": "高兴", "note": ""},  # kept
        {"quote": "公园里的花", "type": "错别字", "fix": "公园中美丽的花", "note": ""},  # length changes: dropped
        {"quote": "我记住了。", "type": "标点", "fix": "我记得了！", "note": ""},  # words change: dropped
        {"quote": "这真是快乐的一天！", "type": "标点", "fix": "这真是快乐的一天。", "note": ""},  # kept
        {"quote": "我看见□只鸟", "type": "病句", "fix": "我看见一只鸟", "note": ""},  # unreadable: dropped
    ]
    kept = [m["quote"] for m in grading.clean_review({"dims": dims, "marks": marks}, essay)["marks"]]
    assert kept == ["高心", "这真是快乐的一天！"]


def test_pinyin_is_written_as_characters():
    essay = {"grade": 1, "genre": "", "requirement": "", "min_chars": 0, "title": "", "text": "我很gāo xìng。小鸟在fēi。我们去hua园。"}
    dims = {k: {"score": 3} for k in grading.DIMS}
    marks = [
        {"quote": "gāo xìng", "type": "错别字", "fix": "高兴", "note": ""},  # kept
        {"quote": "在fēi", "type": "错别字", "fix": "在fei飞", "note": ""},  # fix still has pinyin: dropped
        {"quote": "去hua园", "type": "错别字", "fix": "到花园", "note": ""},  # changes a character around it: dropped
    ]
    kept = [(m["quote"], m["fix"]) for m in grading.clean_review({"dims": dims, "marks": marks}, essay)["marks"]]
    assert kept == [("gāo xìng", "高兴")]
    assert grading.plausible_fix("错别字", "去hua园", "去花园")


def test_check_covers_tips_and_rejects_skipped(models, klass, db):
    t, cid, aid = klass
    models.grade = grade_answer(marks=[
        {"quote": "高心", "type": "错别字", "fix": "高兴", "note": ""},
        {"quote": "我记住了", "type": "修改建议", "fix": "我把妈妈的话牢牢记在心里", "note": "写具体"},
        {"quote": "我和妈妈去公园玩", "type": "病句", "fix": "我和妈妈一起去公园玩", "note": ""},
    ])
    # Text order: 1 = 我和妈妈去公园玩, 2 = 高心, 3 = 我记住了 (the tip); the checker says nothing about 3.
    models.check = {"items": [{"id": 1, "ok": True}, {"id": 2, "ok": True}], "level": "良"}
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    check_body = [b for m, b in models.calls if m == "small"][-1]
    listed = json.loads(check_body["messages"][1]["content"].split("需要复核的标注：\n")[1])
    assert [i["type"] for i in listed] == ["病句", "错别字", "修改建议"]
    review = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
    assert [m["quote"] for m in review["marks"]] == ["我和妈妈去公园玩", "高心"]
    assert review["rejected"][0]["quote"] == "我记住了"


def test_reviews_rescaled_to_25(db):
    from core import schema

    sid = db.execute("SELECT max(id) FROM ess_submissions").fetchone()[0]
    old = {"dims": {"content": {"score": 35, "max": 35}, "structure": {"score": 10, "max": 20}, "language": {"score": 21, "max": 30}, "norms": {"score": 15, "max": 15}},
           "total": 81, "level": "良", "check_level": "中", "marks": []}
    saved = db.execute("SELECT * FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()
    db.execute("INSERT OR REPLACE INTO ess_reviews(submission_id,ai,final,model,prompt_version,created_at) VALUES(?,?,NULL,'m','v',1)", (sid, json.dumps(old)))
    schema.rescale_reviews(db)
    grade = db.execute("SELECT c.grade FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id WHERE s.id=?", (sid,)).fetchone()[0]
    top = grading.BANDS[grading.band_of(grade)]["max"]
    new = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
    assert new["dims"]["content"] == {"score": top["content"], "max": top["content"]}
    assert new["total"] == sum(d["score"] for d in new["dims"].values()) and new["level"] == grading.level_of(new["total"])
    assert new["check_level"] == "及格"
    if saved:
        db.execute("UPDATE ess_reviews SET ai=?,final=? WHERE submission_id=?", (saved["ai"], saved["final"], sid))
    db.commit()


# ---- devices, feature pages, classes overview, student wording ----
def test_device_name():
    from core.essay import device_name

    assert device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit Mobile MicroMessenger/8.0") == "iPhone · 微信"
    assert device_name("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit Chrome/120 Safari/537") == "Windows 电脑 · Chrome"
    assert device_name("Mozilla/5.0 (Linux; Android 14; ALN) AppleWebKit Mobile HuaweiBrowser/15 Chrome Safari") == "安卓手机 · 华为浏览器"
    assert device_name("") == "未知设备"


def test_teacher_sees_and_removes_own_devices(db):
    db.execute("DELETE FROM limits")  # activations are rate limited per address
    db.commit()
    db.execute("DELETE FROM devices WHERE site='teacher' AND license_id=2")
    db.execute("DELETE FROM audit WHERE site='teacher' AND action='device_self_remove'")
    db.commit()
    phones = [teacher_client(TEACHER2_KEY) for _ in range(5)]
    me = phones[0]
    page = me.get("/teacher/profile").get_data(as_text=True)
    assert 'id="devices"' in page and "本机" in page and "<strong>5</strong> 台设备" in page
    assert "我的设备（5/100）" in page  # in the header menu
    rows = db.execute("SELECT created_at FROM devices WHERE site='teacher' AND license_id=2 ORDER BY created_at,token_hash").fetchall()
    links = re.findall(r'devices/(\d+)/remove" data-body=\'\{"created_at": (\d+)\}\'', page)
    assert len(links) == 4  # every device but this one
    mine = next(n for n in range(1, 6) if str(n) not in [x[0] for x in links])
    r = jpost(me, f"/api/me/devices/{mine}/remove", {"created_at": rows[mine - 1]["created_at"]})
    assert r.status_code == 400 and "正在用" in r.get_json()["error"]
    # Remove three others; the removed browsers are signed out at once.
    for _ in range(3):
        page = me.get("/teacher/profile").get_data(as_text=True)
        n, created = re.findall(r'devices/(\d+)/remove" data-body=\'\{"created_at": (\d+)\}\'', page)[0]
        assert jpost(me, f"/api/me/devices/{n}/remove", {"created_at": int(created)}).status_code == 200
    assert db.execute("SELECT count(*) FROM devices WHERE site='teacher' AND license_id=2").fetchone()[0] == 2
    signed_out = [p for p in phones[1:] if "teacher-activate" in p.get("/teacher/").get_data(as_text=True)]
    assert len(signed_out) == 3
    # The fourth removal within 30 days needs the admin.
    page = me.get("/teacher/profile").get_data(as_text=True)
    assert "移除的次数已经用完" in page
    left = db.execute("SELECT created_at FROM devices WHERE site='teacher' AND license_id=2 ORDER BY created_at,token_hash").fetchall()
    errors = [jpost(me, f"/api/me/devices/{n}/remove", {"created_at": left[n - 1][0]}).get_json()["error"] for n in (1, 2)]
    assert any("管理员" in e for e in errors) and any("正在用" in e for e in errors)
    assert db.execute("SELECT count(*) FROM audit WHERE site='teacher' AND action='device_self_remove' AND license_id=2").fetchone()[0] == 3
    # A stale list does not remove the wrong device.
    assert jpost(me, "/api/me/devices/1/remove", {"created_at": 1}).status_code == 400


def test_home_lists_features_and_header_leads_to_classes(models, klass, db):
    t, cid, aid = klass
    home = t.get("/teacher/").get_data(as_text=True)
    assert "我的功能" in home and "我的班级" in home  # the menu entry
    assert "新建班级" not in home and "class-create" not in home  # classes moved behind the header button
    assert '/workspace/chinese/teacher/essay/' in home and "lesson" not in home and "教案" not in home
    assert 'class="header-chip" href="/workspace/chinese/teacher/classes/"' in home
    # Classes overview: statistics per class.
    db.execute("UPDATE ess_assignments SET auto_publish=1 WHERE id=?", (aid,))
    db.commit()
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    page = t.get("/teacher/classes/").get_data(as_text=True)
    assert "三年级二班" in page and 'id="class-create"' not in page and "只能建一个班级" in page and "交齐率" in page and "33%" in page
    r = jpost(t, "/api/classes", {"name": "三年级三班", "grade": 3})
    assert r.status_code == 400 and "只能建一个班级" in r.get_json()["error"]
    assert 'class="level-bar"' in page
    # The essay feature page: pending reviews, assignments, and 布置作文 for any class.
    essay = t.get("/teacher/essay/").get_data(as_text=True)
    assert "快乐的一天" in essay and "布置作文" in essay and "进行中的作业" in essay
    new = t.get(f"/teacher/essay/new?class={cid}").get_data(as_text=True)
    assert f'data-url="/workspace/chinese/api/classes/{cid}/assignments" selected' in new
    assert "布置给：<strong>三年级二班</strong>" in new and "作文主题" in new and "data-units" in new
    assert '"requirement"' in new and '"intro"' in new  # the textbook units
    assert 'name="genre" value="写人" data-many>' in new  # 文体: several or none, none ticked by default
    # The class page sends the teacher to the feature page to assign.
    assert f"/teacher/essay/new?class={cid}" in t.get(f"/teacher/classes/{cid}").get_data(as_text=True)


def test_essay_page_lists_reviews_waiting(models, klass, db):
    t, cid, aid = klass
    db.execute("UPDATE ess_assignments SET auto_publish=0 WHERE id=?", (aid,))
    db.commit()
    s, _ = student_client(db, cid, "李华")
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    page = t.get("/teacher/essay/").get_data(as_text=True)
    assert f"/teacher/essay/submissions/{sid}" in page and "李华" in page
    assert "1</span> 篇作文等审核" in t.get("/teacher/").get_data(as_text=True)


def test_students_never_read_ai(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid, "张三")
    words = ("AI", "模型", "智能", "识别")
    pages = [s.get("/student/").get_data(as_text=True), s.get(f"/student/essay/{aid}").get_data(as_text=True)]
    r = upload(s, f"/api/write/{aid}", [photo_bytes()])
    sid = r.get_json()["id"]
    pages.append(s.get(f"/student/essay/{aid}").get_data(as_text=True))
    pages.append(s.get(f"/api/submissions/{sid}/state").get_data(as_text=True))
    run_worker()
    pages += [s.get("/student/").get_data(as_text=True), s.get(f"/student/essay/results/{sid}").get_data(as_text=True)]
    for html in pages:
        for w in words:
            assert w not in html, (w, html[:300])
    assert "腹有诗书气自华" in pages[0]


def test_lesson_plans_are_gone(db):
    """教案生成 was removed: no pages, no table, no feature."""
    from core.essay import features

    assert "lesson" not in features.FEATURES
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='ws_lessons'").fetchone()
    c = SiteClient("teacher")
    assert c.get("/teacher/lesson/").status_code == 404
    assert c.get("/teacher/trial/lesson").status_code == 404
    assert "教案" not in c.get("/teacher/").get_data(as_text=True)


def test_migration_drops_lesson_data(tmp_path):
    import sqlite3
    from core import schema

    path = tmp_path / "old.sqlite3"
    schema.initialize(path)
    with sqlite3.connect(path) as d:
        d.execute("DELETE FROM migrations WHERE name='drop_lessons'")
        d.execute("CREATE TABLE ws_lessons(id INTEGER PRIMARY KEY)")
        d.execute("INSERT INTO ws_entitlements VALUES(5,'lesson',365,1,2),(5,'essay',365,1,2)")
        d.execute("INSERT INTO ess_meta VALUES('lesson_monthly','60')")
        d.execute("INSERT INTO trials VALUES('teacher','x','lesson',1)")
    schema.initialize(path)
    with sqlite3.connect(path) as d:
        assert not d.execute("SELECT 1 FROM sqlite_master WHERE name='ws_lessons'").fetchone()
        assert [r[0] for r in d.execute("SELECT feature FROM ws_entitlements WHERE license_id=5")] == ["essay"]
        assert not d.execute("SELECT 1 FROM ess_meta WHERE key='lesson_monthly'").fetchone()
        assert not d.execute("SELECT 1 FROM trials WHERE game='lesson'").fetchone()


def test_textbook_units_are_built_and_valid():
    import subprocess, sys
    from core.essay import UNITS

    r = subprocess.run([sys.executable, "tools/units.py", "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert UNITS["1"] == {"上册": [], "下册": []}
    assert all(len(UNITS[str(g)][t]) >= 3 for g in range(2, 7) for t in ("上册", "下册"))
    for terms in UNITS.values():
        for rows in terms.values():
            for u in rows:
                assert all(g in grading.CHOICES for g in u["genres"]) and u["topic"] and u["unit"].startswith("第")
                assert 0 < len(u["intro"]) <= 300 and 0 < len(u["requirement"]) <= 800
    u = UNITS["3"]["上册"][4]
    assert u["topic"] == "我们眼中的缤纷世界" and u["genres"] == ["写人", "写景", "状物"] and u["intro"].startswith("这段时间")
    assert u["requirement"].startswith("1. 观察的准确性") and UNITS["3"]["上册"][1]["genres"] == ["日记"]
    assert UNITS["2"]["上册"][1]["genres"] == []  # 留言条: 不指定


def test_genres_intro_and_delete(models, klass, db):
    t, cid, aid = klass
    # 文体: several kept in GENRES order and joined with 、; unknown ones dropped; none = ''.
    r = jpost(t, f"/api/classes/{cid}/assignments", {"title": "观察", "genre": ["状物", "写人", "乱写"], "intro": "把印象最深的写下来。", "requirement": "1. 观察的准确性"})
    a2 = r.get_json()["id"]
    row = db.execute("SELECT genre,intro,requirement FROM ess_assignments WHERE id=?", (a2,)).fetchone()
    assert tuple(row) == ("写人、状物", "把印象最深的写下来。", "1. 观察的准确性")
    assert jpost(t, f"/api/assignments/{a2}/edit", {"title": "观察", "genre": []}).get_json()["ok"]
    assert db.execute("SELECT genre FROM ess_assignments WHERE id=?", (a2,)).fetchone()[0] == ""
    msgs = grading.grade_messages({"grade": 3, "genre": "", "topic": "观察", "intro": "引言", "requirement": "", "min_chars": 0, "title": "", "text": ESSAY})
    assert "没有指定文体" in msgs[0]["content"] and "文体：不指定" in msgs[1]["content"] and "引言（学生看到的题目说明）：引言" in msgs[1]["content"]
    two = grading.grade_messages({"grade": 3, "genre": "写人、状物", "requirement": "", "min_chars": 0, "title": "", "text": ESSAY})
    assert "写人：" in two[0]["content"] and "状物：" in two[0]["content"]
    jpost(t, f"/api/assignments/{a2}/edit", {"title": "观察", "intro": "引言给学生看", "requirement": "重点只给老师看"})

    # Students see the 引言, not the 习作重点.
    s, _ = student_client(db, cid)
    page = s.get(f"/student/essay/{a2}").get_data(as_text=True)
    assert "引言给学生看" in page and "重点只给老师看" not in page and "一般只交一次" in page
    assert "引言给学生看" in t.get(f"/teacher/essay/assignments/{a2}").get_data(as_text=True)

    # Deleting hides it everywhere but the month's count stays.
    sid = jpost(s, f"/api/write/{a2}", {"text": ESSAY}).get_json()["id"]
    count = "SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id WHERE a.class_id=?"
    used = db.execute(count, (cid,)).fetchone()[0]
    r = jpost(t, f"/api/assignments/{a2}/delete")
    assert r.get_json()["url"].endswith("/teacher/essay/")
    assert t.get(f"/teacher/essay/assignments/{a2}").status_code == 404
    assert t.get(f"/teacher/essay/submissions/{sid}").status_code == 404
    assert "观察" not in t.get("/teacher/essay/").get_data(as_text=True)
    assert "观察" not in s.get("/student/").get_data(as_text=True)
    assert s.get(f"/student/essay/{a2}").status_code == 404
    assert jpost(s, f"/api/write/{a2}", {"text": ESSAY}).status_code == 404
    assert db.execute(count, (cid,)).fetchone()[0] == used  # monthly_used() still counts it


def test_assignment_export_lists_graded_essays(models, klass, db):
    t, cid, aid = klass
    db.execute("UPDATE ess_assignments SET auto_publish=1 WHERE id=?", (aid,))
    db.commit()
    s, _ = student_client(db, cid, "李华")
    jpost(s, f"/api/write/{aid}", {"text": ESSAY})
    run_worker()
    s2, _ = student_client(db, cid, "张三")
    jpost(s2, f"/api/write/{aid}", {"text": ESSAY})  # still queued: skipped
    page = t.get(f"/teacher/essay/assignments/{aid}").get_data(as_text=True)
    assert 'id="export-all"' in page and "redpen" in page
    r = t.get(f"/teacher/essay/assignments/{aid}/export.json")
    data = r.get_json()
    assert r.status_code == 200 and len(data["essays"]) == 1 and data["skipped"] == 2
    assert data["essays"][0]["student"] == "李华" and data["essays"][0]["marks"] and "快乐的一天" in data["name"]
    assert teacher_client(TEACHER2_KEY).get(f"/teacher/essay/assignments/{aid}/export.json").status_code == 404
    assert SiteClient("teacher").get(f"/teacher/essay/assignments/{aid}/export.json").status_code == 403


def test_typo_verify_helpers():
    text = "我设有\n去公园，我高心极了。"
    marks = [
        {"type": "错别字", "start": 1, "end": 3, "quote": "设有", "fix": "没有", "note": ""},
        {"type": "错别字", "start": 9, "end": 11, "quote": "高心", "fix": "高兴", "note": ""},
        {"type": "病句", "start": 0, "end": 3, "quote": "我设有", "fix": "我没有", "note": ""},
    ]
    review = {"marks": marks, "rejected": []}
    pages = [(7, [{"s": 0, "e": 3, "b": [10, 10, 900, 60]}, {"s": 4, "e": 14, "b": [10, 80, 900, 130]}])]
    targets = grading.typo_targets(review, text, pages)
    assert [(t["id"], t["mark"], t["wrong"], t["right"], t["nth"], t["iid"]) for t in targets] == [(1, 0, "设", "没", 2, 7), (2, 1, "心", "兴", 7, 7)]
    assert targets[1]["line"] == "去公园，我高心极了。"
    # A line without a box is not looked at.
    assert [t["mark"] for t in grading.typo_targets(review, text, [(7, pages[0][1][:1])])] == [0]
    msgs = grading.verify_messages(targets, [b"a", b"b"])
    assert "第 2 个字（识别成了「设」，批改认为应该是「没」）" in msgs[1]["content"][0]["text"]
    with pytest.raises(ValueError):
        grading.parse_verify({"items": [{"id": 1, "seen": "没"}]}, 2)
    seen = grading.parse_verify({"items": [{"id": 1, "seen": "没"}, {"id": 2, "seen": "心"}]}, 2)
    new, fixed, dropped = grading.apply_verify(review, text, targets, seen)
    # 没 was written correctly: that mark goes and the text is corrected; 高心 really is wrong and stays.
    assert fixed == "我没有\n去公园，我高心极了。" and [m["quote"] for m in dropped] == ["设有"]
    assert [m["quote"] for m in new["marks"]] == ["高心", "我没有"]
    # Unreadable or another character: nothing changes.
    for answer in ("□", "设", "汉"):
        assert grading.apply_verify(review, text, targets, {1: answer, 2: "心"})[2] == []
    # A mark changing two characters goes only when both are confirmed.
    two = {"marks": [{"type": "错别字", "start": 9, "end": 11, "quote": "高心", "fix": "开兴", "note": ""}]}
    t2 = grading.typo_targets(two, text, pages)
    assert len(t2) == 2
    assert grading.apply_verify(two, text, t2, {1: "开", 2: "心"})[2] == []
    assert grading.apply_verify(two, text, t2, {1: "开", 2: "兴"})[1].endswith("我开兴极了。")


def test_photo_typo_misread_is_dropped_and_text_corrected(models, klass, db):
    t, cid, aid = klass
    st = students_of(db, cid)["王小明"]
    r = upload(t, f"/api/assignments/{aid}/submit_for", [photo_bytes()], student_id=str(st["id"]))
    sid = r.get_json()["id"]
    models.grade = grade_answer(marks=[{"quote": "高心", "type": "错别字", "fix": "高兴", "note": ""}])
    models.check = {"items": [{"id": 1, "reason": "确实写错", "ok": True}], "level": "良"}
    models.verify = {"items": [{"id": 1, "seen": "兴"}]}
    run_worker()
    assert len(models.verified) == 1
    sub = db.execute("SELECT * FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    assert "我高兴极了。" in sub["text"] and "高心" not in sub["text"]
    ai = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
    assert not [m for m in ai["marks"] if m["type"] == "错别字"]
    assert any(m["quote"] == "高心" and "识别" in m["reason"] for m in ai["rejected"])
    # The photo really shows the wrong character: the mark stays; a failing check call keeps it too.
    for verify in ({"items": [{"id": 1, "seen": "心"}]}, {"bad": 1}):
        r = upload(t, f"/api/assignments/{aid}/submit_for", [photo_bytes()], student_id=str(students_of(db, cid)["李华"]["id"]))
        sid = r.get_json()["id"]
        models.verify = verify
        run_worker()
        ai = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
        assert [m["quote"] for m in ai["marks"] if m["type"] == "错别字"] == ["高心"]
        db.execute("DELETE FROM ess_submissions WHERE id=?", (sid,))
        db.commit()


def test_typed_essays_are_not_looked_at_on_a_photo(models, klass, db):
    t, cid, aid = klass
    s, _ = student_client(db, cid, "张三")
    models.grade = grade_answer(marks=[{"quote": "高心", "type": "错别字", "fix": "高兴", "note": ""}])
    models.check = {"items": [{"id": 1, "ok": True}], "level": "良"}
    assert jpost(s, f"/api/write/{aid}", {"title": "", "text": ESSAY}).status_code == 200
    run_worker()
    assert models.verified == []
    check = [b for m, b in models.calls if "复核" in b["messages"][0]["content"][:60]][-1]
    assert "照片识别" not in check["messages"][1]["content"]


def test_post_explains_thinking_model_out_of_tokens(monkeypatch):
    import io, urllib.request

    answer = b'{"choices": [{"index": 0, "message": {"role": "assistant"}, "finish_reason": "length"}], "usage": {"completion_tokens": 17}}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: io.BytesIO(answer))
    row = {"base_url": "https://x/v1", "key_enc": llm.encrypt_key("sk-1"), "timeout": 5, "provider_id": 999, "concurrency": 1}
    with pytest.raises(llm.LLMError) as e:
        llm.post(row, {"model": "gemini-3.8-flash", "messages": []})
    assert "最大输出 token" in str(e.value) and not e.value.retry
