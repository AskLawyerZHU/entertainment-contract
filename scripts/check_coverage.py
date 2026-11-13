#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_coverage.py —— 娱乐法合同技能自检（覆盖率 / 一致性 / 引用真实性）

这不是「代码质量检查」，是**防编造检查**。技能发布给别人的最大风险不是功能少，
是模型在没有依据时自己写一条案号、编一个法条、或者把 62 个判断点说成 61 个。
这个脚本把这类错误变成机器可发现的。

九项检查：
  1  文件可读 + 判断点总数与 schema 声明一致
  2  schema ↔ 正文双向一致（ID / 名称 / 归属文件）
  3  每点五段结构完整（风险 / 判断标准 / 检查方法 / 改法表 / 关联）
  4  各族编号连续、不重、不跳
  5  红线引用可解析（07-redlines.md 里真有这一条）
  6  判例引用可解析（08-precedents*.md / 09-covenant.md 里真有这个编号）
  7  法条引用必须标 ✅ 或 ⚠（禁止裸引用）
  8  检索关键词必须真出现在该判断点正文（防止 schema 自己造词）
  9  判例册体积上限 < 12,000 汉字（基线文件不受此限）

用法：
  python check_coverage.py            # 标准报告
  python check_coverage.py --strict   # 警告也算失败
  python check_coverage.py --json     # 机器可读

退出码：0 通过 / 1 有错误（或 --strict 下有警告）
不依赖任何第三方库。无 Python 环境时，按 SKILL.md 里的 checklist 人工核对。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "references"
SCHEMA = ROOT / "assets" / "clause-schema.json"

CJK = re.compile(r"[\u4e00-\u9fff]")
HEAD_RE = re.compile(r"^## 判断点 ([A-F]-\d{2}) (.+?)\s*$")
REDLINE_ROW_RE = re.compile(r"^\|\s*(\d{2})\s*\|")
# 判例标题层级不统一：08 册用 ###，09 册用 ##；另有「02-4／02-5」合并标题
PREC_HEAD_RE = re.compile(r"^#{2,4}\s*(\d{2})-(\d)")
PREC_ANY_RE = re.compile(r"(?<![\d-])(\d{2})-(\d)(?![\d-])")
PREC_CLUSTER_RE = re.compile(r"^#{2,4}\s*判例\s*(\d{2})")

# 判例册体积上限（策划案 §9）
VOLUME_LIMIT = 12000
VOLUME_FILES = ["08-precedents.md", "08-precedents-b.md", "09-covenant.md"]

# 允许不直接引法条的判断点（附理由，不许随手加）
STATUTE_EXEMPT = {
    "E-07": "纯证据固定与举证清单，属操作事项，无对应实体法条",
}

# 有 NN-x 编号但不是「案例」的小节（结论/方法论），统计子案例数时排除。
# 它们仍可作为引用目标解析，只是不计入案例数量。
NON_CASE_SECTIONS = {
    "09-5": "两条路只能选一条 —— 结论性方法论，非案例",
    "09-6": "三句实话 —— 可直接引用的结论句，非案例",
}

STATUS = {"error": "✗", "warn": "!", "ok": "✓", "info": "·"}


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, check, msg):
        self.items.append({"level": level, "check": check, "msg": msg})

    def err(self, check, msg):
        self.add("error", check, msg)

    def warn(self, check, msg):
        self.add("warn", check, msg)

    def ok(self, check, msg):
        self.add("ok", check, msg)

    def info(self, check, msg):
        self.add("info", check, msg)

    def count(self, level):
        return sum(1 for i in self.items if i["level"] == level)

    def render(self):
        order, seen = [], set()
        for i in self.items:
            if i["check"] not in seen:
                seen.add(i["check"])
                order.append(i["check"])
        out = []
        for chk in order:
            groups = [i for i in self.items if i["check"] == chk]
            has_problem = any(i["level"] in ("error", "warn") for i in groups)
            mark = "✗" if any(i["level"] == "error" for i in groups) else (
                "!" if has_problem else "✓")
            out.append(f"\n{mark} {chk}")
            for i in groups:
                # 有问题时只列问题，不列"通过"那句；无问题则照常显示通过说明
                if i["level"] == "ok" and has_problem:
                    continue
                out.append(f"   {STATUS[i['level']]} {i['msg']}")
        return "\n".join(out)


def cjk_len(text):
    return len(CJK.findall(text))


def load_families():
    """解析 6 份 reference 的判断点块。"""
    fam_files = {
        "A": "02-baseline-talent.md",
        "B": "03-baseline-monetization.md",
        "C": "04-baseline-assets.md",
        "D": "05-baseline-mcn-hr.md",
        "E": "06-baseline-dispute.md",
        "F": "01-scenario-index.md",
    }
    parsed, missing = {}, []
    for fam, fn in fam_files.items():
        p = REF / fn
        if not p.exists():
            missing.append(fn)
            continue
        lines = p.read_text(encoding="utf-8").splitlines()
        blocks, cur = [], None
        for ln in lines:
            m = HEAD_RE.match(ln)
            if m:
                if cur:
                    blocks.append(cur)
                cur = {"id": m.group(1), "name": m.group(2).strip(), "lines": []}
                continue
            if cur:
                if ln.startswith("## ") and not HEAD_RE.match(ln):
                    blocks.append(cur)
                    cur = None
                    continue
                cur["lines"].append(ln)
        if cur:
            blocks.append(cur)
        parsed[fam] = [
            {
                "id": b["id"],
                "name": b["name"],
                "file": f"references/{fn}",
                "body": "\n".join(b["lines"]),
            }
            for b in blocks
        ]
    return parsed, missing


def collect_redlines():
    p = REF / "07-redlines.md"
    if not p.exists():
        return set()
    ids = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        m = REDLINE_ROW_RE.match(ln)
        if m:
            ids.add(f"#{m.group(1)}")
    return ids


def collect_precedents():
    """返回 (子案例集合 {'01-1',...}, 簇集合 {'01',...})"""
    subs, clusters = set(), set()
    for fn in ["08-precedents.md", "08-precedents-b.md", "09-covenant.md"]:
        p = REF / fn
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.startswith("#"):
                # 标题里出现的每个 NN-x 都算（可覆盖「02-4／02-5」这类合并标题）
                for m in PREC_ANY_RE.finditer(ln):
                    subs.add(f"{m.group(1)}-{m.group(2)}")
                    clusters.add(m.group(1))
                m = PREC_CLUSTER_RE.match(ln)
                if m:
                    clusters.add(m.group(1))
    return subs, clusters


def main():
    strict = "--strict" in sys.argv
    as_json = "--json" in sys.argv
    r = Report()

    # ---------- 1 文件与总数 ----------
    if not SCHEMA.exists():
        r.err("01 文件与总数", f"找不到 schema：{SCHEMA}（先跑 _gen_schema.py）")
        emit(r, strict, as_json)
        return 1
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    points = schema["judgment_points"]
    declared = schema["meta"]["judgment_point_total"]

    families, missing_files = load_families()
    if missing_files:
        r.err("01 文件与总数", f"缺少 reference 文件：{', '.join(missing_files)}")
    actual_ids = [p["id"] for fam in families.values() for p in fam]
    if len(actual_ids) != declared:
        r.err("01 文件与总数", f"正文实际 {len(actual_ids)} 个判断点，schema 声明 {declared} 个")
    else:
        r.ok("01 文件与总数", f"判断点总数 {declared} 个，与 schema 声明一致")
    if len(points) != declared:
        r.err("01 文件与总数", f"schema 数组里 {len(points)} 条，meta 声明 {declared} 条")

    # ---------- 2 schema ↔ 正文双向 ----------
    body_by_id = {p["id"]: p for p in actual_ids and [x for v in families.values() for x in v]}
    schema_ids = {p["id"] for p in points}
    body_ids = set(body_by_id)
    ghost = schema_ids - body_ids
    orphan = body_ids - schema_ids
    if ghost:
        r.err("02 schema↔正文", f"schema 有、正文没有（幽灵条目）：{', '.join(sorted(ghost))}")
    if orphan:
        r.err("02 schema↔正文", f"正文有、schema 没登记（漏登记）：{', '.join(sorted(orphan))}")
    mismatch = []
    for p in points:
        b = body_by_id.get(p["id"])
        if not b:
            continue
        if p["name"] != b["name"]:
            mismatch.append(f"{p['id']} 名称不一致：schema「{p['name']}」/ 正文「{b['name']}」")
        if p["file"] != b["file"]:
            mismatch.append(f"{p['id']} 归属文件不一致：schema {p['file']} / 正文 {b['file']}")
    if mismatch:
        for m in mismatch:
            r.err("02 schema↔正文", m)
    elif not ghost and not orphan:
        r.ok("02 schema↔正文", "ID / 名称 / 归属文件逐条一致")

    # ---------- 3 五段结构 ----------
    SEGS = ["**风险**", "**判断标准**", "**检查方法**", "**关联**"]
    broken = []
    for p in sorted(points, key=lambda x: (x["family"], x["id"])):
        b = body_by_id.get(p["id"])
        if not b:
            continue
        body = b["body"]
        lack = [s for s in SEGS if s not in body]
        if not re.search(r"^\|\s*立场", body, re.M):
            lack.append("改法表格")
        if lack:
            broken.append(f"{p['id']} 缺：{'、'.join(lack)}")
    if broken:
        for b in broken:
            r.err("03 五段结构", b)
    else:
        r.ok("03 五段结构", f"{len(points)} 个判断点均为「风险→判断标准→检查方法→改法表→关联」五段")

    # ---------- 4 编号连续 ----------
    seq_bad = []
    for fam, items in sorted(families.items()):
        nums = sorted(int(i["id"].split("-")[1]) for i in items)
        if nums != list(range(1, len(nums) + 1)):
            dup = [n for n in set(nums) if nums.count(n) > 1]
            gap = [n for n in range(1, max(nums) + 1) if n not in nums] if nums else []
            det = []
            if dup:
                det.append(f"重号 {dup}")
            if gap:
                det.append(f"跳号 {gap}")
            seq_bad.append(f"{fam} 族：{'；'.join(det) or '编号非从 01 起'}")
    if seq_bad:
        for s in seq_bad:
            r.err("04 编号连续", s)
    else:
        r.ok("04 编号连续", "六族均自 01 起连续，无跳号重号")

    # ---------- 5 红线可解析 ----------
    redlines = collect_redlines()
    if not redlines:
        r.warn("05 红线引用", "07-redlines.md 里解析不到任何红线编号")
    bad_rl = []
    for p in points:
        for ref in p["redlines"]:
            if ref not in redlines:
                bad_rl.append(f"{p['id']} 引用 {ref}，但 07-redlines.md 无此条")
    if bad_rl:
        for b in bad_rl:
            r.err("05 红线引用", b)
    else:
        r.ok("05 红线引用", f"全部红线引用可解析（红线库 {len(redlines)} 条）")

    # ---------- 6 判例可解析 ----------
    subs, clusters = collect_precedents()
    bad_pr, soft = [], []
    for p in points:
        if p.get("soft_precedent_ref"):
            soft.append(p["id"])
        for ref in p["precedents"]:
            if "-" in ref:
                if ref not in subs:
                    bad_pr.append(f"{p['id']} 引用判例 {ref}，但判例库无此子案例")
            elif ref not in clusters:
                bad_pr.append(f"{p['id']} 引用判例 {ref}，但判例库无此簇")
    if bad_pr:
        for b in bad_pr:
            r.err("06 判例引用", b)
    else:
        case_n = len(subs - set(NON_CASE_SECTIONS))
        r.ok("06 判例引用", f"全部判例引用可解析（{len(clusters)} 簇 / {case_n} 个子案例）")
        if subs & set(NON_CASE_SECTIONS):
            r.info("06 判例引用", f"另有非案例小节 {'、'.join(sorted(subs & set(NON_CASE_SECTIONS)))}，可被引用但不计入案例数")
    if soft:
        r.warn("06 判例引用", f"软引用（有判例字样但无编号，建议补编号）：{', '.join(soft)}")

    # ---------- 7 法条必须标注核验状态 ----------
    naked, no_statute = [], []
    for p in points:
        if not p["statutes"]:
            if p["id"] not in STATUTE_EXEMPT:
                no_statute.append(p["id"])
            continue
        for seg in p["statutes"]:
            if "✅" not in seg and "⚠" not in seg:
                naked.append(f"{p['id']} 法条未标核验状态：{seg[:48]}")
    if naked:
        for n in naked:
            r.err("07 法条标注", n)
    else:
        r.ok("07 法条标注", "全部法条均标 ✅（已核验）或 ⚠（待核验），无裸引用")
    if no_statute:
        r.warn("07 法条标注", f"未挂任何法条：{', '.join(no_statute)}（如属操作类判断点，请登记到 STATUTE_EXEMPT）")

    # ---------- 8 关键词落地 ----------
    kw_bad, kw_thin = [], []
    for p in points:
        b = body_by_id.get(p["id"])
        if not b:
            continue
        kws = p.get("search_keywords") or []
        if len(kws) < 3:
            kw_thin.append(f"{p['id']} 仅 {len(kws)} 个关键词（建议 ≥3）")
        miss = [k for k in kws if k not in b["body"]]
        if miss:
            kw_bad.append(f"{p['id']} 关键词在正文中找不到：{'、'.join(miss)}")
    if kw_bad:
        for k in kw_bad:
            r.err("08 关键词落地", k)
    if kw_thin:
        for k in kw_thin:
            r.warn("08 关键词落地", k)
    if not kw_bad and not kw_thin:
        total_kw = sum(len(p["search_keywords"]) for p in points)
        r.ok("08 关键词落地", f"{total_kw} 个检索关键词全部可在对应判断点正文找到")

    # ---------- 9 体积上限 ----------
    over = []
    for fn in VOLUME_FILES:
        p = REF / fn
        if not p.exists():
            continue
        n = cjk_len(p.read_text(encoding="utf-8"))
        if n >= VOLUME_LIMIT:
            over.append(f"{fn} 约 {n:,} 汉字，超上限 {VOLUME_LIMIT:,}")
    if over:
        for o in over:
            r.err("09 体积上限", o)
    else:
        sizes = []
        for fn in VOLUME_FILES:
            p = REF / fn
            if p.exists():
                sizes.append(f"{fn.replace('.md','')}={cjk_len(p.read_text(encoding='utf-8')):,}")
        r.ok("09 体积上限", f"判例册均在 12,000 汉字内（{'、'.join(sizes)}）")

    # ---------- 10 对外声明数字一致 ----------
    # 曾漏检：「不在 61 点内」这种不带「个判断点」的写法。
    # 现在把「个判断点 / 个点 / 点内 / 点之外」都纳入，扫描范围也扩到 references 与 assets。
    expect = str(declared)
    claim_re = re.compile(r"(\d{2})\s*(?:个判断点|个点|点内|点之外|点以内)")
    targets = [ROOT / "SKILL.md"]
    targets += sorted(REF.glob("*.md"))
    targets += sorted((ROOT / "assets").glob("*.md"))
    targets += [
        ROOT.parent / "_tools" / "_id_map.md",
        ROOT.parent / "娱乐法合同技能-策划案-v1.2.md",
    ]
    claims_bad = []
    for p in targets:
        if not p.exists():
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            # 行内标注 check-ignore 的行跳过：用于引用历史数字、记录故障现场等场景
            if "check-ignore" in ln:
                continue
            for m in claim_re.finditer(ln):
                if m.group(1) != expect:
                    claims_bad.append(f"{p.name}:{i} 写「{m.group(0)}」，应为 {expect}")
    if claims_bad:
        for c in claims_bad:
            r.err("10 声明数字", c)
    else:
        r.ok("10 声明数字", f"对外文件里的判断点数量声明均为 {expect}")

    return emit(r, strict, as_json)


def emit(r, strict, as_json):
    if as_json:
        print(json.dumps(
            {
                "errors": r.count("error"),
                "warnings": r.count("warn"),
                "details": r.items,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print("=" * 62)
        print("娱乐法合同技能 · 自检报告")
        print("=" * 62)
        print(r.render())
        print("\n" + "-" * 62)
        print(f"错误 {r.count('error')} ｜ 警告 {r.count('warn')} ｜ 通过 {r.count('ok')}")
        verdict = "不通过" if r.count("error") or (strict and r.count("warn")) else "通过"
        print(f"结论：{verdict}" + ("（strict 模式）" if strict else ""))
    if r.count("error") or (strict and r.count("warn")):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
