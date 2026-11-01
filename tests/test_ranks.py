import sqlite3, tempfile
from pathlib import Path
import pytest
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
    assert ranks.collect(8, 5, 5) == (9, 0), "the last bronze star costs 10"
    assert ranks.collect(8, 0, 45) == (11, 5), "then silver stars cost 15 each"
    assert ranks.replay(144) == (12, 9)
    assert ranks.replay(4765) == (100, 0), "王者 after 4765 points"


def test_pk_outcomes():
    def results(scores):
        return [ranks.outcome(s, scores[:i] + scores[i + 1 :], True) for i, s in enumerate(scores)]

    assert results([3, 2]) == ["win", "lose"]
    assert results([3, 3]) == ["draw", "draw"]
    assert results([5, 4, 3, 2, 1]) == ["mvp", "win", "draw", "lose", "lose"]
    assert results([6, 5, 4, 3, 2, 1]) == ["mvp", "win", "win", "lose", "lose", "lose"]
    assert results([5, 5, 1]) == ["win", "win", "lose"], "a shared 1st place is not MVP"
    assert ranks.outcome(0, [0], False) == "lose", "not answering loses"


def test_losing_is_protected_in_bronze_silver_and_at_a_tier_floor():
    assert ranks.lose_star(5) == (5, True)
    assert ranks.lose_star(17) == (17, True)
    assert ranks.lose_star(18) == (18, True), "never drops out of 黄金"
    assert ranks.lose_star(19) == (18, False)
    assert ranks.lose_star(120) == (119, False)


def test_migration_divides_points_once_and_sets_tiers():
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
        assert d.execute("SELECT rank_stars,rank_progress FROM profiles").fetchone() == ranks.replay(149)
        d.close()


def test_pk_gives_and_takes_stars(alice, bob, site, db):
    db.execute("INSERT INTO profiles(site,license_id,rank_stars) VALUES(?,1,20),(?,2,20)", (site, site))
    db.commit()
    code = started(alice, bob, site, db, n=2)
    for client, marks in ((alice, [True, True]), (bob, [True, False])):
        for i, ok in enumerate(marks):
            answer(client, code, i, ok, db, site)
    st = alice.get(f"/api/rooms/{code}").get_json()
    assert [(p["id"], p["outcome"], p["points"]) for p in st["players"]] == [(1, "win", 13), (2, "lose", 6)]
    # alice: +1 for the win, 13 points buy no 25-point 黄金 star; bob: -1 for the loss.
    stars = dict(db.execute("SELECT license_id,rank_stars FROM profiles WHERE site=?", (site,)).fetchall())
    assert stars == {1: 21, 2: 19}
    assert st["players"][0]["tier"]["name"] == "黄金 IV"
    rows = alice.get("/api/ranking?period=all").get_json()["rows"]
    assert {r["id"]: r["tier"]["name"] for r in rows} == {1: "黄金 IV", 2: "黄金 IV"}
    assert alice.get("/api/me").get_json()["rank"]["total"] == 21


def test_bronze_loss_is_protected(alice, bob, site, db):
    code = started(alice, bob, site, db, n=1)
    answer(alice, code, 0, True, db, site)
    answer(bob, code, 0, False, db, site)
    st = alice.get(f"/api/rooms/{code}").get_json()
    assert [(p["id"], p["outcome"]) for p in st["players"]] == [(1, "win"), (2, "protect")]
    stars = dict(db.execute("SELECT license_id,rank_stars FROM profiles WHERE site=?", (site,)).fetchall())
    assert stars == {1: 2, 2: 0}, "alice: +1 win, +1 from 11 points (1 + 10 bonus)"


def test_solo_promotion_is_reported(alice, site, db):
    db.execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES(?,1)", (site,))
    db.execute("UPDATE profiles SET rank_stars=8,rank_progress=9 WHERE site=? AND license_id=1", (site,))
    db.commit()
    r = finish(alice, game_of(site), db, site, [True])[1].get_json()
    assert r["stars"] == 1 and r["promoted"] == "白银 III"
