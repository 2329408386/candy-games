"""manage.py: backups keep working when an old one cannot be removed; root runs leave nothing owned by root."""
import os, shutil, sqlite3, time
import pytest
import conftest  # noqa: F401 (puts the project on sys.path)
import manage
from core import schema


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("GAME_DATA", str(tmp_path))
    sqlite3.connect(tmp_path / schema.DB_NAME).close()
    return tmp_path


def test_backup_prunes_old_and_skips_what_it_cannot_remove(data, monkeypatch, capsys):
    root = data / "backups"
    root.mkdir()
    old = time.time() - 4 * 86400
    for name in ("20200101-000000", "20200102-000000"):
        (root / name).mkdir()
        os.utime(root / name, (old, old))
    (root / "20990101-000000").mkdir()  # recent: kept
    real = shutil.rmtree

    def rmtree(path, *a, **kw):
        if path.name == "20200101-000000":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path, *a, **kw)

    monkeypatch.setattr(manage.shutil, "rmtree", rmtree)
    manage.backup()
    names = sorted(p.name for p in root.iterdir())
    assert "20200101-000000" in names and "20200102-000000" not in names and "20990101-000000" in names
    assert len(names) == 3, "the new backup was made"
    out = capsys.readouterr()
    assert "备份完成" in out.out and "旧备份删不掉" in out.err


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root")
def test_root_run_hands_files_to_the_data_owner(data):
    uid, gid = 4321, 4321
    os.chown(data, uid, gid)
    (data / "backups" / "x").mkdir(parents=True)
    (data / "backups" / "x" / "games.sqlite3").write_text("")
    manage.give_back_to_owner()
    for path in [data, *data.rglob("*")]:
        st = path.lstat()
        assert (st.st_uid, st.st_gid) == (uid, gid), path
