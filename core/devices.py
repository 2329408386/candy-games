"""Devices (browsers) a license is activated on: users see nothing about them and cannot remove them; the admin
sees them (with 最近使用, kept up to date by touch()) and unbinds.
"""

import time
from flask import session
from .app import db, digest, site


def touch(lid):
    """Keeps 最近使用 of this device up to date (at most one write an hour)."""
    d = db()
    if d.in_transaction or not session.get("device"):
        return
    now = int(time.time())
    d.execute(
        "UPDATE devices SET last_seen=? WHERE site=? AND license_id=? AND token_hash=? AND last_seen<?",
        (now, site().key, lid, digest(session["device"]), now - 3600),
    )
    d.commit()
