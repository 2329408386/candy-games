"""Keeps each configured model on the newest id of its series (模型设置 → 版本策略).

Providers rename models and replace versions (deepseek-v4.1-flash → deepseek-v4.2-flash). Each step slot
(ess_steps) has auto_mode:
  latest   follow the newest model of the same series (default)
  replace  change only when the configured id has disappeared from the provider's list
  fixed    never change
A series is the id with its version numbers replaced (series("glm-5.3-flash") == "glm-#-flash"), so a model never
moves to a different line (glm-5.3 → glm-5.3-flash) or to a preview / contributor / free variant. Candidates come
from GET {base_url}/models; OpenCode's list also carries release dates, input modalities, structured-output
support and prices, which are used when present.

A switch happens only after a small JSON test request to the new id succeeds. The old id is kept in prev_model
(「改回上一个」 restores it and sets the slot to fixed), the change is written to auto_note and the audit log, and
prices are updated from the list when auto_price is on (USD × ess_meta 'usd_cny', default 7.1).

The worker checks every CHECK_SECONDS (core/essay/worker.py) and llm.chat checks a slot at once when the provider
answers that its model does not exist.
"""

import json, re, time, urllib.error, urllib.request
from . import llm

CHECK_SECONDS = 6 * 3600
VERSION = re.compile(r"v?\d+(?:\.\d+)*", re.I)
USD_CNY = 7.1
DAYS = ("mon", "tue", "wed", "thu", "fri")


def series(model_id):
    return VERSION.sub("#", model_id.lower())


def version(model_id):
    return tuple(int(n) for token in VERSION.findall(model_id) for n in re.findall(r"\d+", token))


def _get_json(url, key):
    headers = {"Accept": "application/json", "User-Agent": llm.USER_AGENT}
    if key:
        headers["Authorization"] = "Bearer " + key
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as r:
        return json.load(r)


def fetch_models(provider):
    """The provider's model list: [{"id": …, …}]. Raises llm.LLMError.
    Asked with the API key first; on 401/403/404 asked again without it (some public lists, such as OpenCode's
    /inference/v1/models, refuse keys they do not issue themselves)."""
    url = provider["base_url"].rstrip("/") + "/models"
    try:
        try:
            data = _get_json(url, llm.decrypt_key(provider["key_enc"]))
        except urllib.error.HTTPError as e:
            if e.code not in (401, 403, 404):
                raise
            data = _get_json(url, "")
    except urllib.error.HTTPError as e:
        raise llm.LLMError(f"模型列表 HTTP {e.code}（{url}）") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise llm.LLMError(f"模型列表读取失败：{e}") from None
    items = data.get("data") if isinstance(data, dict) else data
    return [m for m in items or [] if isinstance(m, dict) and isinstance(m.get("id"), str)]


def usable(model, step):
    if model.get("type") not in (None, "chat"):
        return False
    inputs = (model.get("modalities") or {}).get("input")
    if step == "ocr" and inputs is not None and not any(str(x).startswith("image") for x in inputs):
        return False
    if step != "ocr" and (model.get("capabilities") or {}).get("structured_output") is False:
        return False
    return True


def pick(slot, models):
    """The id the slot should switch to, or None. slot: dict with model, series, auto_mode, step."""
    if slot["auto_mode"] == "fixed":
        return None
    family = slot.get("series") or series(slot["model"])
    present = any(m["id"] == slot["model"] for m in models)
    candidates = [m for m in models if series(m["id"]) == family and usable(m, slot["step"])]
    if not candidates or (present and slot["auto_mode"] == "replace"):
        return None
    best = max(candidates, key=lambda m: (version(m["id"]), str(m.get("release_date") or "")))
    if best["id"] == slot["model"]:
        return None
    if present and version(best["id"]) <= version(slot["model"]):
        return None  # never move sideways or down while the configured id still exists
    return best


def price_from_cost(cost, rate):
    """OpenCode's cost (USD per million tokens) as ess_steps price columns in yuan, or None."""
    try:
        text = cost["text"]
        prices = {
            "price_in": round(float(text["input"]["price"]) * rate, 4),
            "price_cached": round(float((text["input"].get("cache") or {}).get("read", text["input"]["price"])) * rate, 4),
            "price_out": round(float(text["output"]["price"]) * rate, 4),
            "peak_hours": "",
            "peak_factor": 1.0,
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    for rule in cost.get("multipliers") or []:
        when = rule.get("when") or {}
        if set((when.get("utc_days") or {}).get("in") or []) != set(DAYS):
            continue  # only weekday peaks map onto peak_hours (Beijing weekdays)
        spans = []
        for part in when.get("any") or []:
            bounds = {}
            for cond in part.get("all") or []:
                hours = cond.get("utc_hours") or {}
                for key in ("gte", "lt"):
                    if key in hours:
                        bounds[key] = int(str(hours[key]).split(":")[0])
            if "gte" in bounds and "lt" in bounds:
                start, end = (bounds["gte"] + 8) % 24, (bounds["lt"] + 8) % 24 or 24
                if start < end:
                    spans.append(f"{start}-{end}")
        if spans:
            prices.update(peak_hours=",".join(spans), peak_factor=float(rule.get("factor") or 1))
            break
    return prices


def smoke_test(slot, model_id):
    row = {**slot, "model": model_id, "timeout": min(slot["timeout"] or 30, 30)}
    body = llm.request_body(row, [{"role": "user", "content": '只输出这个 JSON，不要输出其他内容：{"ok": true}'}], True)
    body["max_tokens"] = max(body.get("max_tokens") or 0, 300)
    content, *_ = llm.post(row, body)
    llm.parse_json(content)


def _note(d, slot, text, alert):
    d.execute("UPDATE ess_steps SET auto_note=?,auto_alert=? WHERE step=? AND slot=?", (text, int(alert), slot["step"], slot["slot"]))
    d.commit()  # never hold the write lock across the network calls that follow


def due(d, now=None):
    row = d.execute("SELECT value FROM ess_meta WHERE key='models_checked'").fetchone()
    return not row or int(row[0]) + CHECK_SECONDS <= (now or time.time())


def sync(d, only=None):
    """Checks every non-fixed slot (or only=(step, slot)) and switches where pick() says so.
    Returns {(step, slot): new id} for the switches made."""
    now = int(time.time())
    if only is None:
        d.execute("BEGIN IMMEDIATE")
        if not due(d, now):
            d.commit()
            return {}
        d.execute("INSERT INTO ess_meta VALUES('models_checked',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(now),))
        d.commit()
    rate = d.execute("SELECT value FROM ess_meta WHERE key='usd_cny'").fetchone()
    rate = float(rate[0]) if rate else USD_CNY
    sql = (
        "SELECT s.*,p.name provider_name,p.base_url,p.key_enc,p.timeout,p.concurrency FROM ess_steps s"
        " JOIN ess_providers p ON p.id=s.provider_id WHERE s.model!='' AND s.auto_mode!='fixed'"
    )
    slots = [dict(r) for r in d.execute(sql + (" AND s.step=? AND s.slot=?" if only else ""), only or ())]
    lists, switched = {}, {}
    for slot in slots:
        pid = slot["provider_id"]
        if pid not in lists:
            try:
                lists[pid] = fetch_models(slot)
            except llm.LLMError as e:
                lists[pid] = e
        models = lists[pid]
        if isinstance(models, llm.LLMError):
            _note(d, slot, f"检查新版本失败：{models}", False)
            continue
        present = any(m["id"] == slot["model"] for m in models)
        best = pick(slot, models)
        if not best:
            if not present and models:
                _note(d, slot, f"{slot['model']} 已不在服务商的模型列表里，也没找到同系列的新版本，请手动选择模型", True)
            elif slot["auto_alert"] or slot["auto_note"].startswith(("检查新版本失败", "发现 ")):
                _note(d, slot, "", False)  # the list was read and the model is fine: drop the old warning
            continue
        try:
            smoke_test(slot, best["id"])
        except (llm.LLMError, ValueError) as e:
            _note(d, slot, f"发现 {best['id']}，但测试没通过，暂不切换：{str(e)[:120]}", not present)
            continue
        prices = price_from_cost(best.get("cost"), rate) if slot["auto_price"] and best.get("cost") else None
        old = slot["model"]
        note = f"已从 {old} 自动{'升级' if present else '替换'}为 {best['id']}"
        d.execute(
            "UPDATE ess_steps SET model=?,prev_model=?,series=?,auto_at=?,auto_note=?,auto_alert=0 WHERE step=? AND slot=?",
            (best["id"], old, series(best["id"]), now, note, slot["step"], slot["slot"]),
        )
        if prices:
            d.execute(
                "UPDATE ess_steps SET " + ",".join(f"{k}=?" for k in prices) + " WHERE step=? AND slot=?",
                (*prices.values(), slot["step"], slot["slot"]),
            )
        d.execute(
            "INSERT INTO audit(site,created_at,action,details) VALUES('teacher',?,?,?)",
            (now, "essay_auto_model", json.dumps({"step": slot["step"], "slot": slot["slot"], "from": old, "to": best["id"], "prices": prices}, ensure_ascii=False)),
        )
        d.commit()
        switched[(slot["step"], slot["slot"])] = best["id"]
    return switched
