"""Online PK rooms: 2-10 players answer the same questions, each on their own device.

A player is a device (seat), not a license: one license on two devices can play itself. Such a room — or the extra
devices of a license in a mixed room — is practice for that account: a license scores at most once per room (its
best seat), and only when at least two different licenses played. A room with one license scores nothing.

State lives in SQLite so deploys and restarts keep rooms alive; clients poll GET /api/rooms/<code> every ~2 s.
`version` goes up on every change, and a poll that already has the latest version gets a tiny reply.
The host's browser draws the question ids (same quotas as a solo round); the server only checks they are in the
bank. Answers are graded in the browser (the renderers hold the grading logic) and reported one by one in order;
the server keeps the score with the game's rules. When the deadline (+ grace) passes, or everybody has answered
every question, the room ends and points go into `scores` (mode 'online'): a tenth of the game score (rank_points)
plus a placing bonus (PLACE_BONUS). Those points also buy 段位 stars (core/ranks.py); a PK itself adds or takes no stars.
(Tables pk_rooms/pk_seats replaced rooms/room_players, which keyed players by license; those are no longer used.)
"""

import json, secrets, time
from flask import abort, jsonify, request, url_for
from . import ranks
from .app import bank_json, current, db, device_hash, limited, site
from .social import SOLO_PER_DAY, _top_cache, display, ensure_profile, period_start, profile_row, rank_points

MAX_PLAYERS = 10
# Placing bonus by number of scoring licenses: 2 players → 1st +10; 3 or more → +20/+10/+5; nobody loses points.
# Ties share a place.
PLACE_BONUS = {2: (10,), 3: (20, 10, 5)}
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
    return db().execute("SELECT * FROM pk_rooms WHERE site=? AND code=?", (site().key, code)).fetchone()


def seats(code):
    return (
        db()
        .execute(
            "SELECT r.*,p.nickname,p.avatar_ver,p.rank_stars FROM pk_seats r LEFT JOIN profiles p ON p.site=r.site AND p.license_id=r.license_id"
            " WHERE r.site=? AND r.code=? ORDER BY r.joined_at,r.rowid",
            (site().key, code),
        )
        .fetchall()
    )


def my_seat(code):
    return (
        db()
        .execute("SELECT * FROM pk_seats WHERE site=? AND code=? AND device=?", (site().key, code, device_hash()))
        .fetchone()
    )


def bump(code):
    db().execute("UPDATE pk_rooms SET version=version+1 WHERE site=? AND code=?", (site().key, code))


def bank_ids(game):
    return {str(q["id"]) for q in json.loads(bank_json(game))["questions"]}


def best_seats(rows):
    """license_id -> its best seat (score, then correct answers, then earliest joined)."""
    best = {}
    for r in rows:
        b = best.get(r["license_id"])
        if not b or (r["score"], r["correct"]) > (b["score"], b["correct"]):
            best[r["license_id"]] = r
    return best


def finalize(room, now):
    """Ends a running room once and writes the points. Caller holds the write lock."""
    s = site().key
    best = best_seats(seats(room["code"]))
    day = period_start("day", now)
    if len(best) > 1:  # a license playing only itself scores nothing
        prizes = PLACE_BONUS[min(len(best), 3)]
        scores = sorted((r["score"] for r in best.values()), reverse=True)
        for lid, r in best.items():
            place = scores.index(r["score"])  # competition ranking: ties share the higher place
            bonus = prizes[place] if place < len(prizes) and r["score"] > 0 and r["answered"] else 0
            played = db().execute(
                "SELECT count(*) FROM scores WHERE site=? AND license_id=? AND mode='online' AND created_at>=?",
                (s, lid, day),
            ).fetchone()[0]
            result = ""  # pk_outcome: 'scored' when this seat's points went into `scores`
            if r["answered"] and played < SOLO_PER_DAY:
                points = rank_points(r["score"]) + bonus
                db().execute(
                    "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,?,?,?,?,?)",
                    (s, lid, room["game"], "online", points, now),
                )
                ranks.add_points(db(), s, lid, points)
                result = "scored"
            db().execute(
                "UPDATE pk_seats SET bonus=?,counted=1,pk_outcome=? WHERE site=? AND code=? AND seat=?",
                (bonus, result, s, room["code"], r["seat"]),
            )
    _top_cache.clear()
    db().execute(
        "UPDATE pk_rooms SET status='ended',deadline=min(deadline,?),version=version+1 WHERE site=? AND code=?",
        (now, s, room["code"]),
    )


def settle(code, now):
    """Brings a room up to date: ends it when its time is up or everybody has finished. Returns the fresh row."""
    room = room_row(code)
    if room and room["status"] == "running":
        total = len(json.loads(room["question_ids"]))
        rows = seats(code)
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
            "DELETE FROM pk_seats WHERE site=? AND code=? AND seat!=? AND last_seen<?",
            (site().key, code, room["host_seat"], now - IDLE),
        )
        if stale.rowcount:
            bump(code)
            room = room_row(code)
        db().commit()
    return room


def state(room, me, now):
    """The room as one player (seat `me`, or None) sees it."""
    rows = seats(room["code"])
    running = room["status"] in ("running", "ended")
    devices = {}
    out = []
    for r in rows:
        n = devices[r["license_id"]] = devices.get(r["license_id"], 0) + 1
        p = display(r["license_id"], r["nickname"] or "", r["avatar_ver"] or 0)
        if n > 1:
            p["name"] += f" · 设备{n}"
        out.append(
            dict(
                p,
                seat=r["seat"],
                score=r["score"],
                correct=r["correct"],
                answered=r["answered"],
                bonus=r["bonus"],
                counted=bool(r["counted"]),
                # Ranking points this seat earned (0 when the room did not score it).
                points=rank_points(r["score"]) + r["bonus"] if r["pk_outcome"] else 0,
                tier=ranks.badge(r["rank_stars"] or 0),
                host=r["seat"] == room["host_seat"],
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
        "host": room["host_seat"],
        "me": me,
        "joined": any(r["seat"] == me for r in rows),
        "players": out,
        "accounts": len(devices),
        "practice": len(devices) < 2,  # only one license so far: its own devices, no points
        "inClass": in_class(),
        "max": MAX_PLAYERS,
        "startsAt": room["started_at"],
        "deadline": room["deadline"],
        "questions": json.loads(room["question_ids"]) if running else None,
        "version": room["version"],
        "now": now,
        "link": url_for("game_page", game=room["game"], _external=False) + "?room=" + room["code"],
    }


def in_class():
    row = current()
    p = row and profile_row(row["id"])
    return bool(p and p["class_id"])


def new_seat(code, lid, now):
    seat = secrets.token_hex(4)
    db().execute(
        "INSERT INTO pk_seats(site,code,seat,license_id,device,joined_at,last_seen) VALUES(?,?,?,?,?,?,?)",
        (site().key, code, seat, lid, device_hash(), now, now),
    )
    return seat


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
        device = device_hash()
        d = begin()
        old = [r[0] for r in d.execute("SELECT code FROM pk_rooms WHERE site=? AND created_at<?", (s, now - ROOM_TTL))]
        for code in old:
            d.execute("DELETE FROM pk_seats WHERE site=? AND code=?", (s, code))
            d.execute("DELETE FROM pk_rooms WHERE site=? AND code=?", (s, code))
        # One waiting room per host device: opening a new one closes the old.
        d.execute(
            "UPDATE pk_rooms SET status='closed',version=version+1 WHERE site=? AND host_device=? AND status='lobby'",
            (s, device),
        )
        while True:
            code = f"{secrets.randbelow(900000) + 100000}"
            if not room_row(code):
                break
        d.execute(
            "INSERT INTO pk_rooms(site,code,game,host,host_seat,host_device,class_id,status,seconds,question_ids,version,created_at,started_at,deadline)"
            " VALUES(?,?,?,?,'',?,?,'lobby',?,'[]',1,?,0,0)",
            (s, code, game, lid, device, profile_row(lid)["class_id"], seconds, now),
        )
        seat = new_seat(code, lid, now)
        d.execute("UPDATE pk_rooms SET host_seat=? WHERE site=? AND code=?", (seat, s, code))
        d.commit()
        return jsonify(state(room_row(code), seat, now))

    @app.post("/api/rooms/<code>/join")
    def room_join(code):
        if limited("room_join", 60, 600):
            return fail("尝试次数太多，请过几分钟再试", 429)
        row, room = player(code)
        lid, s, now = row["id"], site().key, int(time.time())
        ensure_profile(lid)
        device_hash()  # may write the session; do it before taking the lock
        d = begin()
        room = room_row(code)
        mine = my_seat(code)
        if not mine:
            if room["status"] != "lobby":
                return fail("这个房间已经开始或结束了，请让同学再开一个房间", 409)
            if d.execute("SELECT count(*) FROM pk_seats WHERE site=? AND code=?", (s, code)).fetchone()[0] >= MAX_PLAYERS:
                return fail(f"房间已满{MAX_PLAYERS}人", 409)
            seat = new_seat(code, lid, now)
            bump(code)
        else:
            seat = mine["seat"]
            d.execute("UPDATE pk_seats SET last_seen=? WHERE site=? AND code=? AND seat=?", (now, s, code, seat))
        d.commit()
        return jsonify(state(room_row(code), seat, now))

    @app.post("/api/rooms/<code>/leave")
    def room_leave(code):
        row, room = player(code)
        s, mine = site().key, my_seat(code)
        if mine and room["status"] == "lobby":
            if room["host_seat"] == mine["seat"]:
                db().execute("UPDATE pk_rooms SET status='closed' WHERE site=? AND code=?", (s, code))
            db().execute("DELETE FROM pk_seats WHERE site=? AND code=? AND seat=?", (s, code, mine["seat"]))
            bump(code)
            db().commit()
        return jsonify(ok=True)

    @app.get("/api/rooms/<code>")
    def room_state(code):
        player(code)
        now = int(time.time())
        room = settle(code, now)
        mine = my_seat(code)
        if mine and mine["last_seen"] < now - 10:
            db().execute("UPDATE pk_seats SET last_seen=? WHERE site=? AND code=? AND seat=?", (now, site().key, code, mine["seat"]))
            db().commit()
        if request.args.get("v") == str(room["version"]):
            return jsonify(same=True, now=now)
        return jsonify(state(room, mine and mine["seat"], now))

    @app.post("/api/rooms/<code>/start")
    def room_start(code):
        row, room = player(code)
        s, now, mine = site().key, int(time.time()), my_seat(code)
        if not mine or room["host_seat"] != mine["seat"]:
            return fail("只有开房间的那台设备可以开始", 403)
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
        if d.execute("SELECT count(*) FROM pk_seats WHERE site=? AND code=?", (s, code)).fetchone()[0] < 2:
            return fail("至少要有2个人才能开始")
        start = now + COUNTDOWN
        d.execute(
            "UPDATE pk_rooms SET status='running',question_ids=?,started_at=?,deadline=?,version=version+1 WHERE site=? AND code=?",
            (json.dumps(ids), start, start + room["seconds"], s, code),
        )
        d.execute(  # 统计: one online round for every license in the room
            "INSERT INTO plays(site,game,mode,license_id,created_at)"
            " SELECT DISTINCT site,?,'online',license_id,? FROM pk_seats WHERE site=? AND code=?",
            (room["game"], now, s, code),
        )
        d.commit()
        return jsonify(state(room_row(code), mine["seat"], now))

    @app.post("/api/rooms/<code>/answer")
    def room_answer(code):
        player(code)
        s, now = site().key, time.time()
        data = body()
        index, correct = data.get("index"), data.get("correct")
        if not isinstance(index, int) or not isinstance(correct, bool):
            return fail("答题数据不正确")
        device_hash()
        d = begin()
        room = room_row(code)
        me = my_seat(code)
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
            "UPDATE pk_seats SET score=?,combo=?,correct=correct+?,answered=answered+1,last_answer=?,last_seen=?"
            " WHERE site=? AND code=? AND seat=?",
            (score, combo, int(correct), now, int(now), s, code, me["seat"]),
        )
        bump(code)
        d.commit()
        settle(code, int(now))
        return jsonify(score=score, answered=index + 1)

    @app.get("/api/class/rooms")
    def class_rooms():
        """Waiting rooms of classmates, plus rooms this license opened on another device."""
        row = current()
        if not row:
            abort(403)
        p = profile_row(row["id"])
        now, device = int(time.time()), device_hash()
        rows = (
            db()
            .execute(
                "SELECT r.code,r.game,r.host,r.host_device,r.created_at,p.nickname,p.avatar_ver,"
                " (SELECT count(*) FROM pk_seats x WHERE x.site=r.site AND x.code=r.code) AS players"
                " FROM pk_rooms r LEFT JOIN profiles p ON p.site=r.site AND p.license_id=r.host"
                " WHERE r.site=? AND (r.class_id=? OR r.host=?) AND r.status='lobby' AND r.created_at>=?"
                " ORDER BY r.host=? DESC,r.created_at DESC LIMIT 20",
                (site().key, p["class_id"] if p else None, row["id"], now - LOBBY_TTL, row["id"]),
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
                    "mine": r["host"] == row["id"],  # opened by this license on another device
                    "players": r["players"],
                    "max": MAX_PLAYERS,
                    "link": url_for("game_page", game=r["game"]) + "?room=" + r["code"],
                }
                for r in rows
                if r["game"] in site().games and (not game or r["game"] == game) and r["host_device"] != device
            ]
        )
