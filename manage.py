import argparse, datetime, json, os, secrets, shutil, sqlite3, time, zipfile
from pathlib import Path
from werkzeug.security import generate_password_hash
from core import schema
from core.app import create_app, data_dir, db, licenses_xlsx, group_map, group_labels, GRADES
from sites import ALL_SITES as SITES

os.umask(0o077)
SITE_URLS = {
    "chinese": "http://39.106.16.99",
    "math": "http://39.106.16.99/math",
    "english": "http://39.106.16.99/english",
    "teacher": "http://39.106.16.99/teacher",
}


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
    for old in [*folder.glob("*.csv"), *folder.glob("*.xlsx")]:
        old.unlink()
    for name, rows in groups.items():
        (folder / (name + ".xlsx")).write_bytes(licenses_xlsx(rows))
    with zipfile.ZipFile(site_dir(site) / "新服务器授权码-卖家专用.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.glob("*.xlsx")):
            z.write(f, f.name)
    archived = (
        db().execute("SELECT * FROM licenses WHERE site=? AND archived_at IS NOT NULL ORDER BY id", (s,)).fetchall()
    )
    (site_dir(site) / "历史归档授权码-不可使用.xlsx").write_bytes(licenses_xlsx(archived))
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
        if f.is_dir() and f.stat().st_mtime < time.time() - 3 * 86400:
            shutil.rmtree(f)
    print("备份完成：" + str(folder))


def essay_reread(sid):
    """Grades a photographed essay again from its photos (read again, turned upright when taken sideways). What the
    student saw is replaced by the new grading (published by itself or after the teacher's review, as the assignment
    is set)."""
    from core.essay import enqueue

    d = db()
    row = d.execute("SELECT id,source,status FROM ess_submissions WHERE id=?", (sid,)).fetchone()
    if not row or row["source"] != "photo":
        raise SystemExit(f"没有编号为 {sid} 的拍照作文")
    d.execute("UPDATE ess_submissions SET text='',writing='',status='queued',hold='' WHERE id=?", (sid,))
    d.execute("UPDATE ess_images SET lines='' WHERE submission_id=?", (sid,))
    enqueue(sid)
    d.commit()
    print(f"作文 {sid} 已重新排队（原来是 {row['status']}），会从识别照片开始重新批改")


def essay_recalibrate(max_id, dry_run=False):
    """Moves the line boxes of the photos already graded onto the handwriting, as the worker now does for new ones
    (core/essay/photo.py). Only where the red pen is drawn changes: no model is called, and the text and the marks
    stay. The boxes as they were go to essay-boxes-<time>.json in the data directory first (essay-unboxes restores them).
    max_id: the last photo read before the worker began calibrating; later ones are done already."""
    from core.essay import image_path, photo

    d = db()
    rows = d.execute(
        "SELECT i.id,i.lines,s.text FROM ess_images i JOIN ess_submissions s ON s.id=i.submission_id WHERE i.lines!='' AND i.id<=? ORDER BY i.id", (max_id,)
    ).fetchall()
    if not dry_run:
        backup = data_dir() / f"essay-boxes-{time.strftime('%Y%m%d-%H%M%S')}.json"
        backup.write_text(json.dumps({row["id"]: row["lines"] for row in rows}))
        print(f"原来的行框已存到 {backup}")
    changed = kept = failed = 0
    for row in rows:
        spots = json.loads(row["lines"])
        lines = [{"text": row["text"][s["s"] : s["e"]], "box": s["b"]} for s in spots]
        try:
            new, stats = photo.calibrate(image_path(row["id"]).read_bytes(), lines)
        except (OSError, ValueError) as e:
            failed += 1
            print(f"图片 {row['id']} 跳过：{e}")
            continue
        boxes = [line["box"] for line in new]
        if boxes == [s["b"] for s in spots]:
            kept += 1
            continue
        changed += 1
        print(f"图片 {row['id']}：{stats['moved']}/{stats['lines']} 行，最大移动 {stats['shift']} 行")
        if not dry_run:
            # Not if the teacher corrected the text in the meantime: that stored other positions.
            d.execute("UPDATE ess_images SET lines=? WHERE id=? AND lines=?", (json.dumps([{**s, "b": b} for s, b in zip(spots, boxes)]), row["id"], row["lines"]))
            d.commit()
    print(f"{'试算：' if dry_run else ''}共 {len(rows)} 张，校准 {changed} 张，保留原框 {kept} 张，跳过 {failed} 张")


def essay_unboxes(path):
    """Puts back the line boxes saved by essay-recalibrate."""
    d = db()
    saved = json.loads(Path(path).read_text())
    for iid, lines in saved.items():
        d.execute("UPDATE ess_images SET lines=? WHERE id=?", (lines, int(iid)))
    d.commit()
    print(f"已恢复 {len(saved)} 张照片的行框")


def essay_drop_para():
    """Removes 分段 marks that sit where the child already began a paragraph, from the AI review and the published
    one (grading now drops them as they come in). Nothing else changes. Safe to run again."""
    from core.essay import grading

    d = db()
    rows = d.execute("SELECT r.submission_id,r.ai,r.final,s.text FROM ess_reviews r JOIN ess_submissions s ON s.id=r.submission_id").fetchall()
    for row in rows:
        changed, dropped = {}, 0
        for col in ("ai", "final"):
            if not row[col]:
                continue
            review = json.loads(row[col])
            keep = [m for m in review["marks"] if not (m["type"] == "分段" and grading.para_start(row["text"], m["start"]))]
            if len(keep) < len(review["marks"]):
                dropped = max(dropped, len(review["marks"]) - len(keep))
                changed[col] = json.dumps({**review, "marks": keep}, ensure_ascii=False)
        for col, value in changed.items():
            d.execute(f"UPDATE ess_reviews SET {col}=? WHERE submission_id=?", (value, row["submission_id"]))
        if changed:
            print(f"作文 {row['submission_id']}：去掉 {dropped} 条段首的分段批注")
    d.commit()
    print("完成")


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
    p.add_argument("command", choices=["seed", "export", "backup", "reset-password", "summary", "essay-reread", "essay-drop-para", "essay-recalibrate", "essay-unboxes"])
    p.add_argument("--site", choices=sorted(SITES))
    p.add_argument("--id", type=int, help="essay-reread: 作文提交编号")
    p.add_argument("--dry-run", action="store_true", help="essay-recalibrate: 只算不写")
    p.add_argument("--max-id", type=int, help="essay-recalibrate: 只处理这个编号及之前的照片")
    p.add_argument("--file", help="essay-unboxes: essay-recalibrate 存下的文件")
    args = p.parse_args()
    schema.initialize(data_dir() / schema.DB_NAME)
    if args.command == "backup":
        backup()
        raise SystemExit
    if args.command == "essay-drop-para":
        with create_app(SITES["teacher"]).app_context():
            essay_drop_para()
        raise SystemExit
    if args.command == "essay-recalibrate":
        if not args.max_id:
            p.error("essay-recalibrate 需要 --max-id")
        with create_app(SITES["teacher"]).app_context():
            essay_recalibrate(args.max_id, args.dry_run)
        raise SystemExit
    if args.command == "essay-unboxes":
        if not args.file:
            p.error("essay-unboxes 需要 --file")
        with create_app(SITES["teacher"]).app_context():
            essay_unboxes(args.file)
        raise SystemExit
    if args.command == "essay-reread":
        if not args.id:
            p.error("essay-reread 需要 --id")
        with create_app(SITES["teacher"]).app_context():
            essay_reread(args.id)
        raise SystemExit
    targets = [SITES[args.site]] if args.site else list(SITES.values())
    if args.command == "reset-password" and not args.site:
        p.error("reset-password 需要 --site")
    for site in targets:
        with create_app(site).app_context():
            {"seed": seed, "export": export, "reset-password": password_reset, "summary": summary}[args.command](site)
