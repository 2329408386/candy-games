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
from . import grading, llm, models_sync, photo

THREADS = 3
RETRY_DELAYS = (30, 120, 600)
STALE_SECONDS = 900  # at least this long 'running' means the job was interrupted (process killed): run it again
IDLE_SECONDS = 2
HOLD_SECONDS = 60
BEIJING = 8 * 3600
log = logging.getLogger("essay-worker")


def flag(d, key):
    """A switch in ess_meta (photo.FLAGS), off unless set to 1."""
    row = d.execute("SELECT value FROM ess_meta WHERE key=?", (key,)).fetchone()
    return bool(row) and row[0] == "1"


def pixels(what, submission, fn, *args):
    """A step on the photo's pixels: it only ever helps, so whatever goes wrong the essay is graded without it."""
    try:
        return fn(*args)
    except Exception as e:
        log.warning("%s failed for submission %s: %r", what, submission, e)
        return None


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

    total = longest["ocr"] * MAX_PHOTOS + longest["grade"] + longest["check"] + 300
    if flag(d, "photo_rectify"):  # a flattened photo that shows nothing is read again as it was
        total += longest["ocr"] * MAX_PHOTOS
    if flag(d, "shape_scan"):  # one call per page, and the confirmation
        total += llm.TRIES * sum(r["timeout"] or 120 for r in llm.step_rows(d, llm.look_step(d))) * (MAX_PHOTOS + 1)
    return max(STALE_SECONDS, total)


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
    from . import image_path, replace_image, rotate_image

    meta = dict(license_id=essay["license_id"], submission_id=essay["id"])
    images = d.execute("SELECT * FROM ess_images WHERE submission_id=? ORDER BY page", (essay["id"],)).fetchall()

    def read(img, data=None):
        data = data if data is not None else image_path(img["id"]).read_bytes()
        page, _ = llm.chat(
            d,
            "ocr",
            grading.ocr_messages(photo.fit(data, photo.OCR_SIDE)[0], img["page"], len(images)),
            json_mode=True,
            parse=lambda content: grading.parse_ocr(llm.parse_json(content)),
            **meta,
        )
        return page

    pages, upright = [], []
    rectifying, snapping, scanning = flag(d, "photo_rectify"), flag(d, "photo_snap"), flag(d, "shape_scan")
    for img in images:
        data = image_path(img["id"]).read_bytes()
        flat = pixels("rectify", essay["id"], photo.rectify, data) if rectifying else None
        page = read(img, flat[0]) if flat else None
        if page is not None and grading.char_count("".join(line["text"] for line in page["lines"])) < 10:
            log.info("submission %s page %s: nothing read on the flattened photo, using it as it was", essay["id"], img["page"])
            flat, page = None, None  # the cut went wrong, or this is no essay: judge the photo itself
        if flat:
            img = replace_image(d, img, *flat)
        else:
            small, w, h = photo.fit(data, photo.STORED_SIDE)
            if small is not data:
                img = replace_image(d, img, small, w, h)
            page = read(img)
        # A photo taken sideways (and no EXIF orientation): turn it upright and read it again, so the text is read
        # properly and the red pen can be drawn on it. At most twice, in case the model misjudged the first time.
        for _ in range(2):
            if not page["rotate"]:
                break
            log.info("submission %s page %s turned %s°", essay["id"], img["page"], page["rotate"])
            img = rotate_image(d, img, page["rotate"])
            page = read(img)
        if snapping:
            snapped = pixels("snap", essay["id"], photo.snap_lines, image_path(img["id"]).read_bytes(), page["lines"])
            if snapped:
                page = {**page, "lines": snapped[0]}
                log.info("submission %s page %s line boxes: %s", essay["id"], img["page"], snapped[1])
        pages.append(page)
        upright.append(img)
    images = upright
    title, text, spots, shapes = grading.assemble(pages)
    writing = grading.worst_writing(pages)
    if grading.char_count(text) < 10:
        raise llm.LLMError("照片里没有认出作文，请拍清楚整页作文再交", retry=False)
    text = text[: grading.MAX_TEXT]
    spots = [[l for l in lines if l["e"] <= len(text)] for lines in spots]
    for img, lines in zip(images, spots):
        d.execute("UPDATE ess_images SET lines=? WHERE id=?", (json.dumps(lines), img["id"]))
    d.commit()
    shapes = [s for s in shapes if s["pos"] < len(text)]
    placed = [(img["id"], lines) for img, lines in zip(images, spots)]
    if scanning:
        shapes = grading.merge_shapes(shapes, scan_pages(d, essay, text, placed))
    shapes = verify_shapes(d, essay, shapes, text, placed, closeups=scanning)
    d.execute(
        "UPDATE ess_submissions SET text=?,title=CASE WHEN title='' THEN ? ELSE title END,writing=?,shapes=? WHERE id=?",
        (text, title, writing, json.dumps(shapes, ensure_ascii=False) if shapes else "", essay["id"]),
    )
    d.commit()
    return {**essay, "text": text, "title": essay["title"] or title, "writing": writing, "shapes": json.dumps(shapes, ensure_ascii=False) if shapes else ""}


def scan_pages(d, essay, text, pages):
    """Enlarged lines of each page searched for characters written wrong (one call per page): candidates, which still
    have to pass verify_shapes. A failed call finds nothing, the essay goes on."""
    from . import crop_line

    found = []
    for n, (iid, spots) in enumerate(pages):
        numbered = [(k, s) for k, s in enumerate(spots, 1)]
        if not numbered:
            continue
        try:
            sheets = []
            for at in range(0, len(numbered), grading.SCAN_ROWS):
                part = numbered[at : at + grading.SCAN_ROWS]
                numbers = [k for k, _ in part]
                sheets.append((numbers, photo.stitch_lines([crop_line(iid, s["b"]) for _, s in part], numbers)))
            items, _ = llm.chat(
                d,
                llm.look_step(d),
                grading.scan_messages([(k, text[s["s"] : s["e"]]) for k, s in numbered], sheets),
                json_mode=True,
                parse=lambda content: grading.parse_scan(llm.parse_json(content)),
                license_id=essay["license_id"],
                submission_id=essay["id"],
            )
        except (llm.LLMError, OSError, ValueError) as e:
            log.warning("shape scan failed for submission %s: %s", essay["id"], e)
            continue
        found += grading.scan_shapes(items, text, spots, n)
    return found


def verify_shapes(d, essay, shapes, text, pages, closeups=False):
    """The characters the ocr step says were written wrong (no character at all), looked at again on the photo:
    only the ones the photo check confirms are kept. A failed call keeps none (better miss one than mark a right one).
    closeups: each is shown enlarged and beside its standard form (prompts/shape_verify.md)."""
    from . import crop_line, image_path

    targets = grading.shape_targets(shapes, text, pages)
    if not targets:
        return []
    for n, t in enumerate(targets, 1):
        t["id"] = n
    try:
        crops = [crop_line(t["iid"], t["box"]) for t in targets]
        close = None
        if closeups:
            close = [(photo.crop_char(image_path(t["iid"]).read_bytes(), t["box"], t["line"], t["nth"]), photo.glyph(t["char"])) for t in targets]
        seen, _ = llm.chat(
            d,
            llm.look_step(d),
            grading.verify_messages(targets, crops, close),
            json_mode=True,
            parse=lambda content: grading.parse_verify(llm.parse_json(content), len(targets)),
            license_id=essay["license_id"],
            submission_id=essay["id"],
        )
    except (llm.LLMError, OSError, ValueError) as e:
        log.warning("shape verify failed for submission %s: %s", essay["id"], e)
        return []
    kept = grading.keep_shapes(shapes, targets, seen)
    log.info("submission %s: %s of %s miswritten character(s) confirmed", essay["id"], len(kept), len(targets))
    return kept


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
        review = grading.apply_check(review, verdicts, level)
    except llm.LLMError as e:
        log.warning("check failed for submission %s: %s", essay["id"], e)
        review = {**review, "flags": review["flags"] + ["unchecked"], "rejected": [], "check_level": None}
    if essay["source"] == "photo":
        review = verify_typos(d, essay, review)
        if essay["shapes"]:
            text = d.execute("SELECT text FROM ess_submissions WHERE id=?", (essay["id"],)).fetchone()[0]
            review = grading.shape_marks(review, text, json.loads(essay["shapes"]))
    return review, model


def verify_typos(d, essay, review):
    """Looks at the photo again for every 错别字 mark (grading.typo_targets): a mark the ocr step caused by misreading
    a correctly written character is dropped (kept in review["rejected"] for the teacher) and the stored text
    corrected. A failed call leaves the review as it is."""
    from . import crop_line

    images = d.execute("SELECT id,lines FROM ess_images WHERE submission_id=? ORDER BY page", (essay["id"],)).fetchall()
    pages = [(img["id"], json.loads(img["lines"]) if img["lines"] else []) for img in images]
    targets = grading.typo_targets(review, essay["text"], pages)
    if not targets or not llm.configured(d, llm.look_step(d)):
        return review
    try:
        crops = [crop_line(t["iid"], t["box"]) for t in targets]
        seen, _ = llm.chat(
            d,
            llm.look_step(d),
            grading.verify_messages(targets, crops),
            json_mode=True,
            parse=lambda content: grading.parse_verify(llm.parse_json(content), len(targets)),
            license_id=essay["license_id"],
            submission_id=essay["id"],
        )
    except (llm.LLMError, OSError, ValueError) as e:
        log.warning("typo verify failed for submission %s: %s", essay["id"], e)
        return review
    review, text, dropped = grading.apply_verify(review, essay["text"], targets, seen)
    if dropped:
        log.info("submission %s: %s 错别字 mark(s) were misread on the photo", essay["id"], len(dropped))
        d.execute("UPDATE ess_submissions SET text=? WHERE id=?", (text, essay["id"]))
        d.commit()
        reason = "照片上其实写对了（是照片识别看错了字）"
        review = {**review, "rejected": review.get("rejected", []) + [{**m, "reason": reason} for m in dropped]}
    return review


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


def resume_interrupted(d):
    """At start-up: this process is the only worker, so a job still 'running' was cut off when the last one stopped
    (a release restarts it). Queue those again at once instead of waiting stale_seconds for them."""
    d.execute("BEGIN IMMEDIATE")
    rows = d.execute("SELECT id,submission_id FROM ess_jobs WHERE status='running'").fetchall()
    for job in rows:
        d.execute("UPDATE ess_jobs SET status='queued',run_after=? WHERE id=?", (int(time.time()), job["id"]))
        d.execute("UPDATE ess_submissions SET status='queued' WHERE id=? AND status='grading'", (job["submission_id"],))
    d.commit()
    if rows:
        log.info("resumed %d interrupted job(s): submissions %s", len(rows), ", ".join(str(r["submission_id"]) for r in rows))


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
    resume_interrupted(connect())
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
