"""Online PK rooms: 2-10 licensed players answer the same questions, each on their own device.

State lives in SQLite so deploys and restarts keep rooms alive; clients poll GET /api/rooms/<code> every ~2 s.
`version` goes up on every change, and a poll that already has the latest version gets a tiny reply.
The host's browser draws the question ids (same quotas as a solo round); the server only checks they are in the
bank. Answers are graded in the browser (the renderers hold the grading logic) and reported one by one in order;
the server keeps the score with the game's rules. When the deadline passes, or everybody has answered every
question, the room ends and each player's points go into `scores` (mode 'online'), +20 for the winner(s).
"""

import json, secrets, time
from flask import abort, jsonify, request, url_for
from .app import bank_json, current, db, limited, site
from .social import SOLO_PER_DAY, display, ensure_profile, period_start, profile_row

MAX_PLAYERS = 10
WIN_BONUS = 20
COUNTDOWN = 3  # seconds between 开始 and the first question
GRACE = 3  # answers may arrive this late after the deadline (network delay)
MIN_ANSWER_GAP = 1  # seconds between two answers of one player
ROOM_TTL = 2 * 3600
LOBBY_TTL = 30 * 60  # rooms waiting longer than this disappear from the class list
IDLE = 45  # a player not seen for this long has left the lobby


def begin():
    """Takes the write lock, first committing the implicit transaction an earlier write may have opened."""
    d = db()
    if d.in_transaction:
        d.commit()
    d.execute("BEGIN IMMEDIATE")
    return d


def room_row(code):
    return db().execute("SELECT * FROM rooms WHERE site=? AND code=?", (site().key, code)).fetchone()


def players(code):
    return (
        db()
        .execute(
            "SELECT r.*,p.nickname,p.avatar_ver FROM room_players r LEFT JOIN profiles p ON p.site=r.site AND p.license_id=r.license_id"
            " WHERE r.site=? AND r.code=? ORDER BY r.joined_at,r.license_id",
            (site().key, code),
        )
        .fetchall()
    )


def bump(code):
    db().execute("UPDATE rooms SET version=version+1 WHERE site=? AND code=?", (site().key, code))


def bank_ids(game):
    return {str(q["id"]) for q in json.loads(bank_json(game))["questions"]}


def finalize(room, now):
    """Ends a running room once: writes everybody's points (winner bonus included). Caller holds the write lock."""
    s = site().key
    rows = players(room["code"])
    top = max((r["score"] for r in rows), default=0)
    day = period_start("day", now)
    for r in rows:
        bonus = WIN_BONUS if len(rows) > 1 and top > 0 and r["score"] == top else 0
        db().execute(
            "UPDATE room_players SET bonus=? WHERE site=? AND code=? AND license_id=?",
            (bonus, s, room["code"], r["license_id"]),
        )
        played = db().execute(
            "SELECT count(*) FROM scores WHERE site=? AND license_id=? AND mode='online' AND created_at>=?",
            (s, r["license_id"], day),
        ).fetchone()[0]
        if r["answered"] and played < SOLO_PER_DAY:
            db().execute(
                "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,?,?,?,?,?)",
                (s, r["license_id"], room["game"], "online", r["score"] + bonus, now),
            )
    db().execute(
        "UPDATE rooms SET status='ended',deadline=min(deadline,?),version=version+1 WHERE site=? AND code=?",
        (now, s, room["code"]),
    )


def settle(code, now):
    """Brings a room up to date: ends it when its time is up or everybody has finished. Returns the fresh row."""
    room = room_row(code)
    if room and room["status"] == "running":
        total = len(json.loads(room["question_ids"]))
        rows = players(code)
        # Answers sent in the last second may arrive up to GRACE seconds late, so the room stays open that long.
        if now >= room["deadline"] + GRACE or all(r["answered"] >= total for r in rows):
            d = begin()
            room = room_row(code)
            if room["status"] == "running":
                finalize(room, now)
            d.commit()
            room = room_row(code)
    elif room and room["status"] == "lobby":
        # Lobby players whose page went away (closed tab, lost network) drop out; the host keeps the room.
        stale = db().execute(
            "DELETE FROM room_players WHERE site=? AND code=? AND license_id!=? AND last_seen<?",
            (site().key, code, room["host"], now - IDLE),
        )
        if stale.rowcount:
            bump(code)
            room = room_row(code)
        db().commit()
    return room


def state(room, lid, now):
    rows = players(room["code"])
    running = room["status"] in ("running", "ended")
    out = []
    for r in rows:
        out.append(
            dict(
                display(r["license_id"], r["nickname"] or "", r["avatar_ver"] or 0),
                score=r["score"],
                correct=r["correct"],
                answered=r["answered"],
                bonus=r["bonus"],
                host=r["license_id"] == room["host"],
            )
        )
    if running:
        out.sort(key=lambda p: (-p["score"], -p["correct"]))
        rank = 0
        for i, p in enumerate(out):
            if not i or p["score"] != out[i - 1]["score"]:
                rank = i + 1
            p["rank"] = rank
    return {
        "code": room["code"],
        "game": room["game"],
        "title": site().games[room["game"]],
        "status": room["status"],
        "seconds": room["seconds"],
        "host": room["host"],
        "me": lid,
        "joined": any(r["license_id"] == lid for r in rows),
        "players": out,
        "max": MAX_PLAYERS,
        "startsAt": room["started_at"],
        "deadline": room["deadline"],
        "questions": json.loads(room["question_ids"]) if running else None,
        "version": room["version"],
        "now": now,
        "link": url_for("game_page", game=room["game"], _external=False) + "?room=" + room["code"],
    }


def register_rooms(app):
    def fail(message, status=400, **extra):
        if db().in_transaction:
            db().rollback()
        return jsonify(error=message, **extra), status

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def player(code):
        """(license row, room) for a licensed user of the room's game, or aborts with 403/404."""
        room = room_row(code) if code.isdigit() else None
        if not room:
            abort(404)
        row = current(room["game"])
        if not row:
            abort(403)
        return row, room

    @app.post("/api/rooms/<game>/create")
    def room_create(game):
        if game not in site().games:
            abort(404)
        row = current(game)
        if not row:
            abort(403)
        if limited("room_create", 30, 600):
            return fail("开房间太频繁了，请过几分钟再试", 429)
        seconds = body().get("seconds")
        if not isinstance(seconds, int) or not 30 <= seconds <= 1800:
            return fail("本局时长需要在30到1800秒之间")
        lid, s, now = row["id"], site().key, int(time.time())
        ensure_profile(lid)
        d = begin()
        old = [r[0] for r in d.execute("SELECT code FROM rooms WHERE site=? AND created_at<?", (s, now - ROOM_TTL))]
        for code in old:
            d.execute("DELETE FROM room_players WHERE site=? AND code=?", (s, code))
            d.execute("DELETE FROM rooms WHERE site=? AND code=?", (s, code))
        # One waiting room per host: opening a new one closes the old.
        d.execute("UPDATE rooms SET status='closed',version=version+1 WHERE site=? AND host=? AND status='lobby'", (s, lid))
        while True:
            code = f"{secrets.randbelow(900000) + 100000}"
            if not room_row(code):
                break
        d.execute(
            "INSERT INTO rooms(site,code,game,host,class_id,status,seconds,question_ids,version,created_at,started_at,deadline)"
            " VALUES(?,?,?,?,?,'lobby',?,'[]',1,?,0,0)",
            (s, code, game, lid, profile_row(lid)["class_id"], seconds, now),
        )
        d.execute(
            "INSERT INTO room_players(site,code,license_id,joined_at,last_seen) VALUES(?,?,?,?,?)", (s, code, lid, now, now)
        )
        d.commit()
        return jsonify(state(room_row(code), lid, now))

    @app.post("/api/rooms/<code>/join")
    def room_join(code):
        if limited("room_join", 60, 600):
            return fail("尝试次数太多，请过几分钟再试", 429)
        row, room = player(code)
        lid, s, now = row["id"], site().key, int(time.time())
        ensure_profile(lid)
        d = begin()
        room = room_row(code)
        inside = d.execute(
            "SELECT 1 FROM room_players WHERE site=? AND code=? AND license_id=?", (s, code, lid)
        ).fetchone()
        if not inside:
            if room["status"] != "lobby":
                return fail("这个房间已经开始或结束了，请让同学再开一个房间", 409)
            if d.execute("SELECT count(*) FROM room_players WHERE site=? AND code=?", (s, code)).fetchone()[0] >= MAX_PLAYERS:
                return fail(f"房间已满{MAX_PLAYERS}人", 409)
            d.execute(
                "INSERT INTO room_players(site,code,license_id,joined_at,last_seen) VALUES(?,?,?,?,?)",
                (s, code, lid, now, now),
            )
            bump(code)
        else:
            d.execute("UPDATE room_players SET last_seen=? WHERE site=? AND code=? AND license_id=?", (now, s, code, lid))
        d.commit()
        return jsonify(state(room_row(code), lid, now))

    @app.post("/api/rooms/<code>/leave")
    def room_leave(code):
        row, room = player(code)
        lid, s = row["id"], site().key
        if room["status"] == "lobby":
            if room["host"] == lid:
                db().execute("UPDATE rooms SET status='closed' WHERE site=? AND code=?", (s, code))
            db().execute("DELETE FROM room_players WHERE site=? AND code=? AND license_id=?", (s, code, lid))
            bump(code)
            db().commit()
        return jsonify(ok=True)

    @app.get("/api/rooms/<code>")
    def room_state(code):
        row, room = player(code)
        lid, now = row["id"], int(time.time())
        room = settle(code, now)
        seen = db().execute(
            "UPDATE room_players SET last_seen=? WHERE site=? AND code=? AND license_id=? AND last_seen<?",
            (now, site().key, code, lid, now - 10),
        )
        if seen.rowcount:
            db().commit()
        if request.args.get("v") == str(room["version"]):
            return jsonify(same=True, now=now)
        return jsonify(state(room, lid, now))

    @app.post("/api/rooms/<code>/start")
    def room_start(code):
        row, room = player(code)
        lid, s, now = row["id"], site().key, int(time.time())
        if room["host"] != lid:
            return fail("只有开房间的人可以开始", 403)
        ids = body().get("questions")
        if (
            not isinstance(ids, list)
            or not 1 <= len(ids) <= 20
            or len(set(map(str, ids))) != len(ids)
            or not set(map(str, ids)) <= bank_ids(room["game"])
        ):
            return fail("题目数据不正确，请刷新页面后重试")
        settle(code, now)  # drops lobby players who have gone away
        d = begin()
        room = room_row(code)
        if room["status"] != "lobby":
            return fail("这个房间已经开始了", 409)
        if d.execute("SELECT count(*) FROM room_players WHERE site=? AND code=?", (s, code)).fetchone()[0] < 2:
            return fail("至少要有2个人才能开始")
        start = now + COUNTDOWN
        d.execute(
            "UPDATE rooms SET status='running',question_ids=?,started_at=?,deadline=?,version=version+1 WHERE site=? AND code=?",
            (json.dumps(ids), start, start + room["seconds"], s, code),
        )
        d.commit()
        return jsonify(state(room_row(code), lid, now))

    @app.post("/api/rooms/<code>/answer")
    def room_answer(code):
        row, room = player(code)
        lid, s, now = row["id"], site().key, time.time()
        data = body()
        index, correct = data.get("index"), data.get("correct")
        if not isinstance(index, int) or not isinstance(correct, bool):
            return fail("答题数据不正确")
        d = begin()
        room = room_row(code)
        me = d.execute(
            "SELECT * FROM room_players WHERE site=? AND code=? AND license_id=?", (s, code, lid)
        ).fetchone()
        if not me:
            return fail("你不在这个房间里", 403)
        if room["status"] != "running" or not room["started_at"] <= now <= room["deadline"] + GRACE:
            return fail("这一局已经结束了", 409)
        if index != me["answered"] or index >= len(json.loads(room["question_ids"])):
            return fail("题号不对，请刷新页面", 409, answered=me["answered"])
        if now - me["last_answer"] < MIN_ANSWER_GAP:
            return fail("答得太快了", 429)
        combo = me["combo"] + 1 if correct else 0
        score = me["score"] + 10 + (5 if combo > 1 else 0) if correct else max(0, me["score"] - 5)
        d.execute(
            "UPDATE room_players SET score=?,combo=?,correct=correct+?,answered=answered+1,last_answer=?,last_seen=?"
            " WHERE site=? AND code=? AND license_id=?",
            (score, combo, int(correct), now, int(now), s, code, lid),
        )
        bump(code)
        d.commit()
        settle(code, int(now))
        return jsonify(score=score, answered=index + 1)

    @app.get("/api/class/rooms")
    def class_rooms():
        row = current()
        if not row:
            abort(403)
        p = profile_row(row["id"])
        if not p or not p["class_id"]:
            return jsonify(rooms=[])
        now = int(time.time())
        rows = (
            db()
            .execute(
                "SELECT r.code,r.game,r.host,r.created_at,p.nickname,p.avatar_ver,"
                " (SELECT count(*) FROM room_players x WHERE x.site=r.site AND x.code=r.code) AS players"
                " FROM rooms r LEFT JOIN profiles p ON p.site=r.site AND p.license_id=r.host"
                " WHERE r.site=? AND r.class_id=? AND r.status='lobby' AND r.created_at>=? ORDER BY r.created_at DESC LIMIT 20",
                (site().key, p["class_id"], now - LOBBY_TTL),
            )
            .fetchall()
        )
        game = request.args.get("game")
        return jsonify(
            rooms=[
                {
                    "code": r["code"],
                    "game": r["game"],
                    "title": site().games[r["game"]],
                    "host": display(r["host"], r["nickname"] or "", r["avatar_ver"] or 0),
                    "players": r["players"],
                    "max": MAX_PLAYERS,
                    "link": url_for("game_page", game=r["game"]) + "?room=" + r["code"],
                }
                for r in rows
                if r["game"] in site().games and (not game or r["game"] == game) and r["host"] != row["id"]
            ]
        )
