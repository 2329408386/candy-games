import sqlite3, time
from pathlib import Path
from . import ranks

DB_NAME = "games.sqlite3"
# Every table is scoped by site ('chinese', 'math' or 'english'); ids keep their original per-site numbering.
# Permissions and grades come from license_groups -> game_groups. licenses.grade_scopes and
# generation_batches.grade_scopes are deprecated mirrors: still written so older releases can be rolled back to,
# never read.
# ess_*: 语文老师工作台 (site 'teacher' only, so no site column), see core/essay/__init__.py.
# migrations: one-off data changes already applied (see initialize).
# trials records the one free round each browser (session device token) may play per game.
# profiles/classes/scores/rounds: see core/social.py (one license = one user, in at most one class); pk_rooms/pk_seats: core/rooms.py (rooms/room_players are the unused, license-keyed predecessors).
SCHEMA = """
CREATE TABLE IF NOT EXISTS licenses(site TEXT NOT NULL,id INTEGER NOT NULL,key_hash TEXT NOT NULL,games TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,buyer TEXT NOT NULL DEFAULT '',assigned_at INTEGER,created_at INTEGER NOT NULL,device_limit INTEGER NOT NULL DEFAULT 3 CHECK(device_limit BETWEEN 1 AND 100),batch_id TEXT,grade_scopes TEXT NOT NULL DEFAULT '[]',archived_at INTEGER,PRIMARY KEY(site,id),UNIQUE(site,key_hash));
CREATE INDEX IF NOT EXISTS licenses_batch_id ON licenses(site,batch_id);
CREATE TABLE IF NOT EXISTS license_keys(site TEXT NOT NULL,license_id INTEGER NOT NULL,code TEXT NOT NULL,PRIMARY KEY(site,license_id),UNIQUE(site,code),FOREIGN KEY(site,license_id) REFERENCES licenses(site,id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS devices(site TEXT NOT NULL,license_id INTEGER NOT NULL,token_hash TEXT NOT NULL,label TEXT NOT NULL,created_at INTEGER NOT NULL,last_seen INTEGER NOT NULL,PRIMARY KEY(site,license_id,token_hash),FOREIGN KEY(site,license_id) REFERENCES licenses(site,id));
CREATE TABLE IF NOT EXISTS admin(site TEXT PRIMARY KEY,password TEXT NOT NULL,must_change INTEGER NOT NULL DEFAULT 1,version INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,site TEXT NOT NULL,created_at INTEGER NOT NULL,action TEXT NOT NULL,license_id INTEGER,details TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS audit_license ON audit(site,license_id);
CREATE TABLE IF NOT EXISTS limits(bucket TEXT PRIMARY KEY,count INTEGER NOT NULL,until INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS generation_batches(site TEXT NOT NULL,id TEXT NOT NULL,games TEXT NOT NULL,quantity INTEGER NOT NULL,device_limit INTEGER NOT NULL,created_at INTEGER NOT NULL,grade_scopes TEXT NOT NULL DEFAULT '[]',PRIMARY KEY(site,id));
CREATE TABLE IF NOT EXISTS game_groups(site TEXT NOT NULL,id TEXT NOT NULL,grade TEXT NOT NULL,name TEXT NOT NULL,position INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(site,id),UNIQUE(site,grade,name));
CREATE TABLE IF NOT EXISTS game_memberships(site TEXT NOT NULL,game TEXT NOT NULL,group_id TEXT NOT NULL,PRIMARY KEY(site,game),FOREIGN KEY(site,group_id) REFERENCES game_groups(site,id));
CREATE TABLE IF NOT EXISTS license_groups(site TEXT NOT NULL,license_id INTEGER NOT NULL,group_id TEXT NOT NULL,PRIMARY KEY(site,license_id,group_id),FOREIGN KEY(site,license_id) REFERENCES licenses(site,id),FOREIGN KEY(site,group_id) REFERENCES game_groups(site,id));
CREATE INDEX IF NOT EXISTS license_group_lookup ON license_groups(site,group_id,license_id);
CREATE TABLE IF NOT EXISTS trials(site TEXT NOT NULL,device_hash TEXT NOT NULL,game TEXT NOT NULL,started_at INTEGER NOT NULL,PRIMARY KEY(site,device_hash,game));
CREATE TABLE IF NOT EXISTS batch_groups(site TEXT NOT NULL,batch_id TEXT NOT NULL,group_id TEXT NOT NULL,PRIMARY KEY(site,batch_id,group_id),FOREIGN KEY(site,batch_id) REFERENCES generation_batches(site,id),FOREIGN KEY(site,group_id) REFERENCES game_groups(site,id));
CREATE TABLE IF NOT EXISTS classes(site TEXT NOT NULL,id INTEGER NOT NULL,code TEXT NOT NULL,name TEXT NOT NULL,owner INTEGER NOT NULL,created_at INTEGER NOT NULL,PRIMARY KEY(site,id),UNIQUE(site,code));
CREATE TABLE IF NOT EXISTS profiles(site TEXT NOT NULL,license_id INTEGER NOT NULL,nickname TEXT NOT NULL DEFAULT '',avatar BLOB,avatar_ver INTEGER NOT NULL DEFAULT 0,class_id INTEGER,PRIMARY KEY(site,license_id),FOREIGN KEY(site,license_id) REFERENCES licenses(site,id),FOREIGN KEY(site,class_id) REFERENCES classes(site,id));
CREATE INDEX IF NOT EXISTS profiles_class ON profiles(site,class_id);
CREATE TABLE IF NOT EXISTS scores(id INTEGER PRIMARY KEY,site TEXT NOT NULL,license_id INTEGER NOT NULL,game TEXT NOT NULL,mode TEXT NOT NULL,points INTEGER NOT NULL,created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS scores_license ON scores(site,license_id,created_at);
CREATE INDEX IF NOT EXISTS scores_time ON scores(site,created_at);
CREATE TABLE IF NOT EXISTS rooms(site TEXT NOT NULL,code TEXT NOT NULL,game TEXT NOT NULL,host INTEGER NOT NULL,class_id INTEGER,status TEXT NOT NULL,seconds INTEGER NOT NULL,question_ids TEXT NOT NULL,version INTEGER NOT NULL,created_at INTEGER NOT NULL,started_at INTEGER NOT NULL,deadline INTEGER NOT NULL,PRIMARY KEY(site,code));
CREATE INDEX IF NOT EXISTS rooms_class ON rooms(site,class_id,status);
CREATE TABLE IF NOT EXISTS room_players(site TEXT NOT NULL,code TEXT NOT NULL,license_id INTEGER NOT NULL,score INTEGER NOT NULL DEFAULT 0,combo INTEGER NOT NULL DEFAULT 0,correct INTEGER NOT NULL DEFAULT 0,answered INTEGER NOT NULL DEFAULT 0,bonus INTEGER NOT NULL DEFAULT 0,last_answer REAL NOT NULL DEFAULT 0,joined_at INTEGER NOT NULL,last_seen INTEGER NOT NULL,PRIMARY KEY(site,code,license_id));
CREATE TABLE IF NOT EXISTS pk_rooms(site TEXT NOT NULL,code TEXT NOT NULL,game TEXT NOT NULL,host INTEGER NOT NULL,host_seat TEXT NOT NULL,host_device TEXT NOT NULL,class_id INTEGER,status TEXT NOT NULL,seconds INTEGER NOT NULL,question_ids TEXT NOT NULL,version INTEGER NOT NULL,created_at INTEGER NOT NULL,started_at INTEGER NOT NULL,deadline INTEGER NOT NULL,PRIMARY KEY(site,code));
CREATE INDEX IF NOT EXISTS pk_rooms_class ON pk_rooms(site,class_id,status);
CREATE INDEX IF NOT EXISTS pk_rooms_host ON pk_rooms(site,host,status);
CREATE TABLE IF NOT EXISTS pk_seats(site TEXT NOT NULL,code TEXT NOT NULL,seat TEXT NOT NULL,license_id INTEGER NOT NULL,device TEXT NOT NULL,score INTEGER NOT NULL DEFAULT 0,combo INTEGER NOT NULL DEFAULT 0,correct INTEGER NOT NULL DEFAULT 0,answered INTEGER NOT NULL DEFAULT 0,bonus INTEGER NOT NULL DEFAULT 0,counted INTEGER NOT NULL DEFAULT 0,last_answer REAL NOT NULL DEFAULT 0,joined_at INTEGER NOT NULL,last_seen INTEGER NOT NULL,PRIMARY KEY(site,code,seat),UNIQUE(site,code,device));
CREATE TABLE IF NOT EXISTS rounds(site TEXT NOT NULL,id TEXT NOT NULL,license_id INTEGER NOT NULL,game TEXT NOT NULL,started_at INTEGER NOT NULL,PRIMARY KEY(site,id));
CREATE TABLE IF NOT EXISTS migrations(name TEXT PRIMARY KEY,done_at INTEGER NOT NULL);
""" + """
CREATE TABLE IF NOT EXISTS ess_quota(license_id INTEGER PRIMARY KEY,students INTEGER NOT NULL,monthly INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ess_classes(id INTEGER PRIMARY KEY,license_id INTEGER NOT NULL,name TEXT NOT NULL,grade INTEGER NOT NULL CHECK(grade BETWEEN 1 AND 6),code TEXT NOT NULL UNIQUE,created_at INTEGER NOT NULL,archived_at INTEGER);
CREATE INDEX IF NOT EXISTS ess_classes_owner ON ess_classes(license_id);
CREATE TABLE IF NOT EXISTS ess_students(id INTEGER PRIMARY KEY,class_id INTEGER NOT NULL REFERENCES ess_classes(id),name TEXT NOT NULL,pin TEXT NOT NULL,pin_ver INTEGER NOT NULL DEFAULT 1,fails INTEGER NOT NULL DEFAULT 0,locked_until INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,removed_at INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS ess_students_name ON ess_students(class_id,name) WHERE removed_at IS NULL;
CREATE TABLE IF NOT EXISTS ess_assignments(id INTEGER PRIMARY KEY,class_id INTEGER NOT NULL REFERENCES ess_classes(id),title TEXT NOT NULL,requirement TEXT NOT NULL,genre TEXT NOT NULL,min_chars INTEGER NOT NULL DEFAULT 0,due_at INTEGER,auto_publish INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,closed_at INTEGER);
CREATE INDEX IF NOT EXISTS ess_assignments_class ON ess_assignments(class_id);
CREATE TABLE IF NOT EXISTS ess_submissions(id INTEGER PRIMARY KEY,assignment_id INTEGER NOT NULL REFERENCES ess_assignments(id),student_id INTEGER NOT NULL REFERENCES ess_students(id),attempt INTEGER NOT NULL,source TEXT NOT NULL,title TEXT NOT NULL DEFAULT '',text TEXT NOT NULL,status TEXT NOT NULL,hold TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL,graded_at INTEGER,published_at INTEGER,UNIQUE(assignment_id,student_id,attempt));
CREATE INDEX IF NOT EXISTS ess_submissions_status ON ess_submissions(status);
CREATE TABLE IF NOT EXISTS ess_reviews(submission_id INTEGER PRIMARY KEY REFERENCES ess_submissions(id),ai TEXT NOT NULL,final TEXT,model TEXT NOT NULL,prompt_version TEXT NOT NULL,created_at INTEGER NOT NULL,edited_at INTEGER);
CREATE TABLE IF NOT EXISTS ess_jobs(id INTEGER PRIMARY KEY,submission_id INTEGER NOT NULL REFERENCES ess_submissions(id),kind TEXT NOT NULL,status TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,run_after INTEGER NOT NULL,locked_at INTEGER,error TEXT NOT NULL DEFAULT '',created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ess_jobs_queue ON ess_jobs(status,run_after);
CREATE TABLE IF NOT EXISTS ess_usage(id INTEGER PRIMARY KEY,license_id INTEGER NOT NULL,submission_id INTEGER,step TEXT NOT NULL,model TEXT NOT NULL,tokens_in INTEGER NOT NULL,tokens_cached INTEGER NOT NULL,tokens_out INTEGER NOT NULL,cost REAL NOT NULL,ok INTEGER NOT NULL,created_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ess_usage_time ON ess_usage(created_at);
CREATE INDEX IF NOT EXISTS ess_usage_license ON ess_usage(license_id,created_at);
CREATE TABLE IF NOT EXISTS ess_providers(id INTEGER PRIMARY KEY,name TEXT NOT NULL,base_url TEXT NOT NULL,key_enc TEXT NOT NULL DEFAULT '',key_tail TEXT NOT NULL DEFAULT '',timeout INTEGER NOT NULL DEFAULT 120,concurrency INTEGER NOT NULL DEFAULT 3,created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ess_steps(step TEXT NOT NULL,slot TEXT NOT NULL,provider_id INTEGER REFERENCES ess_providers(id),model TEXT NOT NULL DEFAULT '',temperature REAL,max_tokens INTEGER,extra TEXT NOT NULL DEFAULT '',price_in REAL NOT NULL DEFAULT 0,price_cached REAL NOT NULL DEFAULT 0,price_out REAL NOT NULL DEFAULT 0,peak_factor REAL NOT NULL DEFAULT 1,peak_hours TEXT NOT NULL DEFAULT '',PRIMARY KEY(step,slot));
CREATE TABLE IF NOT EXISTS ess_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(path, timeout=30) as d:
        d.execute("PRAGMA journal_mode=WAL")
        d.executescript(SCHEMA)
        # Every gunicorn worker runs this at start-up: the write lock makes the checks below and migrate() run once.
        d.execute("BEGIN IMMEDIATE")
        # Columns added after a table first shipped (older releases ignore them, so rollbacks stay safe).
        if "mode" not in {r[1] for r in d.execute("PRAGMA table_info(rounds)")}:
            d.execute("ALTER TABLE rounds ADD COLUMN mode TEXT NOT NULL DEFAULT 'solo'")
        columns = {r[1] for r in d.execute("PRAGMA table_info(profiles)")}
        if "rank_stars" not in columns:
            d.execute("ALTER TABLE profiles ADD COLUMN rank_stars INTEGER NOT NULL DEFAULT 0")
            d.execute("ALTER TABLE profiles ADD COLUMN rank_progress INTEGER NOT NULL DEFAULT 0")
        if "pk_outcome" not in {r[1] for r in d.execute("PRAGMA table_info(pk_seats)")}:
            d.execute("ALTER TABLE pk_seats ADD COLUMN pk_outcome TEXT NOT NULL DEFAULT ''")
        migrate(d)
    path.chmod(0o600)


def migrate(d):
    """One-off data changes, each recorded in `migrations` so it runs once. The caller holds the write lock."""
    done = {r[0] for r in d.execute("SELECT name FROM migrations")}
    if "points_div10" not in done:
        # Ranking points became a tenth of the game score (rounded half up); mistake re-practice stays +1 per question.
        # Earlier PK rows hold score + placing bonus together, so the whole row is divided.
        d.execute("UPDATE scores SET points=(points+5)/10 WHERE mode IN ('solo','online')")
        d.execute("INSERT INTO migrations VALUES('points_div10',?)", (int(time.time()),))
    if "ranks_from_points" not in done:
        # Everyone starts at the tier their points so far would have bought.
        totals = d.execute(
            "SELECT s.site,s.license_id,sum(s.points) FROM scores s JOIN licenses l ON l.site=s.site AND l.id=s.license_id"
            " GROUP BY s.site,s.license_id"
        ).fetchall()
        for site, lid, total in totals:
            stars, progress = ranks.replay(total)
            d.execute("INSERT OR IGNORE INTO profiles(site,license_id) VALUES(?,?)", (site, lid))
            d.execute(
                "UPDATE profiles SET rank_stars=?,rank_progress=? WHERE site=? AND license_id=?", (stars, progress, site, lid)
            )
        d.execute("INSERT INTO migrations VALUES('ranks_from_points',?)", (int(time.time()),))
    if "ranks_x3" not in done:
        # Stars became three times as expensive and online PK stopped adding or taking stars: recount from points.
        totals = d.execute(
            "SELECT p.site,p.license_id,coalesce(sum(s.points),0) FROM profiles p"
            " LEFT JOIN scores s ON s.site=p.site AND s.license_id=p.license_id GROUP BY p.site,p.license_id"
        ).fetchall()
        for site, lid, total in totals:
            d.execute(
                "UPDATE profiles SET rank_stars=?,rank_progress=? WHERE site=? AND license_id=?", (*ranks.replay(total), site, lid)
            )
        d.execute("INSERT INTO migrations VALUES('ranks_x3',?)", (int(time.time()),))
    if "english_read_replaces_listen_judge" not in done:
        # 听力判断PK became 读句子 (game id "read"): it takes over the old game's place in its group, so everyone
        # licensed for that group gets the new game. Old scores keep the old id and simply stop being shown.
        d.execute("UPDATE OR IGNORE game_memberships SET game='read' WHERE site='english' AND game='listen-judge'")
        d.execute("DELETE FROM game_memberships WHERE site='english' AND game='listen-judge'")
        d.execute("INSERT INTO migrations VALUES('english_read_replaces_listen_judge',?)", (int(time.time()),))
