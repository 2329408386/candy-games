import sqlite3
from pathlib import Path

DB_NAME = "games.sqlite3"
# Every table is scoped by site ('chinese' or 'math'); ids keep their original per-site numbering.
# Permissions and grades come from license_groups -> game_groups. licenses.grade_scopes and
# generation_batches.grade_scopes are deprecated mirrors: still written so older releases can be rolled back to,
# never read.
# trials records the one free round each browser (session device token) may play per game.
# profiles/classes/scores/rounds: see core/social.py (one license = one user, in at most one class).
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
CREATE TABLE IF NOT EXISTS rounds(site TEXT NOT NULL,id TEXT NOT NULL,license_id INTEGER NOT NULL,game TEXT NOT NULL,started_at INTEGER NOT NULL,PRIMARY KEY(site,id));
"""


def initialize(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sqlite3.connect(path) as d:
        d.execute("PRAGMA journal_mode=WAL")
        d.executescript(SCHEMA)
    path.chmod(0o600)
