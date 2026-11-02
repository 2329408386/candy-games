"""Workbench features and how long a teacher may use each (ws_entitlements).

A teacher license lists features with a number of days. The days start counting when the teacher first activates
the code (activate(), called by /api/activate): expires_at is the end of the last day, Beijing time. The admin can
extend a feature, set its expiry date or add a feature on the license page. Without a feature (or once it has
expired) a teacher still signs in and sees their classes and past work, but cannot start new work with it;
anyone may try each feature once (see core/essay/__init__.py, trial pages).

FEATURES lists every feature in display order; a new one is added here and gets its own pages.
"""

import time
from core.schema import BEIJING, end_of_day

DAY = 86400
FEATURES = {
    "essay": {
        "name": "作文批改",
        "desc": "学生拍照交作文，AI 用红笔批在原稿上，老师看过再发给学生。",
        "icon": "i-pen",
    },
}
DEFAULT_DAYS = 365
MAX_DAYS = 3650 * 2
WARN_DAYS = 30


def local_day(ts):
    return (int(ts) + BEIJING) // DAY


def rows(d, lid):
    return {r["feature"]: r for r in d.execute("SELECT * FROM ws_entitlements WHERE license_id=?", (lid,))}


def describe(row, now=None):
    """What the teacher sees for one feature: state (active / soon / expired / pending / none), days left, date."""
    now = int(now if now is not None else time.time())
    if not row:
        return {"state": "none", "days": None, "expires": None}
    if row["expires_at"] is None:
        return {"state": "pending", "days": row["days"], "expires": None}
    days = local_day(row["expires_at"]) - local_day(now)
    if row["expires_at"] < now:
        return {"state": "expired", "days": 0, "expires": row["expires_at"]}
    return {"state": "soon" if days < WARN_DAYS else "active", "days": days, "expires": row["expires_at"]}


def overview(d, lid, now=None):
    have = rows(d, lid)
    return [{"key": key, **meta, **describe(have.get(key), now)} for key, meta in FEATURES.items()]


def active(d, lid, feature, now=None):
    return describe(rows(d, lid).get(feature), now)["state"] in ("active", "soon")


def require(d, lid, feature):
    """Raises ValueError (shown to the user) unless the license may use the feature now."""
    info = describe(rows(d, lid).get(feature))
    name = FEATURES[feature]["name"]
    if info["state"] == "expired":
        raise ValueError(f"「{name}」已经到期，请联系管理员续期")
    if info["state"] not in ("active", "soon"):
        raise ValueError(f"还没有开通「{name}」，请联系管理员")


def activate(d, lid, now=None):
    """Starts the clock of every feature not started yet (the first activation of the code)."""
    now = int(now if now is not None else time.time())
    for r in d.execute("SELECT feature,days FROM ws_entitlements WHERE license_id=? AND starts_at IS NULL", (lid,)).fetchall():
        d.execute(
            "UPDATE ws_entitlements SET starts_at=?,expires_at=? WHERE license_id=? AND feature=?",
            (now, end_of_day(now + r["days"] * DAY), lid, r["feature"]),
        )


def grant(d, lid, feature, days, started, now=None):
    """Adds a feature (or more days to it). started: whether the license is already in use (clock running)."""
    now = int(now if now is not None else time.time())
    row = rows(d, lid).get(feature)
    if not row:
        d.execute(
            "INSERT INTO ws_entitlements(license_id,feature,days,starts_at,expires_at) VALUES(?,?,?,?,?)",
            (lid, feature, days, now if started else None, end_of_day(now + days * DAY) if started else None),
        )
    elif row["expires_at"] is None:
        d.execute("UPDATE ws_entitlements SET days=days+? WHERE license_id=? AND feature=?", (days, lid, feature))
    else:
        # Extending an expired feature counts from today; a running one from its current end.
        base = max(row["expires_at"], end_of_day(now) - DAY)
        d.execute(
            "UPDATE ws_entitlements SET days=days+?,expires_at=? WHERE license_id=? AND feature=?",
            (days, end_of_day(base + days * DAY), lid, feature),
        )


def set_expiry(d, lid, feature, expires_at, now=None):
    now = int(now if now is not None else time.time())
    row = rows(d, lid).get(feature)
    if not row:
        d.execute(
            "INSERT INTO ws_entitlements(license_id,feature,days,starts_at,expires_at) VALUES(?,?,?,?,?)",
            (lid, feature, max(1, local_day(expires_at) - local_day(now)), now, expires_at),
        )
    else:
        d.execute(
            "UPDATE ws_entitlements SET starts_at=coalesce(starts_at,?),expires_at=? WHERE license_id=? AND feature=?",
            (now, expires_at, lid, feature),
        )


def parse_generation(form):
    """{feature: days} chosen on the admin's 新增授权码 form (feature_<key> checkbox, days_<key>)."""
    chosen = {}
    for key, meta in FEATURES.items():
        if not form.get("feature_" + key):
            continue
        raw = (form.get("days_" + key) or "").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= MAX_DAYS:
            raise ValueError(f"「{meta['name']}」的有效天数要在 1—{MAX_DAYS} 之间")
        chosen[key] = int(raw)
    if not chosen:
        raise ValueError("请至少勾选一个功能")
    return chosen
