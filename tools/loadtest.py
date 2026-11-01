"""Load test for online PK rooms: N simulated players in rooms of 10, polling every 2 s and answering every 8 s.

Never point it at the live data. Run a throwaway instance with its own database instead:

    python3 tools/loadtest.py seed /tmp/lt              # database with 1000 licenses (one per simulated player), keys in /tmp/lt/keys.json
    GAME_DATA=/tmp/lt CHINESE_SECRET=x MATH_SECRET=y ENGLISH_SECRET=z gunicorn --workers 6 --bind 127.0.0.1:18099 wsgi:app
    python3 tools/loadtest.py run http://127.0.0.1:18099 /tmp/lt/keys.json --players 200 --seconds 90

It talks to gunicorn directly and gives every player its own X-Forwarded-For address, so the per-IP rate limits
apply per simulated player as they would in real use. Prints request counts, error counts and latency percentiles.
"""

import argparse, http.cookiejar, json, random, secrets, sqlite3, sys, threading, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POLL, ANSWER_EVERY, ROOM_SIZE = 2.0, 8.0, 10


def seed(data, licenses):
    sys.path.insert(0, str(ROOT))
    from core import schema
    from core.app import GRADES, digest
    from sites import SITES

    data = Path(data)
    schema.initialize(data / schema.DB_NAME)
    d = sqlite3.connect(data / schema.DB_NAME)
    keys = {}
    for site, cfg in SITES.items():
        d.executemany("INSERT OR IGNORE INTO game_groups VALUES(?,?,?,?,0)", [(site, g + "-a", g, "A") for g in GRADES])
        d.executemany("INSERT OR IGNORE INTO game_memberships VALUES(?,?,?)", [(site, g, "g3-t1-a") for g in cfg.games])
        keys[site] = []
        for lid in range(1, licenses + 1):
            key = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(16))
            d.execute(
                "INSERT INTO licenses(site,id,key_hash,games,created_at,device_limit) VALUES(?,?,?,'[]',0,100)",
                (site, lid, digest(key)),
            )
            d.execute("INSERT INTO license_groups VALUES(?,?,?)", (site, lid, "g3-t1-a"))
            keys[site].append(key)
    d.commit()
    (data / "keys.json").write_text(json.dumps(keys))
    print(f"已写入 {data / schema.DB_NAME}，授权码在 {data / 'keys.json'}")


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.times, self.errors = {}, {}

    def add(self, kind, seconds, error=None):
        with self.lock:
            self.times.setdefault(kind, []).append(seconds)
            if error:
                key = f"{kind} {error}"
                self.errors[key] = self.errors.get(key, 0) + 1

    def report(self, wall):
        live = sum(len(self.times.get(k, [])) for k in ("poll", "answer"))
        print(f"\n对战期间 poll+answer 共 {live} 个请求，平均 {live / wall:.0f} 个/秒")
        for kind, values in sorted(self.times.items()):
            values = sorted(values)
            pick = lambda p: values[min(len(values) - 1, int(p * len(values)))] * 1000
            print(f"  {kind:<8} {len(values):>7}  p50 {pick(.5):6.0f} ms  p95 {pick(.95):6.0f} ms  p99 {pick(.99):6.0f} ms")
        for key, count in sorted(self.errors.items()):
            print(f"  错误 {key}: {count}")


class Player:
    def __init__(self, base, site, key, n, stats):
        self.base, self.key, self.stats = base.rstrip("/") + ({"math": "/math", "english": "/english"}.get(site, "")), key, stats
        self.ip = f"10.{n // 65536 % 256}.{n // 256 % 256}.{n % 256}"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""

    def call(self, kind, path, body=None):
        headers = {"X-Forwarded-For": self.ip}
        data = None
        if body is not None:
            headers.update({"Content-Type": "application/json", "X-CSRF-Token": self.csrf})
            data = json.dumps(body).encode()
        start = time.monotonic()
        try:
            with self.opener.open(urllib.request.Request(self.base + path, data, headers), timeout=30) as r:
                out = json.loads(r.read() or b"{}")
            self.stats.add(kind, time.monotonic() - start)
            return out
        except urllib.error.HTTPError as e:
            self.stats.add(kind, time.monotonic() - start, e.code)
            return None
        except Exception as e:  # connection refused, timeouts
            self.stats.add(kind, time.monotonic() - start, type(e).__name__)
            return None

    def login(self):
        self.csrf = (self.call("csrf", "/api/csrf") or {}).get("csrf", "")
        return self.call("activate", "/api/activate", {"key": self.key}) is not None


def room(base, site, game, keys, members, seconds, stats, offset, ready):
    players = [Player(base, site, keys[(offset + i) % len(keys)], offset + i, stats) for i in range(members)]
    if not all(p.login() for p in players):
        ready.wait()
        return
    host = players[0]
    ids = [q["id"] for q in (host.call("bank", f"/games/{game}/bank.json") or {"questions": []})["questions"][:20]]
    st = host.call("create", f"/api/rooms/{game}/create", {"seconds": max(30, seconds)})
    if not st:
        ready.wait()
        return
    code = st["code"]
    for p in players[1:]:
        p.call("join", f"/api/rooms/{code}/join", {})
    ready.wait()  # every room starts together, so the load is steady
    if not host.call("start", f"/api/rooms/{code}/start", {"questions": ids}):
        return
    time.sleep(3.2)
    end = time.monotonic() + seconds

    def run(p, delay):
        time.sleep(delay)
        version, answered, next_answer = 0, 0, time.monotonic() + random.uniform(2, ANSWER_EVERY)
        while time.monotonic() < end:
            got = p.call("poll", f"/api/rooms/{code}?v={version}")
            if got and "version" in got:
                version = got["version"]
            if time.monotonic() >= next_answer and answered < len(ids):
                if p.call("answer", f"/api/rooms/{code}/answer", {"index": answered, "correct": random.random() < 0.7}):
                    answered += 1
                next_answer += ANSWER_EVERY
            time.sleep(POLL)

    threads = [threading.Thread(target=run, args=(p, random.uniform(0, POLL))) for p in players]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def run(base, keys_file, players, seconds, site):
    sys.path.insert(0, str(ROOT))
    from sites import SITES

    game = next(iter(SITES[site].games))
    keys = json.loads(Path(keys_file).read_text())[site]
    stats = Stats()
    rooms = [min(ROOM_SIZE, players - i) for i in range(0, players, ROOM_SIZE)]
    ready = threading.Barrier(len(rooms) + 1)
    threads = [
        threading.Thread(target=room, args=(base, site, game, keys, n, seconds, stats, i * ROOM_SIZE, ready))
        for i, n in enumerate(rooms)
    ]
    for t in threads:
        t.start()
        time.sleep(0.02)
    ready.wait()
    print(f"{players} 名玩家、{len(rooms)} 个房间已就绪，开始对战 {seconds} 秒…", flush=True)
    start = time.monotonic()
    for t in threads:
        t.join()
    stats.report(time.monotonic() - start)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed")
    s.add_argument("data")
    s.add_argument("--licenses", type=int, default=1000)
    r = sub.add_parser("run")
    r.add_argument("base")
    r.add_argument("keys")
    r.add_argument("--players", type=int, default=100)
    r.add_argument("--seconds", type=int, default=90)
    r.add_argument("--site", default="chinese", choices=["chinese", "math", "english"])
    a = parser.parse_args()
    if a.cmd == "seed":
        seed(a.data, a.licenses)
    else:
        run(a.base, a.keys, a.players, a.seconds, a.site)
