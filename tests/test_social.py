import io, time
import pytest
from PIL import Image
from conftest import SiteClient, KEYS
from sites import SITES
from core.social import _top_cache, period_start, round_points


def second_key(site):
    return site.upper().ljust(12, "B") + "0002"


@pytest.fixture(autouse=True)
def reset_social(db):
    for table in ("devices", "limits", "trials", "profiles", "classes", "scores", "rounds"):
        db.execute("DELETE FROM " + table)
    _top_cache.clear()
    db.execute("UPDATE licenses SET enabled=1,device_limit=3 WHERE archived_at IS NULL")
    db.commit()


@pytest.fixture
def alice(site):
    c = SiteClient(site)
    assert c.activate().status_code == 200
    return c


@pytest.fixture
def bob(site):
    c = SiteClient(site)
    assert c.activate(second_key(site)).status_code == 200
    return c


@pytest.fixture
def extra_users(site, db):
    """make(n) → n more activated clients (license ids 10, 11, ...); removed again afterwards."""
    from core.app import digest

    made = []

    def make(n):
        out = []
        for _ in range(n):
            lid = 10 + len(made)
            key = site.upper().ljust(12, "X") + f"{lid:04d}"
            db.execute(
                "INSERT INTO licenses(site,id,key_hash,games,created_at,device_limit,batch_id,grade_scopes)"
                " VALUES(?,?,?,'[]',1,3,'historical-batch','[\"g3-t1\"]')",
                (site, lid, digest(key)),
            )
            db.execute("INSERT INTO license_keys VALUES(?,?,?)", (site, lid, key))
            db.execute("INSERT INTO license_groups VALUES(?,?,?)", (site, lid, "g3-t1-a"))
            db.commit()
            made.append(lid)
            c = SiteClient(site)
            assert c.activate(key).status_code == 200
            out.append(c)
        return out

    yield make
    for table in ("devices", "profiles", "scores"):
        db.execute(f"DELETE FROM {table} WHERE site=? AND license_id>=10", (site,))
    db.execute("DELETE FROM license_groups WHERE site=? AND license_id>=10", (site,))
    db.execute("DELETE FROM license_keys WHERE site=? AND license_id>=10", (site,))
    db.execute("DELETE FROM audit WHERE site=? AND license_id>=10", (site,))
    db.execute("DELETE FROM licenses WHERE site=? AND id>=10", (site,))
    db.commit()


def png(w=300, h=200, color=(200, 60, 60)):
    out = io.BytesIO()
    Image.new("RGB", (w, h), color).save(out, "PNG")
    return out.getvalue()


def upload(client, data, ctype="image/png"):
    return client.c.post(
        client.prefix + "/api/me/avatar", data=data, headers={"X-CSRF-Token": client.token(), "Content-Type": ctype}
    )


def test_scoring_helpers():
    assert round_points([True, True, True]) == 40
    assert round_points([False, True, True, False, True]) == 30
    assert round_points([False, False]) == 0
    monday = period_start("week", 1761926400)  # 2025-11-01 00:00 Beijing, a Saturday
    assert time.gmtime(monday + 8 * 3600)[6:7] == (0,) and time.gmtime(monday + 8 * 3600)[3:6] == (0, 0, 0)
    assert 1761926400 - monday == 5 * 86400
    assert period_start("day", 1761926400 + 3600) == 1761926400


def test_everything_needs_a_license(client, site):
    game = next(iter(SITES[site].games))
    assert client.get("/me").status_code == 302
    r = client.get("/api/me")
    assert r.status_code == 403 and "授权" in r.get_json()["error"]
    assert client.post("/api/class/create", json={"name": "x"}).status_code == 403
    assert client.get("/avatars/1.webp").status_code == 403
    assert client.post(f"/api/rounds/{game}/start", json={}).status_code == 403
    html = client.get("/").get_data(as_text=True)
    assert "header-avatar" not in html and "header-contact" in html, "unlicensed header is unchanged"
    assert '"score"' not in client.get("/games/" + game).get_data(as_text=True), "trial rounds never score"


def test_nickname_and_header_chip(alice, site):
    html = alice.get("/").get_data(as_text=True)
    assert "header-avatar" in html and "同学1" in html and "header-contact" not in html
    assert alice.get("/me").status_code == 200
    assert alice.post("/api/me/nickname", json={"nickname": "这个昵称实在是太长了"}).status_code == 400
    assert alice.post("/api/me/nickname", json={"nickname": "  \u0007 "}).status_code == 400
    r = alice.post("/api/me/nickname", json={"nickname": " 小 明\n"})
    assert r.status_code == 200 and r.get_json()["name"] == "小 明"
    assert "小 明" in alice.get("/").get_data(as_text=True)


def test_avatar_is_reencoded_small(alice, bob, admin, site, db):
    assert upload(alice, b"not an image at all").status_code == 400
    r = upload(alice, png())
    assert r.status_code == 200
    url = r.get_json()["avatar"]
    stored = db.execute("SELECT avatar FROM profiles WHERE site=? AND license_id=1", (site,)).fetchone()[0]
    img = Image.open(io.BytesIO(stored))
    assert img.format == "WEBP" and img.size == (96, 96) and len(stored) < 4000
    got = bob.c.get(url)
    assert got.status_code == 200 and got.mimetype == "image/webp" and "immutable" in got.headers["Cache-Control"]
    assert url.endswith("/avatars/1.webp?v=1")
    assert "/admin/licenses/1/avatar.webp?v=1" in admin.get("/admin/licenses/1").get_data(as_text=True)
    assert admin.get("/admin/licenses/1/avatar.webp?v=1").mimetype == "image/webp"
    assert admin.post("/admin/licenses/1/reset_avatar").status_code == 302
    assert alice.get("/api/me").get_json()["avatar"] is None
    assert bob.c.get(url).status_code == 404


def test_one_class_per_user_and_owner_rights(alice, bob, site):
    assert alice.post("/api/class/create", json={"name": ""}).status_code == 400
    k = alice.post("/api/class/create", json={"name": "三年级二班"}).get_json()["klass"]
    assert k["owner"] and k["members"] == 1 and len(k["code"]) == 6
    assert alice.post("/api/class/create", json={"name": "又一个班"}).status_code == 400
    assert alice.post("/api/class/leave", json={}).status_code == 400, "the owner dissolves instead of leaving"
    assert bob.post("/api/class/join", json={"code": "000000"}).status_code == 404
    joined = bob.post("/api/class/join", json={"code": k["code"]}).get_json()["klass"]
    assert joined["members"] == 2 and not joined["owner"]
    assert bob.post("/api/class/join", json={"code": k["code"]}).status_code == 400
    assert bob.post("/api/class/create", json={"name": "我的班"}).status_code == 400
    assert bob.post("/api/class/rename", json={"name": "改名"}).status_code == 403
    assert bob.post("/api/class/members/1/remove", json={}).status_code == 403
    bob.post("/api/me/nickname", json={"nickname": "坏名字"})
    assert alice.post("/api/class/members/2/nickname", json={}).status_code == 200
    assert bob.get("/api/me").get_json()["nickname"] == ""
    assert alice.post("/api/class/members/2/remove", json={}).status_code == 200
    assert bob.get("/api/me").get_json()["klass"] is None
    bob.post("/api/class/join", json={"code": k["code"]})
    assert bob.post("/api/class/leave", json={}).get_json()["klass"] is None
    bob.post("/api/class/join", json={"code": k["code"]})
    assert alice.post("/api/class/rename", json={"name": "三年级二班（新）"}).get_json()["klass"]["name"] == "三年级二班（新）"
    assert alice.post("/api/class/dissolve", json={}).get_json()["klass"] is None
    assert bob.get("/api/me").get_json()["klass"] is None
    other = SiteClient("math" if site == "chinese" else "chinese")
    other.activate()
    assert other.post("/api/class/join", json={"code": k["code"]}).status_code == 404, "sites do not share classes"


def finish(client, game, db, site, answers, ago=600, mode=None):
    ticket = client.post(f"/api/rounds/{game}/start", json={"mode": mode} if mode else {}).get_json()["round"]
    db.execute("UPDATE rounds SET started_at=started_at-? WHERE site=? AND id=?", (ago, site, ticket))
    db.commit()
    return ticket, client.post(f"/api/rounds/{game}/finish", json={"round": ticket, "answers": answers})


def test_solo_rounds_score_once_and_rank(alice, bob, site, db):
    game = next(iter(SITES[site].games))
    page = alice.get("/games/" + game).get_data(as_text=True)
    assert f"/api/rounds/{game}/finish" in page
    ticket, r = finish(alice, game, db, site, [True, True, False, True])
    assert r.get_json() == {"recorded": True, "points": 30, "in_class": False, "mode": "solo"}
    again = alice.post(f"/api/rounds/{game}/finish", json={"round": ticket, "answers": [True] * 20})
    assert again.status_code == 409, "a ticket counts once"
    assert finish(alice, game, db, site, [True] * 21)[1].status_code == 400
    assert finish(alice, game, db, site, ["yes"])[1].status_code == 400
    fast = finish(alice, game, db, site, [True] * 20, ago=5)[1].get_json()
    assert not fast["recorded"] and "异常" in fast["reason"]
    assert not finish(alice, game, db, site, [])[1].get_json()["recorded"]
    t = bob.post(f"/api/rounds/{game}/start", json={}).get_json()["round"]
    assert alice.post(f"/api/rounds/{game}/finish", json={"round": t, "answers": [True]}).status_code == 409

    code = alice.post("/api/class/create", json={"name": "班"}).get_json()["klass"]["code"]
    assert bob.get("/api/class/ranking").status_code == 404
    bob.post("/api/class/join", json={"code": code})
    assert finish(bob, game, db, site, [True, True])[1].get_json()["in_class"]
    old = period_start("week") - 3600
    db.execute("INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,2,?,'solo',100,?)", (site, game, old))
    db.commit()
    week = alice.get("/api/class/ranking?period=week").get_json()
    assert [(r["id"], r["rank"], r["points"]) for r in week["rows"]] == [(1, 1, 30), (2, 2, 25)]
    total = alice.get("/api/class/ranking").get_json()
    assert [(r["id"], r["points"], r["rounds"]) for r in total["rows"]] == [(2, 125, 2), (1, 30, 1)]
    db.execute("UPDATE licenses SET enabled=0 WHERE site=? AND id=2", (site,))
    db.commit()
    assert [r["id"] for r in alice.get("/api/class/ranking").get_json()["rows"]] == [1], "disabled users drop out"


def test_solo_rounds_per_day_cap(alice, site, db):
    game = next(iter(SITES[site].games))
    now = int(time.time())
    db.executemany(
        "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,1,?,'solo',10,?)",
        [(site, game, now)] * 30,
    )
    db.commit()
    r = finish(alice, game, db, site, [True])[1].get_json()
    assert not r["recorded"] and "明天" in r["reason"]


def test_admin_can_dissolve_and_reset(alice, bob, admin, site):
    code = alice.post("/api/class/create", json={"name": "班"}).get_json()["klass"]["code"]
    bob.post("/api/class/join", json={"code": code})
    alice.post("/api/me/nickname", json={"nickname": "不合适"})
    page = admin.get("/admin/licenses/1").get_data(as_text=True)
    assert "不合适" in page and "解散班级" in page
    assert admin.post("/admin/licenses/1/reset_nickname").status_code == 302
    assert admin.post("/admin/licenses/1/leave_class").status_code == 302
    assert alice.get("/api/me").get_json()["nickname"] == ""
    assert bob.get("/api/me").get_json()["klass"] is None, "removing the owner dissolves the class"


def test_mistake_rounds_score_one_point_per_fixed_mistake(alice, site, db):
    game = next(iter(SITES[site].games))
    r = finish(alice, game, db, site, [True, False, True, True], mode="mistakes")[1].get_json()
    assert r == {"recorded": True, "points": 3, "in_class": False, "mode": "mistakes"}
    fast = finish(alice, game, db, site, [True] * 10, ago=3, mode="mistakes")[1].get_json()
    assert not fast["recorded"]
    rows = db.execute("SELECT mode,points FROM scores WHERE site=?", (site,)).fetchall()
    assert [tuple(r) for r in rows] == [("mistakes", 3)]
    now = int(time.time())
    db.executemany(
        "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,1,?,'solo',10,?)", [(site, game, now)] * 30
    )
    db.commit()
    assert finish(alice, game, db, site, [True], mode="mistakes")[1].get_json()["recorded"], "own daily cap"
    db.executemany(
        "INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,1,?,'mistakes',1,?)", [(site, game, now)] * 30
    )
    db.commit()
    capped = finish(alice, game, db, site, [True], mode="mistakes")[1].get_json()
    assert not capped["recorded"] and "错题重练" in capped["reason"]


def test_site_ranking_top_20(alice, bob, site, db, extra_users):
    game = next(iter(SITES[site].games))
    empty = alice.get("/api/ranking?period=week").get_json()
    assert empty["rows"] == [] and empty["mine"] is None
    others = extra_users(21)
    now, old = int(time.time()), period_start("week") - 3600
    rows = [(site, 10 + i, game, 100 + i, now) for i in range(21)]  # license 30 is best (120), license 10 is 21st
    rows += [(site, 2, game, 500, old), (site, 1, game, 50, now), (site, 2, game, 5, now)]
    db.executemany("INSERT INTO scores(site,license_id,game,mode,points,created_at) VALUES(?,?,?,'solo',?,?)", rows)
    db.commit()
    assert alice.get("/api/ranking?period=week").get_json()["rows"] == [], "cached for a minute"
    _top_cache.clear()
    week = alice.get("/api/ranking?period=week").get_json()
    assert len(week["rows"]) == 20 and week["rows"][0]["id"] == 30 and week["rows"][0]["rank"] == 1
    assert week["rows"][-1]["id"] == 11 and week["mine"] == {"rank": 22, "points": 50, "gap": 52}
    assert others[0].get("/api/ranking?period=week").get_json()["mine"] == {"rank": 21, "points": 100, "gap": 2}
    total = bob.get("/api/ranking").get_json()
    assert total["rows"][0]["id"] == 2 and total["rows"][0]["points"] == 505 and total["mine"]["rank"] == 1
    assert total["mine"]["gap"] == 0
    assert alice.get("/api/ranking").get_json()["period"] == "all"
    db.execute("UPDATE licenses SET enabled=0 WHERE site=? AND id=2", (site,))
    db.commit()
    _top_cache.clear()
    assert 2 not in [r["id"] for r in alice.get("/api/ranking").get_json()["rows"]], "disabled users drop out"


def test_ranking_page_and_header_links(client, alice, bob, site):
    assert client.get("/ranking").status_code == 302
    home = client.get("/").get_data(as_text=True)
    assert "header-rank" not in home, "no boards without a license"
    home = alice.get("/").get_data(as_text=True)
    assert "🏆" in home and "总榜" in home and "班级榜" not in home
    page = alice.get("/ranking").get_data(as_text=True)
    assert "/api/ranking" in page and "加入班级，看班级榜" in page
    assert '"board": "top"' in alice.get("/ranking?board=class").get_data(as_text=True), "no class yet: the top board"
    code = alice.post("/api/class/create", json={"name": "三年级二班"}).get_json()["klass"]["code"]
    home = alice.get("/").get_data(as_text=True)
    assert "班级榜" in home and "board=class" in home
    page = alice.get("/ranking?board=class").get_data(as_text=True)
    assert '"board": "class"' in page and "/api/class/ranking" in page and "三年级二班 · 班级榜" in page
    assert "班级榜" not in bob.get("/").get_data(as_text=True).split("</header>")[0]
    bob.post("/api/class/join", json={"code": code})
    assert "班级榜" in bob.get("/").get_data(as_text=True).split("</header>")[0]
    assert "查看班级榜" in alice.get("/me").get_data(as_text=True)
