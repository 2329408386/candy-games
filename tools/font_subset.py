"""Builds the red-pen font of 语文老师工作台 (sites/teacher/static/redpen-font.woff2).

The font is 龙藏体 (Long Cang, SIL Open Font License 1.1, https://github.com/googlefonts/longcang), a casual
hard-pen handwriting that reads like a teacher's red pen rather than print; it covers all of GB2312. It is
cut down to the 6763 characters of GB2312 plus ASCII and Chinese punctuation, so a teacher's comments, corrections
and scores render in handwriting on every phone without a 5 MB download. Characters outside the subset fall back
to the system's 楷体 (see .redpen in essay.css).

Run once when changing the character set (needs fonttools and brotli, which the server does not), then let
tools/assets.py fingerprint the new file (delete the old redpen-font.<hash>.woff2 first):
    python tools/font_subset.py path/to/LongCang-Regular.ttf && python tools/assets.py --fix
The font's license is sites/teacher/static/redpen-font-OFL.txt.
"""

import sys
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "sites" / "teacher" / "static" / "redpen-font.woff2"
EXTRA = "，。、；：？！“”‘’（）《》【】—…·「」『』～￥％＋－＝×÷→←↑↓√✓✗○●△□☆★①②③④⑤⑥⑦⑧⑨⑩"


def charset():
    chars = {chr(c) for c in range(0x20, 0x7F)} | set(EXTRA)
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes([hi, lo]).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    return "".join(sorted(chars))


def main(source):
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["*"]
    options.name_IDs = ["*"]  # keeps the copyright and license notices
    options.hinting = False
    font = subset.load_font(source, options)
    sub = subset.Subsetter(options)
    sub.populate(text=charset())
    sub.subset(font)
    subset.save_font(font, str(OUT), options)
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
