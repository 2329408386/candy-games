"""Recordings for the English listening questions (sites/english/static/audio/).

  python tools/english_audio.py            synthesize every recording a bank names that does not exist yet
  python tools/english_audio.py --all      synthesize them all again (e.g. after changing the voice)

Each question with "audio" names an mp3 and holds its transcript in "audioText". New files are written without a
content hash; run `python tools/assets.py --fix` afterwards, which renames them and updates the banks.

Runs only on a development machine, never on the server. It needs Kokoro-82M (Apache-2.0) and an mp3 encoder:
  uv venv /tmp/tts && VIRTUAL_ENV=/tmp/tts uv pip install kokoro soundfile lameenc \\
      "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
  /tmp/tts/bin/python tools/english_audio.py
"""

import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = "english"
AUDIO = ROOT / "sites" / SITE / "static" / "audio"
VOICE, RATE = "af_heart", 24000
# Words Kokoro reads wrongly or too weakly on their own: the letter A comes out as the article "uh", and the
# function words lose their stress. Kokoro's [word](/phonemes/) syntax pins the pronunciation.
PRONOUNCE = {"A": "[A](/ˈA/)", "he": "[he](/hˈi/)", "she": "[she](/ʃˈi/)", "I'm": "[I'm](/ˈIm/)"}


def wanted():
    """{file name: transcript} for every recording the English banks use."""
    sys.path.insert(0, str(ROOT))
    from sites import SITES

    files = {}
    for game in SITES[SITE].games:
        bank = json.loads((ROOT / "sites" / SITE / "banks" / f"{game}.json").read_text(encoding="utf-8"))
        for q in bank["questions"]:
            if "audio" in q:
                if files.setdefault(q["audio"], q["audioText"]) != q["audioText"]:
                    raise SystemExit(f"{q['audio']} 对应了两段不同的文字")
    return files


def synthesize(pipeline, text):
    import numpy as np

    single = " " not in text.strip()
    chunks = [a.numpy() if hasattr(a, "numpy") else a for _, _, a in pipeline(PRONOUNCE.get(text, text), voice=VOICE, speed=0.85 if single else 0.9)]
    audio = np.concatenate(chunks)
    loud = np.flatnonzero(np.abs(audio) > 0.02)
    audio = audio[max(0, loud[0] - int(0.03 * RATE)) : loud[-1] + int(0.08 * RATE)]
    audio = audio / max(1e-6, np.abs(audio).max()) * 0.89  # about -1 dBFS
    pad = lambda s: np.zeros(int(s * RATE), dtype=audio.dtype)  # noqa: E731
    return np.concatenate([pad(0.12), audio, pad(0.2)])


def encode(audio):
    import lameenc, numpy as np

    enc = lameenc.Encoder()
    enc.set_bit_rate(48)
    enc.set_in_sample_rate(RATE)
    enc.set_channels(1)
    enc.set_quality(2)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    return enc.encode(pcm) + enc.flush()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--all", action="store_true", help="synthesize every recording again")
    a = p.parse_args()
    files = wanted()
    todo = {name: text for name, text in files.items() if a.all or not (AUDIO / name).is_file()}
    if not todo:
        print(f"{len(files)} 段录音都已存在")
        return
    from kokoro import KPipeline

    pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M")
    AUDIO.mkdir(parents=True, exist_ok=True)
    for i, (name, text) in enumerate(sorted(todo.items()), 1):
        (AUDIO / name).write_bytes(encode(synthesize(pipeline, text)))
        print(f"[{i}/{len(todo)}] {name}  ←  {text}")
    print("完成；请运行 python tools/assets.py --fix 给新文件加上内容哈希")


if __name__ == "__main__":
    main()
