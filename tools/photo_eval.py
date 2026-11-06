"""Measures core/essay/photo.py and the shape scan on photos already handed in, before they are switched on. Read-only:
the database is opened read-only, nothing is stored and nothing is logged to ess_usage. It prints numbers only, no
essay text and no picture leaves the server (--scan calls the configured models, a few cents on the bill).

    GAME_DATA=/var/lib/candy-games python tools/photo_eval.py [--pages 30] [--scan] [--timeout 300]

Needs the site secret in the environment for --scan (EnvironmentFile=/var/lib/candy-games/secret.env).

Always, without any model, as the worker would do it from the stored line boxes: how many photos would be cut
(photo_rectify; "refused": paper was found but a line lies outside it) and how far the boxes would then move on the
page (photo_snap), with the share of the ink they cover before and after, and how far the rows chosen end from the
line ends the model gave (high would mean a page one row off).
--scan: runs the shape scan and its confirmation on each page: candidates, confirmed, tokens and seconds, for the cost.
       Which characters is not printed (students' work); judging whether they are right needs a person.
"""

import argparse, json, sqlite3, statistics, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.essay import grading, llm, photo  # noqa: E402


def stored_lines(img):
    return [{"text": "", "box": s["b"]} for s in (json.loads(img["lines"]) if img["lines"] else [])]


def model_row(d, step, timeout):
    row = next(iter(llm.step_rows(d, step)), None)
    return {**row, "timeout": timeout} if row else None


def ask(row, messages):
    """One call outside llm.chat, so that nothing is logged: (raw JSON, tokens in, tokens out, seconds)."""
    started = time.time()
    content, tin, _, tout = llm.post(row, llm.request_body(row, messages, True))
    return llm.parse_json(content), tin, tout, time.time() - started


def main():
    from core.app import data_dir
    from core.essay import crop_line, image_path

    p = argparse.ArgumentParser()
    p.add_argument("--pages", type=int, default=30, help="the newest N photos")
    p.add_argument("--scan", action="store_true")
    p.add_argument("--timeout", type=int, default=300)
    args = p.parse_args()

    d = sqlite3.connect(f"file:{data_dir() / 'games.sqlite3'}?mode=ro", uri=True)
    d.row_factory = sqlite3.Row
    images = d.execute("SELECT * FROM ess_images ORDER BY id DESC LIMIT ?", (args.pages,)).fetchall()
    look_row = model_row(d, llm.look_step(d), args.timeout) if args.scan else None
    if args.scan and not look_row:
        raise SystemExit("这一步没有配置模型")

    cut, kept, seconds = 0, 0, []
    snapped, unsnapped, shifts, before, after = 0, 0, [], [], []
    refused, ends = 0, []
    scan = {"pages": 0, "fail": 0, "candidates": 0, "confirmed": 0, "tin": 0, "tout": 0, "seconds": 0.0}
    for img in images:
        data = image_path(img["id"]).read_bytes()
        label = f"作文 #{img['submission_id']} 第 {img['page']} 页（{img['w']}×{img['h']}）"
        lines = stored_lines(img)
        started = time.time()
        try:
            flat = photo.rectify(data, lines)
            found = not flat and photo.find_paper(photo._decode(data)) is not None
        except Exception as e:
            flat, found = None, False
            print(f"{label}: 裁正出错 {e!r}")
        seconds.append(time.time() - started)
        cut, kept, refused = cut + bool(flat), kept + (not flat), refused + bool(found)
        line = f"{label}: " + (f"裁正为 {flat[1]}×{flat[2]}" if flat else "有行在纸外，不裁" if found else "不裁")
        if lines:
            try:
                _, stats = photo.snap_lines(flat[0], flat[3]) if flat else photo.snap_lines(data, lines)
                if stats["matched"] >= photo._MIN_MATCHED * stats["lines"] and stats["after"]:
                    snapped += 1
                    shifts.append(abs(stats["shift"]))
                    before.append(stats["before"])
                    after.append(stats["after"])
                    ends.append(stats["ends"])
                else:
                    unsnapped += 1
                line += f"，行框 {stats['matched']}/{stats['lines']} 行对上，平移 {stats['shift']}‰，墨迹覆盖 {stats['before']:.2f}→{stats['after']:.2f}，行尾差 {stats['ends']}‰"
            except Exception as e:
                unsnapped += 1
                line += f"，校准出错 {e!r}"
        print(line)
        spots = json.loads(img["lines"]) if img["lines"] else []
        if look_row and spots:
            text = d.execute("SELECT text FROM ess_submissions WHERE id=?", (img["submission_id"],)).fetchone()[0]
            numbered = list(enumerate(spots, 1))
            try:
                sheets = []
                for at in range(0, len(numbered), grading.SCAN_ROWS):
                    part = numbered[at : at + grading.SCAN_ROWS]
                    numbers = [k for k, _ in part]
                    sheets.append((numbers, photo.stitch_lines([crop_line(img["id"], s["b"]) for _, s in part], numbers)))
                raw, tin, tout, spent = ask(look_row, grading.scan_messages([(k, text[s["s"] : s["e"]]) for k, s in numbered], sheets))
                found = grading.scan_shapes(grading.parse_scan(raw), text, spots, 0)
                scan["pages"] += 1
                scan["candidates"] += len(found)
                scan["tin"] += tin
                scan["tout"] += tout
                scan["seconds"] += spent
                confirmed = 0
                targets = grading.shape_targets(found, text, [(img["id"], spots)])
                for n, t in enumerate(targets, 1):
                    t["id"] = n
                if targets:
                    crops = [crop_line(t["iid"], t["box"]) for t in targets]
                    close = [(photo.crop_char(data, t["box"], t["line"], t["nth"]), photo.glyph(t["char"])) for t in targets]
                    raw, tin, tout, spent = ask(look_row, grading.verify_messages(targets, crops, close))
                    seen = grading.parse_verify(raw, len(targets))
                    confirmed = len(grading.keep_shapes(found, targets, seen))
                    scan["confirmed"] += confirmed
                    scan["tin"] += tin
                    scan["tout"] += tout
                    scan["seconds"] += spent
                print(f"  筛查：{len(found)} 个可疑字，确认写错 {confirmed} 个")
            except (llm.LLMError, OSError, ValueError) as e:
                scan["fail"] += 1
                print(f"  筛查失败：{e}")

    print("\n== 合计")
    if not images:
        print("没有照片")
        return
    print(f"裁正：{len(images)} 张里裁 {cut} 张，不裁 {kept} 张；每张 {statistics.mean(seconds):.2f} 秒")
    print(f"  找到作文纸但有行在纸外、因此不裁的 {refused} 张")
    print(f"行框校准：{snapped} 张校准，{unsnapped} 张保留原框")
    if shifts:
        print(f"  平移中位数 {statistics.median(shifts)}‰，最大 {max(shifts)}‰；墨迹覆盖中位数 {statistics.median(before):.2f}→{statistics.median(after):.2f}；行尾差中位数 {statistics.median(ends)}‰，最大 {max(ends)}‰")
    if args.scan:
        print(f"筛查：{scan['pages']} 页（失败 {scan['fail']}），可疑 {scan['candidates']} 个，确认 {scan['confirmed']} 个；输入 {scan['tin']} / 输出 {scan['tout']} token，共 {scan['seconds']:.0f} 秒")


if __name__ == "__main__":
    main()
