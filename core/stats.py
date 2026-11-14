"""统计: who uses what, per site, per game or feature, per day (Beijing time), for the one admin.

Game rounds come from `plays` (core/app.py log_play: 同屏双人, 单人, 错题重练, 联网PK), free trials from `trials`;
the workbench from its own tables (essays handed in, model cost, the teachers' devices). Rounds before plays existed
were copied from `scores`, which only kept rounds that counted for the rankings (migration plays_backfill).
"""

import time
from flask import render_template, request
from sites import ALL_SITES, SITES
from .app import admin_only, db
from .schema import BEIJING

RANGES = {"1": "今天", "7": "近7天", "30": "近30天"}
MODES = {"pk": "双人PK", "solo": "单人", "mistakes": "错题重练", "online": "联网PK"}
TREND_DAYS = 30
# Categorical slots 1-3 of the data-viz reference palette (validated all-pairs), one per game site.
SITE_COLORS = {"chinese": "#2a78d6", "math": "#eb6834", "english": "#1baf7a"}
ESSAY_COLOR = "#2a78d6"
HEAT = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95"]  # sequential blue, light → dark


def day_of(ts):
    return (int(ts) + BEIJING) // 86400


def day_label(day):
    return time.strftime("%m-%d", time.gmtime(day * 86400))


def heat(value, top):
    if not value:
        return ""
    return HEAT[min(len(HEAT) - 1, (len(HEAT) * value - 1) // max(top, 1))]


def game_stats(since, first):
    """Per site and game: users and rounds since `since`, by mode; trials; rounds per day from day `first`."""
    d = db()
    rows = {}
    for s, game, mode, n in d.execute(
        "SELECT site,game,mode,count(*) FROM plays WHERE created_at>=? GROUP BY site,game,mode", (since,)
    ):
        rows.setdefault((s, game), {"modes": {}, "users": 0})["modes"][mode] = n
    for s, game, users in d.execute(
        "SELECT site,game,count(DISTINCT license_id) FROM plays WHERE created_at>=? GROUP BY site,game", (since,)
    ):
        rows.setdefault((s, game), {"modes": {}, "users": 0})["users"] = users
    trials = {
        (s, game): n
        for s, game, n in d.execute("SELECT site,game,count(*) FROM trials WHERE started_at>=? GROUP BY site,game", (since,))
    }
    daily = {}
    for s, game, day, n in d.execute(
        "SELECT site,game,(created_at+?)/86400 day,count(*) FROM plays WHERE created_at>=? GROUP BY site,game,day",
        (BEIJING, first * 86400 - BEIJING),
    ):
        daily[(s, game, day)] = n
    return rows, trials, daily


def site_summary(key, since):
    d = db()
    users = d.execute(
        "SELECT count(*) FROM licenses l WHERE l.site=? AND l.archived_at IS NULL"
        " AND EXISTS(SELECT 1 FROM devices v WHERE v.site=l.site AND v.license_id=l.id)",
        (key,),
    ).fetchone()[0]
    active, rounds = d.execute(
        "SELECT count(DISTINCT license_id),count(*) FROM plays WHERE site=? AND created_at>=?", (key, since)
    ).fetchone()
    return {"users": users, "active": active, "rounds": rounds}


def workbench_stats(since, first):
    d = db()
    trial_ids = "SELECT license_id FROM ess_trials"
    teachers = d.execute(
        "SELECT count(*) FROM licenses l WHERE l.site='teacher' AND l.archived_at IS NULL AND l.id NOT IN (" + trial_ids + ")"
        " AND EXISTS(SELECT 1 FROM devices v WHERE v.site='teacher' AND v.license_id=l.id)"
    ).fetchone()[0]
    active = d.execute(
        "SELECT count(DISTINCT license_id) FROM devices WHERE site='teacher' AND last_seen>=? AND license_id NOT IN ("
        + trial_ids + ")",
        (since,),
    ).fetchone()[0]
    essays, students, writers = d.execute(
        "SELECT count(*),count(DISTINCT s.student_id),count(DISTINCT c.license_id) FROM ess_submissions s"
        " JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id WHERE s.created_at>=?",
        (since,),
    ).fetchone()
    graded = d.execute("SELECT count(*) FROM ess_submissions WHERE graded_at>=?", (since,)).fetchone()[0]
    published = d.execute("SELECT count(*) FROM ess_submissions WHERE published_at>=?", (since,)).fetchone()[0]
    assignments = d.execute("SELECT count(*) FROM ess_assignments WHERE created_at>=?", (since,)).fetchone()[0]
    trials = d.execute("SELECT count(*) FROM ess_trials WHERE created_at>=?", (since,)).fetchone()[0]
    cost = d.execute("SELECT coalesce(sum(cost),0) FROM ess_usage WHERE created_at>=?", (since,)).fetchone()[0]
    daily = dict(
        d.execute(
            "SELECT (created_at+?)/86400 day,count(*) FROM ess_submissions WHERE created_at>=? GROUP BY day",
            (BEIJING, first * 86400 - BEIJING),
        ).fetchall()
    )
    return {
        "teachers": teachers,
        "active": active,
        "essays": essays,
        "students": students,
        "writers": writers,
        "graded": graded,
        "published": published,
        "assignments": assignments,
        "trials": trials,
        "cost": cost,
        "daily": daily,
    }


def bars(days, series, height=150):
    """Stacked daily bars as SVG geometry: [{x, label, total, parts: [{y, h, color, name, n}]}], plus the scale."""
    top = max([sum(s["daily"].get(day, 0) for s in series) for day in days] + [1])
    step = 1000 / len(days)
    out = []
    for i, day in enumerate(days):
        y, parts = height, []
        for s in series:
            n = s["daily"].get(day, 0)
            if n:
                h = n / top * height
                parts.append({"y": y - h, "h": max(h - 1, 1), "color": s["color"], "name": s["name"], "n": n})
                y -= h
        out.append({"x": i * step + step * 0.18, "w": step * 0.64, "label": day_label(day), "total": height - y, "parts": parts,
                    "sum": sum(p["n"] for p in parts)})
    return {"bars": out, "top": top, "height": height, "step": step}


def register_stats(app):
    @app.get("/admin/stats")
    @admin_only
    def stats_page():
        span = request.args.get("range", "7")
        span = span if span in RANGES else "7"
        today = day_of(time.time())
        since = (today - int(span) + 1) * 86400 - BEIJING
        days = list(range(today - TREND_DAYS + 1, today + 1))
        rows, trials, daily = game_stats(since, days[0])
        sites = []
        for key, cfg in SITES.items():
            games = []
            for game, name in cfg.games.items():
                r = rows.get((key, game), {"modes": {}, "users": 0})
                total = sum(r["modes"].values())
                per_day = [daily.get((key, game, day), 0) for day in days]
                games.append({"name": name, "users": r["users"], "total": total, "modes": r["modes"],
                              "trials": trials.get((key, game), 0), "days": per_day})
            games.sort(key=lambda x: (-x["total"], -x["trials"]))
            sites.append({"key": key, "title": cfg.title, "color": SITE_COLORS[key], "games": games,
                          "summary": site_summary(key, since),
                          "trials": sum(x["trials"] for x in games),
                          "daily": {day: sum(daily.get((key, g, day), 0) for g in cfg.games) for day in days}})
        work = workbench_stats(since, days[0])
        heat_top = max([n for s in sites for x in s["games"] for n in x["days"]] + [1])
        essay_days = [work["daily"].get(day, 0) for day in days]
        first_full = db().execute("SELECT done_at FROM migrations WHERE name='plays_backfill'").fetchone()
        return render_template(
            "stats.html",
            span=span,
            ranges=RANGES,
            modes=MODES,
            sites=sites,
            work=work,
            workbench=ALL_SITES["teacher"],
            days=[day_label(day) for day in days],
            rounds_chart=bars(days, [{"daily": s["daily"], "color": s["color"], "name": s["title"]} for s in sites]),
            essay_chart=bars(days, [{"daily": work["daily"], "color": ESSAY_COLOR, "name": "作文"}], height=90),
            essay_days=essay_days,
            heat=lambda n: heat(n, heat_top),
            essay_heat=lambda n: heat(n, max(essay_days + [1])),
            dark_steps=HEAT[3:],
            first_full=first_full and time.strftime("%Y-%m-%d", time.gmtime(first_full[0] + BEIJING)),
        )
