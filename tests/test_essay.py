"""语文老师工作台: accounts, the submit → grade → review → publish flow, model settings and the grading checks.
Model calls are faked (core.essay.llm.post), so nothing here touches the network."""

import json, re, time
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
            "content": {"score": 30, "reason": "内容完整"},
            "structure": {"score": 18, "reason": "顺序清楚"},
            "language": {"score": 26, "reason": "有比喻"},
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


class FakeModels:
    """Stands in for llm.post: answers grade and check requests, records every call."""

    def __init__(self):
        self.calls = []
        self.grade = grade_answer()
        # Error marks are numbered in text order: 1 = 我和妈妈去公园玩 (病句), 2 = 高心 (错别字).
        self.check = {"items": [{"id": 1, "ok": False, "reason": "原句没有错"}, {"id": 2, "ok": True}], "level": "良"}
        self.fail = set()  # models that raise

    def __call__(self, row, body):
        self.calls.append((row["model"], body))
        if row["model"] in self.fail:
            raise llm.LLMError("HTTP 429: rate limited")
        system = body["messages"][0]["content"]
        answer = self.check if "复核" in system[:60] else self.grade
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
        [("grade", "main", "big", 4.0, 0.2, 12.0), ("check", "main", "small", 1.0, 0.0, 2.0)],
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
def klass():
    """A teacher with a fresh class of three students and one assignment."""
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
    assert "快乐的一天" in s.get("/").get_data(as_text=True)
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
    # Scores clamped to the band maximum (中年级 norms = 15); total and level computed by the server.
    assert review["dims"]["norms"]["score"] == 15 and review["total"] == 30 + 18 + 26 + 15 and review["level"] == "良"
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
    assert s.get(f"/results/{sid}").status_code == 404
    page = t.get(f"/submissions/{sid}").get_data(as_text=True)
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
    edit["dims"]["content"]["score"] = 20
    start = ESSAY.index("这真是")
    edit["marks"].append({"type": "修改建议", "start": start, "end": start + 7, "fix": "", "note": "结尾可以再写写感受"})
    assert jpost(t, f"/api/submissions/{sid}/review", {"review": dict(edit, dims=dict(edit["dims"], norms={"score": 16}))}).status_code == 400
    r = jpost(t, f"/api/submissions/{sid}/review", {"review": edit, "publish": True})
    assert r.status_code == 200 and r.get_json()["level"] == "良"
    html = s.get(f"/results/{sid}").get_data(as_text=True)
    assert "老师改过的评语" in html and "结尾可以再写写感受" in html and 'class="mk mk-typo"' in html
    assert "分</small>" not in html  # students see the level, not the score
    assert "分</small>" in t.get(f"/results/{sid}").get_data(as_text=True)

    # After publishing the student may resubmit, up to MAX_ATTEMPTS in all.
    r = jpost(s, f"/api/write/{aid}", {"text": ESSAY + "我还想再去。"})
    assert r.status_code == 200
    db.execute("UPDATE ess_submissions SET status='published' WHERE assignment_id=?", (aid,))
    db.commit()
    assert jpost(s, f"/api/write/{aid}", {"text": ESSAY}).status_code == 200
    db.execute("UPDATE ess_submissions SET status='published' WHERE assignment_id=?", (aid,))
    db.commit()
    assert "最多交" in jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["error"]


def test_auto_publish_and_publish_all(models, klass, db):
    t, cid, aid = klass
    aid2 = jpost(t, f"/api/classes/{cid}/assignments", {"title": "自动", "genre": "日记", "auto_publish": True}).get_json()["id"]
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid2}", {"text": ESSAY}).get_json()["id"]
    s2, _ = student_client(db, cid, "李华")
    sid2 = jpost(s2, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert s.get(f"/results/{sid}").status_code == 200
    assert db.execute("SELECT status FROM ess_submissions WHERE id=?", (sid2,)).fetchone()[0] == "review"
    assert jpost(t, f"/api/assignments/{aid}/publish_all").get_json()["published"] == 1
    assert s2.get(f"/results/{sid2}").status_code == 200


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
    assert [r[0] for r in db.execute("SELECT ok FROM ess_usage WHERE submission_id=? AND step='grade' ORDER BY id", (sid,))] == [0, 1]

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


def test_check_failure_keeps_marks_and_flags(models, klass, db):
    t, cid, aid = klass
    models.fail = {"small"}
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    review = json.loads(db.execute("SELECT ai FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()[0])
    assert "unchecked" in review["flags"] and any(m["quote"] == "我和妈妈去公园玩" for m in review["marks"])


def test_holds_when_not_configured_or_over_budget(models, klass, db):
    t, cid, aid = klass
    db.execute("DELETE FROM ess_steps WHERE step='grade'")
    db.commit()
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    state = s.get(f"/api/submissions/{sid}/state").get_json()
    assert state["status"] == "queued" and "暂未开通" in state["hold"]
    db.execute("INSERT INTO ess_steps(step,slot,provider_id,model) VALUES('grade','main',1,'big')")
    db.execute("INSERT INTO ess_meta VALUES('daily_budget','0.0000001')")
    db.execute("INSERT INTO ess_usage(license_id,step,model,tokens_in,tokens_cached,tokens_out,cost,ok,created_at) VALUES(1,'grade','x',0,0,0,1,1,?)", (int(time.time()),))
    db.execute("UPDATE ess_jobs SET run_after=0")
    db.commit()
    run_worker()
    assert "额度" in s.get(f"/api/submissions/{sid}/state").get_json()["hold"]
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
    assert "我的作文" in s.get("/").get_data(as_text=True)
    jpost(t, f"/api/students/{st['id']}/pin")  # a new reset signs the student out
    assert "我是学生" in s.get("/").get_data(as_text=True)


def test_isolation_between_teachers_and_students(models, klass, db):
    t, cid, aid = klass
    other = teacher_client(TEACHER2_KEY)
    assert other.get(f"/classes/{cid}").status_code == 404
    assert other.get(f"/assignments/{aid}").status_code == 404
    assert jpost(other, f"/api/classes/{cid}/students", {"names": "坏人"}).status_code == 404
    s, _ = student_client(db, cid)
    sid = jpost(s, f"/api/write/{aid}", {"text": ESSAY}).get_json()["id"]
    run_worker()
    assert other.get(f"/submissions/{sid}").status_code == 404
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
    assert grading.level_of(90) == "优" and grading.level_of(89) == "良" and grading.level_of(59) == "待提高"
    assert grading.clean_text("  a  \r\n\r\n\r\n\r\nb　") == "  a\n\nb"
    essay = {"grade": 1, "genre": "看图写话", "requirement": "", "min_chars": 0, "title": "", "text": ESSAY}
    with pytest.raises(ValueError):
        grading.clean_review({"dims": {}}, essay)
    msgs = grading.grade_messages(essay)
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
