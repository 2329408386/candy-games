import re
import pytest
from conftest import SiteClient, KEYS, FLASK, PREFIX, other_site, bank_size
from sites import SITES


@pytest.fixture(autouse=True)
def reset_state(db):
    db.execute("DELETE FROM devices")
    db.execute("DELETE FROM limits")
    db.execute("DELETE FROM trials")
    db.execute("UPDATE licenses SET enabled=1,device_limit=3 WHERE archived_at IS NULL")
    db.execute("DELETE FROM license_groups WHERE license_id IN (1,2)")
    db.executemany(
        "INSERT INTO license_groups VALUES(?,?,?)", [(s, lid, "g3-t1-a") for s in SITES for lid in (1, 2)]
    )
    db.execute("UPDATE game_memberships SET group_id='g3-t1-a'")
    db.commit()


def licensed(client, game):
    """True when the game page is the licensed one (session guard), not the free trial or the locked page."""
    r = client.get("/games/" + game)
    return r.status_code == 200 and "/api/session?game=" in r.get_data(as_text=True)


def test_home_lists_all_games_and_prefixed_urls(client, site):
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    for name in SITES[site].games.values():
        assert name in html
    if site != "chinese":
        prefix = PREFIX[site]
        assert not re.search(r'(?:href|action)="/(?:admin|api|games|static)', html)
        assert f"{prefix}/static/site.css?v=" in html and f"{prefix}/api/csrf" in html
    assert client.get("/health").get_json() == {"status": "ok"}


def test_session_cookie_names_are_unchanged(client, site):
    cookies = client.get("/api/csrf").headers.getlist("Set-Cookie")
    name = {"chinese": "session", "math": "math_session", "english": "english_session"}[site]
    assert any(c.startswith(name + "=") and "Path=/;" in c for c in cookies), cookies


def test_activation_unlocks_games_and_guard_uses_site_urls(client, site):
    game = next(iter(SITES[site].games))
    assert not licensed(client, game)
    assert client.post("/api/activate", json={"key": "WRONGKEY"}).status_code == 403
    r = client.activate(KEYS[site].lower()[:4] + "-" + KEYS[site][4:])
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body["games"] == list(SITES[site].games) and body["group_scopes"] == [
        {"id": "g3-t1-a", "grade": "g3-t1", "name": "A组"}
    ]
    page = client.get("/games/" + game)
    assert page.status_code == 200
    prefix = PREFIX[site]
    assert f"fetch('{prefix}/api/session?game={game}'" in page.get_data(as_text=True)
    assert f"location.replace('{prefix}/')" in page.get_data(as_text=True)
    status = client.get("/api/session").get_json()
    assert status["device_count"] == 1 and status["device_limit"] == 3
    assert client.get("/api/session?game=unknown").status_code == 403


def test_sites_are_isolated(site):
    other = other_site(site)
    c = SiteClient(site)
    assert c.activate(KEYS[other]).status_code == 403, "a code only works on its own site"
    assert c.activate().status_code == 200
    o = SiteClient(other)
    o.c = c.c  # same browser
    assert o.get("/api/session").status_code == 403, "activation must not leak to the other site"


def test_device_limit(client, site):
    assert client.activate().status_code == 200
    for _ in range(2):
        assert SiteClient(site).activate().status_code == 200
    r = SiteClient(site).activate()
    assert r.status_code == 403 and "设备" in r.get_json()["error"]
    assert client.activate().status_code == 200, "already-bound browser may re-activate"


def test_csrf_and_rate_limit(client, site):
    assert client.c.post(client.prefix + "/api/activate", json={"key": "X"}).status_code == 400
    for _ in range(30):
        client.post("/api/activate", json={"key": "BAD"})
    assert client.post("/api/activate", json={"key": "BAD"}).status_code == 429


def test_legacy_html_routes_only_on_chinese(client, site):
    if site == "chinese":
        assert client.get("/游戏入口.html").headers["Location"] == "/"
        assert "window.PK_PAGE" in client.get("/标点pk.html").get_data(as_text=True)
    else:
        assert client.get("/mental-math.html" if site == "math" else "/scene.html").status_code == 404


def test_admin_pages(admin, site):
    prefix = PREFIX[site]
    for path in [
        "/admin",
        "/admin?archived=1",
        "/admin?q=1",
        "/admin?grade=g3-t1",
        "/admin?group=g3-t1-a",
        "/admin?state=disabled",
        "/admin/games",
        "/admin/groups",
        "/admin/licenses/new",
        "/admin/licenses/1",
        "/admin/password",
    ]:
        r = admin.get(path)
        assert r.status_code == 200, (path, r.status_code)
        if site != "chinese":
            assert not re.search(r'(?:href|action)="/(?:admin|api|games|static)', r.get_data(as_text=True)), path
    assert "<title>游戏分组管理 · " + SITES[site].title + "</title>" in admin.get("/admin/groups").get_data(
        as_text=True
    )
    assert admin.get("/admin?group=unknown").status_code == 400
    assert admin.get("/admin/licenses/99").status_code == 404
    assert SiteClient(site).get("/admin").headers["Location"] == prefix + "/admin/login"


def test_group_permissions_follow_moves(admin, site, db):
    user = SiteClient(site)
    assert user.activate().status_code == 200
    games = list(SITES[site].games)
    moved = games[-1]
    assert (
        admin.post(
            "/admin/groups", {"action": "create", "grade": "g3-t1", "name": "B组" + site, "position": "1"}
        ).status_code
        == 302
    )
    bid = db.execute("SELECT id FROM game_groups WHERE site=? AND name=?", (site, "B组" + site)).fetchone()[0]
    admin.post("/admin/groups", {"action": "move", "grade": "g3-t1", "game": moved, "group": bid})
    assert not licensed(user, moved) and licensed(user, games[0])
    admin.post("/admin/licenses/1/groups", {"groups": ["g3-t1-a", bid]})
    assert user.get("/api/session").get_json()["games"] == games
    admin.post("/admin/licenses/1/groups", {"groups": [bid]})
    assert not licensed(user, games[0]) and licensed(user, moved)
    admin.post("/admin/groups", {"action": "move", "grade": "g3-t1", "game": moved, "group": "g4-t1-a"})
    assert (
        db.execute("SELECT group_id FROM game_memberships WHERE site=? AND game=?", (site, moved)).fetchone()[0] == bid
    ), "cannot move across terms"
    admin.post(
        "/admin/groups", {"action": "rename", "grade": "g3-t1", "group": bid, "name": "拓展组" + site, "position": "2"}
    )
    assert user.get("/api/session").get_json()["group_scopes"][0]["name"] == "拓展组" + site
    admin.post("/admin/groups", {"action": "delete", "grade": "g3-t1", "group": bid})
    assert db.execute(
        "SELECT 1 FROM game_groups WHERE site=? AND id=?", (site, bid)
    ).fetchone(), "referenced group is kept"


def test_generate_codes_is_idempotent_and_numbered_per_site(admin, site, db):
    prefix = PREFIX[site]
    before = db.execute("SELECT max(id) FROM licenses WHERE site=?", (site,)).fetchone()[0]
    nonce = re.search(r'name="nonce" value="([^"]+)"', admin.get("/admin/licenses/new").get_data(as_text=True)).group(1)
    form = {"nonce": nonce, "groups": ["g3-t1-a"], "quantity": "2", "device_limit": "2"}
    r = admin.post("/admin/licenses/new", form)
    assert r.status_code == 302 and r.headers["Location"].startswith(prefix + "/admin?batch=")
    rows = db.execute(
        "SELECT id,device_limit FROM licenses WHERE site=? AND id>? ORDER BY id", (site, before)
    ).fetchall()
    assert [tuple(r) for r in rows] == [(before + 1, 2), (before + 2, 2)]
    admin.post("/admin/licenses/new", form)
    assert (
        db.execute("SELECT max(id) FROM licenses WHERE site=?", (site,)).fetchone()[0] == before + 2
    ), "nonce reuse must not regenerate"
    code = db.execute("SELECT code FROM license_keys WHERE site=? AND license_id=?", (site, before + 1)).fetchone()[0]
    assert SiteClient(site).activate(code).status_code == 200


def test_license_edits_export_and_archive_guard(admin, site):
    user = SiteClient(site)
    assert user.activate().status_code == 200
    admin.post("/admin/licenses/1/assign", {"buyer": "=1+1"})
    csv = admin.get("/admin/export").get_data(as_text=True)
    assert "'=1+1" in csv and KEYS[site] in csv and "三年级上册／A组" in csv
    assert admin.post("/admin/licenses/3/enable", {}).status_code == 403
    admin.post("/admin/licenses/1/limit", {"device_limit": "0"})
    admin.post("/admin/licenses/1/disable", {})
    assert user.get("/api/session").status_code == 403
    admin.post("/admin/licenses/1/enable", {})
    assert user.get("/api/session").status_code == 200
    assert admin.post("/admin/licenses/1/bogus", {}).status_code == 404


def test_admin_password_change_and_logout_keep_device(site, db):
    from werkzeug.security import generate_password_hash
    from conftest import PASSWORD

    a = SiteClient(site)
    assert a.activate().status_code == 200
    assert a.login().status_code == 302
    db.execute("UPDATE admin SET must_change=1 WHERE site=?", (site,))
    db.commit()
    prefix = PREFIX[site]
    assert a.get("/admin").headers["Location"] == prefix + "/admin/password"
    r = a.post("/admin/password", {"old": PASSWORD, "password": "new-password-456", "confirm": "new-password-456"})
    assert r.headers["Location"] == prefix + "/admin/login"
    assert a.get("/api/session").status_code == 200, "admin sign-in/out keeps the game device"
    db.execute("UPDATE admin SET password=?,must_change=0 WHERE site=?", (generate_password_hash(PASSWORD), site))
    db.commit()


def test_static_files(client, site):
    r = client.get("/static/site.css")
    assert r.status_code == 200 and "no-store" not in r.headers.get("Cache-Control", "")
    audio = [
        p.name for p in (SITES[site].static_root.parent.parent.parent / "shared-static" / "game-audio-v3").glob("*.mp3")
    ]
    assert client.get("/static/game-audio-v3/" + audio[0]).status_code == 200
    assert client.get("/").headers["Cache-Control"] == "no-store"


def test_question_bank_requires_license(client, site):
    game = next(iter(SITES[site].games))
    trial = client.get(f"/games/{game}/bank.json").get_json()
    total = bank_size(site, game)
    assert trial["total"] == total and len(trial["questions"]) <= 40, "an unlicensed browser only gets one round"
    assert client.get("/games/unknown/bank.json").status_code == 404
    client.activate(KEYS[site])
    r = client.get(f"/games/{game}/bank.json")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    assert len(r.get_json()["questions"]) == total
    assert client.get("/static/math-banks.js").status_code == 404
    assert client.get("/static/shared/pk/pk-core.js").status_code == 200


def test_mistake_book_page(client, site):
    html = client.get("/mistakes").get_data(as_text=True)
    assert f'"{site}-mistakes-v1"' in html and "错题本" in html
    assert "错题本" in client.get("/").get_data(as_text=True)


def test_grades_come_from_license_groups(admin, site, db):
    # licenses.grade_scopes still says g3-t1; only the group assignment changes.
    db.execute("UPDATE license_groups SET group_id='g4-t1-a' WHERE site=? AND license_id=1", (site,))
    db.commit()
    page = admin.get("/admin?grade=g4-t1").get_data(as_text=True)
    assert "<td>#1</td>" in page and "<td>#2</td>" not in page
    assert "<td>#1</td>" not in admin.get("/admin?grade=g3-t1").get_data(as_text=True)
    user = SiteClient(site)
    assert user.activate().get_json()["grade_scopes"] == ["g4-t1"]
    assert "四年级上册" in user.get("/").get_data(as_text=True)


def start_trial(client, game):
    return client.post(f"/api/trial/{game}/start", json={})


def test_free_trial_is_one_round_per_game(client, site):
    games = list(SITES[site].games)
    game = games[0]
    html = client.get("/").get_data(as_text=True)
    assert html.count("trial-link") == len(games) and "试玩已结束" not in html
    page = client.get("/games/" + game).get_data(as_text=True)
    assert '"trial": {"start": "' in page and "/api/session?game=" not in page, "trial pages have no session guard"
    assert client.c.post(client.prefix + f"/api/trial/{game}/start").status_code == 400, "CSRF is required"
    assert start_trial(client, game).status_code == 200
    r = start_trial(client, game)
    assert r.status_code == 403 and r.get_json()["code"] == "trial_used"
    denied = client.get("/games/" + game)
    assert denied.status_code == 403 and "wechat-qr" in denied.get_data(as_text=True)
    assert client.get(f"/games/{game}/bank.json").status_code == 403
    html = client.get("/").get_data(as_text=True)
    assert html.count("trial-link") == len(games) - 1 and "试玩已结束" in html
    assert start_trial(client, games[1]).status_code == 200, "each game has its own trial"
    assert start_trial(SiteClient(site), game).status_code == 200, "another browser has its own trial"
    assert start_trial(client, "unknown").status_code == 404


def test_trial_does_not_touch_licensed_play_or_the_other_site(client, site):
    game = next(iter(SITES[site].games))
    assert client.activate().status_code == 200
    assert start_trial(client, game).status_code == 200 and licensed(client, game)
    assert len(client.get(f"/games/{game}/bank.json").get_json()["questions"]) == bank_size(site, game)
    other = other_site(site)
    o = SiteClient(other)
    o.c = client.c  # same browser
    client.post("/api/logout", json={})
    assert start_trial(client, game).status_code == 200, "a licensed start does not use up the trial"
    assert start_trial(o, next(iter(SITES[other].games))).status_code == 200


def test_trial_bank_fills_a_two_player_round(client, site):
    from core.app import trial_bank

    with FLASK[site].test_request_context():
        for game in SITES[site].games:
            bank = trial_bank(game)
            draw, questions = bank["draw"], bank["questions"]
            assert bank["total"] == bank_size(site, game) and len({q["id"] for q in questions}) == len(questions)
            if draw.get("count"):
                assert len(questions) == 2 * draw["count"], game
            else:
                assert "alternatives" not in draw
                for key, take in draw["counts"].items():
                    found = [q for q in questions if "/".join(str(q[f]) for f in draw["by"]) == key]
                    assert len(found) == 2 * take, (game, key)


def test_english_pages_audio_and_units():
    client = SiteClient("english")
    assert client.activate().status_code == 200
    for game in SITES["english"].games:
        page = client.get("/games/" + game).get_data(as_text=True)
        assert "/pk/types/english.js?v=" in page and "/pk/types/english.css?v=" in page, game
        bank = client.get(f"/games/{game}/bank.json").get_json()
        if game != "letters":
            assert bank["ui"]["units"] == ["U1", "U2", "U3", "U4", "U5", "U6"], game
        audio = next((q["audio"] for q in bank["questions"] if "audio" in q), None)
        # Listening games have no same-screen 双人PK (both players would hear each other's recording).
        assert ('"noPk": true' in page) == (game in ("listen-word", "listen-judge")), game
        if audio:
            r = client.get("/static/audio/" + audio)
            assert r.status_code == 200 and r.mimetype == "audio/mpeg", game


def test_footer_links_other_subjects_not_admin(client, site):
    html = client.get("/").get_data(as_text=True)
    assert "管理员入口" not in html
    cross = re.search(r'<span class="footer-cross">(.*?)</span>\s*\n', html).group(1)
    assert re.findall(r'href="([^"]+)"', cross) == [s.url for key, s in SITES.items() if key != site]


def test_sign_ins_do_not_expire(admin, site, monkeypatch):
    import time as _time
    from email.utils import parsedate_to_datetime

    user = SiteClient(site)
    r = user.activate()
    expires = parsedate_to_datetime(re.search(r"Expires=([^;]+)", r.headers["Set-Cookie"]).group(1))
    assert expires.timestamp() - _time.time() > 5 * 365 * 86400
    later = _time.time() + 30 * 86400
    monkeypatch.setattr(_time, "time", lambda: later)
    assert admin.get("/admin").status_code == 200, "admin stays signed in"
    assert "data-copy=" in admin.get("/admin").get_data(as_text=True)
