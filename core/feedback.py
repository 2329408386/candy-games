"""改进建议: licensed users of any site tell us what could be better and read the answer here.

The way in is quiet on purpose (the teacher's avatar menu, the bottom of a game site's 我的 page). Free-trial teachers
and workbench students have no license of their own, so they cannot write. Suggestions are read and answered on the
workbench's back office (core/essay/admin.py), which sees every site.
"""

import time
from flask import flash, jsonify, redirect, render_template, request, url_for
from .app import current, db, site
from .schema import BEIJING, end_of_day

MAX_CONTENT = 1000
MAX_REPLY = 2000
DAILY_LIMIT = 5


def author():
    """The license allowed to write here, or None."""
    row = current()
    if not row:
        return None
    if site().kind == "workbench" and db().execute("SELECT 1 FROM ess_trials WHERE license_id=?", (row["id"],)).fetchone():
        return None
    return row


def unread(lid):
    return db().execute(
        "SELECT count(*) FROM feedback WHERE site=? AND license_id=? AND reply IS NOT NULL AND reply_seen_at IS NULL",
        (site().key, lid),
    ).fetchone()[0]


def _day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts + BEIJING))


def register_feedback(app):
    # The workbench shows who is signed in by the path, so its page sits on the teacher side.
    path = ("/teacher" if app.site.kind == "workbench" else "") + "/feedback"

    @app.get(path)
    def feedback_page():
        workbench = site().kind == "workbench"
        row = author()
        if not row:
            return redirect(url_for("teacher_home" if workbench else "home"))
        d = db()
        rows = d.execute(
            "SELECT * FROM feedback WHERE site=? AND license_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
            (site().key, row["id"]),
        ).fetchall()
        items = [
            {
                "content": r["content"],
                "when": _day(r["created_at"]),
                "reply": r["reply"],
                "replied": _day(r["replied_at"]) if r["replied_at"] else "",
                "new": bool(r["reply"]) and r["reply_seen_at"] is None,
            }
            for r in rows
        ]
        if any(i["new"] for i in items):  # opening this page is reading the answer: the header's dot goes away
            d.execute(
                "UPDATE feedback SET reply_seen_at=? WHERE site=? AND license_id=? AND reply IS NOT NULL AND reply_seen_at IS NULL",
                (int(time.time()), site().key, row["id"]),
            )
            d.commit()
        return render_template(
            "feedback.html",
            items=items,
            max_content=MAX_CONTENT,
            back=url_for("teacher_home" if workbench else "me_page"),
        )

    @app.post(path)
    def feedback_submit():
        row = author()
        if not row:
            return jsonify(error="只有已授权的账号才能提建议"), 403
        payload = request.get_json(silent=True)
        content = payload.get("content") if isinstance(payload, dict) else None
        if not isinstance(content, str) or not content.strip():
            return jsonify(error="先写几句吧"), 400
        content = content.strip()
        if len(content) > MAX_CONTENT:
            return jsonify(error=f"一次最多写 {MAX_CONTENT} 个字，可以分成两条"), 400
        d = db()
        d.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        sent = d.execute(
            "SELECT count(*) FROM feedback WHERE site=? AND license_id=? AND created_at>?",
            (site().key, row["id"], end_of_day(now) - 86400),
        ).fetchone()[0]
        if sent >= DAILY_LIMIT:
            d.rollback()
            return jsonify(error=f"今天已经收到你的 {DAILY_LIMIT} 条建议了，谢谢你。还有想说的，明天再告诉我们吧"), 429
        d.execute(
            "INSERT INTO feedback(site,license_id,content,created_at) VALUES(?,?,?,?)",
            (site().key, row["id"], content, now),
        )
        d.commit()
        flash("收到了，谢谢你。", "success")
        return jsonify(ok=True)
