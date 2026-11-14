"""Profiles (nickname + avatar), classes and class rankings. One license code is one user.

Every endpoint needs a licensed browser (current()). A user belongs to at most one class: profiles.class_id.
Points go into `scores`: a solo round is reported once at its end against a ticket from /start (the server
recomputes the game score from the right/wrong sequence and records a tenth of it, rank_points); a mistake
re-practice round scores 1 point per question answered right; free trials never score. Every recorded point also
counts towards the user's 段位 (core/ranks.py). The site-wide leaderboard (top 20) is cached per process for a minute.
"""

import io, secrets, time
from flask import Response, abort, jsonify, redirect, render_template, request, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
from . import devices, feedback, ranks
from .app import current, db, limited, log_play, site

AVATAR_SIZE = 96
NAME_MAX = 8
CLASS_NAME_MAX = 20
CLASS_MAX = 100
PER_ROUND = 20
SOLO_PER_DAY = 30  # rounds per user per day and mode that count towards the rankings
TOP_N = 20
TOP_CACHE_SECONDS = 60
_top_cache = {}
MIN_SECONDS_PER_ANSWER = 1  # a round now moves on by itself 1 s (right) or 2 s (wrong) after each answer
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


def rank_points(score):
    """What a game score is worth on the leaderboards: a tenth, rounded half up (230 → 23, 235 → 24)."""
    return (max(0, score) + 5) // 10


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
            "SELECT license_id,nickname,avatar_ver,class_id,rank_stars,rank_progress FROM profiles WHERE site=? AND license_id=?",
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
    return dict(
        display(row["id"], p["nickname"] if p else "", p["avatar_ver"] if p else 0),
        in_class=bool(p and p["class_id"]),
        tier=ranks.badge(p["rank_stars"] if p else 0),
        feedback_unread=feedback.unread(row["id"]),
    )


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


def ranked(rows):
    """Score rows (license_id, nickname, avatar_ver, rank_stars, points, rounds) → display dicts with competition ranks."""
    out, rank = [], 0
    for i, r in enumerate(rows):
        if not i or r["points"] != rows[i - 1]["points"]:
            rank = i + 1
        out.append(
            dict(
                display(r["license_id"], r["nickname"], r["avatar_ver"]),
                rank=rank,
                points=r["points"],
                rounds=r["rounds"],
                tier=ranks.badge(r["rank_stars"]),
            )
        )
    return out


ACTIVE = " JOIN licenses l ON l.site=p.site AND l.id=p.license_id AND l.enabled=1 AND l.archived_at IS NULL"


def ranking(cid, period):
    since = period_start("week") if period == "week" else 0
    rows = (
        db()
        .execute(
            "SELECT p.license_id,p.nickname,p.avatar_ver,p.rank_stars,coalesce(sum(s.points),0) AS points,count(s.id) AS rounds"
            " FROM profiles p" + ACTIVE +
            " LEFT JOIN scores s ON s.site=p.site AND s.license_id=p.license_id AND s.created_at>=?"
            " WHERE p.site=? AND p.class_id=? GROUP BY p.license_id ORDER BY points DESC,p.license_id",
            (since, site().key, cid),
        )
        .fetchall()
    )
    return ranked(rows)


def site_totals(period):
    """Every active license with points in the period: [(license_id, points)], best first. Cached for a minute."""
    key, now = (site().key, period), time.time()
    hit = _top_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    since = period_start("week") if period == "week" else 0
    totals = [
        (r[0], r[1])
        for r in db().execute(
            "SELECT s.license_id,sum(s.points) AS points FROM scores s"
            " JOIN licenses l ON l.site=s.site AND l.id=s.license_id AND l.enabled=1 AND l.archived_at IS NULL"
            " WHERE s.site=? AND s.created_at>=? GROUP BY s.license_id HAVING points>0 ORDER BY points DESC,s.license_id",
            (site().key, since),
        )
    ]
    _top_cache[key] = (now + TOP_CACHE_SECONDS, totals)
    return totals


def top_ranking(period, lid):
    """The site-wide top TOP_N, plus where `lid` stands ({rank, points, gap} or None without points)."""
    totals = site_totals(period)
    rank, mine = 0, None
    for i, (who, points) in enumerate(totals):
        if not i or points != totals[i - 1][1]:
            rank = i + 1
        if who == lid:
            mine = {"rank": rank, "points": points}
            break
    top = totals[:TOP_N]
    if mine:
        cutoff = top[-1][1] if len(top) == TOP_N else 0
        mine["gap"] = max(0, cutoff - mine["points"] + 1) if mine["rank"] > TOP_N else 0
    elif len(top) == TOP_N:
        mine = {"rank": None, "points": 0, "gap": top[-1][1] + 1}
    ids = [who for who, _ in top]
    info = {}
    if ids:
        marks = ",".join("?" * len(ids))
        info = {
            r["license_id"]: r
            for r in db().execute(
                f"SELECT license_id,nickname,avatar_ver,rank_stars FROM profiles WHERE site=? AND license_id IN ({marks})",
                (site().key, *ids),
            )
        }
    rows = []
    for who, points in top:
        p = info.get(who)
        rows.append(
            {
                "license_id": who,
                "nickname": p["nickname"] if p else "",
                "avatar_ver": p["avatar_ver"] if p else 0,
                "rank_stars": p["rank_stars"] if p else 0,
                "points": points,
                "rounds": 0,
            }
        )
    return ranked(rows), mine


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
            rank=ranks.info(p["rank_stars"], p["rank_progress"]),
            klass=c
            and {"name": c["name"], "code": c["code"], "owner": c["owner"] == lid, "members": member_count(c["id"])},
        )

    @app.get("/me")
    def me_page():
        row = current()
        if not row:
            return redirect(url_for("home"))
        devices.touch(row["id"])
        return render_template("me.html")

    @app.get("/ranking")
    def ranking_page():
        row = current()
        if not row:
            return redirect(url_for("home"))
        p = profile_row(row["id"])
        c = class_row(p["class_id"]) if p and p["class_id"] else None
        board = "class" if request.args.get("board") == "class" and c else "top"
        return render_template("ranking.html", board=board, klass=c and c["name"])

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
            return fail("这个班级是你创建的，不能直接退出；如果不再需要这个班级，可以解散它")
        db().execute("UPDATE profiles SET class_id=NULL WHERE site=? AND license_id=?", (site().key, lid))
        db().commit()
        return jsonify(me_payload(lid))

    @app.post("/api/class/<action>")
    def manage_class(action):
        lid = user()
        c = own_class(lid)
        if not c or c["owner"] != lid:
            return fail("只有班级创建者可以管理班级", 403)
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
            return fail("只有班级创建者可以管理班级", 403)
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

    @app.get("/api/ranking")
    def site_ranking():
        lid = user()
        db().commit()
        period = "week" if request.args.get("period") == "week" else "all"
        rows, mine = top_ranking(period, lid)
        return jsonify(period=period, me=lid, mine=mine, rows=rows)

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
        mode = payload().get("mode")
        mode = mode if mode in ("mistakes", "pk") else "solo"
        log_play(game, mode, row["id"])
        if mode == "pk":  # 同屏双人PK is only counted for 统计; it never scores
            d.commit()
            return jsonify(round=None)
        d.execute("DELETE FROM rounds WHERE site=? AND started_at<?", (site().key, now - 86400))
        ticket = secrets.token_urlsafe(16)
        d.execute(
            "INSERT INTO rounds(site,id,license_id,game,started_at,mode) VALUES(?,?,?,?,?,?)",
            (site().key, ticket, row["id"], game, now, mode),
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
        mode = ticket["mode"]
        # Mistake re-practice: +1 for every mistake answered right (it leaves the mistake book).
        points = sum(marks) if mode == "mistakes" else rank_points(round_points(marks))
        reason = (
            "这一局没有答题，不计分"
            if not marks
            else "答题速度异常，这一局不计分" if now - ticket["started_at"] < MIN_SECONDS_PER_ANSWER * len(marks) else ""
        )
        if reason:
            d.commit()
            return jsonify(recorded=False, points=0, in_class=in_class, reason=reason)
        today = d.execute(
            "SELECT count(*) FROM scores WHERE site=? AND license_id=? AND mode=? AND created_at>=?",
            (s, lid, mode, period_start("day", now)),
        ).fetchone()[0]
        if today >= SOLO_PER_DAY:
            d.commit()
            return jsonify(
                recorded=False, points=points, in_class=in_class, reason=f"今天已有{SOLO_PER_DAY}局{'错题重练' if mode == 'mistakes' else ''}计入排行，明天再来吧"
            )
        d.execute(
            "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,?,?,?,?,?)",
            (s, lid, game, mode, points, now),
        )
        before, after = ranks.add_points(d, s, lid, points)
        d.commit()
        _top_cache.clear()  # this process shows the new points at once; other workers within a minute
        return jsonify(
            recorded=True, points=points, in_class=in_class, mode=mode, rank=after, stars=after["total"] - before["total"], promoted=after["name"] != before["name"] and after["name"]
        )
