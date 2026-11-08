"""Back office pages of the workbench: 模型设置 (providers, the model of each step, prices, daily budget, usage) and
each teacher license's quota (student seats and essays a month). Both need the site's admin sign-in."""

import json, re, time
from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from core import feedback as user_feedback
from core.app import admin_only, audit, db, integer_field, license_row
from . import llm, models_sync, month_start

AUTO_MODES = {"latest": "自动跟随最新版本", "replace": "只在下线时自动替换", "fixed": "固定，不自动改"}
from .worker import spent_today, today_start


def _float(name, low, high, label, default=None):
    raw = request.form.get(name, "").strip()
    if raw == "" and default is not None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ValueError(f"{label}要填数字") from None
    if not low <= value <= high:
        raise ValueError(f"{label}要在 {low}—{high} 之间")
    return value


def _provider_form(existing=None):
    name = " ".join(request.form.get("name", "").split())[:30]
    if not name:
        raise ValueError("请填写服务商名称")
    base_url = request.form.get("base_url", "").strip().rstrip("/")
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]  # the full endpoint pasted: keep the base
    if base_url.endswith("/messages") or "/anthropic" in base_url:
        raise ValueError("这里要填 OpenAI 兼容接口的地址（以 /v1 结尾，请求会发到 …/v1/chat/completions），不能填 Anthropic 格式的 /messages 地址")
    if not re.fullmatch(r"https?://[^\s/]+(/[^\s]*)?", base_url) or len(base_url) > 300:
        raise ValueError("服务地址要以 http:// 或 https:// 开头，例如 https://api.deepseek.com/v1")
    key = request.form.get("api_key", "").strip()
    if key and (len(key) > 500 or any(c.isspace() for c in key)):
        raise ValueError("API Key 格式不正确")
    if not key and not existing:
        raise ValueError("请填写 API Key")
    try:
        timeout = integer_field("timeout", 5, 600)
        concurrency = integer_field("concurrency", 1, 20)
    except ValueError:
        raise ValueError("超时要在 5—600 秒之间，同时请求数要在 1—20 之间") from None
    return name, base_url, key, timeout, concurrency


def _step_form():
    provider = request.form.get("provider_id", "")
    model = request.form.get("model", "").strip()
    if provider == "" or not model:
        return None  # clearing the slot
    if not db().execute("SELECT 1 FROM ess_providers WHERE id=?", (provider,)).fetchone():
        raise ValueError("请选择服务商")
    if len(model) > 100 or any(c.isspace() for c in model):
        raise ValueError("模型名称格式不正确")
    temperature = request.form.get("temperature", "").strip()
    max_tokens = request.form.get("max_tokens", "").strip()
    extra = request.form.get("extra", "").strip()
    if extra:
        try:
            parsed = json.loads(extra)
        except ValueError:
            raise ValueError("额外参数要是 JSON 对象，例如 {\"enable_thinking\": false}") from None
        if not isinstance(parsed, dict) or {"model", "messages"} & set(parsed):
            raise ValueError("额外参数要是 JSON 对象，而且不能包含 model 和 messages")
        extra = json.dumps(parsed, ensure_ascii=False)
    use_hours = request.form.get("use_hours", "").strip()
    if not llm.valid_use_hours(use_hours):
        raise ValueError("首选时段的格式是 18-8 或 0-8,18-24（北京时间，每天）")
    peak_hours = request.form.get("peak_hours", "").strip()
    if not llm.valid_peak_hours(peak_hours):
        raise ValueError("高峰时段的格式是 9-12,14-18（北京时间，周一到周五）")
    return {
        "provider_id": int(provider),
        "model": model,
        "temperature": _float("temperature", 0, 2, "温度") if temperature else None,
        "max_tokens": int(_float("max_tokens", 1, 200000, "最大输出 token")) if max_tokens else None,
        "step_timeout": int(_float("step_timeout", 5, 900, "超时")) if request.form.get("step_timeout", "").strip() else None,
        "extra": extra,
        "price_in": _float("price_in", 0, 1000, "输入价格", 0),
        "price_cached": _float("price_cached", 0, 1000, "缓存命中价格", 0),
        "price_out": _float("price_out", 0, 1000, "输出价格", 0),
        "peak_factor": _float("peak_factor", 1, 10, "高峰倍数", 1),
        "peak_hours": peak_hours,
        "use_hours": use_hours,
        "auto_mode": request.form.get("auto_mode") if request.form.get("auto_mode") in AUTO_MODES else "latest",
        "auto_price": int(bool(request.form.get("auto_price"))),
        "series": models_sync.series(model),
        "auto_note": "",
        "auto_alert": 0,
    }


FEEDBACK_VIEWS = {"pending": "待回复", "replied": "已回复", "all": "全部"}
FEEDBACK_WHERE = {"pending": " WHERE f.replied_at IS NULL", "replied": " WHERE f.replied_at IS NOT NULL", "all": ""}
FEEDBACK_PAGE = 30


def feedback_pending():
    return db().execute("SELECT count(*) FROM feedback WHERE replied_at IS NULL").fetchone()[0]


def register(app):
    app.jinja_env.globals["feedback_pending"] = feedback_pending

    @app.get("/admin/feedback")
    @admin_only
    def feedback_admin():
        view = request.args.get("view")
        view = view if view in FEEDBACK_VIEWS else "pending"
        d = db()
        where = FEEDBACK_WHERE[view]
        total = d.execute("SELECT count(*) FROM feedback f" + where).fetchone()[0]
        pages = max(1, (total + FEEDBACK_PAGE - 1) // FEEDBACK_PAGE)
        page = max(1, min(request.args.get("page", 1, type=int), pages))
        rows = d.execute(
            "SELECT f.*,p.nickname,l.buyer,l.enabled,l.archived_at FROM feedback f"
            " LEFT JOIN profiles p ON p.site=f.site AND p.license_id=f.license_id"
            " LEFT JOIN licenses l ON l.site=f.site AND l.id=f.license_id"
            + where
            + " ORDER BY f.created_at DESC,f.id DESC LIMIT ? OFFSET ?",
            (FEEDBACK_PAGE, (page - 1) * FEEDBACK_PAGE),
        ).fetchall()
        return render_template(
            "feedback_admin.html",
            rows=rows,
            view=view,
            views=FEEDBACK_VIEWS,
            total=total,
            page=page,
            pages=pages,
            max_reply=user_feedback.MAX_REPLY,
        )

    @app.post("/admin/feedback/<int:fid>/reply")
    @admin_only
    def feedback_reply(fid):
        view = request.form.get("view")
        back = url_for("feedback_admin", view=view if view in FEEDBACK_VIEWS else "pending")
        reply = request.form.get("reply", "").strip()
        if not 1 <= len(reply) <= user_feedback.MAX_REPLY:
            flash(f"回复要写 1—{user_feedback.MAX_REPLY} 个字", "error")
            return redirect(back)
        d = db()
        row = d.execute("SELECT site,license_id,replied_at FROM feedback WHERE id=?", (fid,)).fetchone()
        if not row:
            abort(404)
        # Changing an answer counts as a new one: the user is told again.
        d.execute("UPDATE feedback SET reply=?,replied_at=?,reply_seen_at=NULL WHERE id=?", (reply, int(time.time()), fid))
        # Not filed under license_id: the audit is this site's, and the suggestion may come from another site's license.
        audit("feedback_edit_reply" if row["replied_at"] else "feedback_reply", None, f"回复建议 #{fid}（{row['site']} 授权 #{row['license_id']}）")
        d.commit()
        flash("回复已发出，对方打开「改进建议」就能看到", "success")
        return redirect(back)

    @app.route("/admin/models", methods=["GET", "POST"])
    @admin_only
    def models_page():
        d = db()
        if request.method == "POST":
            action = request.form.get("action", "")
            d.execute("BEGIN IMMEDIATE")
            try:
                if action in ("provider_add", "provider_edit"):
                    existing = None
                    if action == "provider_edit":
                        existing = d.execute("SELECT * FROM ess_providers WHERE id=?", (request.form.get("id"),)).fetchone()
                        if not existing:
                            abort(404)
                    name, base_url, key, timeout, concurrency = _provider_form(existing)
                    if existing:
                        d.execute(
                            "UPDATE ess_providers SET name=?,base_url=?,timeout=?,concurrency=? WHERE id=?",
                            (name, base_url, timeout, concurrency, existing["id"]),
                        )
                        if key:
                            d.execute(
                                "UPDATE ess_providers SET key_enc=?,key_tail=? WHERE id=?",
                                (llm.encrypt_key(key), llm.key_tail(key), existing["id"]),
                            )
                    else:
                        d.execute(
                            "INSERT INTO ess_providers(name,base_url,key_enc,key_tail,timeout,concurrency,created_at) VALUES(?,?,?,?,?,?,?)",
                            (name, base_url, llm.encrypt_key(key), llm.key_tail(key), timeout, concurrency, int(time.time())),
                        )
                    if existing:
                        # Old version-check messages were about the old address or key: clear them and check again soon.
                        d.execute("UPDATE ess_steps SET auto_note='',auto_alert=0 WHERE provider_id=? AND auto_note LIKE '检查新版本失败%'", (existing["id"],))
                    d.execute("INSERT INTO ess_meta VALUES('models_checked','0') ON CONFLICT(key) DO UPDATE SET value='0'")
                    details = {"name": name, "base_url": base_url, "key_changed": bool(key), "timeout": timeout, "concurrency": concurrency}
                elif action == "provider_delete":
                    pid = request.form.get("id")
                    if d.execute("SELECT 1 FROM ess_steps WHERE provider_id=?", (pid,)).fetchone():
                        raise ValueError("还有步骤在用这个服务商，请先把那些步骤换掉")
                    d.execute("DELETE FROM ess_providers WHERE id=?", (pid,))
                    details = {"id": pid}
                elif action == "step":
                    step, slot = request.form.get("step"), request.form.get("slot")
                    if step not in llm.STEPS or slot not in llm.SLOTS:
                        abort(400)
                    values = _step_form()
                    if values is None:
                        d.execute("DELETE FROM ess_steps WHERE step=? AND slot=?", (step, slot))
                    else:
                        d.execute(
                            "INSERT INTO ess_steps(step,slot," + ",".join(values) + ") VALUES(?,?," + ",".join("?" * len(values)) + ")"
                            " ON CONFLICT(step,slot) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in values),
                            (step, slot, *values.values()),
                        )
                    details = {"step": step, "slot": slot, **(values or {"cleared": True})}
                elif action == "budget":
                    budget = _float("daily_budget", 0, 100000, "每日花费上限", 0)
                    rate = _float("usd_cny", 1, 20, "美元汇率", models_sync.USD_CNY)
                    trials = int(_float("trial_daily", 0, 10000, "每天试用次数", 50))
                    for key, value in (("daily_budget", budget), ("usd_cny", rate), ("trial_daily", trials)):
                        d.execute(
                            "INSERT INTO ess_meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value))
                        )
                    details = {"daily_budget": budget, "usd_cny": rate, "trial_daily": trials}
                elif action == "check_toggle":
                    on = request.form.get("check_enabled") == "1"
                    d.execute(
                        "INSERT INTO ess_meta VALUES('check_enabled',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if on else "0",)
                    )
                    details = {"check_enabled": on}
                elif action == "models_check":
                    # The worker runs the check within half a minute (model calls do not belong in a web request).
                    d.execute("INSERT INTO ess_meta VALUES('models_checked','0') ON CONFLICT(key) DO UPDATE SET value='0'")
                    details = {}
                elif action == "revert":
                    step, slot = request.form.get("step"), request.form.get("slot")
                    row = d.execute("SELECT * FROM ess_steps WHERE step=? AND slot=?", (step, slot)).fetchone()
                    if not row or not row["prev_model"]:
                        raise ValueError("没有可以改回的模型")
                    d.execute(
                        "UPDATE ess_steps SET model=?,prev_model=?,series=?,auto_mode='fixed',auto_note=?,auto_alert=0 WHERE step=? AND slot=?",
                        (row["prev_model"], row["model"], models_sync.series(row["prev_model"]),
                         f"已改回 {row['prev_model']}，版本策略改为固定", step, slot),
                    )
                    details = {"step": step, "slot": slot, "from": row["model"], "to": row["prev_model"]}
                else:
                    abort(400)
                audit("essay_" + action, details=json.dumps(details, ensure_ascii=False))
                d.commit()
                flash("已保存，马上生效", "success")
            except ValueError as e:
                d.rollback()
                flash(str(e), "error")
            return redirect(url_for("models_page") + request.form.get("anchor", ""))
        providers = d.execute("SELECT * FROM ess_providers ORDER BY id").fetchall()
        steps = {(r["step"], r["slot"]): r for r in d.execute("SELECT * FROM ess_steps")}
        budget = d.execute("SELECT value FROM ess_meta WHERE key='daily_budget'").fetchone()
        rate = d.execute("SELECT value FROM ess_meta WHERE key='usd_cny'").fetchone()
        from . import trial_daily

        checked = d.execute("SELECT value FROM ess_meta WHERE key='models_checked'").fetchone()
        seen = d.execute("SELECT value FROM ess_meta WHERE key='worker_seen'").fetchone()
        month = month_start()
        usage = {
            "today": spent_today(d),
            "month": d.execute("SELECT coalesce(sum(cost),0) FROM ess_usage WHERE created_at>=?", (month,)).fetchone()[0],
            "essays_today": d.execute(
                "SELECT count(DISTINCT submission_id) FROM ess_usage WHERE step='grade' AND ok=1 AND created_at>=?", (today_start(),)
            ).fetchone()[0],
            "by_model": d.execute(
                "SELECT model,step,count(*) calls,sum(ok) ok,sum(tokens_in) tin,sum(tokens_cached) tcached,sum(tokens_out) tout,sum(cost) cost"
                " FROM ess_usage WHERE created_at>=? GROUP BY model,step ORDER BY cost DESC",
                (month,),
            ).fetchall(),
            "by_license": d.execute(
                "SELECT u.license_id,l.buyer,count(DISTINCT u.submission_id) essays,sum(u.cost) cost FROM ess_usage u"
                " LEFT JOIN licenses l ON l.site='teacher' AND l.id=u.license_id WHERE u.created_at>=?"
                " GROUP BY u.license_id ORDER BY cost DESC LIMIT 20",
                (month,),
            ).fetchall(),
            "failures": d.execute(
                "SELECT j.*,s.status FROM ess_jobs j JOIN ess_submissions s ON s.id=j.submission_id"
                " WHERE j.error!='' ORDER BY j.id DESC LIMIT 10"
            ).fetchall(),
            "failed_calls": d.execute(
                "SELECT created_at,step,model,ms,error,submission_id FROM ess_usage WHERE ok=0 ORDER BY id DESC LIMIT 20"
            ).fetchall(),
            "queued": d.execute("SELECT count(*) FROM ess_jobs WHERE status IN ('queued','running')").fetchone()[0],
        }
        return render_template(
            "models.html",
            providers=providers,
            steps=steps,
            first_model={step: (llm.step_rows(d, step) or [{}])[0].get("model", "") for step in llm.STEPS},
            budget=float(budget[0]) if budget else 0,
            usd_cny=float(rate[0]) if rate else models_sync.USD_CNY,
            trial_daily=trial_daily(),
            models_checked=int(checked[0]) if checked else 0,
            check_enabled=llm.check_enabled(d),
            AUTO_MODES=AUTO_MODES,
            worker_seen=int(seen[0]) if seen else None,
            now=int(time.time()),
            usage=usage,
            STEPS=llm.STEPS,
            SLOTS=llm.SLOTS,
        )

    @app.post("/admin/models/test")
    @admin_only
    def models_test():
        """A tiny request through one provider (with a given model) or one configured step slot, run right here."""
        d = db()
        step, slot = request.form.get("step"), request.form.get("slot")
        if step:
            row = next((r for r in llm.step_rows(d, step) if r["slot"] == slot), None)
            if not row:
                return jsonify(ok=False, message="这个步骤还没有配置模型")
        else:
            provider = d.execute("SELECT * FROM ess_providers WHERE id=?", (request.form.get("provider_id"),)).fetchone()
            model = request.form.get("model", "").strip()
            if not provider or not model:
                return jsonify(ok=False, message="请选择服务商并填写模型名称")
            row = {
                **dict(provider), "provider_id": provider["id"], "provider_name": provider["name"], "model": model,
                "temperature": None, "max_tokens": None, "extra": "", "price_in": 0, "price_cached": 0, "price_out": 0,
                "peak_factor": 1, "peak_hours": "", "use_hours": "", "auto_mode": "fixed",
            }
        row = {**row, "timeout": min(row["timeout"] or 20, 20)}  # stays under the web worker's 30 s limit
        body = llm.request_body(row, [{"role": "user", "content": "请只回复两个字：你好"}], False)
        body["max_tokens"] = 1024  # thinking models (gemini-3.x-flash…) spend tokens reasoning before they answer
        started = time.time()
        try:
            content, tin, tcached, tout = llm.post(row, body)
        except llm.LLMError as e:
            return jsonify(ok=False, message=str(e), seconds=round(time.time() - started, 1))
        return jsonify(
            ok=True, message=content[:100] or "（返回了空内容）", seconds=round(time.time() - started, 1), tokens=[tin, tcached, tout]
        )

    @app.post("/admin/licenses/<int:lid>/essay_quota")
    @admin_only
    def essay_quota_save(lid):
        if not license_row(lid):
            abort(404)
        try:
            students = integer_field("students", 1, 2000)
            monthly = integer_field("monthly", 0, 100000)
        except ValueError:
            flash("学生名额要在 1—2000 之间，每月篇数要在 0—100000 之间", "error")
            return redirect(url_for("detail", lid=lid))
        d = db()
        d.execute(
            "INSERT INTO ess_quota VALUES(?,?,?) ON CONFLICT(license_id) DO UPDATE SET students=excluded.students,monthly=excluded.monthly",
            (lid, students, monthly),
        )
        audit("essay_quota", lid, f"学生 {students} 人，每月 {monthly} 篇")
        d.commit()
        flash("已保存名额", "success")
        return redirect(url_for("detail", lid=lid))

    @app.post("/admin/licenses/<int:lid>/trial_convert")
    @admin_only
    def trial_convert(lid):
        """Turns a free-trial account into a paid one in place: its class, students and essays stay."""
        from core.schema import end_of_day

        from . import features, is_trial, referral

        row = license_row(lid)
        if not row or row["archived_at"] is not None or not is_trial(lid):
            abort(404)
        buyer = request.form.get("buyer", "").strip()[:500]
        try:
            days = integer_field("days", 1, features.MAX_DAYS)
            students = integer_field("students", 1, 2000)
            monthly = integer_field("monthly", 0, 100000)
        except ValueError:
            flash(f"天数 1—{features.MAX_DAYS}，学生名额 1—2000，每月篇数 0—100000", "error")
            return redirect(url_for("detail", lid=lid))
        if not buyer:
            flash("请填写买家或订单备注", "error")
            return redirect(url_for("detail", lid=lid))
        d = db()
        d.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        d.execute("DELETE FROM ess_trials WHERE license_id=?", (lid,))
        d.execute("UPDATE licenses SET buyer=?,assigned_at=? WHERE site='teacher' AND id=?", (buyer, now, lid))
        d.execute(
            "INSERT INTO ess_quota VALUES(?,?,?) ON CONFLICT(license_id) DO UPDATE SET students=excluded.students,monthly=excluded.monthly",
            (lid, students, monthly),
        )
        features.set_expiry(d, lid, "essay", end_of_day(now + days * features.DAY), now=now)
        code = request.form.get("referral", "")
        if referral.normalize(code):  # a trial never meets the activation form: the admin enters the teacher's code
            try:
                referral.apply(d, lid, code, now)
            except ValueError as e:
                d.rollback()
                flash(f"没有转为正式：{e}", "error")
                return redirect(url_for("detail", lid=lid))
        audit("trial_convert", lid, f"试用转为正式：{buyer}；作文批改 {days} 天，学生 {students} 人，每月 {monthly} 篇")
        d.commit()
        flash("已转为正式授权，试用里的班级和作文都保留", "success")
        return redirect(url_for("detail", lid=lid))

    @app.post("/admin/licenses/<int:lid>/feature")
    @admin_only
    def feature_save(lid):
        import calendar

        from . import features

        row = license_row(lid)
        if not row or row["archived_at"] is not None:
            abort(404)
        feature = request.form.get("feature", "")
        if feature not in features.FEATURES:
            abort(400)
        d = db()
        d.execute("BEGIN IMMEDIATE")
        started = bool(d.execute("SELECT 1 FROM devices WHERE site='teacher' AND license_id=?", (lid,)).fetchone())
        name = features.FEATURES[feature]["name"]
        if request.form.get("action") == "expiry":
            raw = request.form.get("date", "")
            try:
                day = calendar.timegm(time.strptime(raw, "%Y-%m-%d"))
            except ValueError:
                d.rollback()
                flash("到期日格式不正确", "error")
                return redirect(url_for("detail", lid=lid))
            from core.schema import BEIJING

            features.set_expiry(d, lid, feature, day - BEIJING + 86399)
            audit("feature_expiry", lid, f"{name} 到期日设为 {raw}")
        else:
            try:
                days = integer_field("days", 1, features.MAX_DAYS)
            except ValueError:
                d.rollback()
                flash(f"天数要在 1—{features.MAX_DAYS} 之间", "error")
                return redirect(url_for("detail", lid=lid))
            features.grant(d, lid, feature, days, started)
            audit("feature_extend", lid, f"{name} 增加 {days} 天")
        d.commit()
        flash("已保存有效期", "success")
        return redirect(url_for("detail", lid=lid))
