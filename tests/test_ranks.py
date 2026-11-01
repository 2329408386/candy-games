import sqlite3, tempfile
from pathlib import Path
from core import ranks, schema
from core.social import rank_points
from test_rooms import answer, game_of, started
from test_social import finish, second_key, reset_social, alice, bob, extra_users  # noqa: F401  (fixtures)
from test_rooms import reset_rooms  # noqa: F401


def test_rank_points_are_a_tenth_rounded_half_up():
    assert [rank_points(s) for s in (0, 4, 5, 14, 15, 230, 295)] == [0, 0, 1, 1, 2, 23, 30]


def test_tiers_and_star_prices():
    names = {s: ranks.info(s)["name"] for s in (0, 8, 9, 17, 18, 33, 34, 50, 75, 99, 100, 149, 150)}
    assert names == {
        0: "青铜 III", 8: "青铜 I", 9: "白银 III", 17: "白银 I", 18: "黄金 IV", 33: "黄金 I", 34: "铂金 IV",
        50: "钻石 V", 75: "星耀 V", 99: "星耀 I", 100: "王者", 149: "王者", 150: "荣耀王者",
    }
    assert ranks.info(33)["stars"] == 3 and ranks.info(33)["slots"] == 4
    assert ranks.info(160)["stars"] == 60 and ranks.info(160)["key"] == "glory"
    assert ranks.collect(8, 25, 5) == (9, 0), "the last bronze star costs 30"
    assert ranks.collect(8, 0, 135) == (11, 15), "then silver stars cost 45 each"
    assert ranks.replay(144) == (4, 24)
    assert ranks.replay(14295) == (100, 0), "王者 after 14295 points"


def test_migrations_divide_points_once_and_recount_tiers():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "db.sqlite3"
        schema.initialize(path)
        d = sqlite3.connect(path)
        d.execute("INSERT INTO licenses(site,id,key_hash,games,created_at) VALUES('math',1,'h','[]',1)")
        d.executemany(
            "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES('math',1,'g',?,?,1)",
            [("solo", 1435), ("online", 5), ("mistakes", 4)],
        )
        d.execute("DELETE FROM migrations")
        d.commit()
        schema.initialize(path)
        schema.initialize(path)
        assert [r[0] for r in d.execute("SELECT points FROM scores ORDER BY id")] == [144, 1, 4]
        assert d.execute("SELECT rank_stars,rank_progress FROM profiles").fetchone() == ranks.replay(149) == (4, 29)
        d.execute("UPDATE profiles SET rank_stars=40")  # e.g. stars from the old PK wins
        d.execute("DELETE FROM migrations WHERE name='ranks_x3'")
        d.commit()
        schema.initialize(path)
        assert d.execute("SELECT rank_stars,rank_progress FROM profiles").fetchone() == (4, 29)
        assert [r[0] for r in d.execute("SELECT points FROM scores ORDER BY id")] == [144, 1, 4], "÷10 stays done"
        d.close()


def test_pk_only_adds_points_towards_stars(alice, bob, site, db):
    db.execute("INSERT INTO profiles(site,license_id,rank_stars,rank_progress) VALUES(?,1,20,70),(?,2,20,0)", (site, site))
    db.commit()
    code = started(alice, bob, site, db, n=2)
    for client, marks in ((alice, [True, True]), (bob, [True, False])):
        for i, ok in enumerate(marks):
            answer(client, code, i, ok, db, site)
    st = alice.get(f"/api/rooms/{code}").get_json()
    assert [(p["id"], p["points"]) for p in st["players"]] == [(1, 13), (2, 1)]
    # alice: 70 + 13 points buy one 75-point 黄金 star; bob: 1 point, and losing takes nothing.
    rows = db.execute("SELECT license_id,rank_stars,rank_progress FROM profiles WHERE site=? ORDER BY license_id", (site,))
    assert [tuple(r) for r in rows] == [(1, 21, 8), (2, 20, 1)]
    assert st["players"][0]["tier"]["name"] == "黄金 IV"
    rows = alice.get("/api/ranking?period=all").get_json()["rows"]
    assert {r["id"]: r["tier"]["name"] for r in rows} == {1: "黄金 IV", 2: "黄金 IV"}
    assert alice.get("/api/me").get_json()["rank"]["total"] == 21


def test_solo_promotion_is_reported(alice, site, db):
    db.execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES(?,1)", (site,))
    db.execute("UPDATE profiles SET rank_stars=8,rank_progress=29 WHERE site=? AND license_id=1", (site,))
    db.commit()
    r = finish(alice, game_of(site), db, site, [True])[1].get_json()
    assert r["stars"] == 1 and r["promoted"] == "白银 III"
