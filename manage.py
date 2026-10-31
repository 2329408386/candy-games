import argparse, datetime, json, os, secrets, shutil, sqlite3, time, zipfile
from werkzeug.security import generate_password_hash
from core import schema
from core.app import create_app, data_dir, db, csv_response, group_map, group_labels, GRADES
from sites import SITES

os.umask(0o077)
SITE_URLS = {"chinese": "http://39.106.16.99", "math": "http://39.106.16.99/math"}


def site_dir(site):
    folder = data_dir() / site.key
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    return folder


def password_reset(site):
    password = secrets.token_urlsafe(20)
    db().execute(
        "INSERT INTO admin VALUES(?,?,1,1) ON CONFLICT(site) DO UPDATE SET password=excluded.password,must_change=1,version=version+1",
        (site.key, generate_password_hash(password)),
    )
    db().commit()
    path = site_dir(site) / "管理员初始凭据.txt"
    path.write_text(
        "后台："
        + os.environ.get("SITE_URL", SITE_URLS[site.key]).rstrip("/")
        + "/admin\n用户名：admin\n初始密码："
        + password
        + "\n首次登录必须修改密码。\n"
    )
    print("管理员凭据已写入私有文件：" + str(path))


def seed(site):
    # A fresh site gets one "A组" per semester holding every game; no license codes are generated.
    d = db()
    s = site.key
    if not d.execute("SELECT 1 FROM game_groups WHERE site=?", (s,)).fetchone():
        d.executemany(
            "INSERT INTO game_groups(site,id,grade,name,position) VALUES(?,?,?,?,0)",
            [(s, grade + "-a", grade, "A组") for grade in GRADES],
        )
        d.executemany(
            "INSERT INTO game_memberships(site,game,group_id) VALUES(?,?,?)",
            [(s, game, site.game_grades[game] + "-a") for game in site.games],
        )
        d.commit()
    if not d.execute("SELECT 1 FROM admin WHERE site=?", (s,)).fetchone():
        password_reset(site)
    print("初始化完成；未生成任何正式授权码")


def export(site):
    folder = site_dir(site) / "exports"
    folder.mkdir(exist_ok=True, mode=0o700)
    s = site.key
    groups = {
        "全部授权码": db()
        .execute("SELECT * FROM licenses WHERE site=? AND archived_at IS NULL ORDER BY id", (s,))
        .fetchall()
    }
    for gid in group_map():
        rows = (
            db()
            .execute(
                "SELECT * FROM licenses WHERE site=? AND archived_at IS NULL AND id IN (SELECT license_id FROM license_groups WHERE site=? AND group_id=?) ORDER BY id",
                (s, s, gid),
            )
            .fetchall()
        )
        if rows:
            groups[group_labels([gid])[0].replace("/", "-") + "-" + gid] = rows
    for old in folder.glob("*.csv"):
        old.unlink()
    for name, rows in groups.items():
        (folder / (name + ".csv")).write_text(csv_response(rows), encoding="utf-8")
    with zipfile.ZipFile(site_dir(site) / "新服务器授权码-卖家专用.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.glob("*.csv")):
            z.write(f, f.name)
    archived = (
        db().execute("SELECT * FROM licenses WHERE site=? AND archived_at IS NOT NULL ORDER BY id", (s,)).fetchall()
    )
    (site_dir(site) / "历史归档授权码-不可使用.csv").write_text(csv_response(archived), encoding="utf-8")
    print("导出完成：" + str(folder))


def backup():
    root = data_dir() / "backups"
    root.mkdir(exist_ok=True, mode=0o700)
    folder = root / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    folder.mkdir(mode=0o700)
    with sqlite3.connect(data_dir() / schema.DB_NAME) as source, sqlite3.connect(folder / schema.DB_NAME) as dest:
        source.backup(dest)
    if (data_dir() / "secret.env").exists():
        shutil.copy2(data_dir() / "secret.env", folder / "secret.env")
    for f in root.iterdir():
        if f.is_dir() and f.stat().st_mtime < time.time() - 30 * 86400:
            shutil.rmtree(f)
    print("备份完成：" + str(folder))


def summary(site):
    d = db()
    s = site.key
    print(
        json.dumps(
            {
                "site": s,
                "licenses": d.execute(
                    "SELECT count(*) FROM licenses WHERE site=? AND archived_at IS NULL", (s,)
                ).fetchone()[0],
                "archived": d.execute(
                    "SELECT count(*) FROM licenses WHERE site=? AND archived_at IS NOT NULL", (s,)
                ).fetchone()[0],
                "devices": d.execute("SELECT count(*) FROM devices WHERE site=?", (s,)).fetchone()[0],
                "grade_scopes": [
                    dict(r)
                    for r in d.execute(
                        "SELECT grades AS grade_scopes,count(*) AS codes FROM (SELECT l.id,"
                        " (SELECT json_group_array(grade) FROM (SELECT DISTINCT gg.grade FROM license_groups lg"
                        " JOIN game_groups gg ON gg.site=lg.site AND gg.id=lg.group_id"
                        " WHERE lg.site=l.site AND lg.license_id=l.id ORDER BY gg.grade)) AS grades"
                        " FROM licenses l WHERE l.site=? AND l.archived_at IS NULL) GROUP BY grades",
                        (s,),
                    )
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["seed", "export", "backup", "reset-password", "summary"])
    p.add_argument("--site", choices=sorted(SITES))
    args = p.parse_args()
    schema.initialize(data_dir() / schema.DB_NAME)
    if args.command == "backup":
        backup()
        raise SystemExit
    targets = [SITES[args.site]] if args.site else list(SITES.values())
    if args.command == "reset-password" and not args.site:
        p.error("reset-password 需要 --site")
    for site in targets:
        with create_app(site).app_context():
            {"seed": seed, "export": export, "reset-password": password_reset, "summary": summary}[args.command](site)
