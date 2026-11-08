"""Model calls for the essay workbench: any OpenAI-compatible chat endpoint, configured in the admin.

Configuration lives in the database, not in secret.env, so the admin can switch providers without a deploy:
  ess_providers  one row per service: base_url (…/v1), the API key (Fernet-encrypted with a key derived from the
                 site secret; only the last 4 characters are kept in clear for display), timeout and how many
                 requests may run at once.
  ess_steps      (step, slot): which provider and model each step uses. step is ocr / grade / check, slot is
                 main or backup (used when the main one fails, times out or is rate limited). step_timeout, when set,
                 replaces the provider's timeout for that step (reading photos with a thinking model is slow). Each row also holds
                 temperature, max_tokens, extra (a JSON object merged into the request body, e.g.
                 {"enable_thinking": false}; a key set to null removes it from the request) and prices in yuan per
                 million tokens (input, cached input, output) with an optional peak window and multiplier.

Every call, successful or not, is written to ess_usage with its tokens, the cost those prices give, how long it took
(ms) and, when it failed, why (error) — also when the backup model then answered, so 模型设置 can show it.
"""

import base64, hashlib, json, re, socket, threading, time, urllib.error, urllib.request
from cryptography.fernet import Fernet, InvalidToken

STEPS = {"ocr": "照片识别（OCR）", "grade": "作文批改", "check": "批改复核", "verify": "看图复核"}
SLOTS = {"main": "主模型", "backup": "备用模型"}
BEIJING = 8 * 3600
USER_AGENT = "candy-games-essay/1.0 (+http://39.106.16.99/teacher/)"


class LLMError(Exception):
    """A call that failed; retry says whether trying again later (or with the backup model) may help;
    model_missing that the provider says the model does not exist (models_sync may find its successor)."""

    model_missing = False

    def __init__(self, message, retry=True):
        super().__init__(message)
        self.retry = retry


MODEL_MISSING = re.compile(r"model.{0,40}(not.{0,10}(found|exist|support|available)|does ?n.t exist|unknown|invalid)|(no|unknown|invalid) model|模型.{0,10}(不存在|不支持|下线)", re.I | re.S)


PLEASE_RETRY = re.compile(r"please retry|try again|output became abnormal|请重试|稍后再试", re.I)


class NotConfigured(LLMError):
    def __init__(self, step):
        super().__init__(f"{STEPS.get(step, step)}还没有配置模型", retry=False)


# ---- API keys ----
def _fernet():
    from sites import TEACHER
    from core.app import site_secret

    secret = site_secret(TEACHER)
    if not secret:
        raise RuntimeError("TEACHER_SECRET is not set")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("ess-provider-key:" + secret).encode()).digest()))


def encrypt_key(plain):
    return _fernet().encrypt(plain.encode()).decode() if plain else ""


def decrypt_key(token):
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise LLMError("API Key 无法解密（站点密钥变了？请在模型设置里重新填写）", retry=False) from None


def key_tail(plain):
    return plain[-4:] if len(plain) >= 8 else ""


# ---- configuration ----
def step_rows(d, step, now=None):
    """The configured slots of a step that have a provider and a model, in the order they are tried: main, then
    backup, except that a slot with 首选时段 (use_hours) goes first inside those hours and last outside them (it is
    then only a fallback)."""
    rows = d.execute(
        "SELECT s.*,p.name provider_name,p.base_url,p.key_enc,coalesce(s.step_timeout,p.timeout) timeout,p.concurrency FROM ess_steps s"
        " JOIN ess_providers p ON p.id=s.provider_id WHERE s.step=? AND s.model!='' ORDER BY s.slot='backup'",
        (step,),
    ).fetchall()
    rank = lambda r: 1 if not r["use_hours"] else 0 if in_hours(r["use_hours"], now) else 2
    return sorted((dict(r) for r in rows), key=rank)


HOURS_PART = re.compile(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*")


def in_hours(spec, now=None):
    """spec like "18-8" or "0-8,18-24": Beijing-time hours, every day; a span may run past midnight (18-8)."""
    if not spec:
        return False
    hour = (int(now if now is not None else time.time()) + BEIJING) % 86400 / 3600
    for part in spec.split(","):
        m = HOURS_PART.fullmatch(part)
        if m:
            a, b = int(m[1]), int(m[2])
            if (a <= hour < b) if a < b else (hour >= a or hour < b):
                return True
    return False


def valid_use_hours(spec):
    return not spec or all(
        (m := HOURS_PART.fullmatch(part)) and 0 <= int(m[1]) <= 24 and 0 <= int(m[2]) <= 24 and int(m[1]) != int(m[2])
        for part in spec.split(",")
    )


def configured(d, step):
    return bool(step_rows(d, step))


def check_enabled(d):
    """批改复核 runs only when switched on in 模型设置 (ess_meta 'check_enabled' = '1'); off by default."""
    row = d.execute("SELECT value FROM ess_meta WHERE key='check_enabled'").fetchone()
    return bool(row) and row[0] == "1"


def in_peak(spec, now=None):
    """spec like "9-12,14-18": Beijing-time hours on weekdays (Mon–Fri) when peak prices apply."""
    if not spec:
        return False
    local = int(now if now is not None else time.time()) + BEIJING
    if (local // 86400 + 3) % 7 >= 5:  # 1970-01-01 was a Thursday: 0 = Monday … 5, 6 = weekend
        return False
    hour = local % 86400 / 3600
    for part in spec.split(","):
        m = re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", part)
        if m and int(m[1]) <= hour < int(m[2]):
            return True
    return False


def valid_peak_hours(spec):
    return not spec or all(
        (m := re.fullmatch(r"\s*(\d{1,2})\s*-\s*(\d{1,2})\s*", part)) and 0 <= int(m[1]) < int(m[2]) <= 24
        for part in spec.split(",")
    )


def cost(row, tokens_in, tokens_cached, tokens_out, now=None):
    cached = min(tokens_cached, tokens_in)
    yuan = ((tokens_in - cached) * row["price_in"] + cached * row["price_cached"] + tokens_out * row["price_out"]) / 1e6
    return yuan * (row["peak_factor"] if in_peak(row["peak_hours"], now) else 1)


# ---- calls ----
_limits = {}
_limits_lock = threading.Lock()


def _semaphore(row):
    with _limits_lock:
        limit = max(1, int(row["concurrency"] or 1))
        held = _limits.get(row["provider_id"])
        if not held or held[0] != limit:
            held = _limits[row["provider_id"]] = (limit, threading.BoundedSemaphore(limit))
        return held[1]


def request_body(row, messages, json_mode):
    body = {"model": row["model"], "messages": messages}
    if row["temperature"] is not None:
        body["temperature"] = row["temperature"]
    if row["max_tokens"]:
        body["max_tokens"] = row["max_tokens"]
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if row["extra"]:
        for k, v in json.loads(row["extra"]).items():
            if v is None:
                body.pop(k, None)
            else:
                body[k] = v
    return body


def post(row, body):
    """One HTTP call. Returns (content, tokens_in, tokens_cached, tokens_out)."""
    url = row["base_url"].rstrip("/") + "/chat/completions"
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode(),
        # An explicit User-Agent: Cloudflare in front of some providers rejects urllib's default (error code 1010).
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
            "Authorization": "Bearer " + decrypt_key(row["key_enc"]),
        },
    )
    try:
        with _semaphore(row), urllib.request.urlopen(req, timeout=row["timeout"] or 120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read()[:300].decode(errors="replace")
        # 400/401/403/404 are configuration problems; the backup may still work, but retrying this one won't.
        # …except when the provider itself says to try again (e.g. "Model output became abnormal … Please retry").
        error = LLMError(f"HTTP {e.code}: {detail}", retry=e.code in (408, 409, 429) or e.code >= 500 or bool(PLEASE_RETRY.search(detail)))
        error.model_missing = e.code in (400, 404) and bool(MODEL_MISSING.search(detail))
        raise error from None
    except (TimeoutError, socket.timeout):
        raise LLMError(f"超时（{row['timeout'] or 120} 秒没有返回）") from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, (TimeoutError, socket.timeout)):
            raise LLMError(f"超时（{row['timeout'] or 120} 秒没有返回）") from None
        raise LLMError(f"连接失败：{e}") from None
    except OSError as e:
        raise LLMError(f"连接失败：{e}") from None
    except ValueError:
        raise LLMError("返回的不是 JSON") from None
    try:
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LLMError("返回格式不对：" + json.dumps(data, ensure_ascii=False)[:300]) from None
    if not content.strip() and choice.get("finish_reason") == "length":
        # Thinking models (e.g. gemini-3.x-flash) can spend the whole max_tokens on reasoning and return no content.
        raise LLMError(
            f"输出 token 用完了还没写出回答（模型先思考，用掉了 {(data.get('usage') or {}).get('completion_tokens', '?')} 个 token）："
            "把「最大输出 token」调大或留空",
            retry=False,
        )
    usage = data.get("usage") or {}
    cached = usage.get("prompt_cache_hit_tokens") or (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    return content, int(usage.get("prompt_tokens") or 0), int(cached), int(usage.get("completion_tokens") or 0)


def log_usage(d, row, license_id, submission_id, step, tokens, ok, error="", started=None):
    d.execute(
        "INSERT INTO ess_usage(license_id,submission_id,step,model,tokens_in,tokens_cached,tokens_out,cost,ok,created_at,error,ms)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            license_id,
            submission_id,
            step,
            row["provider_name"] + " / " + row["model"],
            *tokens,
            cost(row, *tokens),
            int(ok),
            int(time.time()),
            str(error)[:500],
            int((time.time() - started) * 1000) if started else None,
        ),
    )
    d.commit()


TRIES = 2  # each model is asked this many times before the next one (a failure that may pass is often gone at once)


def chat(d, step, messages, *, json_mode=False, license_id=0, submission_id=None, parse=None, slot=None):
    """Runs a step on its main model, falling back to the backup. A failure that may pass (timeout, rate limit,
    server error, an answer that is not usable JSON) is tried again on the same model first, up to TRIES times;
    one that cannot (bad key, wrong model name) goes straight to the next model. parse(content) may raise ValueError
    to reject an answer (e.g. JSON that does not fit the schema): that counts as a failure of that model.
    slot: only that slot's model. Returns (parsed or content, model label)."""
    rows = [r for r in step_rows(d, step) if slot in (None, r["slot"])]
    if not rows:
        raise NotConfigured(step)
    errors, retry = [], False
    for row in rows:
        for attempt in range(TRIES):
            started = time.time()
            try:
                try:
                    content, *tokens = post(row, request_body(row, messages, json_mode))
                except LLMError as e:
                    if not (e.model_missing and row["auto_mode"] != "fixed"):
                        raise
                    # Renamed or retired: switch the slot to the newest model of its series and try that once.
                    from . import models_sync

                    log_usage(d, row, license_id, submission_id, step, (0, 0, 0), False, e, started)
                    if not models_sync.sync(d, only=(row["step"], row["slot"])):
                        raise
                    row = next(r for r in step_rows(d, step) if r["slot"] == row["slot"])
                    started = time.time()
                    content, *tokens = post(row, request_body(row, messages, json_mode))
            except LLMError as e:
                log_usage(d, row, license_id, submission_id, step, (0, 0, 0), False, e, started)
                errors.append(f"{SLOTS[row['slot']]}：{e}")
                retry = retry or e.retry
                if e.retry:
                    continue
                break
            try:
                result = parse(content) if parse else content
            except ValueError as e:
                log_usage(d, row, license_id, submission_id, step, tokens, False, f"返回内容不能用：{e}", started)
                errors.append(f"{SLOTS[row['slot']]}：{e}")
                retry = True
                continue
            log_usage(d, row, license_id, submission_id, step, tokens, True, "", started)
            return result, row["provider_name"] + " / " + row["model"]
    raise LLMError("；".join(errors), retry=retry)


def parse_json(content):
    """The JSON object in a model answer (tolerates ```json fences and text around it)."""
    text = content.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m[1]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回答里没有 JSON")
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:
        raise ValueError("回答里的 JSON 格式不对") from None
    if not isinstance(value, dict):
        raise ValueError("回答里的 JSON 不是对象")
    return value
