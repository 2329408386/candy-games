"""读句子 (English site): scores a child's reading of a textbook sentence.

The browser records the reading as 16 kHz 16-bit mono PCM and posts it to /api/speech/<game>?q=<question id>.
The server sends it to Alibaba Cloud 一句话识别 (REST, cn-shanghai) and compares the recognised words with the
sentence from the bank (never with text from the browser): score = matched sentence words / sentence words.

Credentials live only in the server's secret.env: ALIYUN_AK_ID, ALIYUN_AK_SECRET (an AccessKey allowed to call
nls-meta CreateToken) and NLS_APPKEY (an 智能语音交互 project whose recognition model is English 16k). Without
them the endpoint answers 503 "语音评测暂未开通".

Cost and capacity guards: each licensed browser may evaluate once every 2 s and 300 times a day, a free-trial
browser 25 times a day, and at most 3 evaluations run at once across all gunicorn workers (each one holds a sync
worker for about a second while Alibaba Cloud answers).
"""

import base64, hashlib, hmac, json, os, re, threading, time, urllib.error, urllib.parse, urllib.request, uuid
from datetime import datetime, timezone
from flask import jsonify, request, session

TOKEN_URL = "https://nls-meta.cn-shanghai.aliyuncs.com/"
ASR_URL = "https://nls-gateway-cn-shanghai.aliyuncs.com/stream/v1/asr"
MAX_BYTES = 16000 * 2 * 12  # 12 s of 16 kHz 16-bit mono; the page stops recording at 10 s
MIN_BYTES = 16000 * 2 // 4  # a quarter of a second
PASS = 80  # score from which the reading counts as right (the page uses the same value)
LIMITS = {"licensed": 300, "trial": 25}
CONCURRENT = 3


class SpeechError(Exception):
    pass


def configured():
    return all(os.environ.get(k) for k in ("ALIYUN_AK_ID", "ALIYUN_AK_SECRET", "NLS_APPKEY"))


# ---- Alibaba Cloud ----
_token = {"id": None, "until": 0}
_lock = threading.Lock()


def _pct(value):
    return urllib.parse.quote(str(value), safe="-_.~")


def _new_token():
    params = {
        "AccessKeyId": os.environ["ALIYUN_AK_ID"],
        "Action": "CreateToken",
        "Format": "JSON",
        "RegionId": "cn-shanghai",
        "SignatureMethod": "HMAC-SHA1",
        "SignatureNonce": uuid.uuid4().hex,
        "SignatureVersion": "1.0",
        "Timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Version": "2019-02-28",
    }
    query = "&".join(f"{_pct(k)}={_pct(v)}" for k, v in sorted(params.items()))
    key = (os.environ["ALIYUN_AK_SECRET"] + "&").encode()
    signature = base64.b64encode(hmac.new(key, ("GET&%2F&" + _pct(query)).encode(), hashlib.sha1).digest()).decode()
    try:
        with urllib.request.urlopen(TOKEN_URL + "?" + query + "&Signature=" + _pct(signature), timeout=8) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise SpeechError("CreateToken HTTP %s: %s" % (e.code, e.read()[:300])) from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SpeechError("CreateToken: %s" % e) from None
    if "Token" not in data:
        raise SpeechError("CreateToken: %s" % str(data)[:300])
    return data["Token"]["Id"], int(data["Token"]["ExpireTime"])


def token(refresh=False):
    """A cached NLS token (valid about a day); renewed an hour before it expires."""
    with _lock:
        if refresh or not _token["id"] or _token["until"] < time.time() + 3600:
            _token["id"], _token["until"] = _new_token()
        return _token["id"]


def recognize(pcm):
    """English text heard in 16 kHz 16-bit mono PCM."""
    query = urllib.parse.urlencode(
        {
            "appkey": os.environ["NLS_APPKEY"],
            "format": "pcm",
            "sample_rate": 16000,
            "enable_punctuation_prediction": "false",
            "enable_inverse_text_normalization": "false",
        }
    )
    for attempt in range(2):
        req = urllib.request.Request(
            ASR_URL + "?" + query,
            data=pcm,
            method="POST",
            headers={"X-NLS-Token": token(refresh=attempt > 0), "Content-Type": "application/octet-stream"},
        )
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            try:
                data = json.load(e)
            except ValueError:
                raise SpeechError("ASR HTTP %s" % e.code) from None
        except (urllib.error.URLError, TimeoutError, ValueError) as e:
            raise SpeechError("ASR: %s" % e) from None
        status = data.get("status")
        if status == 20000000:
            return data.get("result") or ""
        if status in (40000001, 40000002) and attempt == 0:  # token expired or invalid: renew once
            continue
        raise SpeechError("ASR status %s: %s" % (status, data.get("message")))
    return ""


# ---- scoring ----
CONTRACTIONS = {
    "i'm": "i am", "you're": "you are", "we're": "we are", "they're": "they are",
    "it's": "it is", "he's": "he is", "she's": "she is", "that's": "that is", "what's": "what is",
    "where's": "where is", "who's": "who is", "how's": "how is", "there's": "there is", "here's": "here is",
    "let's": "let us", "isn't": "is not", "aren't": "are not", "don't": "do not", "doesn't": "does not",
    "can't": "can not", "cannot": "can not", "i've": "i have", "i'll": "i will", "won't": "will not",
}
# Recognisers often drop the apostrophe ("lets", "im", "were"). Both the sentence and the reading are normalised the
# same way, so mapping a real word such as "were" to "we are" never fails a correct reading.
CONTRACTIONS.update({k.replace("'", ""): v for k, v in list(CONTRACTIONS.items())})
SAME = {"mom": "mum", "mommy": "mummy", "color": "colour", "colors": "colours", "colorful": "colourful",
        "okay": "ok", "o.k.": "ok", "favorite": "favourite", "mr": "mister", "ms": "miss", "goodbye": "good bye"}
DIGITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()


def normalize(token):
    """Words a written or recognised token stands for, e.g. "I'm" -> ["i", "am"], "3" -> ["three"]."""
    t = token.lower().replace("’", "'").strip()
    t = re.sub(r"[^a-z0-9'.]", "", t).strip("'.")
    if not t:
        return []
    if t.isdigit() and int(t) < len(DIGITS):
        return [DIGITS[int(t)]]
    t = SAME.get(t, t)
    t = CONTRACTIONS.get(t, t)
    return [w for w in re.split(r"[\s.]+", t.replace("'", "")) if w]


def score(sentence, heard):
    """{score, words: [{w, ok}], heard}: each word of the sentence is ok when the reading contains it in order."""
    shown = sentence.split()
    target = [(i, w) for i, token in enumerate(shown) for w in normalize(token)]
    said = [w for token in heard.split() for w in normalize(token)]
    # Longest common subsequence of the sentence's words and the heard words.
    n, m = len(target), len(said)
    table = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            table[i][j] = table[i + 1][j + 1] + 1 if target[i][1] == said[j] else max(table[i + 1][j], table[i][j + 1])
    matched, i, j = set(), 0, 0
    while i < n and j < m:
        if target[i][1] == said[j]:
            matched.add(i)
            i, j = i + 1, j + 1
        elif table[i + 1][j] >= table[i][j + 1]:
            i += 1
        else:
            j += 1
    words = []
    for index, token in enumerate(shown):
        parts = [k for k, (owner, _) in enumerate(target) if owner == index]
        words.append({"w": token, "ok": all(k in matched for k in parts)})
    value = round(100 * len(matched) / n) if n else 0
    return {"score": value, "pass": value >= PASS, "words": words, "heard": heard}


# ---- endpoint ----
def register_speech(app):
    from .app import bank_json, current, db, device_hash, site

    def bump(bucket, window):
        """Counts one use in the shared limits table and returns the count within the window."""
        d, now = db(), int(time.time())
        d.execute("BEGIN IMMEDIATE")
        d.execute("DELETE FROM limits WHERE until<?", (now,))
        d.execute(
            "INSERT INTO limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (bucket, now + window)
        )
        count = d.execute("SELECT count FROM limits WHERE bucket=?", (bucket,)).fetchone()[0]
        d.commit()
        return count

    def release(bucket):
        d = db()
        d.execute("UPDATE limits SET count=count-1 WHERE bucket=? AND count>0", (bucket,))
        d.commit()

    def question(game, qid):
        for q in json.loads(bank_json(game))["questions"]:
            if q.get("id") == qid:
                return q
        return None

    @app.post("/api/speech/<game>")
    def speech(game):
        s = site().key
        if game not in site().games or not (site().bank_root / f"{game}.json").exists():
            return jsonify(error="接口不存在，请刷新页面后重试"), 404
        row = current(game)
        if row:
            who, kind = "l%d" % row["id"], "licensed"
        else:
            started = db().execute(
                "SELECT started_at FROM trials WHERE site=? AND device_hash=? AND game=?", (s, device_hash(), game)
            ).fetchone()
            if not started or started[0] < time.time() - 3 * 3600:
                return jsonify(error="授权已失效，请返回首页重新激活"), 403
            who, kind = "d" + device_hash()[:24], "trial"
        q = question(game, request.args.get("q", ""))
        if not q or not q.get("text"):
            return jsonify(error="题目不存在，请刷新页面后重试"), 404
        if not configured():
            return jsonify(error="语音评测暂未开通，请联系管理员", code="not_configured"), 503
        request.max_content_length = MAX_BYTES
        pcm = request.get_data(cache=False)
        if len(pcm) < MIN_BYTES:
            return jsonify(error="没有录到声音，请靠近一点再读一遍", code="too_short"), 400
        if bump(f"{s}:speech-gap:{who}", 2) > 1:
            return jsonify(error="读得太快啦，稍等一下再试", code="too_fast"), 429
        if bump(f"{s}:speech-day:{who}:{time.strftime('%Y%m%d')}", 86400) > LIMITS[kind]:
            return jsonify(error="今天的读句子次数用完了，明天再来吧", code="daily_limit"), 429
        busy = "speech:inflight"
        if bump(busy, 30) > CONCURRENT:
            release(busy)
            return jsonify(error="现在读句子的人有点多，请几秒后再试", code="busy"), 503
        try:
            heard = recognize(pcm)
        except SpeechError as e:
            app.logger.warning("speech: %s", e)
            return jsonify(error="语音评测暂时不可用，请稍后再试", code="speech_failed"), 502
        finally:
            release(busy)
        return jsonify(score(q["text"], heard))
