"""Teacher licenses made on the fly for workbench tests."""

import secrets
from core.app import digest
from conftest import SiteClient


def make_teacher(db, days=365, trial=False):
    """A new license nobody has activated; returns (id, key)."""
    lid = db.execute("SELECT coalesce(max(id),0)+1 FROM licenses WHERE site='teacher'").fetchone()[0]
    key = "TEACHTEST" + secrets.token_hex(4).upper()
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


def activate(key, **extra):
    c = SiteClient("teacher")
    return c, c.post("/api/activate", json={"key": key, **extra})
