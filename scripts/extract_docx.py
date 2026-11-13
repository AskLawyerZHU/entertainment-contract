# -*- coding: utf-8 -*-
"""docx -> 纯文本 + 批注提取（不依赖 Office / python-docx）

用法：
    python extract_docx.py 合同.docx
    python extract_docx.py 合同.docx -o out.txt
    python extract_docx.py 合同.docx --comments

说明：
    - 只读 word/document.xml 与 word/comments.xml，标准库即可
    - 修订痕迹（w:ins / w:del）默认保留插入内容、标注删除内容
    - 无 python 环境时不要用本脚本，按 assets 里的 checklist 人工核对
"""
import sys
import os
import re
import zipfile
import argparse

NS_P = re.compile(r"<w:p[ >]")
TAG = re.compile(r"<[^>]+>")


def _text_of(xml):
    """把一段 word xml 转成纯文本：段落换行、tab 转空格。"""
    xml = xml.replace("<w:tab/>", "\t").replace("<w:br/>", "\n")
    xml = xml.replace("</w:p>", "\n")
    txt = TAG.sub("", xml)
    txt = (txt.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
              .replace("&quot;", '"').replace("&apos;", "'"))
    txt = re.sub(r"[ \t]+\n", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    # 清掉自选图形/文本框残留的坐标数字，形如 -239395-707390
    txt = re.sub(r"-\d{4,}-\d{4,}", "", txt)
    return txt.strip()


def extract_body(path):
    with zipfile.ZipFile(path) as z:
        if "word/document.xml" not in z.namelist():
            raise SystemExit("不是有效的 docx（缺 word/document.xml）：%s" % path)
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    # 删除修订：先把 w:delText 内容标灰，避免静默丢掉
    xml = re.sub(r"<w:delText[^>]*>(.*?)</w:delText>", r"[删除:\1]", xml, flags=re.S)
    return _text_of(xml)


def extract_comments(path):
    with zipfile.ZipFile(path) as z:
        if "word/comments.xml" not in z.namelist():
            return ""
        xml = z.read("word/comments.xml").decode("utf-8", "ignore")
    items = re.findall(r"<w:comment\b[^>]*>(.*?)</w:comment>", xml, flags=re.S)
    out = []
    for i, c in enumerate(items, 1):
        out.append("[批注 %d] %s" % (i, _text_of(c)))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--comments", action="store_true", help="同时输出 docx 批注")
    a = ap.parse_args()

    if not os.path.exists(a.source):
        raise SystemExit("文件不存在：%s" % a.source)

    body = extract_body(a.source)
    text = body
    if a.comments:
        c = extract_comments(a.source)
        if c:
            text = body + "\n\n===== 批注 =====\n" + c

    dst = a.output or os.path.splitext(a.source)[0] + ".txt"
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)
    print("字符数=%d -> %s" % (len(text), dst))


if __name__ == "__main__":
    main()
