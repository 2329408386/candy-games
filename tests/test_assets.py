import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_asset_fingerprints_are_current():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "assets.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_math_question_banks():
    import shutil, pytest

    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(
        ["node", "test_math_common.cjs"], cwd=ROOT / "sites" / "math" / "tests", capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout + r.stderr
