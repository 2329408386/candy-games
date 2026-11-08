"""推荐码: both sides get their days once, a bad code uses nothing up, and each teacher is told once."""

import secrets, time
import pytest
from core.app import digest
from core.essay import referral
from conftest import SiteClient

DAY = 86400


def make_teacher(db, days=365, trial=False):
    """A new license nobody has activated; returns (id, key)."""
    lid = db.execute("SELECT coalesce(max(id),0)+1 FROM licenses WHERE site='teacher'").fetchone()[0]
    key = "REFERTEST" + secrets.token_hex(4).upper()
    db.execute(
        "INSERT INTO licenses(site,id,key_hash,games,buyer,created_at,device_limit) VALUES('teacher',?,?,'[]','老师',1,100)",
        (lid, digest(key)),
    )
    db.execute("INSERT INTO license_keys VALUES('teacher',?,?)", (lid, key))
    db.execute("INSERT INTO license_groups VALUES('teacher',?,'g1-t1-a')", (lid,))
    db.execute("INSERT INTO ws_entitlements(license_id,feature,days) VALUES(?,'essay',?)", (lid, days))
    if trial:
        db.execute("INSERT INTO ess_trials(license_id,device_hash,created_at) VALUES(?,?,1)", (lid, "h" + key))
    db.commit()
    return lid, key


@pytest.fixture(autouse=True)
def activations_allowed(db):
    db.execute("DELETE FROM limits WHERE bucket LIKE '%:activate:%'")
    db.commit()


def activate(key, code=None):
    c = SiteClient("teacher")
    body = {"key": key}
    if code is not None:
        body["referral"] = code
    return c, c.post("/api/activate", json=body)


def expires(db, lid):
    return db.execute("SELECT expires_at FROM ws_entitlements WHERE license_id=?", (lid,)).fetchone()[0]


def referrer(db):
    """An activated paying teacher who has opened their code: (id, client, code, expiry)."""
    lid, key = make_teacher(db)
    c, r = activate(key)
    assert r.status_code == 200
    page = c.get("/teacher/profile").get_data(as_text=True)
    code = db.execute("SELECT code FROM ws_referral_codes WHERE license_id=?", (lid,)).fetchone()[0]
    assert code in page and 'id="referral"' in page
    return lid, c, code, expires(db, lid)


def untouched(db, lid):
    row = db.execute("SELECT starts_at,expires_at FROM ws_entitlements WHERE license_id=?", (lid,)).fetchone()
    bound = db.execute("SELECT count(*) FROM devices WHERE site='teacher' AND license_id=?", (lid,)).fetchone()[0]
    return row["starts_at"] is None and row["expires_at"] is None and bound == 0


def test_form_offers_the_optional_code():
    page = SiteClient("teacher").get("/teacher/").get_data(as_text=True)
    assert 'name="referral"' in page and "推荐码（选填）" in page


def test_both_sides_get_their_days_once(db):
    rid, rc, code, before = referrer(db)
    lid, key = make_teacher(db, days=100)
    c, r = activate(key, code.lower()[:4] + "-" + code.lower()[4:])  # as typed: lower case, a dash
    assert r.status_code == 200, r.get_json()
    assert expires(db, rid) == before + 62 * DAY
    started = db.execute("SELECT starts_at FROM ws_entitlements WHERE license_id=?", (lid,)).fetchone()[0]
    assert (expires(db, lid) - started) // DAY in (130, 131)  # 100 + 31, up to the end of the Beijing day
    assert db.execute("SELECT referrer_id FROM ws_referrals WHERE referee_id=?", (lid,)).fetchone()[0] == rid
    # Logging in again with the code is refused and adds nothing; without it the teacher signs in as usual.
    after = expires(db, lid), expires(db, rid)
    _, again = activate(key, code)
    assert again.status_code == 400 and "第一次激活" in again.get_json()["error"]
    assert activate(key)[1].status_code == 200
    assert (expires(db, lid), expires(db, rid)) == after
    assert db.execute("SELECT count(*) FROM ws_notices WHERE license_id IN (?,?)", (lid, rid)).fetchone()[0] == 2


def test_no_cap_on_recommending(db):
    rid, _, code, before = referrer(db)
    for _ in range(3):
        assert activate(make_teacher(db)[1], code)[1].status_code == 200
    assert expires(db, rid) == before + 3 * 62 * DAY


@pytest.mark.parametrize("bad", ["NOSUCHCODE", 12345])
def test_bad_code_uses_nothing_up(db, bad):
    lid, key = make_teacher(db)
    _, r = activate(key, bad)
    assert r.status_code == 400
    assert untouched(db, lid)
    # Corrected, the same key works.
    assert activate(key)[1].status_code == 200


def test_blank_code_is_no_code(db):
    lid, key = make_teacher(db)
    assert activate(key, "  ")[1].status_code == 200
    assert not db.execute("SELECT 1 FROM ws_referrals WHERE referee_id=?", (lid,)).fetchone()


def test_disabled_referrer_refused(db):
    rid, _, code, before = referrer(db)
    db.execute("UPDATE licenses SET enabled=0 WHERE site='teacher' AND id=?", (rid,))
    db.commit()
    lid, key = make_teacher(db)
    assert activate(key, code)[1].status_code == 400
    assert untouched(db, lid) and expires(db, rid) == before


def test_own_code_and_second_referral_refused(db):
    rid, _, code, before = referrer(db)
    db.execute("UPDATE ws_entitlements SET starts_at=NULL,expires_at=NULL WHERE license_id=?", (rid,))
    db.commit()
    with_key = db.execute("SELECT code FROM license_keys WHERE site='teacher' AND license_id=?", (rid,)).fetchone()[0]
    r = activate(with_key, code)[1]
    assert r.status_code == 400 and "自己" in r.get_json()["error"]
    # One license is a referee only once, even if its clock is somehow reset.
    other, _, other_code, _ = referrer(db)
    lid, key = make_teacher(db)
    assert activate(key, code)[1].status_code == 200
    db.execute("UPDATE ws_entitlements SET starts_at=NULL,expires_at=NULL WHERE license_id=?", (lid,))
    db.commit()
    assert "用过" in activate(key, other_code)[1].get_json()["error"]


def test_trial_neither_gives_nor_takes(db):
    lid, key = make_teacher(db, days=7, trial=True)
    c, r = activate(key)
    assert r.status_code == 200
    page = c.get("/teacher/profile").get_data(as_text=True)
    assert 'id="referral"' not in page and "我的推荐码" not in page
    assert not db.execute("SELECT 1 FROM ws_referral_codes WHERE license_id=?", (lid,)).fetchone()
    # Even a trial row with a code cannot be used as a referrer.
    db.execute("INSERT INTO ws_referral_codes VALUES(?,'TRIALCDE',1)", (lid,))
    db.commit()
    other, key2 = make_teacher(db)
    assert activate(key2, "TRIALCDE")[1].status_code == 400
    assert untouched(db, other)
    db.execute("DELETE FROM ws_referral_codes WHERE license_id=?", (lid,))
    db.commit()


def test_expired_referrer_counts_from_today(db):
    rid, _, code, _ = referrer(db)
    db.execute("UPDATE ws_entitlements SET expires_at=? WHERE license_id=?", (int(time.time()) - 30 * DAY, rid))
    db.commit()
    assert activate(make_teacher(db)[1], code)[1].status_code == 200
    assert 60 * DAY < expires(db, rid) - time.time() < 62 * DAY


def test_trial_convert_with_code(db):
    rid, _, code, before = referrer(db)
    lid, key = make_teacher(db, days=7, trial=True)
    assert activate(key)[1].status_code == 200
    a = SiteClient("teacher")
    a.login()
    assert 'name="referral"' in a.get(f"/admin/licenses/{lid}").get_data(as_text=True)
    form = {"buyer": "订单 9", "days": "365", "students": "50", "monthly": "200"}
    # A wrong code converts nothing.
    a.post(f"/admin/licenses/{lid}/trial_convert", dict(form, referral="WRONGCDE"))
    assert db.execute("SELECT 1 FROM ess_trials WHERE license_id=?", (lid,)).fetchone()
    assert expires(db, rid) == before
    a.post(f"/admin/licenses/{lid}/trial_convert", dict(form, referral=code))
    assert not db.execute("SELECT 1 FROM ess_trials WHERE license_id=?", (lid,)).fetchone()
    assert expires(db, rid) == before + 62 * DAY
    assert 395 * DAY < expires(db, lid) - time.time() < 397 * DAY  # 365 + 31
    assert db.execute("SELECT count(*) FROM ws_notices WHERE license_id=?", (lid,)).fetchone()[0] == 1
    page = a.get(f"/admin/licenses/{lid}").get_data(as_text=True)
    assert f"授权 #{rid}" in page
    assert f"#{lid}" in a.get(f"/admin/licenses/{rid}").get_data(as_text=True)


def test_notice_shows_once_per_account(db):
    rid, rc, code, _ = referrer(db)
    lid, key = make_teacher(db)
    c, _ = activate(key, code)
    page = c.get("/teacher/").get_data(as_text=True)
    assert 'id="wb-notices"' in page and "多了 31 天" in page and "62 天" in rc.get("/teacher/").get_data(as_text=True)
    ids = [r["id"] for r in referral.pending_notices(db, lid)]
    # Someone else cannot clear it, a bad body changes nothing.
    assert rc.post("/api/teacher/notices/seen", json={"ids": ids}).status_code == 200
    assert c.post("/api/teacher/notices/seen", json={"ids": "x"}).status_code == 400
    assert 'id="wb-notices"' in c.get("/teacher/").get_data(as_text=True)
    assert c.post("/api/teacher/notices/seen", json={"ids": ids}).status_code == 200
    assert 'id="wb-notices"' not in c.get("/teacher/").get_data(as_text=True)
    # A second device of the same teacher is not told again.
    other, _ = activate(key)
    assert 'id="wb-notices"' not in other.get("/teacher/").get_data(as_text=True)
    assert 'id="wb-notices"' in rc.get("/teacher/").get_data(as_text=True)  # the referrer has not closed theirs
    assert SiteClient("teacher").post("/api/teacher/notices/seen", json={"ids": ids}).status_code == 403
