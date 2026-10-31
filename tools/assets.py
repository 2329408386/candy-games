"""Fingerprint static assets so browsers can cache them for a year and still pick up every change.

  python tools/assets.py --check   exit 1 if any reference is stale (run by tests and before deploy)
  python tools/assets.py --fix     rename media to name.<sha8>.ext and rewrite every reference

Media (mp3/images) get the content hash in the file name; js/css references get ?v=<sha8>.
"""

import argparse, hashlib, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = {".mp3", ".webp", ".jpg", ".jpeg", ".png", ".svg", ".gif"}
TEXT = {".html", ".css", ".js", ".json"}
HASHED = re.compile(r"^(?P<stem>.+)\.(?P<hash>[0-9a-f]{8})(?P<ext>\.[^.]+)$")
SCRIPT_REF = re.compile(r'static/(?P<rel>[\w./-]+?\.(?:js|css))(?:\?v=[\w.-]+)?(?=["\')\s])')


def sha8(data):
    return hashlib.sha256(data).hexdigest()[:8]


def static_dirs():
    return sorted(ROOT.glob("sites/*/static")) + [ROOT / "shared-static"]


def text_files():
    roots = [ROOT / "core" / "templates"] + sorted(ROOT.glob("sites/*")) + [ROOT / "shared-static"]
    return sorted({p for r in roots for p in r.rglob("*") if p.is_file() and p.suffix in TEXT})


def plan():
    """Return (renames {old_path: new_path}, rewritten {text_path: new_content})."""
    media = [p for d in static_dirs() for p in d.rglob("*") if p.is_file() and p.suffix.lower() in MEDIA]
    names = {}
    for p in media:
        if p.name in names:
            raise SystemExit(f"媒体文件重名，无法安全改写引用：{p} 与 {names[p.name]}")
        names[p.name] = p
    renames = {}
    for p in media:
        m = HASHED.match(p.name)
        stem, ext = (m["stem"], m["ext"]) if m else (p.stem, p.suffix)
        target = p.with_name(f"{stem}.{sha8(p.read_bytes())}{ext}")
        if target != p:
            renames[p] = target
    contents = {p: p.read_text(encoding="utf-8") for p in text_files()}
    updated = dict(contents)
    for old, new in renames.items():
        pattern = re.compile(r'(?<=[/\'"(])' + re.escape(old.name) + r'(?=[?\'")#\s])')
        for p, text in updated.items():
            updated[p] = pattern.sub(new.name, text)

    def site_static(p):
        rel = p.relative_to(ROOT).parts
        return ROOT / "sites" / rel[1] / "static" if rel[0] == "sites" else None

    for _ in range(5):  # css may reference other fingerprinted files; iterate until stable
        changed = False
        for p, text in updated.items():
            base = site_static(p)
            if not base:
                continue

            def version(m):
                target = base / m["rel"]
                if not target.is_file():
                    raise SystemExit(f'{p.relative_to(ROOT)} 引用了不存在的文件 static/{m["rel"]}')
                data = updated[target].encode() if target in updated else target.read_bytes()
                return f'static/{m["rel"]}?v={sha8(data)}'

            new = SCRIPT_REF.sub(version, text)
            if new != text:
                updated[p] = new
                changed = True
        if not changed:
            break
    return renames, {p: t for p, t in updated.items() if t != contents[p]}


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--fix", action="store_true")
    a = p.parse_args()
    renames, rewritten = plan()
    for old, new in renames.items():
        print("rename ", old.relative_to(ROOT), "->", new.name)
    for path in rewritten:
        print("rewrite", path.relative_to(ROOT))
    if a.check:
        if renames or rewritten:
            print("静态资源指纹已过期，请运行 python tools/assets.py --fix")
            sys.exit(1)
        print("静态资源指纹全部最新")
        return
    for path, text in rewritten.items():
        path.write_text(text, encoding="utf-8")
    for old, new in renames.items():
        old.rename(new)
    print("完成" if renames or rewritten else "无需修改")


if __name__ == "__main__":
    main()
