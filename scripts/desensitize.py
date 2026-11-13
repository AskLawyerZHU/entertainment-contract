# -*- coding: utf-8 -*-
"""合同文本脱敏（律师对外分享 / 做演示样例前必跑）

用法：
    python desensitize.py 合同.txt
    python desensitize.py 合同.txt -o 合同-脱敏.txt
    python desensitize.py 合同.txt --map mymap.json

    mymap.json 格式：{"甲方全称": "某文化传媒公司", "张三": "艺人甲"}

覆盖：
    统一社会信用代码 / 手机号 / 邮箱 / 银行账号 / 身份证号 / 案号 /
    公司名称（后缀识别）/ 自然人姓名（需 --map 或 --names 指定，不猜）

设计取舍：
    - 姓名不做自动识别。中文姓名无法靠正则可靠识别，猜错等于二次泄露，宁可要求显式指定
    - 替换后打印命中计数，便于人工复核漏网项
"""
import sys
import os
import re
import json
import argparse

RULES = [
    # 顺序敏感：先长后短、先数字型后字母型，否则信用代码规则会吃掉银行账号前 18 位
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "【身份证号已隐去】"),
    ("银行账号", re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "【账号已隐去】"),
    ("统一社会信用代码", re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Z])"),
     "【信用代码已隐去】"),
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "【手机号已隐去】"),
    ("邮箱", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "【邮箱已隐去】"),
    ("案号", re.compile(r"[（(]\s*\d{4}\s*[）)]\s*[\u4e00-\u9fa5]{1,4}\s*[\u4e00-\u9fa5]{0,4}\d*\s*"
                        r"[\u4e00-\u9fa5]{1,3}\s*\d+\s*号"), "【案号已隐去】"),
]

# 公司名：保留后缀、替换字号，避免"某公司"读起来不像合同
COMPANY = re.compile(
    r"[\u4e00-\u9fa5]{2,12}(?:文化传媒|传媒|文化|影视|网络科技|科技|电子商务|信息|"
    r"商贸|企业管理|广告|传播)(?:股份)?(?:有限责任公司|有限公司)"
)


def load_map(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def desensitize(text, name_map, names):
    counts = {}
    for label, pat, repl in RULES:
        text, n = pat.subn(repl, text)
        if n:
            counts[label] = counts.get(label, 0) + n

    text, n = COMPANY.subn("某文化传媒公司", text)
    if n:
        counts["公司名称"] = n

    for full, alias in name_map.items():
        n = text.count(full)
        if n:
            text = text.replace(full, alias)
            counts["映射:%s" % alias] = counts.get("映射:%s" % alias, 0) + n

    for i, nm in enumerate(names, 1):
        if not nm:
            continue
        n = text.count(nm)
        if n:
            alias = "张某" if i == 1 else "李某"
            text = text.replace(nm, alias)
            counts["姓名:%s" % alias] = counts.get("姓名:%s" % alias, 0) + n

    return text, counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--map", dest="mapfile", default=None)
    ap.add_argument("--names", default="", help="自然人姓名，逗号分隔")
    a = ap.parse_args()

    if not os.path.exists(a.source):
        raise SystemExit("文件不存在：%s" % a.source)

    with open(a.source, encoding="utf-8") as f:
        raw = f.read()

    name_map = load_map(a.mapfile)
    names = [x.strip() for x in a.names.split(",") if x.strip()]

    out, counts = desensitize(raw, name_map, names)

    dst = a.output or os.path.splitext(a.source)[0] + "-脱敏.txt"
    with open(dst, "w", encoding="utf-8") as f:
        f.write(out)

    print("已脱敏 -> %s" % dst)
    if counts:
        for k, v in counts.items():
            print("  %s: %d 处" % (k, v))
    else:
        print("  未命中任何规则——请人工确认原文是否真的不含敏感信息")

    # 残留自检
    residual = []
    for label, pat, _ in RULES:
        if pat.search(out):
            residual.append(label)
    if re.search(r"[\u4e00-\u9fa5]{2,12}(?:有限责任公司|有限公司)", out):
        residual.append("公司名称")
    if residual:
        print("⚠ 疑似残留：%s —— 请人工复核" % "、".join(residual))


if __name__ == "__main__":
    main()
