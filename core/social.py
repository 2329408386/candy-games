"""Profiles (nickname + avatar), classes and class rankings. One license code is one user.

Every endpoint needs a licensed browser (current()). A user belongs to at most one class: profiles.class_id.
Points go into `scores`: a solo round is reported once at its end against a ticket from /start (the server
recomputes the points from the right/wrong sequence), mistake re-practice and free trials never score.
"""

import io, secrets, time
from flask import Response, abort, jsonify, redirect, render_template, request, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from .app import current, db, limited, site

AVATAR_SIZE = 96
NAME_MAX = 8
CLASS_NAME_MAX = 20
CLASS_MAX = 100
PER_ROUND = 20
SOLO_PER_DAY = 30  # solo rounds per user per day that count towards the rankings
MIN_SECONDS_PER_ANSWER = 2
BEIJING = 8 * 3600


def period_start(kind, now=None):
    """Unix time of today's (kind='day') or this Monday's (kind='week') 00:00 Beijing time."""
    local = int(now if now is not None else time.time()) + BEIJING
    day = local - local % 86400
    if kind == "week":
        day -= ((day // 86400 + 3) % 7) * 86400  # 1970-01-01 was a Thursday
    return day - BEIJING


def round_points(marks):
    """Same scoring as the game: +10 per right answer, +5 more from the second in a row, -5 per wrong (min 0)."""
    score = combo = 0
    for ok in marks:
        if ok:
            combo += 1
            score += 10 + (5 if combo > 1 else 0)
        else:
            combo = 0
            score = max(0, score - 5)
    return score


def clean_text(value, limit):
    if not isinstance(value, str):
        return ""
    text = "".join(c for c in value if c.isprintable()).strip()
    return " ".join(text.split())[:limit]


def ensure_profile(lid):
    db().execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES(?,?)", (site().key, lid))


def profile_row(lid):
    return (
        db()
        .execute(
            "SELECT license_id,nickname,avatar_ver,class_id FROM profiles WHERE site=? AND license_id=?",
            (site().key, lid),
        )
        .fetchone()
    )


def display(lid, nickname, avatar_ver):
    """What other people see of a user: name, avatar URL (or None) and a colour slot for the letter avatar."""
    name = nickname or f"同学{lid}"
    return {
        "id": lid,
        "name": name,
        "initial": name[0],
        "color": lid % 6,
        "avatar": url_for("avatar", lid=lid, v=avatar_ver) if avatar_ver else None,
    }


def header_profile():
    """The signed-in user's chip for the page header, or None for unlicensed browsers."""
    row = current()
    if not row:
        return None
    p = profile_row(row["id"])
    return display(row["id"], p["nickname"] if p else "", p["avatar_ver"] if p else 0)


def class_row(cid):
    return db().execute("SELECT * FROM classes WHERE site=? AND id=?", (site().key, cid)).fetchone()


def member_count(cid):
    return db().execute("SELECT count(*) FROM profiles WHERE site=? AND class_id=?", (site().key, cid)).fetchone()[0]


def reencode_avatar(data):
    """Any small JPEG/PNG/WebP/GIF → a 96×96 WebP without metadata, a few KB. Raises ValueError."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG", "WEBP", "GIF") or max(img.size) > 4096:
            raise ValueError
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGBA")
        flat = Image.new("RGB", img.size, (255, 254, 250))
        flat.paste(img, mask=img.getchannel("A"))
        flat = ImageOps.fit(flat, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        flat.save(out, "WEBP", quality=70, method=6)
        return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ValueError("图片格式不支持，请换一张图片")


def dissolve(cid):
    d = db()
    d.execute("UPDATE profiles SET class_id=NULL WHERE site=? AND class_id=?", (site().key, cid))
    d.execute("DELETE FROM classes WHERE site=? AND id=?", (site().key, cid))


def leave_class(lid):
    """Takes a user out of their class; an owner leaving dissolves it. Used by the admin page."""
    p = profile_row(lid)
    if p and p["class_id"]:
        c = class_row(p["class_id"])
        if c and c["owner"] == lid:
            dissolve(c["id"])
        else:
            db().execute("UPDATE profiles SET class_id=NULL WHERE site=? AND license_id=?", (site().key, lid))


def reset_profile(lid, field):
    column = {"nickname": "nickname=''", "avatar": "avatar=NULL,avatar_ver=0"}[field]
    db().execute(f"UPDATE profiles SET {column} WHERE site=? AND license_id=?", (site().key, lid))


def ranking(cid, period):
    since = period_start("week") if period == "week" else 0
    rows = (
        db()
        .execute(
            "SELECT p.license_id,p.nickname,p.avatar_ver,coalesce(sum(s.points),0) AS points,count(s.id) AS rounds"
            " FROM profiles p JOIN licenses l ON l.site=p.site AND l.id=p.license_id AND l.enabled=1 AND l.archived_at IS NULL"
            " LEFT JOIN scores s ON s.site=p.site AND s.license_id=p.license_id AND s.created_at>=?"
            " WHERE p.site=? AND p.class_id=? GROUP BY p.license_id ORDER BY points DESC,p.license_id",
            (since, site().key, cid),
        )
        .fetchall()
    )
    out, rank = [], 0
    for i, r in enumerate(rows):
        if not i or r["points"] != rows[i - 1]["points"]:
            rank = i + 1
        out.append(dict(display(r["license_id"], r["nickname"], r["avatar_ver"]), rank=rank, points=r["points"], rounds=r["rounds"]))
    return out


def register_social(app):
    app.jinja_env.globals.update(header_profile=header_profile)

    def user():
        row = current()
        if not row:
            abort(403)
        if not profile_row(row["id"]):
            ensure_profile(row["id"])
            db().commit()  # before any BEGIN IMMEDIATE below
        return row["id"]

    def payload():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def fail(message, status=400):
        db().rollback()
        return jsonify(error=message), status

    def me_payload(lid):
        p = profile_row(lid)
        c = class_row(p["class_id"]) if p["class_id"] else None
        return dict(
            display(lid, p["nickname"], p["avatar_ver"]),
            nickname=p["nickname"],
            klass=c
            and {"name": c["name"], "code": c["code"], "owner": c["owner"] == lid, "members": member_count(c["id"])},
        )

    @app.get("/me")
    def me_page():
        if not current():
            return redirect(url_for("home"))
        return render_template("me.html")

    @app.get("/avatars/<int:lid>.webp")
    def avatar(lid):
        if not current():
            abort(403)
        row = db().execute("SELECT avatar FROM profiles WHERE site=? AND license_id=?", (site().key, lid)).fetchone()
        if not row or not row["avatar"]:
            abort(404)
        r = Response(row["avatar"], mimetype="image/webp")
        # The URL carries ?v=<avatar_ver>, so a new avatar gets a new URL.
        r.headers["Cache-Control"] = "private, max-age=31536000, immutable"
        r.headers["Content-Security-Policy"] = "default-src 'none'"
        return r

    @app.get("/api/me")
    def me():
        lid = user()
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/me/nickname")
    def set_nickname():
        lid = user()
        if limited("nickname", 30, 600):
            return fail("改得太频繁了，请过几分钟再试", 429)
        name = clean_text(payload().get("nickname"), NAME_MAX + 1)
        if not 1 <= len(name) <= NAME_MAX:
            return fail(f"昵称需要1到{NAME_MAX}个字")
        db().execute("UPDATE profiles SET nickname=? WHERE site=? AND license_id=?", (name, site().key, lid))
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/me/avatar")
    def set_avatar():
        lid = user()
        if limited("avatar", 20, 3600):
            return fail("头像换得太频繁了，请一小时后再试", 429)
        try:
            data = reencode_avatar(request.get_data())
        except ValueError as exc:
            return fail(str(exc))
        db().execute(
            "UPDATE profiles SET avatar=?,avatar_ver=avatar_ver+1 WHERE site=? AND license_id=?",
            (data, site().key, lid),
        )
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/me/avatar/reset")
    def clear_avatar():
        lid = user()
        reset_profile(lid, "avatar")
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/class/create")
    def create_class():
        lid = user()
        d = db()
        d.execute("BEGIN IMMEDIATE")
        if profile_row(lid)["class_id"]:
            return fail("你已经在一个班级里了，先退出才能创建新班级")
        name = clean_text(payload().get("name"), CLASS_NAME_MAX + 1)
        if not 1 <= len(name) <= CLASS_NAME_MAX:
            return fail(f"班级名称需要1到{CLASS_NAME_MAX}个字")
        s = site().key
        cid = d.execute("SELECT coalesce(max(id),0)+1 FROM classes WHERE site=?", (s,)).fetchone()[0]
        while True:
            code = f"{secrets.randbelow(900000) + 100000}"
            if not d.execute("SELECT 1 FROM classes WHERE site=? AND code=?", (s, code)).fetchone():
                break
        d.execute(
            "INSERT INTO classes(site,id,code,name,owner,created_at) VALUES(?,?,?,?,?,?)",
            (s, cid, code, name, lid, int(time.time())),
        )
        d.execute("UPDATE profiles SET class_id=? WHERE site=? AND license_id=?", (cid, s, lid))
        d.commit()
        return jsonify(me_payload(lid))

    @app.post("/api/class/join")
    def join_class():
        lid = user()
        if limited("class_join", 20, 600):
            return fail("尝试次数太多，请10分钟后再试", 429)
        d = db()
        d.execute("BEGIN IMMEDIATE")
        if profile_row(lid)["class_id"]:
            return fail("你已经在一个班级里了，先退出才能加入别的班级")
        code = clean_text(payload().get("code"), 12).replace(" ", "")
        c = d.execute("SELECT * FROM classes WHERE site=? AND code=?", (site().key, code)).fetchone()
        if not c:
            return fail("没有找到这个班级码，请检查后再试", 404)
        if member_count(c["id"]) >= CLASS_MAX:
            return fail(f"这个班级已满{CLASS_MAX}人")
        d.execute("UPDATE profiles SET class_id=? WHERE site=? AND license_id=?", (c["id"], site().key, lid))
        d.commit()
        return jsonify(me_payload(lid))

    def own_class(lid):
        p = profile_row(lid)
        return class_row(p["class_id"]) if p["class_id"] else None

    @app.post("/api/class/leave")
    def leave():
        lid = user()
        c = own_class(lid)
        if not c:
            return fail("你还没有加入班级")
        if c["owner"] == lid:
            return fail("你是班主，不能直接退出；如果不再需要这个班级，可以解散它")
        db().execute("UPDATE profiles SET class_id=NULL WHERE site=? AND license_id=?", (site().key, lid))
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/class/<action>")
    def manage_class(action):
        lid = user()
        c = own_class(lid)
        if not c or c["owner"] != lid:
            return fail("只有班主可以管理班级", 403)
        if action == "rename":
            name = clean_text(payload().get("name"), CLASS_NAME_MAX + 1)
            if not 1 <= len(name) <= CLASS_NAME_MAX:
                return fail(f"班级名称需要1到{CLASS_NAME_MAX}个字")
            db().execute("UPDATE classes SET name=? WHERE site=? AND id=?", (name, site().key, c["id"]))
        elif action == "dissolve":
            dissolve(c["id"])
        else:
            abort(404)
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/class/members/<int:member>/<action>")
    def manage_member(member, action):
        lid = user()
        c = own_class(lid)
        if not c or c["owner"] != lid:
            return fail("只有班主可以管理班级", 403)
        p = profile_row(member)
        if member == lid or not p or p["class_id"] != c["id"]:
            return fail("这位同学不在你的班级里", 404)
        if action == "remove":
            db().execute("UPDATE profiles SET class_id=NULL WHERE site=? AND license_id=?", (site().key, member))
        elif action in ("nickname", "avatar"):
            reset_profile(member, action)
        else:
            abort(404)
        db().commit()
        return jsonify(ok=True)

    @app.get("/api/class/ranking")
    def class_ranking():
        lid = user()
        c = own_class(lid)
        db().commit()
        if not c:
            return jsonify(error="你还没有加入班级"), 404
        period = "week" if request.args.get("period") == "week" else "all"
        return jsonify(period=period, me=lid, rows=ranking(c["id"], period))

    # ---- solo rounds: a ticket at the start, the right/wrong sequence at the end ----
    @app.post("/api/rounds/<game>/start")
    def round_start(game):
        if game not in site().games:
            abort(404)
        row = current(game)
        if not row:
            return jsonify(error="授权已失效"), 403
        d = db()
        now = int(time.time())
        d.execute("DELETE FROM rounds WHERE site=? AND started_at<?", (site().key, now - 86400))
        ticket = secrets.token_urlsafe(16)
        d.execute(
            "INSERT INTO rounds(site,id,license_id,game,started_at) VALUES(?,?,?,?,?)",
            (site().key, ticket, row["id"], game, now),
        )
        d.commit()
        return jsonify(round=ticket)

    @app.post("/api/rounds/<game>/finish")
    def round_finish(game):
        if game not in site().games:
            abort(404)
        row = current(game)
        if not row:
            return jsonify(error="授权已失效"), 403
        data = payload()
        marks = data.get("answers")
        if (
            not isinstance(data.get("round"), str)
            or not isinstance(marks, list)
            or len(marks) > PER_ROUND
            or not all(isinstance(m, bool) for m in marks)
        ):
            return jsonify(error="成绩格式不正确"), 400
        d = db()
        d.execute("BEGIN IMMEDIATE")
        s, lid, now = site().key, row["id"], int(time.time())
        ticket = d.execute(
            "SELECT * FROM rounds WHERE site=? AND id=? AND license_id=? AND game=?", (s, data["round"], lid, game)
        ).fetchone()
        if not ticket:
            return fail("这一局的成绩已经提交过了", 409)
        d.execute("DELETE FROM rounds WHERE site=? AND id=?", (s, ticket["id"]))
        ensure_profile(lid)
        in_class = bool(profile_row(lid)["class_id"])
        points = round_points(marks)
        reason = (
            "这一局没有答题，不计分"
            if not marks
            else "答题速度异常，这一局不计分" if now - ticket["started_at"] < MIN_SECONDS_PER_ANSWER * len(marks) else ""
        )
        if reason:
            d.commit()
            return jsonify(recorded=False, points=0, in_class=in_class, reason=reason)
        today = d.execute(
            "SELECT count(*) FROM scores WHERE site=? AND license_id=? AND mode='solo' AND created_at>=?",
            (s, lid, period_start("day", now)),
        ).fetchone()[0]
        if today >= SOLO_PER_DAY:
            d.commit()
            return jsonify(
                recorded=False, points=points, in_class=in_class, reason=f"今天已有{SOLO_PER_DAY}局计入排行，明天再来吧"
            )
        d.execute(
            "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,?,?,?,?,?)",
            (s, lid, game, "solo", points, now),
        )
        d.commit()
        return jsonify(recorded=True, points=points, in_class=in_class)
