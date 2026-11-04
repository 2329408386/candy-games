"""The essay worker: a separate process (systemd candy-games-essay-worker) that runs model calls off the web workers.

    python -m core.essay.worker          run forever (THREADS threads, each polling ess_jobs)
    python -m core.essay.worker --once   process the queue until empty, then exit (tests, debugging)

An essay sent as photos has no text yet: the job first runs the ocr step on each photo (read_photos), stores the
text and where each line is on its photo, then grades as usual; a retry does not read the photos again.
Web requests only insert a row into ess_jobs and set the submission to 'queued'; pages poll the submission's status.
A job is claimed under a write lock, so several threads (or a second process during a deploy) never run it twice.
Failures that may pass (timeouts, rate limits, malformed answers) are retried after RETRY_DELAYS; when those run
out, or the failure cannot pass (bad key, wrong model name), the submission becomes 'failed' and the teacher can
press 重新批改. When the check step fails the review goes to the teacher ('review') instead of being
published. Jobs wait without being counted as attempts while the step is not configured or today's spending
has reached the daily budget (ess_meta 'daily_budget', yuan); the submission's `hold` column says why.
"""

import argparse, json, logging, sqlite3, threading, time
from . import grading, llm, models_sync

THREADS = 3
RETRY_DELAYS = (30, 120, 600)
STALE_SECONDS = 900  # at least this long 'running' means the job was interrupted (process killed): run it again
IDLE_SECONDS = 2
HOLD_SECONDS = 60
BEIJING = 8 * 3600
log = logging.getLogger("essay-worker")


def connect(path=None):
    from core import schema
    from core.app import data_dir

    d = sqlite3.connect(path or data_dir() / schema.DB_NAME, timeout=30)
    d.row_factory = sqlite3.Row
    d.execute("PRAGMA foreign_keys=ON")
    return d


def today_start(now=None):
    local = int(now if now is not None else time.time()) + BEIJING
    return local - local % 86400 - BEIJING


def spent_today(d):
    return d.execute("SELECT coalesce(sum(cost),0) FROM ess_usage WHERE created_at>=?", (today_start(),)).fetchone()[0]


def daily_budget(d):
    row = d.execute("SELECT value FROM ess_meta WHERE key='daily_budget'").fetchone()
    return float(row[0]) if row and row[0] else 0.0


def heartbeat(d):
    d.execute("INSERT INTO ess_meta VALUES('worker_seen',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(int(time.time())),))
    d.commit()


def stale_seconds(d):
    """How long a job may run before it counts as interrupted: the longest a job can take with the configured
    timeouts (every model of a step timing out llm.TRIES times; every photo read that way), plus a margin.
    Shorter would run a slow job a second time while the first is still going."""
    longest = {
        step: llm.TRIES * sum(r["timeout"] or 120 for r in llm.step_rows(d, step)) for step in ("ocr", "grade", "check")
    }
    from . import MAX_PHOTOS

    return max(STALE_SECONDS, longest["ocr"] * MAX_PHOTOS + longest["grade"] + longest["check"] + 300)


def claim(d):
    now = int(time.time())
    stale = stale_seconds(d)
    d.execute("BEGIN IMMEDIATE")
    d.execute(
        "UPDATE ess_jobs SET status='queued' WHERE status='running' AND locked_at<?", (now - stale,)
    )
    row = d.execute(
        "SELECT * FROM ess_jobs WHERE status='queued' AND run_after<=? ORDER BY run_after,id LIMIT 1", (now,)
    ).fetchone()
    if row:
        d.execute("UPDATE ess_jobs SET status='running',locked_at=? WHERE id=?", (now, row["id"]))
    d.commit()
    return row


def essay_of(d, submission_id):
    return d.execute(
        "SELECT s.*,a.title topic,a.genre,a.intro,a.requirement,a.min_chars,a.auto_publish,c.grade,c.license_id FROM ess_submissions s"
        " JOIN ess_assignments a ON a.id=s.assignment_id JOIN ess_classes c ON c.id=a.class_id WHERE s.id=?",
        (submission_id,),
    ).fetchone()


def hold(d, job, submission_id, reason):
    d.execute("UPDATE ess_jobs SET status='queued',run_after=? WHERE id=?", (int(time.time()) + HOLD_SECONDS, job["id"]))
    d.execute("UPDATE ess_submissions SET hold=? WHERE id=?", (reason, submission_id))
    d.commit()


def read_photos(d, essay):
    """OCR of every photo of a submission → essay dict with the text (and title when the student gave none).
    Raises llm.LLMError."""
    from . import image_path

    meta = dict(license_id=essay["license_id"], submission_id=essay["id"])
    images = d.execute("SELECT * FROM ess_images WHERE submission_id=? ORDER BY page", (essay["id"],)).fetchall()
    pages = []
    for img in images:
        page, _ = llm.chat(
            d,
            "ocr",
            grading.ocr_messages(image_path(img["id"]).read_bytes(), img["page"], len(images)),
            json_mode=True,
            parse=lambda content: grading.parse_ocr(llm.parse_json(content)),
            **meta,
        )
        pages.append(page)
    title, text, spots = grading.assemble(pages)
    if grading.char_count(text) < 10:
        raise llm.LLMError("照片里没有认出作文，请拍清楚整页作文再交", retry=False)
    text = text[: grading.MAX_TEXT]
    d.execute(
        "UPDATE ess_submissions SET text=?,title=CASE WHEN title='' THEN ? ELSE title END WHERE id=?",
        (text, title, essay["id"]),
    )
    for img, lines in zip(images, spots):
        d.execute("UPDATE ess_images SET lines=? WHERE id=?", (json.dumps([l for l in lines if l["e"] <= len(text)]), img["id"]))
    d.commit()
    return {**essay, "text": text, "title": essay["title"] or title}


def grade(d, essay):
    """Runs (ocr +) grade + check for one submission. Returns (review dict, model label)."""
    meta = dict(license_id=essay["license_id"], submission_id=essay["id"])
    if essay["source"] == "photo" and not essay["text"]:
        essay = read_photos(d, essay)
    review, model = llm.chat(
        d,
        "grade",
        grading.grade_messages(essay),
        json_mode=True,
        parse=lambda content: grading.clean_review(llm.parse_json(content), essay),
        **meta,
    )
    # The checker always runs, even without error marks: it also gives its own level (llm.chat tries each model
    # twice). A review that could not be checked is not published by itself (run_job sends it to the teacher first).
    try:
        (verdicts, level), _ = llm.chat(
            d,
            "check",
            grading.check_messages(essay, review["marks"]),
            json_mode=True,
            parse=lambda content: grading.parse_check(llm.parse_json(content)),
            **meta,
        )
        return grading.apply_check(review, verdicts, level), model
    except llm.LLMError as e:
        log.warning("check failed for submission %s: %s", essay["id"], e)
    return {**review, "flags": review["flags"] + ["unchecked"], "rejected": [], "check_level": None}, model


def run_job(d, job):
    essay = essay_of(d, job["submission_id"])
    if not essay or essay["status"] not in ("queued", "grading"):
        d.execute("UPDATE ess_jobs SET status='done' WHERE id=?", (job["id"],))
        d.commit()
        return
    if not llm.configured(d, "grade") or essay["source"] == "photo" and not essay["text"] and not llm.configured(d, "ocr"):
        return hold(d, job, essay["id"], "not_configured")
    budget = daily_budget(d)
    if budget and spent_today(d) >= budget:
        return hold(d, job, essay["id"], "budget")
    d.execute("UPDATE ess_submissions SET status='grading',hold='' WHERE id=?", (essay["id"],))
    d.commit()
    try:
        review, model = grade(d, dict(essay))
    except llm.LLMError as e:
        attempts = job["attempts"] + 1
        if e.retry and attempts <= len(RETRY_DELAYS):
            d.execute(
                "UPDATE ess_jobs SET status='queued',attempts=?,run_after=?,error=? WHERE id=?",
                (attempts, int(time.time()) + RETRY_DELAYS[attempts - 1], str(e)[:500], job["id"]),
            )
            d.execute("UPDATE ess_submissions SET status='queued' WHERE id=?", (essay["id"],))
        else:
            d.execute("UPDATE ess_jobs SET status='failed',attempts=?,error=? WHERE id=?", (attempts, str(e)[:500], job["id"]))
            d.execute("UPDATE ess_submissions SET status='failed' WHERE id=?", (essay["id"],))
        d.commit()
        log.warning("grading submission %s failed: %s", essay["id"], e)
        return
    now = int(time.time())
    # Unchecked feedback may be wrong: the teacher looks at it first even when the assignment auto-publishes.
    published = bool(essay["auto_publish"]) and "unchecked" not in review["flags"]
    d.execute(
        "INSERT INTO ess_reviews(submission_id,ai,final,model,prompt_version,created_at) VALUES(?,?,?,?,?,?)"
        " ON CONFLICT(submission_id) DO UPDATE SET ai=excluded.ai,final=excluded.final,model=excluded.model,"
        "prompt_version=excluded.prompt_version,created_at=excluded.created_at,edited_at=NULL",
        (essay["id"], json.dumps(review, ensure_ascii=False), json.dumps(review, ensure_ascii=False) if published else None, model, grading.PROMPT_VERSION, now),
    )
    d.execute(
        "UPDATE ess_submissions SET status=?,graded_at=?,published_at=? WHERE id=?",
        ("published" if published else "review", now, now if published else None, essay["id"]),
    )
    d.execute("UPDATE ess_jobs SET status='done',error='' WHERE id=?", (job["id"],))
    d.commit()


def work(d, stop=None, once=False):
    last_beat = 0
    while not (stop and stop.is_set()):
        if time.time() - last_beat > 30:
            heartbeat(d)
            last_beat = time.time()
            if models_sync.due(d):
                try:
                    models_sync.sync(d)  # takes the slot under a lock, so only one thread runs it
                except Exception:
                    log.exception("model version check failed")
                    d.rollback()
        job = claim(d)
        if not job:
            if once:
                return
            time.sleep(IDLE_SECONDS)
            continue
        try:
            run_job(d, job)
        except Exception:  # never let one bad job kill the thread; it is retried as stale later
            log.exception("job %s crashed", job["id"])
            d.rollback()
            d.execute("UPDATE ess_jobs SET status='failed',error='内部错误' WHERE id=?", (job["id"],))
            d.execute("UPDATE ess_submissions SET status='failed' WHERE id=? AND status IN ('queued','grading')", (job["submission_id"],))
            d.commit()


def thread_main(stop=None):
    """One worker thread. Its connection must be opened here: sqlite3 connections belong to their thread."""
    work(connect(), stop)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from core import schema
    from core.app import data_dir

    schema.initialize(data_dir() / schema.DB_NAME)
    if args.once:
        work(connect(), once=True)
        return
    threads = [threading.Thread(target=thread_main, daemon=True) for _ in range(THREADS)]
    for t in threads:
        t.start()
    log.info("essay worker started with %d threads", THREADS)
    while all(t.is_alive() for t in threads):
        time.sleep(5)
    raise SystemExit("a worker thread stopped")


if __name__ == "__main__":
    main()
