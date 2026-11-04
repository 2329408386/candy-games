"""语文老师工作台 (/workspace/chinese/): the Chinese teacher's workbench, for primary school. Its feature is
作文批改 (essay grading, this module); features and how long each may be used are in features.py. The teacher's
home lists the features (我的功能); each feature has its own page (/teacher/essay/). Classes are shared by the features and live behind the 班级 button in the header (/teacher/classes/,
statistics per class).

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

Devices. A teacher sees the devices their code is activated on (我的设备 on the profile page) and may remove other
devices themselves, at most SELF_REMOVALS times in 30 days (audit 'device_self_remove'); the admin can always unbind.

Students never read "AI" or "模型": their pages and the status API they poll speak of the teacher marking the work
(STUDENT_STATUS); why a job waits (HOLDS) is shown to teachers only.

Trial. Without a license, each browser may try 作文批改 once, the whole way through: 开始试用 creates a real teacher
license bound to this browser (ess_trials; buyer 免费试用) with 作文批改 for TRIAL_DAYS days, TRIAL_STUDENTS students
and TRIAL_ESSAYS essays in all (not per month); it may set one assignment (TRIAL_ASSIGNMENTS). Everything else — the
class, students signing in, grading, export — is the normal flow. Pressing 开始试用 again on the same browser signs
back in to its trial. trial_check() (trials table, game "essay-class") limits one per browser, a few per network a
day and trial_daily() across the site. Earlier trials graded one essay under a hidden class of license 0; their
results can still be viewed (session["trial_essay"]).

Module map: llm.py (configurable OpenAI-compatible models), grading.py (prompts and validation), worker.py,
features.py, admin.py (模型设置, quotas and feature expiry in the back office); pages are in sites/teacher/templates.
"""

import calendar, io, json, re, secrets, time
from flask import Response, abort, g, jsonify, redirect, render_template, request, session, url_for
from core.app import KEY_ALPHABET, csrf, current, data_dir, db, device_hash, limited, new_xlsx, site, xlsx_download
from . import features, grading

DEFAULT_STUDENTS = 60
DEFAULT_MONTHLY = 300
MAX_CLASSES = 1  # one class per teacher
MAX_ATTEMPTS = 2  # the newest graded one counts
MAX_REGRADES = 3
INTRO_MAX = 300
REQUIREMENT_MAX = 800
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
TRIAL_GAME = "essay-class"  # trials.game of the full trial (the old one-essay trial used "essay")
TRIAL_DAYS = 7
TRIAL_STUDENTS = 60
TRIAL_ESSAYS = 80
TRIAL_ASSIGNMENTS = 1
BUSY = ("queued", "grading")
STATUS = {"queued": "排队批改中", "grading": "AI 批改中", "review": "等老师审核", "published": "已发布", "failed": "批改失败"}
STUDENT_STATUS = {"queued": "老师批改中", "grading": "老师批改中", "review": "老师正在看", "published": "老师批好了", "failed": "老师还没批完，可以重新交一次"}
SELF_REMOVALS = 3
SELF_REMOVAL_DAYS = 30
# 统编版 习作 / 写话 per grade and term (tools/units.py builds it): 布置作文 fills in 主题, 文体 and 要求 from it.
UNITS = json.loads((grading.PROMPTS.parent / "data" / "units.json").read_text(encoding="utf-8"))
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
    """Essays handed in this month (a trial: since it started, its quota is for the whole trial)."""
    return db().execute(
        "SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id"
        " JOIN ess_classes c ON c.id=a.class_id WHERE c.license_id=? AND s.created_at>=?",
        (lid, 0 if is_trial(lid) else month_start()),
    ).fetchone()[0]


def is_trial(lid):
    cache = g.setdefault("trial_lids", {})
    if lid not in cache:
        cache[lid] = bool(db().execute("SELECT 1 FROM ess_trials WHERE license_id=?", (lid,)).fetchone())
    return cache[lid]


def trial_info(lid):
    """For the trial banner: days left (None once over), essays used and the limit."""
    info = features.describe(features.rows(db(), lid).get("essay"))
    return {
        "days": info["days"] if info["state"] in ("active", "soon") else None,
        "used": monthly_used(lid),
        "limit": quota(lid)["monthly"],
    }


def trial_daily():
    row = db().execute("SELECT value FROM ess_meta WHERE key='trial_daily'").fetchone()
    return int(float(row[0])) if row else TRIAL_DAILY


def teacher():
    row = current("essay")
    if row and "seen_touched" not in g:
        g.seen_touched = True
        touch_device(row["id"])
    return row


def touch_device(lid):
    """Keeps 最近使用 of this device up to date (at most one write an hour)."""
    from core.app import digest

    d = db()
    if d.in_transaction:
        return
    now = int(time.time())
    d.execute(
        "UPDATE devices SET last_seen=? WHERE site='teacher' AND license_id=? AND token_hash=? AND last_seen<?",
        (now, lid, digest(session["device"]), now - 3600),
    )
    d.commit()


def device_name(ua):
    """A short, friendly name for a device from its User-Agent, e.g. 「iPhone · 微信」."""
    ua = ua or ""
    if "iPad" in ua or ("Macintosh" in ua and "Mobile" in ua):
        kind = "iPad"
    elif "iPhone" in ua:
        kind = "iPhone"
    elif "HarmonyOS" in ua or "OpenHarmony" in ua:
        kind = "鸿蒙设备"
    elif "Android" in ua:
        kind = "安卓平板" if "Mobile" not in ua else "安卓手机"
    elif "Windows" in ua:
        kind = "Windows 电脑"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        kind = "Mac 电脑"
    elif "CrOS" in ua:
        kind = "Chromebook"
    elif "Linux" in ua:
        kind = "Linux 电脑"
    else:
        kind = "未知设备"
    browsers = (
        ("MicroMessenger", "微信"), ("wxwork", "企业微信"), ("DingTalk", "钉钉"), ("QQ/", "QQ"), ("MQQBrowser", "QQ 浏览器"),
        ("UCBrowser", "UC 浏览器"), ("Quark", "夸克"), ("HuaweiBrowser", "华为浏览器"), ("HeyTapBrowser", "OPPO 浏览器"),
        ("MiuiBrowser", "小米浏览器"), ("VivoBrowser", "vivo 浏览器"), ("baiduboxapp", "百度"), ("Edg", "Edge"),
        ("SE 2.X", "搜狗浏览器"), ("360", "360 浏览器"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("CriOS", "Chrome"),
        ("Safari", "Safari"),
    )
    browser = next((name for key, name in browsers if key in ua), "")
    return f"{kind} · {browser}" if browser else kind


def my_devices(lid):
    """This license's devices, oldest first: [{n, name, created_at, last_seen, me}]; n is what the page sends back."""
    from core.app import digest

    mine = digest(session.get("device") or "")
    rows = db().execute(
        "SELECT token_hash,label,created_at,last_seen FROM devices WHERE site='teacher' AND license_id=? ORDER BY created_at,token_hash",
        (lid,),
    ).fetchall()
    return [
        {"n": i, "name": device_name(r["label"]), "created_at": r["created_at"], "last_seen": r["last_seen"],
         "me": r["token_hash"] == mine, "token_hash": r["token_hash"]}
        for i, r in enumerate(rows, 1)
    ]


def self_removals(lid):
    since = int(time.time()) - SELF_REMOVAL_DAYS * 86400
    return db().execute(
        "SELECT count(*) FROM audit WHERE site='teacher' AND license_id=? AND action='device_self_remove' AND created_at>=?",
        (lid, since),
    ).fetchone()[0]


# ---- trials: each browser may try each feature once ----
def trial_used(game):
    return bool(db().execute("SELECT 1 FROM trials WHERE site='teacher' AND device_hash=? AND game=?", (device_hash(), game)).fetchone())


def trial_check(game, name):
    """None when this browser may start a trial of the feature now, else the error response."""
    if trial_used(game):
        return error(f"这台设备已经试用过{name}了。输入授权码就能继续使用。", 403)
    today = db().execute("SELECT count(*) FROM trials WHERE site='teacher' AND game=? AND started_at>=?", (game, day_start())).fetchone()[0]
    if today >= trial_daily():
        return error("今天的免费试用名额已经用完了，请明天再来", 429)
    if limited("trial_" + game, TRIAL_PER_IP, 86400):
        return error("今天这个网络已经试用过好几次了，请明天再来", 429)
    return None


def record_trial(game):
    db().execute("INSERT INTO trials(site,device_hash,game,started_at) VALUES('teacher',?,?,?)", (device_hash(), game, int(time.time())))


def pending_reviews(lid):
    return db().execute(
        "SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id"
        " WHERE c.license_id=? AND c.archived_at IS NULL AND a.deleted_at IS NULL AND s.status='review'",
        (lid,),
    ).fetchone()[0]


def class_rows(lid):
    return db().execute(
        "SELECT c.*,(SELECT count(*) FROM ess_students s WHERE s.class_id=c.id AND s.removed_at IS NULL) students"
        " FROM ess_classes c WHERE c.license_id=? AND c.archived_at IS NULL ORDER BY c.id",
        (lid,),
    ).fetchall()


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
        " WHERE a.id=? AND c.license_id=? AND c.archived_at IS NULL AND a.deleted_at IS NULL",
        (aid, lid),
    ).fetchone()
    if not row:
        abort(404)
    return row


SUBMISSION_SQL = (
    "SELECT s.*,a.title assignment_title,a.genre,a.intro,a.requirement,a.deleted_at,a.min_chars,a.class_id,c.grade,c.license_id,"
    "c.name class_name,st.name student_name FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id"
    " JOIN ess_classes c ON c.id=a.class_id JOIN ess_students st ON st.id=s.student_id"
)


def own_submission(sid, lid):
    row = db().execute(
        SUBMISSION_SQL + " WHERE s.id=? AND c.license_id=? AND c.archived_at IS NULL AND a.deleted_at IS NULL", (sid, lid)
    ).fetchone()
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
            raise ValueError("试用的批改篇数已经用完了" if is_trial(lid) else "本月的批改篇数已经用完，请联系老师")
    now = int(time.time())
    sid = d.execute(
        "INSERT INTO ess_submissions(assignment_id,student_id,attempt,source,title,text,status,created_at) VALUES(?,?,?,?,?,?,'queued',?)",
        (assignment["id"], student_id, attempt, source, title, text, now),
    ).lastrowid
    if photos:
        store_photos(sid, photos)
    enqueue(sid)
    return sid


def state_payload(row, for_student=False):
    if for_student:
        return {"status": row["status"], "label": STUDENT_STATUS.get(row["status"], ""), "hold": ""}
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
        "level": grading.level_of(review["total"]),
        "showScore": show_score,
        "student": sub["student_name"] if "student_name" in sub.keys() else "",
        "summary": review.get("summary") or "",
    }


MARK_CLASS = {"错别字": "typo", "用词不当": "word", "病句": "sentence", "标点": "punct", "好词好句": "good", "修改建议": "tip", "分段": "para"}


def register_essay(app):
    from . import admin

    app.jinja_env.globals.update(
        STATUS=STATUS,
        STUDENT_STATUS=STUDENT_STATUS,
        HOLDS=HOLDS,
        BUSY=BUSY,
        DIMS=grading.DIMS,
        FLAGS=grading.FLAGS,
        GENRES=list(grading.GENRES),
        GENRE_CHOICES=grading.CHOICES,
        TOTAL=grading.TOTAL,
        essay_quota=quota,
        essay_students_used=students_used,
        essay_monthly_used=monthly_used,
        workbench_features=lambda lid: features.overview(db(), lid),
        workbench_feature_list=features.FEATURES,
        device_name=device_name,
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
        if row:
            me = teacher_profile(row["id"])
            me["classes"] = db().execute(
                "SELECT count(*) FROM ess_classes WHERE license_id=? AND archived_at IS NULL", (row["id"],)
            ).fetchone()[0]
            me["devices"] = db().execute("SELECT count(*) FROM devices WHERE site='teacher' AND license_id=?", (row["id"],)).fetchone()[0]
            me["device_limit"] = row["device_limit"]
            me["trial"] = trial_info(row["id"]) if is_trial(row["id"]) else None
        return {
            "side": side,
            "me_teacher": me if row else None,
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
            return render_template("teacher_login.html")
        d = db()
        return render_template(
            "teacher_home.html",
            feats=features.overview(d, row["id"]),
            pending=pending_reviews(row["id"]),
            has_classes=bool(class_rows(row["id"])),
        )

    @app.get("/teacher/profile")
    def profile_page():
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        return render_template(
            "profile.html",
            name_max=TEACHER_NAME_MAX,
            devices=my_devices(row["id"]),
            device_limit=row["device_limit"],
            removals_left=max(0, SELF_REMOVALS - self_removals(row["id"])),
            removal_days=SELF_REMOVAL_DAYS,
        )

    @app.get("/teacher/classes/")
    def classes_page():
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        d = db()
        stats = []
        for c in class_rows(row["id"]):
            a = d.execute(
                "SELECT count(*) n, coalesce(sum((SELECT count(DISTINCT s.student_id) FROM ess_submissions s WHERE s.assignment_id=a.id)),0) handed"
                " FROM ess_assignments a WHERE a.class_id=? AND a.deleted_at IS NULL",
                (c["id"],),
            ).fetchone()
            pending = d.execute(
                "SELECT count(*) FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id WHERE a.class_id=? AND a.deleted_at IS NULL AND s.status='review'",
                (c["id"],),
            ).fetchone()[0]
            totals = [
                json.loads(r[0])["total"]
                for r in d.execute(
                    "SELECT coalesce(r.final,r.ai) FROM ess_reviews r JOIN ess_submissions s ON s.id=r.submission_id"
                    " JOIN ess_assignments a ON a.id=s.assignment_id WHERE a.class_id=? AND a.deleted_at IS NULL AND s.status='published'"
                    # the newest published attempt of each student counts
                    " AND s.attempt=(SELECT max(x.attempt) FROM ess_submissions x WHERE x.assignment_id=s.assignment_id"
                    " AND x.student_id=s.student_id AND x.status='published')",
                    (c["id"],),
                )
            ]
            levels = {name: 0 for name in reversed(grading.LEVELS)}
            for t in totals:
                levels[grading.level_of(t)] += 1
            expected = a["n"] * c["students"]
            stats.append({
                "c": c,
                "assignments": a["n"],
                "rate": round(100 * a["handed"] / expected) if expected else None,
                "graded": len(totals),
                "avg": round(sum(totals) / len(totals), 1) if totals else None,
                "levels": levels,
                "pending": pending,
            })
        return render_template(
            "classes.html",
            stats=stats,
            max_classes=MAX_CLASSES,
            quota=quota(row["id"]),
            students_used=students_used(row["id"]),
        )

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
            " FROM ess_assignments a WHERE a.class_id=? AND a.deleted_at IS NULL ORDER BY a.id DESC",
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

    @app.get("/teacher/essay/")
    def essay_home():
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        d = db()
        classes = class_rows(row["id"])
        pending = d.execute(
            "SELECT s.id,s.attempt,s.created_at,s.title,a.title assignment_title,c.name class_name,st.name student_name"
            " FROM ess_submissions s JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id"
            " JOIN ess_students st ON st.id=s.student_id"
            " WHERE c.license_id=? AND c.archived_at IS NULL AND a.deleted_at IS NULL AND s.status='review' ORDER BY s.created_at LIMIT 200",
            (row["id"],),
        ).fetchall()
        assignments = d.execute(
            "SELECT a.*,c.name class_name,"
            " (SELECT count(*) FROM ess_students st WHERE st.class_id=c.id AND st.removed_at IS NULL) students,"
            " (SELECT count(DISTINCT s.student_id) FROM ess_submissions s WHERE s.assignment_id=a.id) submitted,"
            " (SELECT count(*) FROM ess_submissions s WHERE s.assignment_id=a.id AND s.status='review') pending"
            " FROM ess_assignments a JOIN ess_classes c ON c.id=a.class_id"
            " WHERE c.license_id=? AND c.archived_at IS NULL AND a.deleted_at IS NULL ORDER BY a.id DESC",
            (row["id"],),
        ).fetchall()
        now = int(time.time())
        ongoing = [a for a in assignments if not a["closed_at"] and not (a["due_at"] and a["due_at"] < now)]
        ended = [a for a in assignments if a not in ongoing]
        return render_template(
            "essay_home.html",
            feature=features.describe(features.rows(d, row["id"]).get("essay")),
            classes=classes,
            pending=pending,
            ongoing=ongoing,
            ended=ended,
            quota=quota(row["id"]),
            students_used=students_used(row["id"]),
            monthly_used=monthly_used(row["id"]),
        )

    @app.get("/teacher/essay/new")
    def essay_new():
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        classes = class_rows(row["id"])
        chosen = request.args.get("class", type=int)
        month = time.gmtime(time.time() + BEIJING).tm_mon
        return render_template(
            "essay_new.html",
            classes=classes,
            units=UNITS,
            term="下册" if 2 <= month <= 7 else "上册",
            chosen=chosen if any(c["id"] == chosen for c in classes) else (classes[0]["id"] if classes else None),
            feature=features.describe(features.rows(db(), row["id"]).get("essay")),
        )

    @app.get("/teacher/classes/<int:cid>/slips.xlsx")
    def class_slips(cid):
        """登录条 as an Excel sheet: the 网址 and 班级码 once at the top, then one bordered row per student to print and cut."""
        row = teacher()
        if not row:
            return redirect(url_for("teacher_home"))
        klass = own_class(cid, row["id"])
        students = db().execute("SELECT * FROM ess_students WHERE class_id=? AND removed_at IS NULL ORDER BY id", (cid,)).fetchall()
        url = request.host_url.rstrip("/") + url_for("student_home")
        out, book = new_xlsx()
        ws = book.add_worksheet("登录条")
        title = book.add_format({"bold": True, "font_size": 16, "valign": "vcenter"})
        info = book.add_format({"font_size": 14, "valign": "vcenter"})
        head = book.add_format({"bold": True, "font_size": 14, "border": 1, "align": "center", "valign": "vcenter", "bg_color": "#EEEEEE"})
        cell = book.add_format({"font_size": 16, "border": 1, "align": "center", "valign": "vcenter"})
        name = book.add_format({"font_size": 16, "border": 1, "bold": True, "align": "center", "valign": "vcenter"})
        ws.set_column(0, 0, 8)
        ws.set_column(1, 1, 18)
        ws.set_column(2, 3, 16)
        ws.merge_range(0, 0, 0, 3, klass["name"] + " 登录条", title)
        ws.merge_range(1, 0, 1, 3, "学生登录网址：" + url, info)
        ws.merge_range(2, 0, 2, 3, "班级码：" + klass["code"] + "　　（登录时填 班级码 + 姓名 + 口令）", info)
        ws.set_row(0, 30)
        ws.set_row(1, 24)
        ws.set_row(2, 24)
        ws.write_row(4, 0, ["序号", "姓名", "班级码", "口令"], head)
        for i, s in enumerate(students, 1):
            ws.set_row(4 + i, 32)
            ws.write_number(4 + i, 0, i, cell)
            ws.write_string(4 + i, 1, s["name"], name)
            ws.write_string(4 + i, 2, klass["code"], cell)
            ws.write_string(4 + i, 3, s["pin"], cell)
        ws.freeze_panes(5, 0)
        ws.set_paper(9)  # A4
        ws.fit_to_pages(1, 0)
        ws.center_horizontally()
        ws.repeat_rows(4)
        ws.set_header("&L&12" + url.replace("&", "&&"))
        book.close()
        return xlsx_download(out.getvalue(), klass["name"] + " 登录条.xlsx")

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

    @app.get("/teacher/essay/assignments/<int:aid>/export.json")
    def assignment_export(aid):
        """批量导出: each student's newest graded essay (published, or waiting for review as it stands now), in the
        order of the class list, for RedPen.exportMany; skipped = students with nothing graded yet."""
        row = teacher()
        if not row:
            return error("请先激活老师授权码", 403)
        assignment = own_assignment(aid, row["id"])
        d = db()
        students = d.execute(
            "SELECT * FROM ess_students WHERE class_id=? AND removed_at IS NULL ORDER BY id", (assignment["class_id"],)
        ).fetchall()
        essays = []
        for st in students:
            sub = d.execute(
                SUBMISSION_SQL + " WHERE s.assignment_id=? AND s.student_id=? AND s.status IN ('review','published')"
                " ORDER BY s.attempt DESC LIMIT 1",
                (aid, st["id"]),
            ).fetchone()
            review = sub and review_of(sub["id"])
            if review:
                essays.append(redpen_data(sub, review["final"] or review["ai"]))
        return jsonify(
            essays=essays,
            skipped=len(students) - len(essays),
            name=f"《{assignment['title']}》-{assignment['class_name']}-全部批改.pdf",
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
            " FROM ess_assignments a WHERE a.class_id=? AND a.deleted_at IS NULL ORDER BY a.id DESC",
            (me["id"], me["id"], me["class_id"]),
        ).fetchall()
        return render_template("student_home.html", me=me, assignments=assignments)

    @app.get("/student/essay/<int:aid>")
    def write_page(aid):
        me = student()
        if not me:
            return redirect(url_for("student_home"))
        assignment = db().execute(
            "SELECT * FROM ess_assignments WHERE id=? AND class_id=? AND deleted_at IS NULL", (aid, me["class_id"])
        ).fetchone()
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
        sub = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND s.student_id=? AND a.deleted_at IS NULL", (sid, me["id"])).fetchone()
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
        redpen={**redpen_data(sub, final), **({"student": ""} if request.args.get("anon") == "1" else {})},
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


def genre_field(value):
    """文体: any of grading.CHOICES (a list, or one string joined with 、), stored joined with 、; '' = 不指定."""
    if isinstance(value, str):
        value = re.split(r"[、,，\s]+", value)
    if not isinstance(value, list):
        return ""
    chosen = {v for v in value if isinstance(v, str)}
    return "、".join(g for g in grading.CHOICES if g in chosen)


def assignment_fields(data):
    genre = genre_field(data.get("genre"))
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
    if not isinstance(requirement, str) or len(requirement) > REQUIREMENT_MAX:
        raise ValueError(f"习作重点最多 {REQUIREMENT_MAX} 个字")
    intro = data.get("intro", "")
    if not isinstance(intro, str) or len(intro) > INTRO_MAX:
        raise ValueError(f"引言最多 {INTRO_MAX} 个字")
    return {
        "title": text_field(data, "title", 40, True, "作文主题"),
        "requirement": grading.clean_text(requirement),
        "intro": grading.clean_text(intro),
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

    @app.post("/api/me/devices/<int:n>/remove")
    @teacher_api
    def device_remove(row, n):
        from core.app import audit

        created = payload().get("created_at")
        device = next((x for x in my_devices(row["id"]) if x["n"] == n and x["created_at"] == created), None)
        if not device:
            raise ValueError("设备列表变了，请刷新页面再试")
        if device["me"]:
            raise ValueError("不能移除正在用的这台设备；要换设备，请在这台设备上退出登录")
        if self_removals(row["id"]) >= SELF_REMOVALS:
            raise ValueError(f"{SELF_REMOVAL_DAYS} 天内最多自己移除 {SELF_REMOVALS} 台设备，请联系管理员解绑")
        db().execute(
            "DELETE FROM devices WHERE site='teacher' AND license_id=? AND token_hash=?", (row["id"], device["token_hash"])
        )
        audit("device_self_remove", row["id"], device["name"])
        return jsonify(ok=True)

    @app.post("/api/classes")
    @teacher_api
    def class_create(row):
        data = payload()
        d = db()
        count = d.execute("SELECT count(*) FROM ess_classes WHERE license_id=? AND archived_at IS NULL", (row["id"],)).fetchone()[0]
        if count >= MAX_CLASSES:
            raise ValueError("每位老师只能建一个班级")
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
        return jsonify(ok=True, url=url_for("classes_page"))

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
        if is_trial(row["id"]):
            made = db().execute(
                "SELECT count(*) FROM ess_assignments a JOIN ess_classes c ON c.id=a.class_id WHERE c.license_id=?", (row["id"],)
            ).fetchone()[0]
            if made >= TRIAL_ASSIGNMENTS:
                raise ValueError(f"试用只能布置 {TRIAL_ASSIGNMENTS} 次作文，输入授权码后可以继续布置")
        f = assignment_fields(payload())
        aid = db().execute(
            "INSERT INTO ess_assignments(class_id,title,intro,requirement,genre,min_chars,due_at,auto_publish,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (cid, f["title"], f["intro"], f["requirement"], f["genre"], f["min_chars"], f["due_at"], f["auto_publish"], int(time.time())),
        ).lastrowid
        return jsonify(ok=True, id=aid, url=url_for("assignment_page", aid=aid))

    @app.post("/api/assignments/<int:aid>/edit")
    @teacher_api
    def assignment_edit(row, aid):
        own_assignment(aid, row["id"])
        f = assignment_fields(payload())
        db().execute(
            "UPDATE ess_assignments SET title=?,intro=?,requirement=?,genre=?,min_chars=?,due_at=?,auto_publish=? WHERE id=?",
            (f["title"], f["intro"], f["requirement"], f["genre"], f["min_chars"], f["due_at"], f["auto_publish"], aid),
        )
        return jsonify(ok=True)

    @app.post("/api/assignments/<int:aid>/delete")
    @teacher_api
    def assignment_delete(row, aid):
        """Hidden from every page (teachers and students); the rows stay so the month's 批改篇数 still counts them."""
        own_assignment(aid, row["id"])
        db().execute("UPDATE ess_assignments SET deleted_at=? WHERE id=?", (int(time.time()), aid))
        return jsonify(ok=True, url=url_for("essay_home"))

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
        as_student = False
        if not sub and me:
            sub = db().execute(SUBMISSION_SQL + " WHERE s.id=? AND s.student_id=?", (sid, me["id"])).fetchone()
            as_student = bool(sub)
        if not sub and session.get("trial_essay") == sid:
            sub = db().execute(SUBMISSION_SQL + " WHERE s.id=?", (sid,)).fetchone()
        if not row and not me and not sub:
            return error("请先登录", 403)
        if not sub:
            abort(404)
        return jsonify(state_payload(sub, for_student=as_student))


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
        assignment = d.execute(
            "SELECT * FROM ess_assignments WHERE id=? AND class_id=? AND deleted_at IS NULL", (aid, me["class_id"])
        ).fetchone()
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


# ---- trial: a temporary teacher account per browser, for one whole assignment ----
def trial_of_this_device():
    """The license id of this browser's trial when it can still sign in to it, else None."""
    from core.app import digest

    token = session.get("device")
    if not token:
        return None
    row = db().execute(
        "SELECT t.license_id FROM ess_trials t JOIN licenses l ON l.site='teacher' AND l.id=t.license_id"
        " JOIN devices v ON v.site='teacher' AND v.license_id=t.license_id AND v.token_hash=?"
        " WHERE t.device_hash=? AND l.enabled=1 AND l.archived_at IS NULL ORDER BY t.license_id DESC LIMIT 1",
        (digest(token), digest(token)),
    ).fetchone()
    return row[0] if row else None


def create_trial_license(d, now):
    """A teacher license for this browser's trial (the caller holds the write lock); returns its id."""
    from core.app import digest, group_map

    token = session["device"]
    lid = d.execute("SELECT coalesce(max(id),0)+1 FROM licenses WHERE site='teacher'").fetchone()[0]
    while True:
        key = "".join(secrets.choice(KEY_ALPHABET) for _ in range(16))
        if not d.execute("SELECT 1 FROM licenses WHERE site='teacher' AND key_hash=?", (digest(key),)).fetchone():
            break
    d.execute(
        "INSERT INTO licenses(site,id,key_hash,games,buyer,assigned_at,created_at,device_limit) VALUES('teacher',?,?,'[]',?,?,?,1)",
        (lid, digest(key), "免费试用 " + time.strftime("%Y-%m-%d", time.gmtime(now + BEIJING)), now, now),
    )
    d.execute("INSERT INTO license_keys(site,license_id,code) VALUES('teacher',?,?)", (lid, key))
    d.executemany("INSERT INTO license_groups(site,license_id,group_id) VALUES('teacher',?,?)", [(lid, gid) for gid in group_map()])
    features.grant(d, lid, "essay", TRIAL_DAYS, started=True, now=now)
    d.execute("INSERT INTO ess_quota(license_id,students,monthly) VALUES(?,?,?)", (lid, TRIAL_STUDENTS, TRIAL_ESSAYS))
    d.execute(
        "INSERT INTO devices(site,license_id,token_hash,label,created_at,last_seen) VALUES('teacher',?,?,?,?,?)",
        (lid, digest(token), request.user_agent.string[:200], now, now),
    )
    d.execute("INSERT INTO ess_trials(license_id,device_hash,created_at) VALUES(?,?,?)", (lid, digest(token), now))
    return lid


def register_trial(app):
    @app.get("/teacher/trial/essay")
    def trial_page():
        row = teacher()
        if row and features.active(db(), row["id"], "essay"):
            return redirect(url_for("essay_home"))
        csrf()
        # A result of the earlier one-essay trial stays viewable.
        sid = session.get("trial_essay")
        sub = sid and db().execute(SUBMISSION_SQL + " WHERE s.id=?", (sid,)).fetchone()
        final = None
        if sub and sub["status"] == "published":
            review = review_of(sid)
            final = review["final"] or review["ai"]
        return render_template(
            "trial.html",
            final=final,
            redpen=redpen_data(sub, final) if final else None,
            mine=bool(trial_of_this_device()),
            used=trial_used(TRIAL_GAME),
            days=TRIAL_DAYS,
            students=TRIAL_STUDENTS,
            essays=TRIAL_ESSAYS,
        )

    @app.post("/api/trial/essay/start")
    def trial_start_essay():
        """Signs this browser in to its trial account, creating it the first time."""
        lid = trial_of_this_device()
        if not lid:
            problem = trial_check(TRIAL_GAME, "作文批改")
            if problem:
                return problem
            d = db()
            d.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            lid = create_trial_license(d, now)
            record_trial(TRIAL_GAME)
            d.commit()
        session["license"] = lid
        session.permanent = True
        return jsonify(ok=True, url=url_for("teacher_home"))
