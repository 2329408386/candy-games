import json, time
import pytest
from conftest import SiteClient
from sites import SITES
from test_social import second_key, reset_social, alice, bob  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def reset_rooms(db):
    db.execute("DELETE FROM rooms")
    db.execute("DELETE FROM room_players")
    db.commit()


def game_of(site):
    return next(iter(SITES[site].games))


def ids(client, site, n=3):
    bank = client.get(f"/games/{game_of(site)}/bank.json").get_json()
    return [q["id"] for q in bank["questions"][:n]]


def open_room(alice, bob, site, db):
    st = alice.post(f"/api/rooms/{game_of(site)}/create", json={"seconds": 120}).get_json()
    assert bob.post(f"/api/rooms/{st['code']}/join", json={}).status_code == 200
    return st["code"]


def started(alice, bob, site, db, n=3):
    code = open_room(alice, bob, site, db)
    assert alice.post(f"/api/rooms/{code}/start", json={"questions": ids(alice, site, n)}).status_code == 200
    db.execute("UPDATE rooms SET started_at=started_at-5 WHERE site=? AND code=?", (site, code))
    db.commit()
    return code


def answer(client, code, index, correct, db, site):
    db.execute("UPDATE room_players SET last_answer=0 WHERE site=? AND code=?", (site, code))
    db.commit()
    return client.post(f"/api/rooms/{code}/answer", json={"index": index, "correct": correct})


def test_rooms_need_a_license(client, alice, site):
    game = game_of(site)
    assert client.post(f"/api/rooms/{game}/create", json={"seconds": 120}).status_code == 403
    assert alice.post(f"/api/rooms/{game}/create", json={"seconds": 10}).status_code == 400
    assert alice.post("/api/rooms/unknown/create", json={"seconds": 120}).status_code == 404
    code = alice.post(f"/api/rooms/{game}/create", json={"seconds": 120}).get_json()["code"]
    assert client.post(f"/api/rooms/{code}/join", json={}).status_code == 403
    assert client.get(f"/api/rooms/{code}").status_code == 403
    assert alice.get("/api/rooms/999999").status_code == 404
    page = alice.get("/games/" + game).get_data(as_text=True)
    assert '"room": {"create": "' in page and "/api/rooms/CODE/answer" in page
    assert '"room"' not in client.get("/games/" + game).get_data(as_text=True)


def test_lobby_join_leave_and_versions(alice, bob, site, db):
    game = game_of(site)
    st = alice.post(f"/api/rooms/{game}/create", json={"seconds": 120}).get_json()
    assert st["status"] == "lobby" and [p["id"] for p in st["players"]] == [1] and st["link"].endswith("?room=" + st["code"])
    code, v = st["code"], st["version"]
    assert alice.get(f"/api/rooms/{code}?v={v}").get_json()["same"]
    joined = bob.post(f"/api/rooms/{code}/join", json={}).get_json()
    assert [p["id"] for p in joined["players"]] == [1, 2] and joined["joined"]
    assert "same" not in alice.get(f"/api/rooms/{code}?v={v}").get_json(), "a join bumps the version"
    assert bob.post(f"/api/rooms/{code}/start", json={"questions": ids(bob, site)}).status_code == 403
    assert bob.post(f"/api/rooms/{code}/leave", json={}).status_code == 200
    assert not bob.get(f"/api/rooms/{code}").get_json()["joined"]
    assert alice.post(f"/api/rooms/{code}/start", json={"questions": ids(alice, site)}).status_code == 400, "needs 2"
    db.executemany(
        "INSERT INTO room_players(site,code,license_id,joined_at,last_seen) VALUES(?,?,?,?,?)",
        [(site, code, 100 + i, 0, int(time.time())) for i in range(9)],
    )
    db.commit()
    assert bob.post(f"/api/rooms/{code}/join", json={}).status_code == 409, "full at 10"
    db.execute("UPDATE room_players SET last_seen=0 WHERE site=? AND license_id>=100", (site,))
    db.commit()
    assert len(alice.get(f"/api/rooms/{code}").get_json()["players"]) == 1, "players gone for 45 s drop out"
    # A new room closes the host's old waiting room.
    code2 = alice.post(f"/api/rooms/{game}/create", json={"seconds": 60}).get_json()["code"]
    assert bob.post(f"/api/rooms/{code}/join", json={}).status_code == 409
    bob.post(f"/api/rooms/{code2}/join", json={})
    alice.post(f"/api/rooms/{code2}/leave", json={})
    assert bob.get(f"/api/rooms/{code2}").get_json()["status"] == "closed"


def test_start_checks_questions(alice, bob, site, db):
    code = open_room(alice, bob, site, db)
    for bad in ([], ["no-such-id"], ids(alice, site, 1) * 2, ids(alice, site, 21), "x"):
        assert alice.post(f"/api/rooms/{code}/start", json={"questions": bad}).status_code == 400, bad
    st = alice.post(f"/api/rooms/{code}/start", json={"questions": ids(alice, site)}).get_json()
    assert st["status"] == "running" and st["startsAt"] == st["now"] + 3 and st["deadline"] == st["startsAt"] + 120
    assert st["questions"] == ids(alice, site)
    assert SiteClient(site).get("/").status_code == 200
    assert alice.post(f"/api/rooms/{code}/start", json={"questions": ids(alice, site)}).status_code == 409
    assert answer(bob, code, 0, True, db, site).status_code == 409, "not before the countdown ends"


def test_answers_score_in_order_and_room_ends(alice, bob, site, db):
    code = started(alice, bob, site, db)
    assert answer(alice, code, 1, True, db, site).status_code == 409
    assert answer(alice, code, 0, True, db, site).get_json() == {"score": 10, "answered": 1}
    assert alice.post(f"/api/rooms/{code}/answer", json={"index": 1, "correct": True}).status_code == 429
    assert answer(alice, code, 0, True, db, site).status_code == 409, "an answer counts once"
    assert answer(alice, code, 1, True, db, site).get_json()["score"] == 25
    assert answer(alice, code, 2, False, db, site).get_json()["score"] == 20
    assert answer(alice, code, 3, True, db, site).status_code == 409, "only 3 questions"
    assert answer(bob, code, 0, False, db, site).get_json()["score"] == 0
    st = bob.get(f"/api/rooms/{code}").get_json()
    assert st["status"] == "running" and [(p["id"], p["rank"]) for p in st["players"]] == [(1, 1), (2, 2)]
    newcomer = SiteClient(site)
    newcomer.activate()  # alice's license on another browser: already a player, so it may come back
    assert newcomer.post(f"/api/rooms/{code}/join", json={}).status_code == 200
    answer(bob, code, 1, True, db, site)
    answer(bob, code, 2, True, db, site)
    st = bob.get(f"/api/rooms/{code}").get_json()
    assert st["status"] == "ended", "everybody finished, so the room ends early"
    assert [(p["id"], p["score"], p["bonus"]) for p in st["players"]] == [(2, 25, 20), (1, 20, 0)]
    rows = db.execute("SELECT license_id,points,mode FROM scores WHERE site=? ORDER BY license_id", (site,)).fetchall()
    assert [tuple(r) for r in rows] == [(1, 20, "online"), (2, 45, "online")]
    bob.get(f"/api/rooms/{code}")
    assert db.execute("SELECT count(*) FROM scores WHERE site=?", (site,)).fetchone()[0] == 2, "scored once"
    assert answer(bob, code, 3, True, db, site).status_code == 409


def test_deadline_ends_the_room_and_ties_share_the_bonus(alice, bob, site, db):
    code = started(alice, bob, site, db)
    answer(alice, code, 0, True, db, site)
    answer(bob, code, 0, True, db, site)
    db.execute("UPDATE rooms SET deadline=? WHERE site=? AND code=?", (int(time.time()) - 10, site, code))
    db.commit()
    assert answer(alice, code, 1, True, db, site).status_code == 409, "too late"
    st = alice.get(f"/api/rooms/{code}").get_json()
    assert st["status"] == "ended" and [p["bonus"] for p in st["players"]] == [20, 20]
    assert [p["rank"] for p in st["players"]] == [1, 1]


def test_class_lobby_lists_classmates_rooms(alice, bob, site, db):
    code = alice.post("/api/class/create", json={"name": "班"}).get_json()["klass"]["code"]
    bob.post("/api/class/join", json={"code": code})
    room = alice.post(f"/api/rooms/{game_of(site)}/create", json={"seconds": 120}).get_json()["code"]
    listed = bob.get("/api/class/rooms").get_json()["rooms"]
    assert [r["code"] for r in listed] == [room] and listed[0]["players"] == 1 and listed[0]["host"]["id"] == 1
    assert alice.get("/api/class/rooms").get_json()["rooms"] == [], "your own room is not listed"
    other = [g for g in SITES[site].games if g != game_of(site)][0]
    assert bob.get(f"/api/class/rooms?game={other}").get_json()["rooms"] == []
    bob.post(f"/api/rooms/{room}/join", json={})
    alice.post(f"/api/rooms/{room}/start", json={"questions": ids(alice, site)})
    assert bob.get("/api/class/rooms").get_json()["rooms"] == [], "started rooms leave the list"
