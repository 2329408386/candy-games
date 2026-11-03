"""段位 (tiers): 青铜 → 白银 → 黄金 → 铂金 → 钻石 → 星耀 → 王者 (→ 荣耀王者 at 50 王者 stars).

A user's tier is one number, profiles.rank_stars: the stars collected so far. Each tier has sub-levels
(III/II/I or V…I) of a few stars each; filling a sub-level's stars moves you up a level.
Stars are bought with ranking points only: every point written to `scores` (solo, mistake re-practice, online PK
score + placing bonus) goes into profiles.rank_progress; each time it covers the price of one star at the current
tier (the last column of TIERS, higher tiers cost more), the price is taken off and a star is added. Stars are never taken away.
This module does not import the app, so the database migration in schema.py can use it.
"""

# (key, name, sub-levels, stars per sub-level, ranking points per star); the last tier has no sub-levels and no cap.
TIERS = [
    ("bronze", "青铜", 3, 3, 30),
    ("silver", "白银", 3, 3, 45),
    ("gold", "黄金", 4, 4, 150),
    ("platinum", "铂金", 4, 4, 360),
    ("diamond", "钻石", 5, 5, 720),
    ("star", "星耀", 5, 5, 1200),
    ("king", "王者", 0, 0, 1800),
]
GLORY_AT = 50  # 王者 stars that make 荣耀王者
ROMAN = ["", "I", "II", "III", "IV", "V"]


def _floors():
    out, total = [], 0
    for t in TIERS:
        out.append(total)
        total += t[2] * t[3]
    return out


FLOORS = _floors()  # first star count of each tier


def tier_index(stars):
    i = 0
    while i + 1 < len(TIERS) and stars >= FLOORS[i + 1]:
        i += 1
    return i


def star_cost(stars):
    return TIERS[tier_index(stars)][4]


def info(stars, progress=0):
    """Everything the pages show about a tier: name '黄金 II', filled stars of `slots`, points to the next star."""
    i = tier_index(stars)
    key, tier, subs, per, cost = TIERS[i]
    inside = stars - FLOORS[i]
    if subs:
        level = inside // per
        name, filled, slots = f"{tier} {ROMAN[subs - level]}", inside % per, per
    else:
        glory = inside >= GLORY_AT
        key, name, filled, slots = ("glory" if glory else key), ("荣耀王者" if glory else tier), inside, 0
    return {
        "key": key,
        "tier": tier,
        "name": name,
        "stars": filled,
        "slots": slots,
        "total": stars,
        "progress": progress,
        "cost": cost,
        "toNext": cost - progress,
    }


def badge(stars):
    """The small version for leaderboards and PK results."""
    i = info(stars)
    return {"key": i["key"], "name": i["name"], "stars": i["stars"], "slots": i["slots"]}


def collect(stars, progress, points):
    """Adds ranking points; returns the new (stars, progress)."""
    progress += max(0, points)
    while progress >= star_cost(stars):
        progress -= star_cost(stars)
        stars += 1
    return stars, progress


def replay(total):
    """Tier for someone who has earned `total` ranking points (used by the migrations)."""
    return collect(0, 0, total)


def _row(d, site, lid):
    d.execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES(?,?)", (site, lid))
    return d.execute("SELECT rank_stars,rank_progress FROM profiles WHERE site=? AND license_id=?", (site, lid)).fetchone()


def _save(d, site, lid, stars, progress):
    d.execute("UPDATE profiles SET rank_stars=?,rank_progress=? WHERE site=? AND license_id=?", (stars, progress, site, lid))


def add_points(d, site, lid, points):
    """Puts ranking points towards stars. Returns (before, after) info dicts."""
    stars, progress = _row(d, site, lid)
    new = collect(stars, progress, points)
    _save(d, site, lid, *new)
    return info(stars, progress), info(*new)
