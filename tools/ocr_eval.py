"""Compares ways of running the ocr step on photos already handed in: by default the configured main OCR model as
it is now (thinking on) and with thinking switched off. Read-only: the database is opened read-only and nothing is
logged to ess_usage (the calls cost a few cents on the provider's bill).

    GAME_DATA=/var/lib/candy-games python tools/ocr_eval.py [--pages 10] [--timeout 300] [--slot main]
        [--variant '名称={"enable_thinking": false}' ...]

Needs the site secret in the environment (as the worker has: EnvironmentFile=/var/lib/candy-games/secret.env).
For every page it prints each variant's time, output tokens, lines with a box, and how far its text is from the
text stored for that page (what grading used) and from the first variant; then the places where the variants differ.
"""

import argparse, difflib, json, sqlite3, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.essay import grading, llm  # noqa: E402

DEFAULT_VARIANTS = [("现在（开思考）", {}), ("关思考", {"enable_thinking": False})]


def stored_page_text(d, img):
    """The part of the submission's text this photo holds (from the stored line positions)."""
    text = d.execute("SELECT text FROM ess_submissions WHERE id=?", (img["submission_id"],)).fetchone()[0]
    spots = json.loads(img["lines"]) if img["lines"] else []
    if not spots:
        return ""
    return text[min(s["s"] for s in spots) : max(s["e"] for s in spots)]


def page_text(page):
    return "".join(line["text"] for line in page["lines"])


def distance(a, b):
    """Characters that differ (inserted, deleted or replaced) between two texts, whitespace ignored."""
    a, b = "".join(a.split()), "".join(b.split())
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if tag != "equal")


def differences(a, b, limit=8):
    a, b = "".join(a.split()), "".join(b.split())
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag != "equal":
            out.append(f"…{a[max(0, i1 - 4):i1]}[{a[i1:i2]}→{b[j1:j2]}]{a[i2:i2 + 4]}…")
    return out[:limit]


def main():
    from core.app import data_dir
    from core.essay import image_path

    p = argparse.ArgumentParser()
    p.add_argument("--pages", type=int, default=10, help="the newest N photos")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--slot", default="main")
    p.add_argument("--variant", action="append", default=[], help='名称={"key": value} merged into the request')
    args = p.parse_args()
    variants = [(v.split("=", 1)[0], json.loads(v.split("=", 1)[1])) for v in args.variant] or DEFAULT_VARIANTS

    d = sqlite3.connect(f"file:{data_dir() / 'games.sqlite3'}?mode=ro", uri=True)
    d.row_factory = sqlite3.Row
    row = next((r for r in llm.step_rows(d, "ocr") if r["slot"] == args.slot), None)
    if not row:
        raise SystemExit("ocr step has no such slot")
    row = {**row, "timeout": args.timeout}
    print(f"模型：{row['provider_name']} / {row['model']}，额外参数：{row['extra'] or '无'}，超时 {args.timeout} 秒")
    images = d.execute("SELECT * FROM ess_images ORDER BY id DESC LIMIT ?", (args.pages,)).fetchall()
    totals = {name: {"seconds": 0.0, "out": 0, "fail": 0, "dist": 0} for name, _ in variants}
    for img in images:
        n = d.execute("SELECT count(*) FROM ess_images WHERE submission_id=?", (img["submission_id"],)).fetchone()[0]
        messages = grading.ocr_messages(image_path(img["id"]).read_bytes(), img["page"], n)
        stored = stored_page_text(d, img)
        print(f"\n== 作文 #{img['submission_id']} 第 {img['page']} 页（{img['w']}×{img['h']}），已存文字 {len(stored)} 字")
        texts = []
        for name, extra in variants:
            body = llm.request_body(row, messages, True)
            body.update(extra)
            started = time.time()
            try:
                content, tin, _, tout = llm.post(row, body)
                page = grading.parse_ocr(llm.parse_json(content))
            except (llm.LLMError, ValueError) as e:
                seconds = time.time() - started
                totals[name]["fail"] += 1
                totals[name]["seconds"] += seconds
                print(f"  {name}: 失败（{seconds:.0f} 秒）{e}")
                texts.append(None)
                continue
            seconds = time.time() - started
            text = page_text(page)
            boxed = sum(1 for line in page["lines"] if line["box"])
            dist = distance(stored, text) if stored else None
            totals[name]["seconds"] += seconds
            totals[name]["out"] += tout
            totals[name]["dist"] += dist or 0
            first = next((t for t in texts if t is not None), None)
            vs_first = f"，和「{variants[0][0]}」差 {distance(first, text)} 字" if first is not None and texts else ""
            print(f"  {name}: {seconds:.0f} 秒，输出 {tout} token，{len(page['lines'])} 行（{boxed} 行有位置），和已存文字差 {dist} 字{vs_first}")
            texts.append(text)
        if len(texts) >= 2 and texts[0] is not None and texts[1] is not None:
            for diff in differences(texts[0], texts[1]):
                print("    " + diff)
    print("\n== 合计")
    for name, t in totals.items():
        print(f"  {name}: 共 {t['seconds']:.0f} 秒，输出 {t['out']} token，失败 {t['fail']} 次，和已存文字共差 {t['dist']} 字")


if __name__ == "__main__":
    main()
