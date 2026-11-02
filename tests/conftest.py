import json, os, re, sys, tempfile, time
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory(prefix="candy-games-test-")
os.environ.update(GAME_DATA=TMP.name, CHINESE_SECRET="c" * 64, MATH_SECRET="m" * 64, ENGLISH_SECRET="e" * 64)
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash
from werkzeug.test import Client
from core import schema
from core.app import digest
import wsgi
from sites import SITES

PASSWORD = "test-password-123"
KEYS = {"chinese": "CHINESEKEYAAAAAA", "math": "MATHKEYAAAAAAAAA", "english": "ENGLISHKEYAAAAAA", "teacher": "TEACHERKEYAAAAAA"}
TEACHER2_KEY = "TEACHERKEYBBBBBB"
PREFIX = {"chinese": "", "math": "/math", "english": "/english", "teacher": "/teacher"}
FLASK = {"chinese": wsgi.chinese, "math": wsgi.math, "english": wsgi.english}


def _seed():
    schema.initialize(Path(TMP.name) / schema.DB_NAME)
    import sqlite3

    d = sqlite3.connect(Path(TMP.name) / schema.DB_NAME)
    from core.app import GRADES

    for key, site in SITES.items():
        d.executemany(
            "INSERT INTO game_groups VALUES(?,?,?,?,0)", [(key, grade + "-a", grade, "A组") for grade in GRADES]
        )
        d.executemany("INSERT INTO game_memberships VALUES(?,?,?)", [(key, game, "g3-t1-a") for game in site.games])
        d.execute("INSERT INTO admin VALUES(?,?,0,1)", (key, generate_password_hash(PASSWORD)))
        d.execute(
            "INSERT INTO generation_batches VALUES(?,?,?,?,?,?,?)",
            (key, "historical-batch", "[]", 2, 3, 1, '["g3-t1"]'),
        )
        d.execute("INSERT INTO batch_groups VALUES(?,?,?)", (key, "historical-batch", "g3-t1-a"))
        for lid, code in ((1, KEYS[key]), (2, key.upper().ljust(12, "B") + "0002")):
            d.execute(
                "INSERT INTO licenses(site,id,key_hash,games,buyer,created_at,device_limit,batch_id,grade_scopes) VALUES(?,?,?,'[]',?,1,3,'historical-batch','[\"g3-t1\"]')",
                (key, lid, digest(code), "测试备注" + str(lid)),
            )
            d.execute("INSERT INTO license_keys VALUES(?,?,?)", (key, lid, code))
            d.execute("INSERT INTO license_groups VALUES(?,?,?)", (key, lid, "g3-t1-a"))
        d.execute(
            "INSERT INTO licenses(site,id,key_hash,games,enabled,created_at,grade_scopes,archived_at) VALUES(?,3,?,?,0,1,'[]',2)",
            (key, digest(key + "ARCHIVED"), json.dumps([next(iter(site.games))])),
        )
        d.execute("INSERT INTO license_keys VALUES(?,3,?)", (key, key.upper() + "ARCHIVED"))
    # 语文老师工作台: its license group is created by create_app (seed_workbench); two teachers.
    d.execute("INSERT INTO admin VALUES('teacher',?,0,1)", (generate_password_hash(PASSWORD),))
    for lid, code in ((1, KEYS["teacher"]), (2, TEACHER2_KEY)):
        d.execute(
            "INSERT INTO licenses(site,id,key_hash,games,buyer,created_at,device_limit) VALUES('teacher',?,?,'[]','老师',1,100)",
            (lid, digest(code)),
        )
        d.execute("INSERT INTO license_keys VALUES('teacher',?,?)", (lid, code))
        d.execute("INSERT INTO license_groups VALUES('teacher',?,'g1-t1-a')", (lid,))
    d.commit()
    d.close()


_seed()


def _app_with_addr(environ, start):
    environ.setdefault("REMOTE_ADDR", "127.0.0.1")
    return wsgi.app(environ, start)


class SiteClient:
    """Talks to the merged WSGI app exactly as nginx does: real '/math' and '/english' prefixes, one process."""

    def __init__(self, site):
        self.site = site
        self.prefix = PREFIX[site]
        self.c = Client(_app_with_addr)

    def get(self, path, **kw):
        return self.c.get(self.prefix + path, **kw)

    def token(self):
        return self.get("/api/csrf").get_json()["csrf"]

    def post(self, path, data=None, **kw):
        token = self.token()
        if "json" in kw:
            return self.c.post(self.prefix + path, headers={"X-CSRF-Token": token}, **kw)
        return self.c.post(self.prefix + path, data={**(data or {}), "csrf": token}, **kw)

    def activate(self, key=None):
        return self.post("/api/activate", json={"key": key or KEYS[self.site]})

    def login(self):
        return self.post("/admin/login", {"username": "admin", "password": PASSWORD})


def other_site(site):
    """Some other site than this one (for isolation checks)."""
    return "math" if site == "chinese" else "chinese"


def bank_size(site, game):
    return len(json.loads((ROOT / "sites" / site / "banks" / f"{game}.json").read_text(encoding="utf-8"))["questions"])


@pytest.fixture(params=["chinese", "math", "english"])
def site(request):
    return request.param


@pytest.fixture
def client(site):
    return SiteClient(site)


@pytest.fixture
def admin(site):
    c = SiteClient(site)
    assert c.login().status_code == 302
    return c


@pytest.fixture
def db():
    import sqlite3

    d = sqlite3.connect(Path(TMP.name) / schema.DB_NAME)
    d.row_factory = sqlite3.Row
    yield d
    d.close()
