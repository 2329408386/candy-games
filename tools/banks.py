"""Question banks: the single source of every game's questions.

  python tools/banks.py --check    validate every bank, its file format and the generated docs (run by the tests)
  python tools/banks.py --fix      rewrite banks in the canonical format and regenerate docs/<site>/*.md

Each game has sites/<site>/banks/<game>.json:
  type       renderer in shared-static/pk/ ("choice", "blanks", "tiles", "expand", "edit", "measure", "read",
             "handwrite" or "modes": hands-on modes mixed with choice questions, see types/modes.js)
  draw       how each player's 20 questions are drawn (see draw_plans); pk-core.js scales it to the 5—30 chosen in 设置
  ui         per-game options for the renderer (task, instruction, blankOptions, lettered, allowUnusedCards,
             joiner: text between word cards, units/unitNames: the start page offers 期末综合 or one unit)
English questions may add unit (one of ui.units), audio (a recording in sites/<site>/static/audio/, transcript
audioText), pictures (options shown as pictures) and figure "pic" with pic (pictures drawn under the question).
  meta       provenance shared by all questions
  questions  every question has id, explanation and source.label (shown above the question);
             source.detailTitle/source.detail are shown under the feedback
"""

import argparse, json, math, re, sys
from collections import Counter
from decimal import Decimal
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
    if game == "grammar":
        if len(options) != 3:
            errors.append("句子小侦探每题应有3个选项")
        if q.get("prompt", "").count("___") != 1:
            errors.append("句子小侦探的题干应有且只有一处 ___")
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


def check_read(q, bank, game):
    """读句子: the sentence the child reads (text), its meaning (zh) and the model reading (audio = text)."""
    errors = []
    if not q.get("text") or not q.get("zh"):
        errors.append("缺少 text 或 zh")
    if not q.get("audio") or q.get("audioText") != q.get("text"):
        errors.append("示范录音的 audioText 必须与 text 相同")
    return errors


HANDWRITE_KINDS = {"upper-to-lower": 1, "lower-to-upper": 1}


def check_handwrite(q, bank, game):
    """字母奇妙屋: answer is the letters to write, one per box ("Bb", "b" or "B")."""
    errors = []
    answer = q.get("answer", "")
    if q.get("kind") not in HANDWRITE_KINDS:
        return [f"未知 kind {q.get('kind')}"]
    if len(answer) != HANDWRITE_KINDS[q["kind"]] or not answer.isascii() or not answer.isalpha():
        errors.append(f"answer 应为 {HANDWRITE_KINDS[q['kind']]} 个字母：{answer}")
    elif len(answer) == 2 and (answer[0] != answer[0].upper() or answer[1] != answer[0].lower()):
        errors.append(f"两格题应先大写后小写：{answer}")
    if q["kind"] == "upper-to-lower" and q.get("shown") != answer.upper():
        errors.append("shown 应为对应大写")
    if q["kind"] == "lower-to-upper" and q.get("shown") != answer.lower():
        errors.append("shown 应为对应小写")
    if q.get("audio"):
        errors.append("字母奇妙屋不播放读音，不应有 audio")
    return errors


def oral_division(total, count):
    """Division taught by 三年级上册: 表内除法（24÷3）, 每一位都能整除（84÷4）, or 整十整百 ÷ 一位数（240÷3）."""
    if total % count:
        return False
    if count < 10 and total // count < 10:
        return True
    if all(int(d) % count == 0 for d in str(total)):
        return True
    trimmed = total
    while trimmed % 10 == 0:
        trimmed //= 10
    return total != trimmed and count < 10 and trimmed % count == 0


def angle_class(points, i):
    """Exact kind of a polygon's vertex from its coordinates; None when too close to a right angle to judge by eye."""
    n = len(points)
    (x, y), (ax, ay), (bx, by) = points[i], points[i - 1], points[(i + 1) % n]
    u, v = (ax - x, ay - y), (bx - x, by - y)
    dot = u[0] * v[0] + u[1] * v[1]
    if dot == 0:
        return "right"
    deg = math.degrees(math.acos(dot / (math.hypot(*u) * math.hypot(*v))))
    if abs(deg - 90) < 15:
        return None
    return "acute" if deg < 90 else "obtuse"


def is_convex(points):
    n, signs = len(points), set()
    for i in range(n):
        (ax, ay), (bx, by), (cx, cy) = points[i], points[(i + 1) % n], points[(i + 2) % n]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross:
            signs.add(cross > 0)
    return len(signs) == 1


KIND_NAME = {"right": "直角", "acute": "锐角", "obtuse": "钝角"}
CUT_CORNERS = {"two-corners": 3, "corner-side": 4, "two-sides": 5}


def deg_kind(deg):
    return "right" if deg == 90 else "acute" if deg < 90 else "obtuse"


def check_outfit(q):
    errors = []
    calc = q.get("calc")
    if q.get("mode") == "dress":
        names = [i["name"] for i in q["tops"] + q["bottoms"]]
        if len(set(names)) != len(names):
            errors.append("衣服名字重复")
        if len(q["tops"]) * len(q["bottoms"]) > 9:
            errors.append("换装题最多 9 套，否则点不完")
    elif q["category"] == "missing":
        all_lines = {f"{t['name']}＋{b['name']}" for t in q["rows"][0]["items"] for b in q["rows"][1]["items"]}
        gone = all_lines - set(q["table"])
        if gone != {q["answer"]} or len(q["table"]) != len(all_lines) - 1:
            errors.append(f"记录表应正好漏一套：{gone}")
        if any(o != q["answer"] and o not in q["table"] for o in q["options"]):
            errors.append("干扰项应是记录表里已有的搭配")
    elif calc["kind"] == "product":
        if math.prod(calc["factors"]) != q["answer"]:
            errors.append(f"搭配数错误：{calc['factors']} → {q['answer']}")
        if "rows" in q and [len(r["items"]) for r in q["rows"]] != calc["factors"]:
            errors.append("图上的件数和算式不一致")
        if "route" in q and [q["route"]["n1"], q["route"]["n2"]] != calc["factors"]:
            errors.append("路线图的条数和算式不一致")
    elif calc["kind"] == "quotient":
        if calc["known"] * q["answer"] != calc["total"]:
            errors.append(f"反推错误：{calc}")
    return errors


def check_angles(q):
    errors = []
    fig = q.get("fig", {})
    if q.get("mode") == "turn":
        if q["start"] % q["step"] or not q["step"] <= q["start"] <= 180 - q["step"]:
            errors.append("起始角度不在转动范围内")
        if deg_kind(q["start"]) == q["target"]:
            errors.append("一开始就已经符合要求")
        if q["target"] == "right" and 90 % q["step"]:
            errors.append("每次转动的角度转不到直角")
    elif q.get("mode") == "pick":
        points = q["points"]
        if not is_convex(points):
            errors.append("图形不是凸多边形，角的类别难以判断")
        kinds = [angle_class(points, i) for i in range(len(points))]
        if None in kinds:
            errors.append(f"有接近直角的角，不好辨认：{kinds}")
        if [i for i, k in enumerate(kinds) if k == q["target"]] != q["answer"]:
            errors.append(f"答案和坐标算出的不一致：{kinds} → {q['answer']}")
        if not q["answer"]:
            errors.append("没有可选的角")
    elif fig.get("kind") == "angle":
        if KIND_NAME[deg_kind(fig["deg"])] != q["answer"]:
            errors.append(f"看图辨角答案错误：{fig['deg']} → {q['answer']}")
    elif fig.get("kind") == "clock":
        h = fig["hour"]
        if KIND_NAME[deg_kind(min(h, 12 - h) * 30)] != q["answer"]:
            errors.append(f"钟面角答案错误：{h}时 → {q['answer']}")
    elif fig.get("kind") == "compare":
        a, b = fig["a"]["deg"], fig["b"]["deg"]
        if ("角①大" if a > b else "角②大" if b > a else "一样大") != q["answer"]:
            errors.append(f"比较角的答案错误：{a}、{b} → {q['answer']}")
    elif fig.get("kind") == "rays":
        n = len(fig["rays"])
        if n * (n - 1) // 2 != q["answer"]:
            errors.append(f"数角错误：{n} 条线 → {q['answer']}")
    elif fig.get("kind") == "poly":
        points = fig["points"]
        want = len(points) if q["count"] == "all" else [angle_class(points, i) for i in range(len(points))].count(q["count"])
        if want != q["answer"]:
            errors.append(f"数角错误：应为 {want}")
    elif fig.get("kind") == "cut":
        if CUT_CORNERS[fig["cut"]] != q["answer"]:
            errors.append(f"剪角答案错误：{fig['cut']} → {q['answer']}")
    return errors


def check_odometer(q):
    errors = []
    # 三年级的孩子算一千以内的数：题里出现的里程、读数、答案都不超过 1000。
    text = json.dumps({k: v for k, v in q.items() if k != "source"}, ensure_ascii=False)
    big = sorted({int(n) for n in re.findall(r"\d+", text) if int(n) > 1000})
    if big:
        errors.append(f"出现超过 1000 的数：{big}")
    stops = q.get("stops")
    if stops and [s["km"] for s in stops] != sorted({s["km"] for s in stops}):
        errors.append("里程应从小到大且不重复")
    calc = q.get("calc", {})
    km = [s["km"] for s in stops] if stops else []
    want = None
    if q.get("mode") == "drive":
        if abs(km[q["answer"]] - km[q["from"]]) != q["km"] or q["answer"] == q["from"]:
            errors.append(f"开到哪一站错误：{q['km']} → {q['answer']}")
        return errors
    kind = calc.get("kind")
    if kind == "between":
        want = km[calc["to"]] - km[calc["from"]]
    elif kind == "sum":
        want = km[calc["to"]] - km[calc["from"]]
    elif kind == "round":
        want = 2 * (km[calc["to"]] - km[calc["from"]])
    elif kind == "back":
        want = (km[calc["far"]] - km[calc["from"]]) + (km[calc["far"]] - km[calc["to"]])
    elif kind == "meaning":
        a, b = calc["from"], calc["to"]
        if f"{km[b]}－{km[a]}" not in q["prompt"] or q["answer"] != f"{stops[a]['name']}—{stops[b]['name']}":
            errors.append("算式和答案的两站不一致")
        if any(o.split("—")[0] == o.split("—")[1] for o in q["options"]):
            errors.append("选项的两站相同")
    elif kind == "reading":
        r = [x["value"] for x in q["readings"]]
        if r != sorted(r):
            errors.append("读数应递增")
        want = r[calc["to"]] - r[calc["from"]]
    elif kind == "before":
        want = q["readings"][0]["value"] - calc["driven"]
    elif kind == "enough":
        need = calc["target"] - q["readings"][-1]["value"]
        if ("够" if calc["fuel"] >= need else "不够") != q["answer"] or need <= 0:
            errors.append(f"够不够错误：还要 {need}，能跑 {calc['fuel']}")
    else:
        errors.append(f"未知的里程题：{kind}")
    if want is not None:
        if q["fields"][0]["answer"] != str(want):
            errors.append(f"路程错误：应为 {want}，题库 {q['fields'][0]['answer']}")
        if kind in ("between", "reading", "before") and not 0 < want <= 1000:
            errors.append(f"结果超出范围：{want}")
    return errors


FORMULA = str.maketrans({"×": "*", "÷": "/", "＋": "+", "－": "-"})


def eval_formula(text):
    if not re.fullmatch(r"[\d×÷＋－()]+", text):
        raise ValueError(text)
    return eval(text.translate(FORMULA), {"__builtins__": {}})  # text is limited to digits and operators above


def check_shop(q):
    errors = []
    calc = q.get("calc", {})
    if q.get("mode") == "cart":
        total = sum(q["items"][o["item"]]["price"] * o["count"] for o in q["order"])
        if str(total) != str(q["total"]):
            errors.append(f"总价错误：应为 {total}")
        if len({o["item"] for o in q["order"]}) != len(q["order"]):
            errors.append("订单重复")
    elif q["category"] == "enough":
        cost = calc["price"] * calc["count"]
        if q.get("mode") == "fill":
            if str(abs(calc["wallet"] - cost)) != q["fields"][0]["answer"]:
                errors.append("找零或差额错误")
            if calc["wallet"] < cost and "差" not in q["prompt"]:
                errors.append("钱不够却没问还差多少")
        elif ("够" if calc["wallet"] >= cost else "不够") != q["answer"]:
            errors.append(f"够不够错误：{cost} 元，有 {calc['wallet']} 元")
    elif q["category"] == "most":
        n, r = q["fields"][0]["answer"], q["fields"][1]["answer"]
        if calc["wallet"] != int(n) * calc["price"] + int(r) or not 0 <= int(r) < calc["price"]:
            errors.append("最多买几个错误")
        if calc["price"] > 9 or int(n) > 9:
            errors.append("超出表内除法范围")
    elif q["category"] == "price":
        if int(q["fields"][0]["answer"]) * calc["count"] != calc["total"]:
            errors.append("单价错误")
        if not oral_division(calc["total"], calc["count"]):
            errors.append(f"{calc['total']}÷{calc['count']} 超出三上的除法范围")
    elif q["category"] == "formula":
        if eval_formula(q["answer"]) != q["result"]:
            errors.append(f"算式错误：{q['answer']} ≠ {q['result']}")
        if [o for o in q["options"] if o != q["answer"] and eval_formula(o) == q["result"]]:
            errors.append("干扰算式的结果也对")
    elif q["category"] == "deal":
        each = {s["name"]: s["total"] / s["count"] for s in q["stores"]}
        cheapest = min(each.values())
        if [n for n, v in each.items() if v == cheapest] != [q["answer"]]:
            errors.append(f"最便宜的店错误：{each}")
        for s in q["stores"]:
            if not oral_division(s["total"], s["count"]):
                errors.append(f"{s['total']}÷{s['count']} 超出三上的除法范围")
    return errors


def check_decimals(q):
    errors = []
    calc = q.get("calc", {})
    kind = calc.get("kind")
    if q.get("mode") == "pay":
        if int(Decimal(q["priceText"].removesuffix("元")) * 100) != q["fen"]:
            errors.append(f"价格和分数不一致：{q['priceText']} / {q['fen']}")
    elif q.get("mode") == "stick":
        if not 0 < q["answer"] < q["metres"] * 10 or Decimal(calc["metres"]) * 10 != q["answer"]:
            errors.append(f"尺上位置错误：{calc} → {q['answer']}")
        if str(calc["metres"]) not in q["prompt"]:
            errors.append("题干里没有要找的小数")
    elif kind == "bug":
        if Decimal(q["fields"][0]["answer"]) * 10 != q["dm"]:
            errors.append(f"小虫位置错误：{q['dm']}分米 → {q['fields'][0]['answer']}米")
    elif kind == "convert":
        got = sum(Decimal(f["answer"]) * w for f, w in zip(q["fields"], calc["weights"]))
        if got != calc["value"]:
            errors.append(f"改写错误：应为 {calc['value']}，算得 {got}")
    elif kind == "compare":
        a, b = calc["a"], calc["b"]
        if (">" if a > b else "<" if a < b else "=") != q["answer"]:
            errors.append(f"比大小错误：{a}、{b} → {q['answer']}")
    elif kind == "cheapest":
        low = min(calc["prices"].values())
        if [k for k, v in calc["prices"].items() if v == low] != [q["answer"]]:
            errors.append(f"最便宜的店错误：{calc['prices']}")
    elif kind == "add" or kind == "sub":
        a, b = Decimal(calc["a"]), Decimal(calc["b"])
        want = a + b if kind == "add" else a - b
        if want != Decimal(q["fields"][0]["answer"]) or want < 0:
            errors.append(f"小数加减错误：{a}、{b} → {q['fields'][0]['answer']}")
        if a.as_tuple().exponent < -1 or b.as_tuple().exponent < -1:
            errors.append("加减只用一位小数")
    elif kind == "gate":
        gate = q["gate"]
        high = Decimal(str(gate["truck"])) + Decimal(str(gate["load"]))
        if high == Decimal(str(gate["limit"])) or ("能" if high < Decimal(str(gate["limit"])) else "不能") != q["answer"]:
            errors.append(f"限高判断错误：{gate} → {q['answer']}")
    return errors


MODE_CHECKS = {"outfit": check_outfit, "angles": check_angles, "odometer": check_odometer, "shop": check_shop, "decimals": check_decimals}


def check_modes(q, bank, game):
    mode = q.get("mode")
    errors = []
    if mode is None:
        errors += check_choice(q, bank, game)
    elif not q.get("prompt"):
        errors.append("缺少题干")
    if mode == "fill":
        for field in q.get("fields") or [{}]:
            if not re.fullmatch(r"\d+(\.\d+)?", str(field.get("answer"))):
                errors.append(f"填空答案不是数字：{field}")
    return errors + MODE_CHECKS[game](q)


def modes_answer(q):
    mode = q.get("mode")
    if mode is None:
        return q.get("answerText", f"{q['answer']}{q.get('optionSuffix', '')}")
    if mode == "fill":
        return q.get("answerText") or " ".join(f.get("before", "") + f["answer"] + f.get("unit", "") for f in q["fields"])
    if mode == "dress":
        return f"{len(q['tops'])}×{len(q['bottoms'])}＝{len(q['tops']) * len(q['bottoms'])} 套"
    if mode == "turn":
        return KIND_NAME[q["target"]]
    if mode == "pick":
        return "、".join("ABCDEFGH"[i] for i in q["answer"]) + f"（{q['shape']}）"
    if mode == "drive":
        return q["stops"][q["answer"]]["name"]
    if mode == "cart":
        return q["calcText"]
    if mode == "pay":
        return q["priceText"]
    if mode == "stick":
        return f"{q['answer']} 分米处"
    raise ValueError(mode)


CHECKS = {
    "choice": check_choice,
    "blanks": check_blanks,
    "tiles": check_tiles,
    "expand": check_expand,
    "edit": check_edit,
    "measure": check_measure,
    "read": check_read,
    "handwrite": check_handwrite,
    "modes": check_modes,
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
        elif t == "read":
            yield f"- {q['id']}（{q['unit']}）：**{q['text']}** {q['zh']}"
        elif t == "handwrite":
            yield f"- {q['id']}：{q['prompt']} 答案：**{q['answer']}**"
        elif t == "modes":
            options = f" 选项：{' / '.join(map(str, q['options']))}" if "options" in q else ""
            yield f"- {q['id']}（{q['category']}）：{q['prompt']}{options} 答案：**{modes_answer(q)}**"
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
        draw = "每人从全部题目中随机抽题（默认20题，设置里可改5—30题），双方不重复。"
    else:
        draw = (
            "每人默认20题（设置里可改5—30题，按比例抽），按分类抽取，双方不重复："
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
