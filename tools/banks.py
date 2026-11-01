"""Question banks: the single source of every game's questions.

  python tools/banks.py --check    validate every bank, its file format and the generated docs (run by the tests)
  python tools/banks.py --fix      rewrite banks in the canonical format and regenerate docs/<site>/*.md

Each game has sites/<site>/banks/<game>.json:
  type       renderer in shared-static/pk/ ("choice", "blanks", "tiles", "expand", "edit" or "measure")
  draw       how each player's 20 questions are drawn (see draw_plans)
  ui         per-game options for the renderer (task, instruction, blankOptions, lettered, allowUnusedCards,
             joiner: text between word cards, units/unitNames: the start page offers 期末综合 or one unit)
English questions may add unit (one of ui.units), audio (a recording in sites/<site>/static/audio/, transcript
audioText), pictures (options shown as pictures) and figure "pic" with pic (pictures drawn under the question).
  meta       provenance shared by all questions
  questions  every question has id, explanation and source.label (shown above the question);
             source.detailTitle/source.detail are shown under the feedback
"""

import argparse, json, re, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sites import SITES  # noqa: E402

PER_PLAYER = 20
FACTORS = {"毫米": 1, "厘米": 10, "分米": 100, "米": 1000, "千米": 1000000}
EIGHT = {
    (0, -1): "北",
    (1, -1): "东北",
    (1, 0): "东",
    (1, 1): "东南",
    (0, 1): "南",
    (-1, 1): "西南",
    (-1, 0): "西",
    (-1, -1): "西北",
}
BLANK = re.compile(r"\[(\d+)\]")
RHETORICAL_MARKS = ("难道", "怎么", "什么", "吗", "呢", "？")
NEGATIONS = ("不", "没", "没有", "不能", "不会", "不肯", "从不")


def bank_path(site, game):
    return ROOT / "sites" / site / "banks" / f"{game}.json"


def load(site, game):
    return json.loads(bank_path(site, game).read_text(encoding="utf-8"))


# ---- canonical file format: short values stay on one line, everything else is expanded ----
def dumps(value, indent=0, width=118):
    flat = json.dumps(value, ensure_ascii=False)
    if not isinstance(value, (dict, list)) or len(flat) + indent <= width and indent > 0:
        return flat
    pad, inner = " " * indent, " " * (indent + 1)
    if isinstance(value, dict):
        if not value:
            return "{}"
        items = [f"{inner}{json.dumps(k, ensure_ascii=False)}: {dumps(v, indent + 1, width)}" for k, v in value.items()]
        return "{\n" + ",\n".join(items) + "\n" + pad + "}"
    if not value:
        return "[]"
    return "[\n" + ",\n".join(inner + dumps(v, indent + 1, width) for v in value) + "\n" + pad + "]"


def canonical(bank):
    return dumps(bank) + "\n"


# ---- drawing ----
def draw_key(q, by):
    return "/".join(str(q.get(field)) for field in by)


def draw_plans(bank):
    """Every per-player quota the game may use: [{key: count}], or [None] for a plain random draw."""
    draw = bank["draw"]
    if "count" in draw:
        return [None]
    return draw.get("alternatives") or [draw["counts"]]


# ---- per-type checks; each returns a list of problems for one question ----
def audio_dir(site):
    return ROOT / "sites" / site / "static" / "audio"


def check_english(q, bank, site):
    """Fields shared by the English games: unit, audio and pictures (picture names are checked in test_engine.cjs)."""
    errors = []
    units = bank["ui"].get("units")
    if units and q.get("unit") not in units:
        errors.append(f"unit 不在 ui.units 中：{q.get('unit')}")
    if "audio" in q:
        if not q.get("audioText"):
            errors.append("有录音但缺少 audioText")
        if not (audio_dir(site) / q["audio"]).is_file():
            errors.append(f"缺少录音文件 {q['audio']}（运行 tools/english_audio.py）")
    if q.get("figure") == "pic" and not q.get("pic"):
        errors.append("figure 为 pic 但缺少 pic")
    return errors


def check_choice(q, bank, game):
    errors = []
    options = q.get("options") or []
    if not isinstance(q.get("prompt"), str) or not q["prompt"]:
        errors.append("缺少题干")
    if len(options) < 2 or len(set(map(str, options))) != len(options):
        errors.append(f"选项少于2个或重复：{options}")
    if q.get("answer") not in options:
        errors.append(f"答案 {q.get('answer')!r} 不在选项中")
    if "focus" in q:
        start, length = q["focus"]
        if not (0 <= start and length > 0 and start + length <= len(q["prompt"])):
            errors.append(f"focus 越界：{q['focus']}")
        elif q.get("blank") and q["prompt"].count(q["prompt"][start : start + length]) > 1:
            errors.append("挖空的字在句中其他位置出现，答案会被看到")
    elif q.get("blank"):
        errors.append("blank 需要 focus")
    if q.get("blank") and q["prompt"][q["focus"][0] : q["focus"][0] + q["focus"][1]] != q["answer"]:
        errors.append("空格处原字与答案不一致")
    if "targetWord" in q and q["prompt"][q["focus"][0]] not in q["targetWord"]:
        errors.append("标出的字不在目标词中")
    if game == "similar-char":
        if not 3 <= len(options) <= 5:
            errors.append("形近字题应有3~5个选项（2~4个干扰字）")
        if q.get("origin") not in ("textbook", "extension"):
            errors.append("缺少 origin")
    if game == "conversion":
        given = (
            sum(v * FACTORS[u] for v, u in zip(q["givenParts"], q["givenUnits"]))
            if "givenParts" in q
            else q["given"] * FACTORS[q["fromUnit"]]
        )
        if given != q["answer"] * FACTORS[q["toUnit"]]:
            errors.append(f"换算答案错误：{q['prompt']} = {q['answer']}{q['toUnit']}")
    if game == "mental":
        expected = q["a"] + q["b"] if q["operator"] == "+" else q["a"] - q["b"]
        if expected != q["answer"]:
            errors.append(f"口算答案错误：{q['a']}{q['operator']}{q['b']}={q['answer']}")
    if game == "directions":
        if q["category"] == "eight":
            dx, dy = q["target"]["x"] - q["ref"]["x"], q["target"]["y"] - q["ref"]["y"]
            sign = (dx > 0) - (dx < 0), (dy > 0) - (dy < 0)
            if abs(dx) != abs(dy) and dx and dy or EIGHT.get(sign) != q["answer"]:
                errors.append(f"方向答案错误：{q['answer']}")
        else:
            target = (q["ref"]["clock"] + 6) % 12 if q["target"].get("center") else q["target"]["clock"]
            if q["ref"].get("center"):
                target = q["target"]["clock"]
            expected = (target - q["heading"]) % 12 or 12
            if expected != q["answer"]:
                errors.append(f"几点钟方向答案错误：{q['answer']}，应为{expected}")
    return errors


def check_blanks(q, bank, game):
    errors, options = [], set(bank["ui"]["blankOptions"])
    slots = [int(i) for i in BLANK.findall(q["text"])]
    if slots != list(range(len(slots))) or not slots:
        errors.append("横线编号必须从[0]起连续")
    capacities = q.get("capacities", [1] * len(slots))
    for answers in [q["answers"]] + q.get("acceptedAnswers", []):
        if len(answers) != len(slots) or len(capacities) != len(slots):
            errors.append("答案数与横线数不一致")
            continue
        for answer, capacity in zip(answers, capacities):
            if len(answer) != capacity or not set(answer) <= options:
                errors.append(f"答案 {answer!r} 与横线容量 {capacity} 或可选符号不符")
    if q["acceptedAnswers"][0] != q["answers"] if "acceptedAnswers" in q else False:
        errors.append("acceptedAnswers 第一项必须是标准答案")
    filled = BLANK.sub(lambda m: q["answers"][int(m.group(1))], q["text"])
    if filled != q["complete"]:
        errors.append(f"填入答案后与完整句不一致：{filled}")
    return errors


def tile_order_ok(cards, answer, use_all, joiner=""):
    """Can the answer be spelled by joining distinct cards with joiner (all of them when use_all)?"""
    texts = [c["text"] for c in cards]

    def search(rest, left, first):
        if not rest:
            return not use_all or not left
        if not first:
            if not rest.startswith(joiner):
                return False
            rest = rest[len(joiner) :]
        return any(rest.startswith(t) and search(rest[len(t) :], left[:i] + left[i + 1 :], False) for i, t in enumerate(left))

    return search(answer, texts, True)


def tile_keep_ok(cards, answer):
    """Can the answer be obtained by keeping some cards in their order?"""
    texts = [c["text"] for c in cards]

    def search(i, rest):
        if i == len(texts):
            return not rest
        return search(i + 1, rest) or rest.startswith(texts[i]) and search(i + 1, rest[len(texts[i]) :])

    return search(0, answer)


def expand_answers(q):
    """Every sentence the slots allow: each slot puts one of its words at one of its positions, positions distinct."""
    tokens, results = q["tokens"], []

    def walk(i, placed):
        if i == len(q["slots"]):
            ins = dict(placed)
            text = "".join(ins.get(k, "") + t for k, t in enumerate(tokens)) + ins.get(len(tokens), "")
            if text not in results:
                results.append(text)
            return
        slot = q["slots"][i]
        for pos in slot["positions"]:
            for word in slot["words"]:
                if pos not in dict(placed) and word not in dict(placed).values():
                    walk(i + 1, placed + [(pos, word)])

    walk(0, [])
    return results


def check_expand(q, bank, game):
    errors, tokens = [], q.get("tokens", [])
    if "".join(tokens) != q.get("base"):
        errors.append("tokens 拼起来不等于原句")
    slots = q.get("slots", [])
    if len(slots) < 2:
        errors.append("至少要扩写两处")
    words = [w for s in slots for w in s["words"]]
    for s in slots:
        if not s["positions"] or not all(0 <= p <= len(tokens) for p in s["positions"]):
            errors.append(f"插入位置超出范围：{s}")
    if not set(words) <= set(q.get("cards", [])):
        errors.append("扩写用词不在词卡中")
    if len(set(q.get("cards", []))) != len(q.get("cards", [])):
        errors.append("词卡重复")
    if len(q.get("cards", [])) - len(set(words)) < 2:
        errors.append("至少要有两张干扰词卡")
    expected = expand_answers(q) if not errors else []
    if not errors and q.get("answers") != expected:
        errors.append(f"answers 应为 slots 的全部组合：{expected[:3]}…")
    if not q["source"].get("type") or q.get("origin") not in ("textbook", "extension"):
        errors.append("缺少 origin 或来源类型")
    return errors


def check_tiles(q, bank, game):
    errors, kind = [], q.get("kind")
    if not q.get("answers"):
        return ["缺少答案"]
    if kind in ("order", "keep"):
        ids = [c["id"] for c in q["cards"]]
        if len(set(ids)) != len(ids):
            errors.append("词卡 id 重复")
    for answer in q["answers"]:
        tail = q.get("tail", "")
        if kind == "order" and not (
            answer.endswith(tail)
            and tile_order_ok(
                q["cards"], answer[: len(answer) - len(tail)], not bank["ui"].get("allowUnusedCards"), bank["ui"].get("joiner", "")
            )
        ):
            errors.append(f"词卡拼不出答案：{answer}")
        if kind == "keep" and not (answer.endswith(tail) and tile_keep_ok(q["cards"], answer[: len(answer) - len(tail)])):
            errors.append(f"按顺序选词卡得不到答案：{answer}")
        if kind == "keep" and answer == q["prompt"]:
            errors.append("答案与原句相同，没有缩写")
    if kind == "keep":
        if "".join(c["text"] for c in q["cards"]) + q.get("tail", "") != q["prompt"]:
            errors.append("词卡按顺序拼起来不等于原句")
        if q.get("origin") not in ("textbook", "extension"):
            errors.append("缺少 origin")
        for c in q["cards"]:
            if c["text"] in NEGATIONS and not all(c["text"] in a for a in q["answers"]):
                errors.append(f"否定词“{c['text']}”应当保留")
    if kind not in ("order", "keep"):
        errors.append(f"未知题型 {kind}")
    if game == "rhetorical":
        texts = [c["text"] for c in q["cards"]]
        missing = [m for m in RHETORICAL_MARKS if m in q["prompt"] and m not in texts and m not in q["answers"][0]]
        if missing:
            errors.append(f"原句里的反问词没有放进词卡：{'、'.join(missing)}")
    return errors


def apply_edit(q, e):
    """The sentence after one edit; mirrors applyEdit in shared-static/pk/types/edit.js."""
    texts = {t["id"]: t["text"] for t in q["tokens"]}
    if e["kind"] == "insert":
        return q["original"][: e["position"]] + e["value"] + q["original"][e["position"] :]
    if e["kind"] == "swap":
        a, b = e["first"], e["second"]
        return "".join(texts[b] if t["id"] == a else texts[a] if t["id"] == b else t["text"] for t in q["tokens"])
    replacement = "" if e["kind"] == "delete" else e["value"]
    return "".join(replacement if t["id"] == e["target"] else t["text"] for t in q["tokens"])


def check_edit(q, bank, game):
    errors = [] if q.get("category") and q.get("subtype") else ["缺少 category 或 subtype"]
    if "".join(t["text"] for t in q["tokens"]) != q["original"]:
        errors.append("字词块拼不回原句")
    if q["original"] == q["corrected"]:
        errors.append("原句与改正句相同")
    starts = {0}
    for t in q["tokens"]:
        starts.add(max(starts) + len(t["text"]))
    results = []
    for e in q["accepted"]:
        if e["kind"] in ("replace", "insert") and e["value"] not in q["candidates"]:
            errors.append(f"字词卡中没有 {e['value']}")
        if e["kind"] == "insert" and e["position"] not in starts:
            errors.append(f"插入位置 {e['position']} 不在字词块之间")
        results.append(apply_edit(q, e))
    # accepted[0] is the standard correction; any further edits are other fixes that also keep the meaning.
    if not results or results[0] != q["corrected"]:
        errors.append(f"标准修改得不到改正句：{results[:1]}")
    if q["original"] in results or len(set(results)) != len(results):
        errors.append("其他改法与原句或彼此重复")
    return errors


def check_measure(q, bank, game):
    errors, units, parts = [], q["answerUnits"], q["answerParts"]
    if q["endMm"] - q["startMm"] != q["lengthMm"] or not 0 <= q["startMm"] < q["endMm"] <= q["rulerLengthMm"]:
        errors.append("起点、终点与长度不一致或超出尺子")
    if sum(v * FACTORS[u] for v, u in zip(parts, units)) != q["lengthMm"]:
        errors.append("答案各部分加起来不等于长度")
    if len(units) == 2:
        big, small = FACTORS[units[0]], FACTORS[units[1]]
        if big // small != 10:
            errors.append(f"复名数应使用相邻单位：{units}")
        if parts[1] >= big // small:
            errors.append("小单位部分应小于进率")
    if q["answer"] != "".join(f"{v}{u}" for v, u in zip(parts, units)):
        errors.append(f"answer 文本与各部分不一致：{q['answer']}")
    return errors


CHECKS = {
    "choice": check_choice,
    "blanks": check_blanks,
    "tiles": check_tiles,
    "expand": check_expand,
    "edit": check_edit,
    "measure": check_measure,
}


def check_bank(site, game, bank):
    errors = []
    questions = bank["questions"]
    ids = [q.get("id") for q in questions]
    for qid, n in Counter(ids).items():
        if n > 1:
            errors.append(f"题号重复：{qid}")
    if bank.get("type") not in CHECKS:
        return errors + [f"未知题型 {bank.get('type')}"]
    for q in questions:
        problems = []
        if not isinstance(q.get("explanation"), str) or not q["explanation"].strip():
            problems.append("缺少解析")
        if not isinstance(q.get("source", {}).get("label"), str):
            problems.append("缺少 source.label")
        if bool(q.get("source", {}).get("detail")) != bool(q.get("source", {}).get("detailTitle")):
            problems.append("source.detail 与 detailTitle 必须同时出现")
        problems += CHECKS[bank["type"]](q, bank, game)
        if site == "english":
            problems += check_english(q, bank, site)
        errors += [f"{q.get('id')}：{p}" for p in problems]
    units = bank["ui"].get("units")
    if units:
        pools = Counter(q.get("unit") for q in questions)
        for unit in units:
            if pools[unit] < PER_PLAYER:
                errors.append(f"单元 {unit} 只有 {pools[unit]} 题，单元闯关不够一人20题")
    for plan in draw_plans(bank):
        if plan is None:
            if len(questions) < 2 * PER_PLAYER:
                errors.append("题量不足两人各20题")
            continue
        if sum(plan.values()) != PER_PLAYER:
            errors.append(f"抽题配额合计应为{PER_PLAYER}：{plan}")
        pools = Counter(draw_key(q, bank["draw"]["by"]) for q in questions)
        for key, count in plan.items():
            if pools[key] < 2 * count:
                errors.append(f"抽题分类 {key} 只有 {pools[key]} 题，不够两人各 {count} 题")
    return errors


# ---- generated docs ----
def doc_rows(site, game, bank):
    t = bank["type"]
    for q in bank["questions"]:
        src = q["source"]
        if t == "choice":
            prompt = q["prompt"]
            if "focus" in q:
                s, n = q["focus"]
                prompt = prompt[:s] + ("（ ）" if q.get("blank") else f"【{prompt[s:s + n]}】") + prompt[s + n :]
            answer = q.get("answerText", q["answer"])
            if q.get("audioText"):
                prompt += f"（录音：{q['audioText']}）"
            if q.get("picText"):
                prompt += f"（{q['picText']}）"
            shown = "图片：" if q.get("pictures") else "选项："
            yield f"- {q['id']}：{prompt} {shown}{' / '.join(map(str, q['options']))} 答案：**{answer}**"
        elif t == "blanks":
            alt = (
                "；也接受 " + " 或 ".join(" / ".join(a) for a in q["acceptedAnswers"][1:])
                if q.get("acceptedAnswers")
                else ""
            )
            yield f"- {q['id']}：{BLANK.sub('（ ）', q['text'])} 答案：**{' / '.join(q['answers'])}**{alt}"
        elif t == "expand":
            more = f"（共{len(q['answers'])}种正确写法）" if len(q["answers"]) > 1 else ""
            others = "、".join(w for w in q["cards"] if w not in {x for s in q["slots"] for x in s["words"]})
            yield f"- {q['id']}：{q['base']} 答案：**{q['answers'][0]}**{more}　干扰词：{others}"
        elif t == "tiles":
            shown = q["prompt"] + ("（词卡：" + " / ".join(c["text"] for c in q["cards"]) + "）" if bank["ui"].get("joiner") else "")
            yield f"- {q['id']}：{shown} 答案：**{'；或 '.join(q['answers'])}**"
        elif t == "edit":
            others = [apply_edit(q, e) for e in q["accepted"][1:]]
            also = "；也接受 " + " 或 ".join(others) if others else ""
            yield f"- {q['id']}（{q['category']}·{q['subtype']}）：{q['original']} → **{q['corrected']}**{also}"
        elif t == "measure":
            yield (
                f"- {q['id']}：{q['object']}，起点{q['startMm']}毫米，终点{q['endMm']}毫米，"
                f"长{q['lengthMm']}毫米；答案 **{q['answer']}**"
            )
        yield f"  - 解析：{q['explanation']}　来源：{src['label']}"


def render_doc(site, game, bank):
    title = SITES[site].games[game]
    plans = draw_plans(bank)
    if plans == [None]:
        draw = "每人从全部题目中随机抽20题，双方不重复。"
    else:
        draw = (
            "每人20题，按分类抽取，双方不重复："
            + "；或 ".join("、".join(f"{k}×{v}" for k, v in plan.items() if v) for plan in plans)
            + "。"
        )
    counts = Counter(draw_key(q, bank["draw"].get("by", [])) for q in bank["questions"]) if "by" in bank["draw"] else {}
    lines = [
        f"# {title}题库复核清单",
        "",
        "> 本文件由 `python tools/banks.py --fix` 根据 "
        f"`sites/{site}/banks/{game}.json` 生成，请修改题库后重新生成，不要直接编辑。",
        "",
        f"共 {len(bank['questions'])} 题。{draw}",
    ]
    if bank["meta"].get("notes"):
        lines += [""] + [f"- {note}" for note in bank["meta"]["notes"]]
    if counts:
        lines += ["", "| 分类 | 题数 |", "|---|---:|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())]
    lines += ["", "## 逐题清单", ""] + list(doc_rows(site, game, bank))
    return "\n".join(lines) + "\n"


def doc_path(site, game):
    return ROOT / "docs" / site / f"{SITES[site].games[game]}题库.md"


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--fix", action="store_true")
    a = p.parse_args()
    problems, stale = [], []
    expected_docs = set()
    for site, config in SITES.items():
        for game in config.games:
            path = bank_path(site, game)
            if not path.exists():
                problems.append(f"{site}/{game}：缺少题库文件 {path.relative_to(ROOT)}")
                continue
            text = path.read_text(encoding="utf-8")
            bank = json.loads(text)
            problems += [f"{site}/{game} {e}" for e in check_bank(site, game, bank)]
            doc = doc_path(site, game)
            expected_docs.add(doc)
            outputs = {path: canonical(bank), doc: render_doc(site, game, bank)}
            for target, content in outputs.items():
                current = target.read_text(encoding="utf-8") if target.exists() else None
                if current != content:
                    stale.append(target)
                    if a.fix:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(content, encoding="utf-8")
    extra = [d for d in (ROOT / "docs").rglob("*题库.md") if d not in expected_docs]
    for p_ in problems:
        print("✗", p_)
    if a.fix:
        for d in extra:
            d.unlink()
        for target in stale:
            print("rewrite", target.relative_to(ROOT))
    elif stale or extra:
        for target in stale + extra:
            print("过期：", target.relative_to(ROOT))
        print("题库格式或生成的文档已过期，请运行 python tools/banks.py --fix")
    if problems or (a.check and (stale or extra)):
        sys.exit(1)
    print("题库检查通过")


if __name__ == "__main__":
    main()
