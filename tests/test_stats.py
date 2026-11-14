"""统计: every round is counted (plays) and the statistics page shows it, per site, per game and per day."""

import re
import pytest
from conftest import SiteClient
from sites import SITES
from test_social import second_key, reset_social, alice, bob, extra_users  # noqa: F401  (fixtures)
from test_rooms import game_of, started


@pytest.fixture(autouse=True)
def reset_plays(db):
    for table in ("plays", "pk_rooms", "pk_seats"):
        db.execute("DELETE FROM " + table)
    db.commit()


def plays(db, site):
    return sorted(tuple(r) for r in db.execute("SELECT game,mode,license_id FROM plays WHERE site=?", (site,)))


def test_every_kind_of_round_is_counted(alice, bob, site, db):
    game = game_of(site)
    assert alice.post(f"/api/rounds/{game}/start", json={}).get_json()["round"]
    assert alice.post(f"/api/rounds/{game}/start", json={"mode": "mistakes"}).get_json()["round"]
    assert alice.post(f"/api/rounds/{game}/start", json={"mode": "pk"}).get_json() == {"round": None}  # no ticket
    assert db.execute("SELECT count(*) FROM rounds WHERE site=? AND mode='pk'", (site,)).fetchone()[0] == 0
    started(alice, bob, site, db)
    assert plays(db, site) == sorted([(game, "solo", 1), (game, "mistakes", 1), (game, "pk", 1), (game, "online", 1), (game, "online", 2)])
    assert SiteClient(site).post(f"/api/rounds/{game}/start", json={"mode": "pk"}).status_code == 403  # trial or none


def test_statistics_page(alice, bob, site, db):
    game = game_of(site)
    for mode in ("solo", "pk", "pk"):
        alice.post(f"/api/rounds/{game}/start", json={"mode": mode})
    bob.post(f"/api/rounds/{game}/start", json={})
    admin = SiteClient("chinese")
    assert admin.c.get("/admin/stats").status_code == 302  # signed out
    admin.login()
    page = admin.c.get("/admin/stats?range=30").get_data(as_text=True)
    assert "使用统计" in page and "近30天每天的局数" in page and "作文批改" in page
    row = re.search(r'<td class="st-name">' + SITES[site].games[game] + r"</td>(.*?)</tr>", page).group(1)
    numbers = [int(n) for n in re.findall(r'<td class="st-num">(?:<strong>)?(\d+)', row)]
    assert numbers == [2, 4, 2, 2, 0, 0, 0]  # users, total, 双人PK, 单人, 错题重练, 联网PK, 试玩
    for span in ("1", "7", "bogus"):
        assert admin.c.get("/admin/stats?range=" + span).status_code == 200
