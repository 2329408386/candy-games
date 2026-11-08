"""我的设备 on the game sites: a user sees (read only) the devices (browsers) their license is activated on.

Users cannot remove devices themselves; only the admin unbinds. The teacher workbench shows no devices at all.
"""

import time
from flask import session
from .app import db, digest, site


def device_name(ua):
    """A short, friendly name for a device from its User-Agent, e.g. 「iPhone · 微信」."""
    ua = ua or ""
    if "iPad" in ua or ("Macintosh" in ua and "Mobile" in ua):
        kind = "iPad"
    elif "iPhone" in ua:
        kind = "iPhone"
    elif "HarmonyOS" in ua or "OpenHarmony" in ua:
        kind = "鸿蒙设备"
    elif "Android" in ua:
        kind = "安卓平板" if "Mobile" not in ua else "安卓手机"
    elif "Windows" in ua:
        kind = "Windows 电脑"
    elif "Macintosh" in ua or "Mac OS X" in ua:
        kind = "Mac 电脑"
    elif "CrOS" in ua:
        kind = "Chromebook"
    elif "Linux" in ua:
        kind = "Linux 电脑"
    else:
        kind = "未知设备"
    browsers = (
        ("MicroMessenger", "微信"), ("wxwork", "企业微信"), ("DingTalk", "钉钉"), ("QQ/", "QQ"), ("MQQBrowser", "QQ 浏览器"),
        ("UCBrowser", "UC 浏览器"), ("Quark", "夸克"), ("HuaweiBrowser", "华为浏览器"), ("HeyTapBrowser", "OPPO 浏览器"),
        ("MiuiBrowser", "小米浏览器"), ("VivoBrowser", "vivo 浏览器"), ("baiduboxapp", "百度"), ("Edg", "Edge"),
        ("SE 2.X", "搜狗浏览器"), ("360", "360 浏览器"), ("Firefox", "Firefox"), ("Chrome", "Chrome"), ("CriOS", "Chrome"),
        ("Safari", "Safari"),
    )
    browser = next((name for key, name in browsers if key in ua), "")
    return f"{kind} · {browser}" if browser else kind


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


def my_devices(lid):
    """This license's devices, oldest first: [{n, name, created_at, last_seen, me, token_hash}]; n is what the page sends back."""
    mine = digest(session.get("device") or "")
    rows = db().execute(
        "SELECT token_hash,label,created_at,last_seen FROM devices WHERE site=? AND license_id=? ORDER BY created_at,token_hash",
        (site().key, lid),
    ).fetchall()
    return [
        {"n": i, "name": device_name(r["label"]), "created_at": r["created_at"], "last_seen": r["last_seen"],
         "me": r["token_hash"] == mine, "token_hash": r["token_hash"]}
        for i, r in enumerate(rows, 1)
    ]
