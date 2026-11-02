"""教案生成: a teacher enters a lesson (grade, term, kind, topic, how many periods, optional notes and the text of the
lesson) and the essay worker writes a lesson plan with the 'lesson' model step (prompts/lesson.md).

ws_lessons holds the request and its state (queued → running → done / failed; `hold` while the step is not
configured or the daily budget is spent), the plan as the model wrote it (`plan`, cleaned by clean()) and the
teacher's edited version (`edited`). 重新生成 makes a new row from the same inputs, so every generation counts
towards the monthly limit (ess_meta 'lesson_monthly', LESSON_MONTHLY by default; failed ones do not count).
A lesson plan downloads as an HTML-based .doc that Word and WPS open, so the server needs no extra library.
Trials: one per browser (trials, game 'lesson'), filed under license 0 and remembered in session["trial_lesson"].
"""

import json, logging, re, time
from html import escape
from urllib.parse import quote

from . import llm
from .grading import PROMPTS

LESSON_PROMPT = (PROMPTS / "lesson.md").read_text(encoding="utf-8")
KINDS = ("阅读课", "识字写字", "古诗文", "习作", "口语交际", "语文园地", "复习课")
TERMS = ("上册", "下册")
LESSON_MONTHLY = 60
PERIOD_MINUTES = 40
MAX_PERIODS = 3
TOPIC_MAX, NOTES_MAX, SOURCE_MAX = 30, 300, 4000
RETRY_DELAYS = (30, 120, 600)
STALE_SECONDS = 900
HOLD_SECONDS = 60
STATUS = {"queued": "排队中", "running": "正在写教案", "done": "已完成", "failed": "生成失败"}
HOLDS = {
    "not_configured": "教案生成服务暂未开通，开通后会自动生成",
    "budget": "今天的生成额度用完了，稍后会自动继续",
}
# Longest text kept for each part of a plan (a model may ramble; a teacher may write more when editing).
LIMITS = {
    "title": 40, "analysis": 600, "learners": 400, "goal": 120, "key": 200, "difficult": 200, "prep": 200,
    "period": 20, "step": 40, "teacher": 1200, "student": 600, "intent": 200, "board": 400, "homework": 300,
}
MAX_GOALS, MAX_STEPS = 6, 12
log = logging.getLogger("essay-worker")


# ---- the plan ----
def _text(value, limit, keep_lines=True):
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "".join(c for c in value if c == "\n" or c.isprintable())
    if keep_lines:
        value = re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in value.split("\n"))).strip()
    else:
        value = " ".join(value.split())
    return value[:limit]


def _fix_minutes(steps):
    """Makes the steps of one period add up to PERIOD_MINUTES, changing the longest step (or scaling all)."""
    total = sum(s["minutes"] for s in steps)
    if total == PERIOD_MINUTES or not steps:
        return
    longest = max(steps, key=lambda s: s["minutes"])
    if longest["minutes"] + PERIOD_MINUTES - total >= 2:
        longest["minutes"] += PERIOD_MINUTES - total
        return
    for s in steps:
        s["minutes"] = max(1, round(s["minutes"] * PERIOD_MINUTES / total))
    steps[-1]["minutes"] = max(1, steps[-1]["minutes"] + PERIOD_MINUTES - sum(s["minutes"] for s in steps))


def clean(plan, periods=None, fix_minutes=True):
    """A plan as stored and shown. Raises ValueError when it is unusable (a model answer: retried)."""
    if not isinstance(plan, dict):
        raise ValueError("教案格式不对")
    out = {k: _text(plan.get(k), LIMITS[k], k not in ("title", "key", "difficult", "prep"))
           for k in ("title", "analysis", "learners", "key", "difficult", "prep")}
    goals = plan.get("goals")
    goals = goals if isinstance(goals, list) else [goals] if isinstance(goals, str) else []
    out["goals"] = [g for g in (_text(g, LIMITS["goal"], False) for g in goals[:MAX_GOALS]) if g]
    out["periods"] = []
    raw_periods = plan.get("periods") if isinstance(plan.get("periods"), list) else []
    for i, p in enumerate(raw_periods[: periods or MAX_PERIODS], 1):
        if not isinstance(p, dict):
            continue
        steps = []
        for s in (p.get("steps") if isinstance(p.get("steps"), list) else [])[:MAX_STEPS]:
            if not isinstance(s, dict):
                continue
            try:
                minutes = int(float(s.get("minutes") or 0))
            except (TypeError, ValueError):
                minutes = 0
            step = {
                "name": _text(s.get("name"), LIMITS["step"], False),
                "minutes": min(PERIOD_MINUTES, max(1, minutes)),
                "teacher": _text(s.get("teacher"), LIMITS["teacher"]),
                "student": _text(s.get("student"), LIMITS["student"]),
                "intent": _text(s.get("intent"), LIMITS["intent"]),
            }
            if step["name"] or step["teacher"]:
                steps.append(step)
        if not steps:
            continue
        if fix_minutes:
            _fix_minutes(steps)
        name = _text(p.get("name"), LIMITS["period"], False) or f"第{'一二三'[i - 1] if i <= 3 else i}课时"
        out["periods"].append({"name": name, "steps": steps})
    out["board"] = _text(plan.get("board"), LIMITS["board"])
    hw = plan.get("homework") if isinstance(plan.get("homework"), dict) else {}
    out["homework"] = {k: _text(hw.get(k), LIMITS["homework"]) for k in ("basic", "extend")}
    if not out["title"] or not out["goals"] or not out["periods"]:
        raise ValueError("教案缺少课题、教学目标或教学过程")
    if periods and len(out["periods"]) < periods:
        raise ValueError(f"要 {periods} 个课时，只写了 {len(out['periods'])} 个")
    return out


def parse(periods):
    return lambda content: clean(llm.parse_json(content), periods)


def messages(row):
    user = (
        f"年级：{row['grade']}年级{row['term']}\n课型：{row['kind']}\n课题：{row['topic']}\n"
        f"课时：共 {row['periods']} 课时（periods 数组正好 {row['periods']} 项）\n"
        f"老师的补充说明：{row['notes'] or '无'}\n"
        f"课文原文：\n{row['source_text'] or '（老师没有提供，不要编造原句）'}"
    )
    return [{"role": "system", "content": LESSON_PROMPT}, {"role": "user", "content": user}]


def to_text(plan):
    """The whole plan as plain text (复制全文)."""
    out = [plan["title"], ""]
    for label, key in (("教材分析", "analysis"), ("学情分析", "learners")):
        if plan.get(key):
            out += [f"【{label}】", plan[key], ""]
    out += ["【教学目标】"] + [f"{i}. {g}" for i, g in enumerate(plan["goals"], 1)] + [""]
    for label, key in (("教学重点", "key"), ("教学难点", "difficult"), ("教学准备", "prep")):
        if plan.get(key):
            out += [f"【{label}】{plan[key]}"]
    out += ["", "【教学过程】"]
    for p in plan["periods"]:
        out += ["", p["name"]]
        for s in p["steps"]:
            out += [f"{s['name']}（{s['minutes']} 分钟）"]
            out += [f"教师活动：{s['teacher']}"] if s["teacher"] else []
            out += [f"学生活动：{s['student']}"] if s["student"] else []
            out += [f"设计意图：{s['intent']}"] if s["intent"] else []
    if plan.get("board"):
        out += ["", "【板书设计】", plan["board"]]
    hw = plan.get("homework") or {}
    if hw.get("basic") or hw.get("extend"):
        out += ["", "【作业设计】"] + ([f"基础：{hw['basic']}"] if hw.get("basic") else []) + ([f"拓展：{hw['extend']}"] if hw.get("extend") else [])
    return "\n".join(out).strip() + "\n"


def _html(text):
    return escape(text).replace("\n", "<br>")


def to_doc(plan, meta):
    """An HTML document Word and WPS open as a .doc (tables with borders, 宋体 body, A4 margins)."""
    h = [
        '<html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" '
        'xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"><title>' + escape(plan["title"]) + "</title>",
        "<style>@page{size:21cm 29.7cm;margin:2cm 2cm}body{font-family:宋体,SimSun,serif;font-size:12pt;line-height:1.6}"
        "h1{font-family:黑体,SimHei,sans-serif;font-size:18pt;text-align:center}h2{font-family:黑体,SimHei,sans-serif;font-size:13pt;margin:14pt 0 4pt}"
        "h3{font-size:12pt;margin:10pt 0 4pt}table{border-collapse:collapse;width:100%}td,th{border:1px solid #000;padding:4pt;vertical-align:top;font-size:10.5pt}"
        "th{background:#eee}.meta{text-align:center;color:#555}</style></head><body>",
        f"<h1>{escape(plan['title'])}</h1><p class=\"meta\">{escape(meta)}</p>",
    ]
    for label, key in (("教材分析", "analysis"), ("学情分析", "learners")):
        if plan.get(key):
            h.append(f"<h2>{label}</h2><p>{_html(plan[key])}</p>")
    h.append("<h2>教学目标</h2><ol>" + "".join(f"<li>{escape(g)}</li>" for g in plan["goals"]) + "</ol>")
    for label, key in (("教学重点", "key"), ("教学难点", "difficult"), ("教学准备", "prep")):
        if plan.get(key):
            h.append(f"<p><b>{label}：</b>{escape(plan[key])}</p>")
    h.append("<h2>教学过程</h2>")
    for p in plan["periods"]:
        h.append(f"<h3>{escape(p['name'])}</h3><table><tr><th width=\"16%\">环节</th><th width=\"38%\">教师活动</th><th width=\"26%\">学生活动</th><th width=\"20%\">设计意图</th></tr>")
        for s in p["steps"]:
            h.append(
                f"<tr><td>{escape(s['name'])}<br>（{s['minutes']} 分钟）</td><td>{_html(s['teacher'])}</td>"
                f"<td>{_html(s['student'])}</td><td>{_html(s['intent'])}</td></tr>"
            )
        h.append("</table>")
    if plan.get("board"):
        h.append(f"<h2>板书设计</h2><table><tr><td>{_html(plan['board'])}</td></tr></table>")
    hw = plan.get("homework") or {}
    if hw.get("basic") or hw.get("extend"):
        h.append("<h2>作业设计</h2>")
        if hw.get("basic"):
            h.append(f"<p><b>基础：</b>{_html(hw['basic'])}</p>")
        if hw.get("extend"):
            h.append(f"<p><b>拓展：</b>{_html(hw['extend'])}</p>")
    h.append("<h2>教学反思</h2><p>&nbsp;</p><p>&nbsp;</p></body></html>")
    return "".join(h)


def meta_line(row):
    return f"{row['grade']}年级{row['term']} · {row['kind']} · 共 {row['periods']} 课时"


# ---- the worker side (core/essay/worker.py calls claim and run) ----
def claim(d):
    now = int(time.time())
    d.execute("BEGIN IMMEDIATE")
    d.execute("UPDATE ws_lessons SET status='queued' WHERE status='running' AND locked_at<?", (now - STALE_SECONDS,))
    row = d.execute(
        "SELECT * FROM ws_lessons WHERE status='queued' AND run_after<=? AND deleted_at IS NULL ORDER BY run_after,id LIMIT 1", (now,)
    ).fetchone()
    if row:
        d.execute("UPDATE ws_lessons SET status='running',locked_at=? WHERE id=?", (now, row["id"]))
    d.commit()
    return row


def run(d, row, budget_spent):
    """Writes one lesson plan. budget_spent: whether today's model budget is used up (worker.spent_today)."""
    now = int(time.time())
    hold = "not_configured" if not llm.configured(d, "lesson") else "budget" if budget_spent else ""
    if hold:
        d.execute("UPDATE ws_lessons SET status='queued',hold=?,run_after=? WHERE id=?", (hold, now + HOLD_SECONDS, row["id"]))
        d.commit()
        return
    d.execute("UPDATE ws_lessons SET hold='' WHERE id=?", (row["id"],))
    d.commit()
    try:
        plan, model = llm.chat(d, "lesson", messages(row), json_mode=True, license_id=row["license_id"], parse=parse(row["periods"]))
    except llm.LLMError as e:
        attempts = row["attempts"] + 1
        if e.retry and attempts <= len(RETRY_DELAYS):
            d.execute(
                "UPDATE ws_lessons SET status='queued',attempts=?,run_after=?,error=? WHERE id=?",
                (attempts, int(time.time()) + RETRY_DELAYS[attempts - 1], str(e)[:500], row["id"]),
            )
        else:
            d.execute("UPDATE ws_lessons SET status='failed',attempts=?,error=? WHERE id=?", (attempts, str(e)[:500], row["id"]))
        d.commit()
        log.warning("lesson %s failed: %s", row["id"], e)
        return
    d.execute(
        "UPDATE ws_lessons SET status='done',plan=?,model=?,error='',done_at=? WHERE id=?",
        (json.dumps(plan, ensure_ascii=False), model, int(time.time()), row["id"]),
    )
    d.commit()


# ---- pages and API ----
def monthly_limit(d):
    row = d.execute("SELECT value FROM ess_meta WHERE key='lesson_monthly'").fetchone()
    return int(float(row[0])) if row else LESSON_MONTHLY


def monthly_count(d, lid):
    from . import month_start

    return d.execute(
        "SELECT count(*) FROM ws_lessons WHERE license_id=? AND created_at>=? AND status!='failed'", (lid, month_start())
    ).fetchone()[0]


def fields(data):
    from . import int_field, text_field

    term, kind = data.get("term"), data.get("kind")
    if term not in TERMS:
        raise ValueError("请选择上册或下册")
    if kind not in KINDS:
        raise ValueError("请选择课型")
    source = data.get("source_text") or ""
    if not isinstance(source, str):
        raise ValueError("课文原文格式不正确")
    source = _text(source, SOURCE_MAX + 1)
    if len(source) > SOURCE_MAX:
        raise ValueError(f"课文原文最多 {SOURCE_MAX} 个字，可以只贴要讲的部分")
    notes = data.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > NOTES_MAX:
        raise ValueError(f"补充说明最多 {NOTES_MAX} 个字")
    return {
        "grade": int_field(data, "grade", 1, 6, "年级"),
        "term": term,
        "kind": kind,
        "topic": text_field(data, "topic", TOPIC_MAX, True, "课题"),
        "periods": int_field(data, "periods", 1, MAX_PERIODS, "课时数"),
        "notes": _text(notes, NOTES_MAX),
        "source_text": source,
    }


def insert(d, lid, f):
    now = int(time.time())
    return d.execute(
        "INSERT INTO ws_lessons(license_id,grade,term,kind,topic,periods,notes,source_text,status,run_after,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,'queued',?,?)",
        (lid, f["grade"], f["term"], f["kind"], f["topic"], f["periods"], f["notes"], f["source_text"], now, now),
    ).lastrowid


def state(row):
    return {
        "status": row["status"],
        "label": STATUS.get(row["status"], row["status"]),
        "hold": HOLDS.get(row["hold"], ""),
    }


def current_plan(row):
    raw = row["edited"] or row["plan"]
    return json.loads(raw) if raw else None


def register(app):
    from flask import Response, abort, jsonify, redirect, render_template, request, session, url_for
    from core.app import csrf, db
    from . import error, features, payload, teacher, teacher_api, trial_check, trial_used

    def check_room(d, lid):
        features.require(d, lid, "lesson")
        if monthly_count(d, lid) >= monthly_limit(d):
            raise ValueError(f"本月已经生成 {monthly_limit(d)} 份教案，下个月再来吧")

    def own(lesson_id):
        """The lesson if it belongs to the signed-in teacher or to this browser's trial; else 404."""
        row = db().execute("SELECT * FROM ws_lessons WHERE id=? AND deleted_at IS NULL", (lesson_id,)).fetchone()
        t = teacher()
        if row and ((t and row["license_id"] == t["id"]) or (row["license_id"] == 0 and session.get("trial_lesson") == row["id"])):
            return row
        abort(404)

    def view_data(row):
        plan = current_plan(row)
        return {
            "id": row["id"],
            "status": row["status"],
            "plan": plan,
            "text": to_text(plan) if plan else "",
            "trial": row["license_id"] == 0,
            "urls": {
                "state": url_for("lesson_state", lesson_id=row["id"]),
                "save": url_for("lesson_save", lesson_id=row["id"]),
                "regenerate": url_for("lesson_regenerate", lesson_id=row["id"]),
            },
        }

    @app.get("/teacher/lesson/")
    def lesson_home():
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        d = db()
        lessons = d.execute(
            "SELECT id,grade,term,kind,topic,periods,status,hold,created_at FROM ws_lessons"
            " WHERE license_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 100",
            (row["id"],),
        ).fetchall()
        return render_template(
            "lesson_home.html",
            feature=features.describe(features.rows(d, row["id"]).get("lesson")),
            lessons=lessons,
            used=monthly_count(d, row["id"]),
            limit=monthly_limit(d),
            KINDS=KINDS,
            TERMS=TERMS,
            LESSON_STATUS=STATUS,
            limits={"topic": TOPIC_MAX, "notes": NOTES_MAX, "source": SOURCE_MAX, "periods": MAX_PERIODS},
        )

    @app.get("/teacher/lesson/<int:lesson_id>")
    def lesson_view(lesson_id):
        if not teacher():
            return redirect(url_for("teacher_home"))
        row = own(lesson_id)
        return render_template("lesson_view.html", lesson=row, meta=meta_line(row), plan=current_plan(row),
                               page_data=view_data(row), LESSON_STATUS=STATUS, LESSON_HOLDS=HOLDS)

    @app.get("/teacher/lesson/<int:lesson_id>.doc")
    def lesson_doc(lesson_id):
        row = own(lesson_id)
        plan = current_plan(row)
        if not plan:
            abort(404)
        name = re.sub(r"[\\/:*?\"<>|\s]+", "", plan["title"])[:40] or "教案"
        r = Response("﻿" + to_doc(plan, meta_line(row)), mimetype="application/msword")
        r.headers["Content-Disposition"] = f"attachment; filename=lesson-{row['id']}.doc; filename*=UTF-8''{quote(name)}.doc"
        return r

    @app.post("/api/lessons")
    @teacher_api
    def lesson_create(row):
        d = db()
        f = fields(payload())
        check_room(d, row["id"])
        lesson_id = insert(d, row["id"], f)
        return jsonify(ok=True, id=lesson_id, url=url_for("lesson_view", lesson_id=lesson_id))

    @app.get("/api/lessons/<int:lesson_id>")
    def lesson_state(lesson_id):
        return jsonify(state(own(lesson_id)))

    @app.post("/api/lessons/<int:lesson_id>/save")
    @teacher_api
    def lesson_save(row, lesson_id):
        lesson = own(lesson_id)
        if lesson["status"] != "done":
            raise ValueError("教案还没有写好")
        plan = clean(payload().get("plan"), fix_minutes=False)
        db().execute("UPDATE ws_lessons SET edited=? WHERE id=?", (json.dumps(plan, ensure_ascii=False), lesson_id))
        return jsonify(ok=True)

    @app.post("/api/lessons/<int:lesson_id>/regenerate")
    @teacher_api
    def lesson_regenerate(row, lesson_id):
        lesson = own(lesson_id)
        d = db()
        check_room(d, row["id"])
        new_id = insert(d, row["id"], dict(lesson))
        if lesson["status"] == "failed":
            d.execute("UPDATE ws_lessons SET deleted_at=? WHERE id=?", (int(time.time()), lesson_id))
        return jsonify(ok=True, id=new_id, url=url_for("lesson_view", lesson_id=new_id))

    @app.post("/api/lessons/<int:lesson_id>/delete")
    @teacher_api
    def lesson_delete(row, lesson_id):
        own(lesson_id)
        db().execute("UPDATE ws_lessons SET deleted_at=? WHERE id=?", (int(time.time()), lesson_id))
        return jsonify(ok=True, url=url_for("lesson_home"))

    @app.get("/teacher/trial/lesson")
    def lesson_trial_page():
        t = teacher()
        if t and features.active(db(), t["id"], "lesson"):
            return redirect(url_for("lesson_home"))
        csrf()
        lesson_id = session.get("trial_lesson")
        row = lesson_id and db().execute("SELECT * FROM ws_lessons WHERE id=? AND license_id=0", (lesson_id,)).fetchone()
        retry = False
        if row and row["status"] == "failed":
            # A trial that failed on our side does not count: this browser may try again.
            from . import device_hash

            d = db()
            d.execute("DELETE FROM trials WHERE site='teacher' AND device_hash=? AND game='lesson'", (device_hash(),))
            d.commit()
            session.pop("trial_lesson", None)
            row, retry = None, True
        return render_template(
            "lesson_trial.html",
            lesson=row,
            meta=meta_line(row) if row else "",
            plan=current_plan(row) if row else None,
            page_data=view_data(row) if row else None,
            used=trial_used("lesson"),
            retry=retry,
            KINDS=KINDS,
            TERMS=TERMS,
            LESSON_STATUS=STATUS,
            LESSON_HOLDS=HOLDS,
            limits={"topic": TOPIC_MAX, "notes": NOTES_MAX, "source": SOURCE_MAX, "periods": MAX_PERIODS},
        )

    @app.post("/api/trial/lesson")
    def lesson_trial_submit():
        d = db()
        try:
            f = fields(payload())
        except ValueError as e:
            return error(str(e))
        problem = trial_check("lesson", "教案生成")
        if problem:
            return problem
        d.execute("BEGIN IMMEDIATE")
        lesson_id = insert(d, 0, f)
        from . import record_trial

        record_trial("lesson")
        d.commit()
        session["trial_lesson"] = lesson_id
        return jsonify(ok=True, id=lesson_id, url=url_for("lesson_trial_page"))
