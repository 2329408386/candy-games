"""The essay worker: a separate process (systemd candy-games-essay-worker) that runs model calls off the web workers.

    python -m core.essay.worker          run forever (THREADS threads, each polling ess_jobs)
    python -m core.essay.worker --once   process the queue until empty, then exit (tests, debugging)

An essay sent as photos has no text yet: the job first runs the ocr step on each photo (read_photos), moves the line
boxes onto the handwriting (photo.py), stores the text and where each line is on its photo, then grades as usual; a
retry does not read the photos again.
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
BOX_READS = (None, None, "backup")  # read_for_boxes: the model slot of each further reading (None: as usual)
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
        step: llm.TRIES * sum(r["timeout"] or 120 for r in llm.step_rows(d, step)) for step in ("ocr", "grade", "check", "verify")
    }
    from . import MAX_PHOTOS

    return max(STALE_SECONDS, longest["ocr"] * MAX_PHOTOS + longest["grade"] + longest["check"] + 2 * longest["verify"] + 300)


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


def calibrated(img, page):
    """The page with its line boxes moved onto the handwriting. Only the red pen's position depends on it, so whatever
    goes wrong here the essay is still graded, with the model's boxes."""
    from . import image_path, photo

    try:
        lines, stats = photo.calibrate(image_path(img["id"]).read_bytes(), page["lines"])
    except Exception:
        log.exception("image %s: line boxes not calibrated", img["id"])
        return page
    log.info("image %s line boxes: %s", img["id"], stats)
    return {**page, "lines": lines}


def split_spread(d, img, page):
    """A photo of two pages side by side as two photos, each with its own lines: [(image row, page)]. Whatever goes
    wrong here the photo stays whole; the essay is still graded, only with smaller writing to draw on."""
    from . import image_path, photo, split_image

    try:
        cut = photo.gutter(image_path(img["id"]).read_bytes(), page["lines"])
        if cut is None:
            return [(img, page)]
        sides = grading.split_page(page, cut)
        rows = split_image(d, img, cut)
    except Exception:
        log.exception("image %s: two pages not cut apart", img["id"])
        d.rollback()
        return [(img, page)]
    log.info("image %s: two pages on one photo, cut at %s", img["id"], cut)
    return list(zip(rows, sides))


class PhotoProblem(llm.LLMError):
    """The photos hold no handwritten essay to grade; code is one of grading.OCR_ERRORS or 太短 (core/essay FAILS).
    Not retried: reading the same photos again finds the same."""

    def __init__(self, code):
        super().__init__(f"照片里没有认出作文（{code}）", retry=False)
        self.code = code


def photo_problem(pages, text):
    """Why photos read as these pages (with this joined text, under 10 characters) cannot be graded: what the ocr step
    said, else 太短 when some characters were read, else 模糊."""
    for page in pages:
        if page.get("error"):
            return page["error"]
    return "太短" if grading.char_count(text) else "模糊"


def read_page(d, img, pages, meta, both_sides=False, slot=None):
    """The ocr step on one photo of a submission of that many pages → a page from grading.parse_ocr."""
    from . import image_path

    def parse(content):
        raw = llm.parse_json(content)
        page = grading.parse_ocr(raw)
        if grading.missing_boxes(page):
            log.warning("submission %s image %s: %s", meta["submission_id"], img["id"], grading.box_report(raw))
        return page

    page, _ = llm.chat(
        d,
        "ocr",
        grading.ocr_messages(image_path(img["id"]).read_bytes(), img["page"], pages, both_sides),
        json_mode=True,
        parse=parse,
        slot=slot,
        **meta,
    )
    return page


def read_for_boxes(read, img, page):
    """A page with lines lacking a usable box, read again: such a reply is often a one-off. Lines without a box get no
    red pen; a page with no box at all takes the red pen off the photos of the whole essay, so that one gets further
    readings, the last by the backup model (BOX_READS). The best reading is kept; a failed one only costs the red pen."""
    for k, slot in enumerate(BOX_READS):
        missing = grading.missing_boxes(page)
        if not missing or (k and missing < len(page["lines"])):
            break
        try:
            again = read(img, slot=slot)
        except llm.LLMError as e:
            log.warning("image %s not read again for its line boxes: %s", img["id"], e)
            continue
        if grading.better_boxes(again, page):
            log.info("image %s: read again for the line boxes", img["id"])
            page = again
    return page


def read_photos(d, essay):
    """OCR of every photo of a submission → essay dict with the text (and title when the student gave none).
    Raises llm.LLMError."""
    from . import rotate_image

    meta = dict(license_id=essay["license_id"], submission_id=essay["id"])
    images = d.execute("SELECT * FROM ess_images WHERE submission_id=? ORDER BY page", (essay["id"],)).fetchall()

    def read(img, both_sides=False, slot=None):
        return read_page(d, img, len(images), meta, both_sides, slot)

    pages, upright = [], []
    for img in images:
        # Read afresh: cutting an earlier photo in two moves the ones after it a page on.
        img = d.execute("SELECT * FROM ess_images WHERE id=?", (img["id"],)).fetchone()
        page = read(img)
        # A photo taken sideways (and no EXIF orientation): turn it upright and read it again, so the text is read
        # properly and the red pen can be drawn on it. At most twice, in case the model misjudged the first time.
        for _ in range(2):
            if not page["rotate"]:
                break
            log.info("submission %s page %s turned %s°", essay["id"], img["page"], page["rotate"])
            img = rotate_image(d, img, page["rotate"])
            page = read(img)
        page = read_for_boxes(read, img, page)
        if grading.reads_one_side(page, img["w"], img["h"]):
            # The right page may have been left out. A second reading that finds no more keeps the first.
            again = read(img, both_sides=True)
            if not grading.reads_one_side(again, img["w"], img["h"]):
                log.info("submission %s page %s: read again, now both sides", essay["id"], img["page"])
                page = again
        for row, side in split_spread(d, img, page):
            pages.append(calibrated(row, side))
            upright.append(row)
    images = upright
    title, text, spots, shapes = grading.assemble(pages)
    writing = grading.worst_writing(pages)
    if grading.char_count(text) < 10:
        raise PhotoProblem(photo_problem(pages, text))
    text = text[: grading.MAX_TEXT]
    spots = [[l for l in lines if l["e"] <= len(text)] for lines in spots]
    for img, lines in zip(images, spots):
        d.execute("UPDATE ess_images SET lines=? WHERE id=?", (json.dumps(lines), img["id"]))
    d.commit()
    shapes = verify_shapes(d, essay, [s for s in shapes if s["pos"] < len(text)], text, [(img["id"], lines) for img, lines in zip(images, spots)])
    # The title the child wrote on the paper wins: the form's title field (filled in with the assignment's name)
    # is only kept when none was read, so the grading sees whether the child made up a title of their own.
    title = title or essay["title"]
    d.execute(
        "UPDATE ess_submissions SET text=?,title=?,writing=?,shapes=? WHERE id=?",
        (text, title, writing, json.dumps(shapes, ensure_ascii=False) if shapes else "", essay["id"]),
    )
    d.commit()
    return {**essay, "text": text, "title": title, "writing": writing, "shapes": json.dumps(shapes, ensure_ascii=False) if shapes else ""}


def verify_step(d):
    """Who looks at the photo again: the verify step, or the ocr step's models while that has none."""
    return "verify" if llm.configured(d, "verify") else "ocr"


def verify_shapes(d, essay, shapes, text, pages):
    """The characters the ocr step says were written wrong (no character at all), looked at again on the photo:
    only the ones the photo check confirms are kept. A failed call keeps none (better miss one than mark a right one)."""
    from . import crop_line

    targets = grading.shape_targets(shapes, text, pages)
    if not targets:
        return []
    for n, t in enumerate(targets, 1):
        t["id"] = n
    try:
        crops = [crop_line(t["iid"], t["box"]) for t in targets]
        seen, _ = llm.chat(
            d,
            verify_step(d),
            grading.verify_messages(targets, crops),
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
    # 批改复核 is off unless switched on in 模型设置: every mark is kept and the review is published as usual.
    if not llm.check_enabled(d):
        review = {**review, "rejected": [], "check_level": None}
    else:
        # The checker runs even without error marks: it also gives its own level (llm.chat tries each model twice).
        # A review that could not be checked is not published by itself (run_job sends it to the teacher first).
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
    """Looks at the photo again for every 错别字 mark (grading.typo_targets): one the photo does not confirm becomes a
    书写提醒 (the original kept in review["rejected"] for the teacher), and the stored text is corrected where the
    photo shows the fix. A failed call leaves the review as it is."""
    from . import crop_line

    images = d.execute("SELECT id,lines FROM ess_images WHERE submission_id=? ORDER BY page", (essay["id"],)).fetchall()
    pages = [(img["id"], json.loads(img["lines"]) if img["lines"] else []) for img in images]
    targets = grading.typo_targets(review, essay["text"], pages)
    if not targets or not llm.configured(d, verify_step(d)):
        return review
    try:
        crops = [crop_line(t["iid"], t["box"]) for t in targets]
        seen, _ = llm.chat(
            d,
            verify_step(d),
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
        log.info("submission %s: %s 错别字 mark(s) not confirmed on the photo", essay["id"], len(dropped))
        d.execute("UPDATE ess_submissions SET text=? WHERE id=?", (text, essay["id"]))
        d.commit()
        review = {**review, "rejected": review.get("rejected", []) + [{**m, "reason": grading.VERIFY_REASON} for m in dropped]}
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
            fail = e.code if isinstance(e, PhotoProblem) else "系统"
            d.execute("UPDATE ess_submissions SET status='failed',fail=? WHERE id=?", (fail, essay["id"]))
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
