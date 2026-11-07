"""推荐码: a paying teacher gives their code to another teacher, who fills it in when first activating a license.

The referee gets REFEREE_DAYS more and the referrer REFERRER_DAYS more of the feature, with no cap on how many teachers
one may recommend. A license can be a referee once, and only on its first activation, so two teachers already using
the workbench cannot fill in each other's codes. Free trials neither give nor take a code; the admin may enter one when
converting a trial to a paid license. The code is separate from the license key, so sharing it gives nothing away.

Each side hears about the extra days once: ws_notices is kept on the server, so another device does not repeat it.
"""

import secrets, time
from core.app import KEY_ALPHABET, audit
from core.schema import BEIJING
from . import features

REFERRER_DAYS = 62
REFEREE_DAYS = 31
FEATURE = "essay"
CODE_LENGTH = 8


def normalize(raw):
    return raw.strip().upper().replace("-", "").replace(" ", "")


def _is_trial(d, lid):
    return bool(d.execute("SELECT 1 FROM ess_trials WHERE license_id=?", (lid,)).fetchone())


def code_for(d, lid, now=None):
    """This teacher's code, made the first time it is asked for (the caller commits); None for a free trial."""
    if _is_trial(d, lid):
        return None
    while True:
        row = d.execute("SELECT code FROM ws_referral_codes WHERE license_id=?", (lid,)).fetchone()
        if row:
            return row["code"]
        code = "".join(secrets.choice(KEY_ALPHABET) for _ in range(CODE_LENGTH))
        d.execute(
            "INSERT OR IGNORE INTO ws_referral_codes(license_id,code,created_at) VALUES(?,?,?)",
            (lid, code, int(now if now is not None else time.time())),
        )


def first_activation(d, lid):
    """Whether the clock of this license has yet to start; ask before features.activate() starts it."""
    return bool(d.execute("SELECT 1 FROM ws_entitlements WHERE license_id=? AND starts_at IS NULL", (lid,)).fetchone())


def on_activation(d, lid, raw, first, now):
    """The optional 推荐码 of the activation form. Raises ValueError (shown to the teacher; the caller rolls back)."""
    if raw is None or raw == "":
        return
    if not isinstance(raw, str):
        raise ValueError("推荐码格式不正确")
    if not normalize(raw):
        return
    if not first:
        raise ValueError("这个授权码之前已经激活过，推荐码只能在第一次激活时填写；清空推荐码再登录就可以")
    apply(d, lid, raw, now)


def _until(d, lid):
    """'，现在可以用到 <date>', or nothing while the license's clock has not started (the days still count then)."""
    expires = features.rows(d, lid)[FEATURE]["expires_at"]
    return f"，现在可以用到 {time.strftime('%Y-%m-%d', time.gmtime(expires + BEIJING))}" if expires else ""


def apply(d, referee, raw, now):
    """Gives both sides their days and tells them once. The caller holds the write lock and commits."""
    row = d.execute(
        "SELECT r.license_id,l.enabled,l.archived_at FROM ws_referral_codes r"
        " JOIN licenses l ON l.site='teacher' AND l.id=r.license_id WHERE r.code=?",
        (normalize(raw),),
    ).fetchone()
    if not row or not row["enabled"] or row["archived_at"] is not None or _is_trial(d, row["license_id"]):
        raise ValueError("这个推荐码不对或已经失效，请核对一下；没有推荐码可以不填")
    referrer = row["license_id"]
    if referrer == referee:
        raise ValueError("不能填自己的推荐码")
    if d.execute("SELECT 1 FROM ws_referrals WHERE referee_id=?", (referee,)).fetchone():
        raise ValueError("这个授权码已经用过推荐码了")
    d.execute("INSERT INTO ws_referrals(referee_id,referrer_id,created_at) VALUES(?,?,?)", (referee, referrer, now))
    name = features.FEATURES[FEATURE]["name"]
    features.grant(d, referee, FEATURE, REFEREE_DAYS, started=True, now=now)
    features.grant(d, referrer, FEATURE, REFERRER_DAYS, started=True, now=now)
    _notify(d, referee, f"你填的推荐码已经生效，「{name}」多了 {REFEREE_DAYS} 天{_until(d, referee)}。", now)
    _notify(d, referrer, f"有一位老师用了你的推荐码，「{name}」多了 {REFERRER_DAYS} 天{_until(d, referrer)}。谢谢你的推荐！", now)
    audit("referral_used", referee, f"填了授权 #{referrer} 的推荐码，{name} 增加 {REFEREE_DAYS} 天")
    audit("referral_reward", referrer, f"推荐了授权 #{referee}，{name} 增加 {REFERRER_DAYS} 天")


def _notify(d, lid, body, now):
    d.execute("INSERT INTO ws_notices(license_id,body,created_at) VALUES(?,?,?)", (lid, body, now))


def pending_notices(d, lid):
    return [
        {"id": r["id"], "body": r["body"]}
        for r in d.execute("SELECT id,body FROM ws_notices WHERE license_id=? AND seen_at IS NULL ORDER BY id", (lid,))
    ]


def mark_seen(d, lid, ids, now):
    """Only the notices the teacher was shown: one that arrived since then still gets its turn."""
    if not isinstance(ids, list):
        raise ValueError("请求格式不正确")
    for notice in ids[:50]:
        if isinstance(notice, int):
            d.execute("UPDATE ws_notices SET seen_at=? WHERE id=? AND license_id=? AND seen_at IS NULL", (now, notice, lid))


def count_referred(d, lid):
    return d.execute("SELECT count(*) FROM ws_referrals WHERE referrer_id=?", (lid,)).fetchone()[0]


def admin_info(d, lid):
    """推荐码 section of the license page. It only reads: a code appears once the teacher has opened theirs."""
    code = d.execute("SELECT code FROM ws_referral_codes WHERE license_id=?", (lid,)).fetchone()
    by = d.execute("SELECT referrer_id,created_at FROM ws_referrals WHERE referee_id=?", (lid,)).fetchone()
    referred = d.execute("SELECT referee_id FROM ws_referrals WHERE referrer_id=? ORDER BY created_at DESC LIMIT 50", (lid,)).fetchall()
    return {
        "code": code["code"] if code else None,
        "referrer": by["referrer_id"] if by else None,
        "referred": [r["referee_id"] for r in referred],
        "count": count_referred(d, lid),
    }
