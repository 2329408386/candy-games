"""语文老师工作台 (/workspace/chinese/): the Chinese teacher's workbench, for primary school. Its first feature is
作文批改 (essay grading); features and how long each may be used are in features.py.

Areas. /teacher/… is the teacher side, /student/… the student side, / the entry page that leads to either, and
/admin the back office (core/app.py, admin.py). Both sides stay signed in until they sign out (the session cookie
lasts ten years), and a teacher and a student may be signed in on the same browser.

Accounts. A teacher is a license of the 'teacher' site (activated like the game sites, core/app.py), with a name and
an avatar (profiles, site 'teacher') and ess_quota: how many students across their classes and how many essays a
month (calendar month, Beijing time). Students have no license: the teacher creates them in a class (ess_students,
a 4-digit 口令 the teacher can see and print), and they sign in with 班级码 + 姓名 + 口令 (session["student"] =
[id, pin_ver]; resetting the 口令 signs them out). Their name is the one on the teacher's list. Five wrong 口令 in a
row lock the student for 10 minutes.

Flow. A student (or the teacher on their behalf) submits an essay for an assignment, as photos (the default) or
typed → ess_submissions 'queued' and an ess_jobs row → the worker (core/essay/worker.py) reads the photos (ocr) and
grades the text → 'published' when the assignment auto-publishes (the default), otherwise 'review' until the teacher
edits and publishes it → the student sees the review written in red pen on their own page (static/redpen.js). Each
student may submit an assignment up to MAX_ATTEMPTS times (a resubmission waits until the previous one is published
or failed). Starting work needs the feature to be active (features.require); what is already queued is finished.

Trial. Without a license, each browser may try 作文批改 once (trials, game 'essay'): the essay is filed under a
hidden archived class of license 0 and published straight away. trial_daily() caps trials across the site per day.

Module map: llm.py (configurable OpenAI-compatible models), grading.py (prompts and validation), worker.py,
features.py, admin.py (模型设置, quotas and feature expiry in the back office); pages are in sites/teacher/templates.
"""

import calendar, io, json, re, secrets, time
from flask import Response, abort, g, jsonify, redirect, render_template, request, session, url_for
from core.app import KEY_ALPHABET, csrf, current, data_dir, db, device_hash, limited, site
from . import features, grading

DEFAULT_STUDENTS = 60
DEFAULT_MONTHLY = 300
MAX_CLASSES = 10
MAX_ATTEMPTS = 3
MAX_REGRADES = 3
NAME_MAX = 12
TEACHER_NAME_MAX = 12
CLASS_NAME_MAX = 20
LOCK_AFTER = 5
LOCK_SECONDS = 600
BEIJING = 8 * 3600
MAX_PHOTOS = 3
PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_SIDE = 2000
TRIAL_DAILY = 50
TRIAL_PER_IP = 3
BUSY = ("queued", "grading")
STATUS = {"queued": "排队批改中", "grading": "AI 批改中", "review": "等老师审核", "published": "已发布", "failed": "批改失败"}
HOLDS = {
    "not_configured": "批改服务暂未开通，作文已保存，开通后会自动批改",
    "budget": "今天的批改额度用完了，稍后会自动继续",
}


# ---- helpers ----
def month_start(now=None):
    local = time.gmtime(int(now if now is not None else time.time()) + BEIJING)
    return calendar.timegm((local.tm_year, local.tm_mon, 1, 0, 0, 0)) - BEIJING


def day_start(now=None):
    local = int(now if now is not None else time.time()) + BEIJING
    return local - local % 86400 - BEIJING


def quota(lid):
    row = db().execute("SELECT students,monthly FROM ess_quota WHERE license_id=?", (lid,)).fetchone()
    return {"students": row[0], "monthly": row[1]} if row else {"students": DEFAULT_STUDENTS, "monthly": DEFAULT_MONTHLY}


def students_used(lid):
    return db().execute(
        "SELECT count(*) FROM ess_students s JOIN ess_classes c ON c.id=s.class_id"
        " WHERE c.license_id=? AND c.archived_at IS NULL AND s.removed_at IS NULL",
        (lid,),
    ).fetchone()[0]


def monthly_used(lid):
    return db().execute(
        "SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id"
        " JOIN ess_classes c ON c.id=a.class_id WHERE c.license_id=? AND s.created_at>=?",
        (lid, month_start()),
    ).fetchone()[0]


def trial_daily():
    row = db().execute("SELECT value FROM ess_meta WHERE key='trial_daily'").fetchone()
    return int(float(row[0])) if row else TRIAL_DAILY


def teacher():
    return current("essay")


def student():
    if "student_row" not in g:
        g.student_row = None
        value = session.get("student")
        if isinstance(value, list) and len(value) == 2:
            g.student_row = db().execute(
                "SELECT s.*,c.name class_name,c.grade,c.license_id FROM ess_students s JOIN ess_classes c ON c.id=s.class_id"
                " JOIN licenses l ON l.site='teacher' AND l.id=c.license_id"
                " WHERE s.id=? AND s.pin_ver=? AND s.removed_at IS NULL AND c.archived_at IS NULL AND l.enabled=1 AND l.archived_at IS NULL",
                tuple(value),
            ).fetchone()
    return g.student_row


def teacher_profile(lid):
    row = db().execute("SELECT nickname,avatar_ver FROM profiles WHERE site='teacher' AND license_id=?", (lid,)).fetchone()
    name = row["nickname"] if row and row["nickname"] else ""
    return {
        "name": name or "老师",
        "nickname": name,
        "initial": (name or "师")[0],
        "avatar": url_for("avatar", lid=lid, v=row["avatar_ver"]) if row and row["avatar_ver"] else None,
    }


def error(message, status=400):
    return jsonify(error=message), status


def payload():
    """The request's fields: a JSON object, or the form fields of a multipart upload (photos)."""
    if request.mimetype == "multipart/form-data":
        return request.form.to_dict()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        abort(400)
    return data


def text_field(data, key, limit, required=False, label=""):
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{label}格式不正确")
    value = " ".join("".join(c for c in value if c.isprintable()).split())
    if required and not value:
        raise ValueError(f"请填写{label}")
    if len(value) > limit:
        raise ValueError(f"{label}最多 {limit} 个字")
    return value


def int_field(data, key, low, high, label):
    value = data.get(key)
    if isinstance(value, str) and value.strip().isdigit():
        value = int(value)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ValueError(f"{label}要在 {low}—{high} 之间")
    return value


def new_code(table, column, length, alphabet=KEY_ALPHABET):
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(length))
        if not db().execute(f"SELECT 1 FROM {table} WHERE {column}=?", (code,)).fetchone():
            return code


def new_pin():
    return "".join(secrets.choice("0123456789") for _ in range(4))


def own_class(cid, lid):
    row = db().execute("SELECT * FROM ess_classes WHERE id=? AND license_id=? AND archived_at IS NULL", (cid, lid)).fetchone()
    if not row:
        abort(404)
    return row


def own_assignment(aid, lid):
    row = db().execute(
        "SELECT a.*,c.name class_name,c.grade FROM ess_assignments a JOIN ess_classes c ON c.id=a.class_id"
        " WHERE a.id=? AND c.license_id=? AND c.archived_at IS NULL",
        (aid, lid),
    ).fetchone()
    if not row:
        abort(404)
    return row


SUBMISSION_SQL = (
    "SELECT s.*,a.title assignment_title,a.genre,a.requirement,a.min_chars,a.class_id,c.grade,c.license_id,"
    "c.name class_name,st.name student_name FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id"
    " JOIN ess_classes c ON c.id=a.class_id JOIN ess_students st ON st.id=s.student_id"
)


def own_submission(sid, lid):
    row = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND c.license_id=? AND c.archived_at IS NULL", (sid, lid)).fetchone()
    if not row:
        abort(404)
    return row


def review_of(sid):
    row = db().execute("SELECT * FROM ess_reviews WHERE submission_id=?", (sid,)).fetchone()
    if not row:
        return None
    return {
        "ai": json.loads(row["ai"]),
        "final": json.loads(row["final"]) if row["final"] else None,
        "model": row["model"],
        "prompt_version": row["prompt_version"],
        "edited_at": row["edited_at"],
    }


def latest_submissions(aid):
    """{student_id: newest submission row} for an assignment."""
    rows = db().execute("SELECT s.* FROM ess_submissions s WHERE s.assignment_id=? ORDER BY s.attempt", (aid,)).fetchall()
    return {r["student_id"]: r for r in rows}


def enqueue(sid):
    now = int(time.time())
    db().execute("UPDATE ess_jobs SET status='done' WHERE submission_id=? AND status IN ('queued','running')", (sid,))
    db().execute(
        "INSERT INTO ess_jobs(submission_id,kind,status,run_after,created_at) VALUES(?,'grade','queued',?,?)", (sid, now, now)
    )


# ---- photos ----
def images_dir():
    path = data_dir() / "essay-images"
    path.mkdir(mode=0o700, exist_ok=True)
    return path


def image_path(iid):
    return images_dir() / f"{iid}.jpg"


def read_photos():
    """The uploaded photos (form field "photos"), upright, at most PHOTO_SIDE px, as JPEG: [(bytes, w, h)]."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    files = [f for f in request.files.getlist("photos") if f and f.filename is not None]
    if len(files) > MAX_PHOTOS:
        raise ValueError(f"最多传 {MAX_PHOTOS} 张照片")
    out = []
    for f in files:
        data = f.read(PHOTO_BYTES + 1)
        if len(data) > PHOTO_BYTES:
            raise ValueError("照片太大了，请重新拍一张")
        try:
            img = Image.open(io.BytesIO(data))
            if img.format not in ("JPEG", "PNG", "WEBP") or img.width * img.height > 40_000_000:
                raise ValueError
            img = ImageOps.exif_transpose(img).convert("RGB")
            img.thumbnail((PHOTO_SIDE, PHOTO_SIDE), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=85, optimize=True)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            raise ValueError("照片格式不支持，请换一张（JPG 或 PNG）") from None
        out.append((buf.getvalue(), img.width, img.height))
    return out


def store_photos(sid, photos):
    now = int(time.time())
    for page, (data, w, h) in enumerate(photos, 1):
        iid = db().execute(
            "INSERT INTO ess_images(submission_id,page,w,h,created_at) VALUES(?,?,?,?,?)", (sid, page, w, h, now)
        ).lastrowid
        image_path(iid).write_bytes(data)


def images_of(sid):
    return db().execute("SELECT * FROM ess_images WHERE submission_id=? ORDER BY page", (sid,)).fetchall()


def create_submission(assignment, student_id, title, text, source, photos=None, check_quota=True):
    """Validates and stores a submission (the caller holds the write lock); returns its id or raises ValueError.
    With photos the text is left empty: the worker reads it from the photos first."""
    d = db()
    if assignment["closed_at"]:
        raise ValueError("这次作业已经停止提交")
    if photos:
        text, source = "", "photo"
    else:
        text = grading.clean_text(text if isinstance(text, str) else "")
        if grading.char_count(text) < 20:
            raise ValueError("作文太短了，至少写 20 个字")
        if len(text) > grading.MAX_TEXT:
            raise ValueError(f"作文太长了，最多 {grading.MAX_TEXT} 个字")
    previous = d.execute(
        "SELECT attempt,status FROM ess_submissions WHERE assignment_id=? AND student_id=? ORDER BY attempt DESC LIMIT 1",
        (assignment["id"], student_id),
    ).fetchone()
    if previous and previous["status"] not in ("published", "failed"):
        raise ValueError("上一次交的作文还在批改或等老师审核，发布以后才能再交")
    attempt = previous["attempt"] + 1 if previous else 1
    if attempt > MAX_ATTEMPTS:
        raise ValueError(f"这次作业最多交 {MAX_ATTEMPTS} 次")
    if check_quota:
        lid = d.execute("SELECT license_id FROM ess_classes WHERE id=?", (assignment["class_id"],)).fetchone()[0]
        if monthly_used(lid) >= quota(lid)["monthly"]:
            raise ValueError("本月的批改篇数已经用完，请联系老师")
    now = int(time.time())
    sid = d.execute(
        "INSERT INTO ess_submissions(assignment_id,student_id,attempt,source,title,text,status,created_at) VALUES(?,?,?,?,?,?,'queued',?)",
        (assignment["id"], student_id, attempt, source, title, text, now),
    ).lastrowid
    if photos:
        store_photos(sid, photos)
    enqueue(sid)
    return sid


def state_payload(row):
    return {"status": row["status"], "label": STATUS.get(row["status"], row["status"]), "hold": HOLDS.get(row["hold"], "")}


def redpen_data(sub, review, show_score=True):
    """What static/redpen.js draws: the photos with each recognised line's place, or (typed essays, photos without
    usable places) the text on squared paper; the marks and the short comments."""
    pages = [
        {"src": url_for("essay_image", iid=r["id"]), "w": r["w"], "h": r["h"], "lines": json.loads(r["lines"]) if r["lines"] else []}
        for r in images_of(sub["id"])
    ]
    on_photo = bool(pages) and all(p["lines"] for p in pages)
    return {
        "text": sub["text"],
        "title": sub["title"] or sub["assignment_title"],
        "pages": pages,
        "onPhoto": on_photo,
        "marks": [{k: m[k] for k in ("type", "start", "end", "fix", "note")} for m in review["marks"]],
        "total": review["total"],
        "max": grading.TOTAL,
        "level": review["level"],
        "showScore": show_score,
        "summary": review.get("summary") or "",
        "praise": review.get("praise") or [],
        "advice": review.get("advice") or [],
    }


MARK_CLASS = {"错别字": "typo", "用词不当": "word", "病句": "sentence", "标点": "punct", "好词好句": "good", "修改建议": "tip"}


def register_essay(app):
    from . import admin

    app.jinja_env.globals.update(
        STATUS=STATUS,
        HOLDS=HOLDS,
        BUSY=BUSY,
        DIMS=grading.DIMS,
        FLAGS=grading.FLAGS,
        GENRES=list(grading.GENRES),
        TOTAL=grading.TOTAL,
        essay_quota=quota,
        essay_students_used=students_used,
        essay_monthly_used=monthly_used,
        workbench_features=lambda lid: features.overview(db(), lid),
        workbench_feature_list=features.FEATURES,
    )
    app.jinja_env.filters["mark_class"] = lambda t: MARK_CLASS.get(t, "tip")
    app.jinja_env.filters["date"] = lambda value: time.strftime("%Y-%m-%d", time.gmtime(value + BEIJING)) if value else ""
    app.jinja_env.filters["moment"] = lambda value: time.strftime("%m-%d %H:%M", time.gmtime(value + BEIJING)) if value else ""

    @app.context_processor
    def who():
        # The header shows the side the page belongs to: the teacher's name and avatar, or the student's name.
        path = request.path
        side = "teacher" if path.startswith("/teacher") else "student" if path.startswith("/student") else None
        row = teacher() if side == "teacher" else None
        return {
            "side": side,
            "me_teacher": teacher_profile(row["id"]) if row else None,
            "me_student": student() if side == "student" else None,
        }

    register_pages(app)
    register_teacher_api(app)
    register_student_api(app)
    register_trial(app)
    admin.register(app)


# ---- pages ----
def register_pages(app):
    @app.get("/static/shared/<path:filename>")
    def shared_static(filename):
        from flask import send_from_directory
        from core.app import SHARED_STATIC

        return send_from_directory(SHARED_STATIC, filename)

    @app.get("/health")
    def health():
        from core.app import ROOT

        db().execute("SELECT count(*) FROM ess_classes").fetchone()
        seen = db().execute("SELECT value FROM ess_meta WHERE key='worker_seen'").fetchone()
        revision = ROOT / "REVISION"
        return jsonify(
            status="ok",
            worker_seen=int(seen[0]) if seen else None,
            **({"revision": revision.read_text().strip()} if revision.exists() else {}),
        )

    @app.get("/")
    def home():
        return render_template("entry.html", signed_teacher=bool(teacher()), signed_student=bool(student()))

    @app.get("/teacher")
    @app.get("/teacher/")
    def teacher_home():
        row = teacher()
        if not row:
            csrf()
            used = db().execute(
                "SELECT 1 FROM trials WHERE site='teacher' AND device_hash=? AND game='essay'", (device_hash(),)
            ).fetchone()
            return render_template("teacher_login.html", trial_used=bool(used))
        d = db()
        classes = d.execute(
            "SELECT c.*,(SELECT count(*) FROM ess_students s WHERE s.class_id=c.id AND s.removed_at IS NULL) students,"
            " (SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id"
            "  WHERE a.class_id=c.id AND s.status='review') pending"
            " FROM ess_classes c WHERE c.license_id=? AND c.archived_at IS NULL ORDER BY c.id",
            (row["id"],),
        ).fetchall()
        return render_template(
            "teacher_home.html",
            classes=classes,
            feats=features.overview(d, row["id"]),
            quota=quota(row["id"]),
            students_used=students_used(row["id"]),
            monthly_used=monthly_used(row["id"]),
            pending=sum(c["pending"] for c in classes),
            max_classes=MAX_CLASSES,
        )

    @app.get("/teacher/profile")
    def profile_page():
        if not teacher():
            return redirect(url_for("teacher_home"))
        return render_template("profile.html", name_max=TEACHER_NAME_MAX)

    @app.get("/teacher/classes/<int:cid>")
    def class_page(cid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        klass = own_class(cid, row["id"])
        d = db()
        students = d.execute("SELECT * FROM ess_students WHERE class_id=? AND removed_at IS NULL ORDER BY id", (cid,)).fetchall()
        assignments = d.execute(
            "SELECT a.*,(SELECT count(DISTINCT s.student_id) FROM ess_submissions s WHERE s.assignment_id=a.id) submitted,"
            " (SELECT count(*) FROM ess_submissions s WHERE s.assignment_id=a.id AND s.status='review') pending"
            " FROM ess_assignments a WHERE a.class_id=? ORDER BY a.id DESC",
            (cid,),
        ).fetchall()
        return render_template(
            "class.html",
            klass=klass,
            students=students,
            assignments=assignments,
            essay=features.describe(features.rows(d, row["id"]).get("essay")),
            quota=quota(row["id"]),
            students_used=students_used(row["id"]),
        )

    @app.get("/teacher/classes/<int:cid>/slips")
    def class_slips(cid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        klass = own_class(cid, row["id"])
        students = db().execute("SELECT * FROM ess_students WHERE class_id=? AND removed_at IS NULL ORDER BY id", (cid,)).fetchall()
        return render_template("slips.html", klass=klass, students=students)

    @app.get("/teacher/essay/assignments/<int:aid>")
    def assignment_page(aid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        assignment = own_assignment(aid, row["id"])
        students = db().execute(
            "SELECT * FROM ess_students WHERE class_id=? AND removed_at IS NULL ORDER BY id", (assignment["class_id"],)
        ).fetchall()
        latest = latest_submissions(aid)
        scores = {}
        for sid in [r["id"] for r in latest.values() if r["status"] in ("review", "published")]:
            review = review_of(sid)
            final = review and (review["final"] or review["ai"])
            if final:
                scores[sid] = final
        return render_template(
            "assignment.html",
            assignment=assignment,
            students=students,
            latest=latest,
            scores=scores,
            max_attempts=MAX_ATTEMPTS,
            essay_open=features.active(db(), row["id"], "essay"),
        )

    @app.get("/teacher/essay/submissions/<int:sid>")
    def review_page(sid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        sub = own_submission(sid, row["id"])
        review = review_of(sid)
        others = db().execute(
            "SELECT id,attempt,status FROM ess_submissions WHERE assignment_id=? AND student_id=? ORDER BY attempt",
            (sub["assignment_id"], sub["student_id"]),
        ).fetchall()
        queue = [
            r[0]
            for r in db().execute(
                "SELECT id FROM ess_submissions WHERE assignment_id=? AND status='review' ORDER BY student_id,attempt",
                (sub["assignment_id"],),
            )
        ]
        band = grading.BANDS[grading.band_of(sub["grade"])]
        next_id = next((q for q in queue if q != sid), None)
        current_review = review and (review["final"] or review["ai"])
        return render_template(
            "review.html",
            sub=sub,
            review=review,
            others=others,
            band=band,
            next_id=next_id,
            photos=images_of(sid),
            page_data={
                "id": sid,
                "status": sub["status"],
                "text": sub["text"],
                "review": current_review,
                "redpen": redpen_data(sub, current_review) if current_review else None,
                "max": band["max"],
                "types": grading.MARK_TYPES,
                "errorTypes": grading.ERROR_TYPES,
                "urls": {
                    "save": url_for("review_save", sid=sid),
                    "regrade": url_for("regrade", sid=sid),
                    "state": url_for("submission_state", sid=sid),
                    "csrf": url_for("csrf_token"),
                    "next": url_for("review_page", sid=next_id) if next_id else url_for("assignment_page", aid=sub["assignment_id"]),
                },
            },
        )

    @app.get("/teacher/essay/results/<int:sid>")
    def teacher_result(sid):
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        sub = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND c.license_id=?", (sid, row["id"])).fetchone()
        return result_view(sub, as_teacher=True)

    @app.get("/student")
    @app.get("/student/")
    def student_home():
        me = student()
        if not me:
            return render_template("student_login.html")
        assignments = db().execute(
            "SELECT a.*,(SELECT s.status FROM ess_submissions s WHERE s.assignment_id=a.id AND s.student_id=?"
            " ORDER BY s.attempt DESC LIMIT 1) my_status,"
            " (SELECT s.id FROM ess_submissions s WHERE s.assignment_id=a.id AND s.student_id=? AND s.status='published'"
            " ORDER BY s.attempt DESC LIMIT 1) my_result"
            " FROM ess_assignments a WHERE a.class_id=? ORDER BY a.id DESC",
            (me["id"], me["id"], me["class_id"]),
        ).fetchall()
        return render_template("student_home.html", me=me, assignments=assignments)

    @app.get("/student/essay/<int:aid>")
    def write_page(aid):
        me = student()
        if not me:
            return redirect(url_for("student_home"))
        assignment = db().execute("SELECT * FROM ess_assignments WHERE id=? AND class_id=?", (aid, me["class_id"])).fetchone()
        if not assignment:
            abort(404)
        attempts = db().execute(
            "SELECT * FROM ess_submissions WHERE assignment_id=? AND student_id=? ORDER BY attempt", (aid, me["id"])
        ).fetchall()
        last = attempts[-1] if attempts else None
        essay_open = features.active(db(), me["license_id"], "essay")
        can_submit = (
            essay_open
            and not assignment["closed_at"]
            and len(attempts) < MAX_ATTEMPTS
            and (not last or last["status"] in ("published", "failed"))
        )
        return render_template(
            "write.html",
            me=me,
            assignment=assignment,
            attempts=attempts,
            last=last,
            can_submit=can_submit,
            essay_open=essay_open,
            max_attempts=MAX_ATTEMPTS,
            max_photos=MAX_PHOTOS,
            draft_text=last["text"] if last and last["source"] != "photo" else "",
            draft_title=last["title"] if last else "",
        )

    @app.get("/student/essay/results/<int:sid>")
    def result_page(sid):
        me = student()
        if not me:
            return redirect(url_for("student_home"))
        sub = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND s.student_id=?", (sid, me["id"])).fetchone()
        return result_view(sub, as_teacher=False)

    @app.get("/images/<int:iid>.jpg")
    def essay_image(iid):
        row = db().execute(
            "SELECT i.id,s.student_id,c.license_id FROM ess_images i JOIN ess_submissions s ON s.id=i.submission_id"
            " JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id WHERE i.id=?",
            (iid,),
        ).fetchone()
        t, me = teacher(), student()
        allowed = row and (
            (t and t["id"] == row["license_id"])
            or (me and me["id"] == row["student_id"])
            or (row["license_id"] == 0 and session.get("trial_essay") is not None and trial_owns(iid))
        )
        if not allowed or not image_path(iid).exists():
            abort(404)
        r = Response(image_path(iid).read_bytes(), mimetype="image/jpeg")
        r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        r.headers["Content-Security-Policy"] = "default-src 'none'"
        return r

    @app.get("/avatars/<int:lid>.webp")
    def avatar(lid):
        row = teacher()
        if not row or row["id"] != lid:
            abort(404)
        data = db().execute("SELECT avatar FROM profiles WHERE site='teacher' AND license_id=?", (lid,)).fetchone()
        if not data or not data["avatar"]:
            abort(404)
        r = Response(data["avatar"], mimetype="image/webp")
        r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        r.headers["Content-Security-Policy"] = "default-src 'none'"
        return r


def trial_owns(iid):
    sid = session.get("trial_essay")
    return bool(db().execute("SELECT 1 FROM ess_images WHERE id=? AND submission_id=?", (iid, sid)).fetchone())


def result_view(sub, as_teacher):
    if not sub or sub["status"] != "published":
        abort(404)
    review = review_of(sub["id"])
    final = review["final"] or review["ai"]
    previous = db().execute(
        "SELECT s.id,s.attempt,r.final FROM ess_submissions s JOIN ess_reviews r ON r.submission_id=s.id"
        " WHERE s.assignment_id=? AND s.student_id=? AND s.attempt<? AND s.status='published' ORDER BY s.attempt DESC LIMIT 1",
        (sub["assignment_id"], sub["student_id"], sub["attempt"]),
    ).fetchone()
    return render_template(
        "result.html",
        sub=sub,
        final=final,
        redpen=redpen_data(sub, final),
        previous=previous and json.loads(previous["final"]),
        as_teacher=as_teacher,
        project=as_teacher and request.args.get("project") == "1",
        hide_name=request.args.get("anon") == "1",
    )


# ---- teacher API ----
def teacher_api(fn):
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        row = teacher()
        if not row:
            return error("请先激活老师授权码", 403)
        d = db()
        d.execute("BEGIN IMMEDIATE")
        try:
            result = fn(row, *a, **kw)
        except ValueError as e:
            d.rollback()
            return error(str(e))
        d.commit()
        return result

    return wrapper


def parse_names(raw):
    if not isinstance(raw, str):
        raise ValueError("名单格式不正确")
    names = [n.strip() for n in re.split(r"[\n,，、;；\t]+", raw)]
    names = [" ".join("".join(c for c in n if c.isprintable()).split()) for n in names]
    names = list(dict.fromkeys(n for n in names if n))
    for n in names:
        if len(n) > NAME_MAX:
            raise ValueError(f"「{n[:20]}」太长了，姓名最多 {NAME_MAX} 个字")
    if not names:
        raise ValueError("请填写学生姓名，一行一个")
    return names


def assignment_fields(data):
    genre = data.get("genre")
    if genre not in grading.GENRES:
        raise ValueError("请选择文体")
    due = data.get("due") or ""
    if due:
        if not isinstance(due, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", due):
            raise ValueError("截止日期格式不正确")
        try:
            # Due at the end of that day, Beijing time.
            due_at = calendar.timegm(time.strptime(due, "%Y-%m-%d")) - BEIJING + 86399
        except ValueError:
            raise ValueError("截止日期格式不正确") from None
    else:
        due_at = None
    requirement = data.get("requirement", "")
    if not isinstance(requirement, str) or len(requirement) > 600:
        raise ValueError("习作要求最多 600 个字")
    return {
        "title": text_field(data, "title", 40, True, "作文题目"),
        "requirement": grading.clean_text(requirement),
        "genre": genre,
        "min_chars": int_field(data, "min_chars", 0, 2000, "字数要求") if data.get("min_chars") not in (None, "") else 0,
        "due_at": due_at,
        "auto_publish": int(bool(data.get("auto_publish"))),
    }


def submission_photos():
    return read_photos() if request.mimetype == "multipart/form-data" else []


def register_teacher_api(app):
    @app.post("/api/me/name")
    @teacher_api
    def teacher_name(row):
        name = text_field(payload(), "name", TEACHER_NAME_MAX, True, "名字")
        db().execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES('teacher',?)", (row["id"],))
        db().execute("UPDATE profiles SET nickname=? WHERE site='teacher' AND license_id=?", (name, row["id"]))
        return jsonify(ok=True, name=name)

    @app.post("/api/me/avatar")
    def teacher_avatar():
        from core.social import reencode_avatar

        row = teacher()
        if not row:
            return error("请先激活老师授权码", 403)
        if limited("avatar", 20, 3600):
            return error("头像换得太频繁了，请一小时后再试", 429)
        try:
            data = reencode_avatar(request.get_data())
        except ValueError as e:
            return error(str(e))
        d = db()
        d.execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES('teacher',?)", (row["id"],))
        d.execute("UPDATE profiles SET avatar=?,avatar_ver=avatar_ver+1 WHERE site='teacher' AND license_id=?", (data, row["id"]))
        d.commit()
        return jsonify(ok=True, avatar=teacher_profile(row["id"])["avatar"])

    @app.post("/api/classes")
    @teacher_api
    def class_create(row):
        data = payload()
        d = db()
        count = d.execute("SELECT count(*) FROM ess_classes WHERE license_id=? AND archived_at IS NULL", (row["id"],)).fetchone()[0]
        if count >= MAX_CLASSES:
            raise ValueError(f"最多建 {MAX_CLASSES} 个班")
        name = text_field(data, "name", CLASS_NAME_MAX, True, "班级名称")
        grade = int_field(data, "grade", 1, 6, "年级")
        cid = d.execute(
            "INSERT INTO ess_classes(license_id,name,grade,code,created_at) VALUES(?,?,?,?,?)",
            (row["id"], name, grade, new_code("ess_classes", "code", 6), int(time.time())),
        ).lastrowid
        return jsonify(ok=True, id=cid, url=url_for("class_page", cid=cid))

    @app.post("/api/classes/<int:cid>/edit")
    @teacher_api
    def class_edit(row, cid):
        own_class(cid, row["id"])
        data = payload()
        db().execute(
            "UPDATE ess_classes SET name=?,grade=? WHERE id=?",
            (text_field(data, "name", CLASS_NAME_MAX, True, "班级名称"), int_field(data, "grade", 1, 6, "年级"), cid),
        )
        return jsonify(ok=True)

    @app.post("/api/classes/<int:cid>/archive")
    @teacher_api
    def class_archive(row, cid):
        own_class(cid, row["id"])
        db().execute("UPDATE ess_classes SET archived_at=? WHERE id=?", (int(time.time()), cid))
        return jsonify(ok=True, url=url_for("teacher_home"))

    @app.post("/api/classes/<int:cid>/students")
    @teacher_api
    def students_add(row, cid):
        own_class(cid, row["id"])
        names = parse_names(payload().get("names"))
        d = db()
        existing = {r[0] for r in d.execute("SELECT name FROM ess_students WHERE class_id=? AND removed_at IS NULL", (cid,))}
        names = [n for n in names if n not in existing]
        if not names:
            raise ValueError("这些学生都已经在班里了")
        room = quota(row["id"])["students"] - students_used(row["id"])
        if len(names) > room:
            raise ValueError(f"学生名额不够：还能加 {max(0, room)} 人，这次要加 {len(names)} 人")
        now = int(time.time())
        d.executemany(
            "INSERT INTO ess_students(class_id,name,pin,created_at) VALUES(?,?,?,?)", [(cid, n, new_pin(), now) for n in names]
        )
        return jsonify(ok=True, added=len(names))

    def own_student(sid, lid):
        st = db().execute(
            "SELECT s.* FROM ess_students s JOIN ess_classes c ON c.id=s.class_id WHERE s.id=? AND c.license_id=? AND s.removed_at IS NULL",
            (sid, lid),
        ).fetchone()
        if not st:
            abort(404)
        return st

    @app.post("/api/students/<int:sid>/pin")
    @teacher_api
    def student_pin(row, sid):
        own_student(sid, row["id"])
        pin = new_pin()
        db().execute("UPDATE ess_students SET pin=?,pin_ver=pin_ver+1,fails=0,locked_until=0 WHERE id=?", (pin, sid))
        return jsonify(ok=True, pin=pin)

    @app.post("/api/students/<int:sid>/rename")
    @teacher_api
    def student_rename(row, sid):
        st = own_student(sid, row["id"])
        name = text_field(payload(), "name", NAME_MAX, True, "姓名")
        if db().execute(
            "SELECT 1 FROM ess_students WHERE class_id=? AND name=? AND id!=? AND removed_at IS NULL", (st["class_id"], name, sid)
        ).fetchone():
            raise ValueError("班里已经有同名的学生")
        db().execute("UPDATE ess_students SET name=? WHERE id=?", (name, sid))
        return jsonify(ok=True)

    @app.post("/api/students/<int:sid>/remove")
    @teacher_api
    def student_remove(row, sid):
        own_student(sid, row["id"])
        db().execute("UPDATE ess_students SET removed_at=? WHERE id=?", (int(time.time()), sid))
        return jsonify(ok=True)

    @app.post("/api/classes/<int:cid>/assignments")
    @teacher_api
    def assignment_create(row, cid):
        own_class(cid, row["id"])
        features.require(db(), row["id"], "essay")
        f = assignment_fields(payload())
        aid = db().execute(
            "INSERT INTO ess_assignments(class_id,title,requirement,genre,min_chars,due_at,auto_publish,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (cid, f["title"], f["requirement"], f["genre"], f["min_chars"], f["due_at"], f["auto_publish"], int(time.time())),
        ).lastrowid
        return jsonify(ok=True, id=aid, url=url_for("assignment_page", aid=aid))

    @app.post("/api/assignments/<int:aid>/edit")
    @teacher_api
    def assignment_edit(row, aid):
        own_assignment(aid, row["id"])
        f = assignment_fields(payload())
        db().execute(
            "UPDATE ess_assignments SET title=?,requirement=?,genre=?,min_chars=?,due_at=?,auto_publish=? WHERE id=?",
            (f["title"], f["requirement"], f["genre"], f["min_chars"], f["due_at"], f["auto_publish"], aid),
        )
        return jsonify(ok=True)

    @app.post("/api/assignments/<int:aid>/<any(close,reopen):action>")
    @teacher_api
    def assignment_close(row, aid, action):
        own_assignment(aid, row["id"])
        if action == "reopen":
            features.require(db(), row["id"], "essay")
        db().execute("UPDATE ess_assignments SET closed_at=? WHERE id=?", (int(time.time()) if action == "close" else None, aid))
        return jsonify(ok=True)

    @app.post("/api/assignments/<int:aid>/publish_all")
    @teacher_api
    def publish_all(row, aid):
        own_assignment(aid, row["id"])
        d = db()
        now = int(time.time())
        ids = [r[0] for r in d.execute("SELECT id FROM ess_submissions WHERE assignment_id=? AND status='review'", (aid,))]
        for sid in ids:
            d.execute("UPDATE ess_reviews SET final=coalesce(final,ai) WHERE submission_id=?", (sid,))
            d.execute("UPDATE ess_submissions SET status='published',published_at=? WHERE id=?", (now, sid))
        return jsonify(ok=True, published=len(ids))

    @app.post("/api/assignments/<int:aid>/submit_for")
    @teacher_api
    def submit_for(row, aid):
        assignment = own_assignment(aid, row["id"])
        features.require(db(), row["id"], "essay")
        data = payload()
        st = db().execute(
            "SELECT * FROM ess_students WHERE id=? AND class_id=? AND removed_at IS NULL", (data.get("student_id"), assignment["class_id"])
        ).fetchone()
        if not st:
            raise ValueError("请选择学生")
        photos = submission_photos()
        sid = create_submission(assignment, st["id"], text_field(data, "title", 40, label="标题"), data.get("text"), "teacher", photos)
        return jsonify(ok=True, id=sid)

    @app.post("/api/submissions/<int:sid>/review")
    @teacher_api
    def review_save(row, sid):
        sub = own_submission(sid, row["id"])
        if sub["status"] not in ("review", "published"):
            raise ValueError("这篇作文还没有批改好")
        data = payload()
        review = review_of(sid)
        band = grading.BANDS[grading.band_of(sub["grade"])]
        final = grading.clean_final(data.get("review"), review["final"] or review["ai"], sub["text"], band["max"])
        now = int(time.time())
        db().execute(
            "UPDATE ess_reviews SET final=?,edited_at=? WHERE submission_id=?", (json.dumps(final, ensure_ascii=False), now, sid)
        )
        if data.get("publish") and sub["status"] == "review":
            db().execute("UPDATE ess_submissions SET status='published',published_at=? WHERE id=?", (now, sid))
        return jsonify(ok=True, total=final["total"], level=final["level"], redpen=redpen_data(sub, final))

    def regrade_check(sub):
        if sub["status"] not in ("review", "failed"):
            raise ValueError("只有等审核或批改失败的作文可以重新批改")
        features.require(db(), sub["license_id"], "essay")
        jobs = db().execute("SELECT count(*) FROM ess_jobs WHERE submission_id=?", (sub["id"],)).fetchone()[0]
        if jobs > MAX_REGRADES:
            raise ValueError(f"每篇作文最多重新批改 {MAX_REGRADES} 次")

    @app.post("/api/submissions/<int:sid>/regrade")
    @teacher_api
    def regrade(row, sid):
        sub = own_submission(sid, row["id"])
        regrade_check(sub)
        db().execute("UPDATE ess_submissions SET status='queued',hold='' WHERE id=?", (sid,))
        enqueue(sid)
        return jsonify(ok=True)

    @app.post("/api/submissions/<int:sid>/text")
    @teacher_api
    def correct_text(row, sid):
        """The teacher corrects what was read from the photos; the essay is graded again on the corrected text."""
        sub = own_submission(sid, row["id"])
        regrade_check(sub)
        text = grading.clean_text(payload().get("text") or "")
        if grading.char_count(text) < 20:
            raise ValueError("作文太短了，至少 20 个字")
        if len(text) > grading.MAX_TEXT:
            raise ValueError(f"作文太长了，最多 {grading.MAX_TEXT} 个字")
        d = db()
        photos = images_of(sid)
        if sub["text"] and photos:
            spots = grading.remap(sub["text"], text, [json.loads(p["lines"]) if p["lines"] else [] for p in photos])
            for p, lines in zip(photos, spots):
                if p["lines"]:
                    d.execute("UPDATE ess_images SET lines=? WHERE id=?", (json.dumps(lines), p["id"]))
        d.execute("UPDATE ess_submissions SET text=?,status='queued',hold='' WHERE id=?", (text, sid))
        enqueue(sid)
        return jsonify(ok=True)

    @app.get("/api/submissions/<int:sid>/state")
    def submission_state(sid):
        row, me = teacher(), student()
        sub = None
        if row:
            sub = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND c.license_id=?", (sid, row["id"])).fetchone()
        if not sub and me:
            sub = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND s.student_id=?", (sid, me["id"])).fetchone()
        if not sub and session.get("trial_essay") == sid:
            sub = db().execute(SUBMISSION_SQL + " WHERE s.id=?", (sid,)).fetchone()
        if not row and not me and not sub:
            return error("请先登录", 403)
        if not sub:
            abort(404)
        return jsonify(state_payload(sub))


# ---- student API ----
def register_student_api(app):
    @app.post("/api/student/login")
    def student_login():
        if limited("student_login", 200, 300):  # per IP: a whole class may sign in from one school network
            return error("尝试太多次了，请 5 分钟后再试", 429)
        data = payload()
        code = data.get("code", "")
        code = code.strip().upper().replace(" ", "") if isinstance(code, str) else ""
        name = data.get("name", "")
        name = " ".join(name.split()) if isinstance(name, str) else ""
        pin = data.get("pin", "")
        pin = pin.strip() if isinstance(pin, str) else ""
        d = db()
        d.execute("BEGIN IMMEDIATE")
        st = d.execute(
            "SELECT s.* FROM ess_students s JOIN ess_classes c ON c.id=s.class_id"
            " JOIN licenses l ON l.site='teacher' AND l.id=c.license_id"
            " WHERE c.code=? AND s.name=? AND s.removed_at IS NULL AND c.archived_at IS NULL AND l.enabled=1 AND l.archived_at IS NULL",
            (code, name),
        ).fetchone()
        if not st:
            d.rollback()
            return error("班级码或姓名不对，请看老师发的登录条", 403)
        now = int(time.time())
        if st["locked_until"] > now:
            d.rollback()
            return error(f"口令输错太多次，请 {(st['locked_until'] - now) // 60 + 1} 分钟后再试", 429)
        if not secrets.compare_digest(pin, st["pin"]):
            fails = st["fails"] + 1
            d.execute(
                "UPDATE ess_students SET fails=?,locked_until=? WHERE id=?",
                (0 if fails >= LOCK_AFTER else fails, now + LOCK_SECONDS if fails >= LOCK_AFTER else 0, st["id"]),
            )
            d.commit()
            return error("口令不对" + ("，请 10 分钟后再试" if fails >= LOCK_AFTER else ""), 403)
        d.execute("UPDATE ess_students SET fails=0 WHERE id=?", (st["id"],))
        d.commit()
        session["student"] = [st["id"], st["pin_ver"]]
        session.permanent = True
        return jsonify(ok=True)

    @app.post("/api/student/logout")
    def student_logout():
        session.pop("student", None)
        return jsonify(ok=True, url=url_for("student_home"))

    @app.post("/api/write/<int:aid>")
    def student_submit(aid):
        me = student()
        if not me:
            return error("请先登录", 403)
        d = db()
        assignment = d.execute("SELECT * FROM ess_assignments WHERE id=? AND class_id=?", (aid, me["class_id"])).fetchone()
        if not assignment:
            abort(404)
        if limited("submit", 150, 600):
            return error("提交太频繁了，请稍后再试", 429)
        try:
            data = payload()
            photos = submission_photos()
            d.execute("BEGIN IMMEDIATE")
            if not features.active(d, me["license_id"], "essay"):
                raise ValueError("老师的作文批改功能已经到期，暂时不能交作文")
            sid = create_submission(assignment, me["id"], text_field(data, "title", 40, label="标题"), data.get("text"), "typed", photos)
        except ValueError as e:
            d.rollback()
            return error(str(e))
        d.commit()
        return jsonify(ok=True, id=sid, state=url_for("submission_state", sid=sid))


# ---- trial: one essay per browser without a license ----
def register_trial(app):
    @app.get("/teacher/trial/essay")
    def trial_page():
        if teacher() and features.active(db(), teacher()["id"], "essay"):
            return redirect(url_for("teacher_home"))
        csrf()
        sid = session.get("trial_essay")
        sub = sid and db().execute(SUBMISSION_SQL + " WHERE s.id=?", (sid,)).fetchone()
        used = db().execute("SELECT 1 FROM trials WHERE site='teacher' AND device_hash=? AND game='essay'", (device_hash(),)).fetchone()
        final = None
        if sub and sub["status"] == "published":
            review = review_of(sid)
            final = review["final"] or review["ai"]
        return render_template(
            "trial.html",
            sub=sub,
            final=final,
            redpen=redpen_data(sub, final) if final else None,
            used=bool(used),
            max_photos=MAX_PHOTOS,
        )

    @app.post("/api/trial/essay")
    def trial_submit():
        d = db()
        try:
            data = payload()
            grade = int_field(data, "grade", 1, 6, "年级")
            genre = data.get("genre")
            if genre not in grading.GENRES:
                raise ValueError("请选择文体")
            title = text_field(data, "title", 40, label="题目")
            photos = submission_photos()
        except ValueError as e:
            return error(str(e))
        device = device_hash()
        if d.execute("SELECT 1 FROM trials WHERE site='teacher' AND device_hash=? AND game='essay'", (device,)).fetchone():
            return error("这台设备已经试用过作文批改了。输入授权码就能继续使用。", 403)
        today = d.execute("SELECT count(*) FROM trials WHERE site='teacher' AND game='essay' AND started_at>=?", (day_start(),)).fetchone()[0]
        if today >= trial_daily():
            return error("今天的免费试用名额已经用完了，请明天再来", 429)
        if limited("trial_essay", TRIAL_PER_IP, 86400):
            return error("今天这个网络已经试用过好几次了，请明天再来", 429)
        now = int(time.time())
        d.execute("BEGIN IMMEDIATE")
        try:
            # A hidden, archived class per grade under license 0 holds the trials; each trial is its own assignment.
            code = f"TRIAL{grade}"
            d.execute(
                "INSERT OR IGNORE INTO ess_classes(license_id,name,grade,code,created_at,archived_at) VALUES(0,'试用',?,?,?,?)",
                (grade, code, now, now),
            )
            cid = d.execute("SELECT id FROM ess_classes WHERE code=?", (code,)).fetchone()[0]
            aid = d.execute(
                "INSERT INTO ess_assignments(class_id,title,requirement,genre,min_chars,auto_publish,created_at) VALUES(?,?,'',?,0,1,?)",
                (cid, title or "试用", genre, now),
            ).lastrowid
            st = d.execute(
                "INSERT INTO ess_students(class_id,name,pin,created_at,removed_at) VALUES(?,?,'',?,?)", (cid, f"试用{aid}", now, now)
            ).lastrowid
            assignment = d.execute("SELECT * FROM ess_assignments WHERE id=?", (aid,)).fetchone()
            sid = create_submission(assignment, st, title, data.get("text"), "trial", photos, check_quota=False)
            d.execute("INSERT INTO trials(site,device_hash,game,started_at) VALUES('teacher',?,'essay',?)", (device, now))
        except ValueError as e:
            d.rollback()
            return error(str(e))
        d.commit()
        session["trial_essay"] = sid
        return jsonify(ok=True, id=sid, url=url_for("trial_page"))
