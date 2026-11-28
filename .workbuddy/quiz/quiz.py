# -*- coding: utf-8 -*-
"""09 面试答辩手册 · 背诵状态机（唯一数据源：state.json）

用法（在项目根目录或任意目录执行）：
  python .workbuddy/quiz/quiz.py init      # 首次建库；已存在则拒绝覆盖，防止进度分叉
  python .workbuddy/quiz/quiz.py next      # 出本轮 5 题（数量见 config），写入 current
  python .workbuddy/quiz/quiz.py extend 2  # 当前轮追加 2 道新题，不增加轮次
  python .workbuddy/quiz/quiz.py answer Q1 # 查看标准答案（仅供评分，不给答题者）
  python .workbuddy/quiz/quiz.py record Q1 85 "漏点备注"
  python .workbuddy/quiz/quiz.py stats     # 覆盖率 / 池子 / 最近成绩
  python .workbuddy/quiz/quiz.py config pool_per_round 1   # 改规则
"""
import io
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "09-面试项目答辩手册.md"
STATE = Path(__file__).resolve().parent / "state.json"

DEFAULT_CONFIG = {
    "pass_score": 80,           # > 该分算通过（"80 分以上"）
    "streak_to_pass": 1,        # 连续通过几次才出池
    "questions_per_round": 5,   # 每轮题量
    "pool_per_round": 1,        # 每轮回炉题数量（其余必须是新题：5 题 = 4 新 + 1 回炉）
    "cooldown_rounds": 0,       # 错题隔几轮才能再抽（0 = 下一轮即可）
    "max_long_per_round": 1,    # 每轮最多几道 >=200 字的展开题
    "long_threshold": 200,
}


def load():
    if not STATE.exists():
        sys.exit("状态文件不存在，请先执行 init")
    return json.loads(io.open(STATE, encoding="utf-8").read())


def save(s):
    with io.open(STATE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)


def parse_doc():
    lines = io.open(DOC, encoding="utf-8").read().split("\n")
    chap, out = "", {}
    for i, l in enumerate(lines, 1):
        if l.startswith("## "):
            chap = l.lstrip("# ").strip()
        m = re.match(r"^\*\*(Q-[A-Z]+\d+|Q\d+)\.\s*(.*?)\*\*\s*$", l)
        if not m:
            continue
        qid, title = m.group(1), m.group(2)
        ans = ""
        for j in range(i, min(i + 6, len(lines))):
            if lines[j].startswith("A："):
                ans = lines[j][2:]
                break
        if not ans:
            continue
        out[qid] = {
            "title": title, "line": i, "ans_len": len(ans),
            "chapter": chap, "status": "new", "history": [],
            "streak": 0, "times": 0, "last_round": 0,
        }
    return out


def cmd_init():
    if STATE.exists():
        sys.exit("state.json 已存在，拒绝重建（防止进度分叉）。如需重置请手动删除。")
    s = {
        "source": "docs/09-面试项目答辩手册.md",
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "config": dict(DEFAULT_CONFIG),
        "round": 0,
        "current": [],
        "pool": [],
        "questions": parse_doc(),
    }
    save(s)
    print("初始化完成：%d 题，状态文件 %s" % (len(s["questions"]), STATE))


def answer_of(qid):
    lines = io.open(DOC, encoding="utf-8").read().split("\n")
    st = load()["questions"].get(qid)
    if not st:
        return "未找到该题"
    buf = []
    for l in lines[st["line"] - 1: st["line"] + 8]:
        if l.startswith("A："):
            buf.append(l[2:])
        elif buf and (l.startswith("**Q") or l.startswith("## ") or l.startswith("> ") or l.startswith("| ") or l.startswith("```")):
            break
        elif buf and l.strip() and not l.startswith("A："):
            buf.append(l)
    return "\n".join(buf).strip()


def cmd_next():
    s = load()
    c = s["config"]
    # 上一轮若还有未评分的题，拒绝推进
    if s["current"]:
        sys.exit("上一轮还有未评分的题：%s，请先 record" % ", ".join(s["current"]))
    s["round"] += 1
    rnd = s["round"]
    target = c["questions_per_round"]
    pool = [q for q in s["pool"] if rnd - s["questions"][q]["last_round"] >= c["cooldown_rounds"]]
    pool.sort(key=lambda q: (s["questions"][q]["last_round"],
                             (s["questions"][q]["history"] or [{}])[-1].get("score", 0)))
    take_pool = min(c["pool_per_round"], len(pool), target - 1)
    picked = [(q, "回炉") for q in pool[:take_pool]]

    new_ids = [q for q, v in s["questions"].items() if v["status"] == "new"]
    random.shuffle(new_ids)
    long_used = sum(1 for q, _ in picked if s["questions"][q]["ans_len"] >= c["long_threshold"])
    for q in new_ids:
        if len(picked) >= target:
            break
        is_long = s["questions"][q]["ans_len"] >= c["long_threshold"]
        if is_long and long_used >= c["max_long_per_round"]:
            continue
        # 尽量不与紧邻前一题同章
        if picked and s["questions"][q]["chapter"] == s["questions"][picked[-1][0]]["chapter"] and len(new_ids) > target:
            continue
        picked.append((q, "新题"))
        long_used += int(is_long)
    # 新题耗尽后，用已通过题做保温复验
    if len(picked) < target:
        passed = [q for q, v in s["questions"].items() if v["status"] == "passed" and q not in [p[0] for p in picked]]
        random.shuffle(passed)
        for q in passed[:target - len(picked)]:
            picked.append((q, "保温"))

    random.shuffle(picked)
    s["current"] = [q for q, _ in picked]
    for q, _ in picked:
        v = s["questions"][q]
        v["times"] += 1
        v["last_round"] = rnd
    save(s)
    print("第 %d 轮" % rnd)
    for n, (q, kind) in enumerate(picked, 1):
        v = s["questions"][q]
        print("%d. %s  【%s · %s · %d字】\n   %s" % (n, q, kind, v["chapter"][:10], v["ans_len"], v["title"]))
    print("\n(待评测池 %d 题 / 未覆盖新题 %d 题)" % (len(s["pool"]), sum(1 for v in s["questions"].values() if v["status"] == "new")))


def cmd_extend(n):
    """在当前轮追加新题（不增加轮次），用于半路把 3 题改成 5 题"""
    s = load()
    c = s["config"]
    if not s["current"]:
        sys.exit("当前没有进行中的轮次，请先 next")
    target = c["questions_per_round"]
    need = min(n, target - len(s["current"]))
    if need <= 0:
        sys.exit("本轮已满 %d 题" % target)
    existing = set(s["current"])
    cand = [q for q, v in s["questions"].items() if v["status"] == "new" and q not in existing]
    random.shuffle(cand)
    long_used = sum(1 for q in s["current"] if s["questions"][q]["ans_len"] >= c["long_threshold"])
    last_chapter = s["questions"][s["current"][-1]]["chapter"]
    added = []
    for q in cand:
        if len(added) >= need:
            break
        v = s["questions"][q]
        if v["ans_len"] >= c["long_threshold"] and long_used >= c["max_long_per_round"]:
            continue
        if v["chapter"] == last_chapter and len(cand) > need:
            continue
        added.append(q)
        long_used += int(v["ans_len"] >= c["long_threshold"])
        last_chapter = v["chapter"]
    if len(added) < need:
        passed = [q for q, v in s["questions"].items()
                  if v["status"] == "passed" and q not in existing and q not in added]
        random.shuffle(passed)
        added.extend(passed[:need - len(added)])
    for q in added:
        v = s["questions"][q]
        v["times"] += 1
        v["last_round"] = s["round"]
    s["current"].extend(added)
    save(s)
    print("第 %d 轮 · 补入 %d 题，本轮共 %d 题：" % (s["round"], len(added), len(s["current"])))
    for i, q in enumerate(s["current"], 1):
        v = s["questions"][q]
        print("%d. %s  【%s · %d字】\n   %s" % (i, q, v["chapter"][:10], v["ans_len"], v["title"]))


def cmd_record(qid, score, note=""):
    s = load()
    if qid not in s["current"]:
        sys.exit("%s 不在本轮题目中，本轮：%s" % (qid, ", ".join(s["current"])))
    c = s["config"]
    v = s["questions"][qid]
    v["history"].append({"round": s["round"], "score": score, "note": note})
    ok = score > c["pass_score"]
    if ok:
        v["streak"] += 1
        if v["streak"] >= c["streak_to_pass"]:
            v["status"] = "passed"
            if qid in s["pool"]:
                s["pool"].remove(qid)
            result = "通过 · 出池"
        else:
            v["status"] = "reviewing"
            if qid not in s["pool"]:
                s["pool"].append(qid)
            result = "本次通过 · 需再复验 1 次（%d/%d）" % (v["streak"], c["streak_to_pass"])
    else:
        v["streak"] = 0
        v["status"] = "learning"
        if qid not in s["pool"]:
            s["pool"].append(qid)
        result = "未通过 · 回待评测池"
    s["current"].remove(qid)
    save(s)
    print("%s %d 分 → %s；本轮剩余未评分：%s" % (qid, score, result, ", ".join(s["current"]) or "无，可进入下一轮"))


def cmd_stats():
    s = load()
    qs = s["questions"]
    cnt = Counter(v["status"] for v in qs.values())
    scores = [h["score"] for v in qs.values() for h in v["history"]]
    print("轮次 %d ｜ 已通过 %d ｜ 待评测池 %d ｜ 未覆盖 %d ｜ 总题数 %d" %
          (s["round"], cnt["passed"], len(s["pool"]), cnt["new"], len(qs)))
    print("通过率 %.1f%% ｜ 累计作答 %d 次 ｜ 平均 %.1f 分" %
          (100 * cnt["passed"] / len(qs), len(scores), sum(scores) / len(scores) if scores else 0))
    print("状态分布：", dict(cnt))
    print("当前规则：", json.dumps(s["config"], ensure_ascii=False))
    if scores:
        print("最近 8 次：", scores[-8:])
    if s["current"]:
        print("本轮待评分：", ", ".join(s["current"]))


def cmd_config(key, val):
    s = load()
    try:
        v = json.loads(val)
    except Exception:
        v = val
    s["config"][key] = v
    save(s)
    print("%s → %s" % (key, s["config"][key]))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "init":
        cmd_init()
    elif a[0] == "next":
        cmd_next()
    elif a[0] == "extend":
        cmd_extend(int(a[1]))
    elif a[0] == "answer":
        print(answer_of(a[1]))
    elif a[0] == "record":
        cmd_record(a[1], int(a[2]), a[3] if len(a) > 3 else "")
    elif a[0] == "stats":
        cmd_stats()
    elif a[0] == "config":
        cmd_config(a[1], a[2])
    else:
        sys.exit(__doc__)
