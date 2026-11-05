"""Grades a class's essays again with the model settings as they are now and saves the new results next to the old
ones, to compare. Nothing the site uses is changed: the run works on a copy of the database (and of the photos) in
a scratch directory, so the new reviews, re-read texts, turned photos and usage rows all stay in that copy.
The calls cost what a normal grading costs on the provider's bill (not counted in 模型设置's spending).

    GAME_DATA=/var/lib/candy-games python tools/essay_compare.py --key <授权码> --out /tmp/compare.json
        [--scratch DIR] [--threads 3] [--ids 12,13] [--text-only]

Photo essays are read from the photos again (like manage.py essay-reread) unless --text-only, which grades the
text stored now. Needs the site secret in the environment (EnvironmentFile=/var/lib/candy-games/secret.env).
"""

import argparse, json, os, shutil, sqlite3, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import schema  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True, help="授权码")
    p.add_argument("--out", required=True)
    p.add_argument("--scratch")
    p.add_argument("--threads", type=int, default=3)
    p.add_argument("--ids", default="", help="只跑这些作文编号（逗号分隔）")
    p.add_argument("--text-only", action="store_true")
    args = p.parse_args()

    src = Path(os.environ.get("GAME_DATA", "/var/lib/candy-games"))
    scratch = Path(args.scratch or tempfile.mkdtemp(prefix="essay-compare-"))
    scratch.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{src / schema.DB_NAME}?mode=ro", uri=True) as live, sqlite3.connect(scratch / schema.DB_NAME) as copy:
        live.backup(copy)
    os.environ["GAME_DATA"] = str(scratch)  # image_path / rotate_image / crop_line now use the copy

    from core.essay import worker

    d = worker.connect()
    code = args.key.upper().replace("-", "").replace(" ", "")
    lic = d.execute(
        "SELECT license_id FROM license_keys WHERE site='teacher' AND upper(replace(replace(code,'-',''),' ',''))=?", (code,)
    ).fetchone()
    if not lic:
        raise SystemExit("没有这个授权码")
    rows = d.execute(
        "SELECT s.id,s.source,s.status,s.text,s.title,st.name student,a.title topic,c.name class FROM ess_submissions s"
        " JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id"
        " JOIN ess_students st ON st.id=s.student_id"
        " WHERE c.license_id=? AND s.status IN ('review','published','failed') ORDER BY s.id",
        (lic[0],),
    ).fetchall()
    if args.ids:
        wanted = {int(x) for x in args.ids.split(",")}
        rows = [r for r in rows if r["id"] in wanted]
    (images_dir := scratch / "essay-images").mkdir(mode=0o700, exist_ok=True)
    for r in rows:
        for (iid,) in d.execute("SELECT id FROM ess_images WHERE submission_id=?", (r["id"],)):
            if (f := src / "essay-images" / f"{iid}.jpg").exists():
                shutil.copy2(f, images_dir / f.name)
    print(f"授权 #{lic[0]}：{len(rows)} 篇作文，副本在 {scratch}", flush=True)

    results, lock = [], threading.Lock()

    def run(r):
        dd = worker.connect()
        old = dd.execute("SELECT ai,final,model,prompt_version,created_at FROM ess_reviews WHERE submission_id=?", (r["id"],)).fetchone()
        item = {
            "id": r["id"], "student": r["student"], "class": r["class"], "topic": r["topic"], "source": r["source"],
            "status": r["status"],
            "old": {
                "text": r["text"], "model": old and old["model"], "prompt_version": old and old["prompt_version"],
                "created_at": old and old["created_at"],
                "ai": json.loads(old["ai"]) if old else None,
                "final": json.loads(old["final"]) if old and old["final"] else None,
            },
        }
        if r["source"] == "photo" and not args.text_only:
            dd.execute("UPDATE ess_submissions SET text='',writing='' WHERE id=?", (r["id"],))
            dd.commit()
        started = time.time()
        try:
            review, model = worker.grade(dd, dict(worker.essay_of(dd, r["id"])))
            text = dd.execute("SELECT text FROM ess_submissions WHERE id=?", (r["id"],)).fetchone()[0]
            item["new"] = {"text": text, "model": model, "review": review}
        except Exception as e:  # noqa: BLE001 — one essay failing should not stop the rest
            item["new"] = {"error": f"{type(e).__name__}: {e}"}
        item["new"]["seconds"] = round(time.time() - started, 1)
        item["new"]["usage"] = [
            dict(u) for u in dd.execute(
                "SELECT step,model,ok,tokens_in,tokens_out,cost,ms,error FROM ess_usage WHERE submission_id=? AND created_at>=?",
                (r["id"], int(started)),
            )
        ]
        dd.close()
        with lock:
            results.append(item)
            new = item["new"]
            done = new.get("review", {}).get("total") if "review" in new else "失败 " + new["error"][:80]
            print(f"[{len(results)}/{len(rows)}] #{r['id']} {r['student']}：旧 {(item['old']['final'] or item['old']['ai'] or {}).get('total')} → 新 {done}（{new['seconds']} 秒）", flush=True)
            Path(args.out).write_text(json.dumps(sorted(results, key=lambda x: x["id"]), ensure_ascii=False, indent=1))

    with ThreadPoolExecutor(args.threads) as pool:
        list(pool.map(run, rows))
    print("完成：", args.out)


if __name__ == "__main__":
    main()
