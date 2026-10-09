# -*- coding: utf-8 -*-
"""手动下载收件模块：扫描指定目录（默认 D:/下载）的新 PDF，
提取首页文本与未完成记录的题名做模糊匹配，自动改名入库并更新状态表。

适用场景：SPIE/Incapsula 等自动化被拦的出版社，人工在浏览器点下载后
跑本脚本一键收件。

用法:
  python pickup_manual.py --dir D:/下载
  python pickup_manual.py --dir D:/下载 --dry-run   # 只看匹配结果不入库
"""
import argparse
import csv
import difflib
import hashlib
import os
import re
import shutil

BASE = os.path.dirname(os.path.abspath(__file__))


def norm(t):
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower()).strip()


def first_page_text(path):
    try:
        from pypdf import PdfReader
        r = PdfReader(path)
        for pg in r.pages[:2]:
            t = pg.extract_text() or ""
            if len(t.strip()) > 40:
                return t
        return ""
    except Exception:
        return ""


def similarity(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


def main():
    ap = argparse.ArgumentParser(description="扫描手动下载的 PDF 并自动入库")
    ap.add_argument("--dir", default="D:/下载", help="手动下载目录（默认 D:/下载）")
    ap.add_argument("--csv", default=os.path.join(BASE, "待下载清单.csv"))
    ap.add_argument("--status", default=os.path.join(BASE, "下载状态_输出.csv"))
    ap.add_argument("--inbox", default=os.path.join(BASE, "_inbox"))
    ap.add_argument("--threshold", type=float, default=0.72,
                    help="题名相似度阈值（默认 0.72）")
    ap.add_argument("--dry-run", action="store_true", help="只显示匹配不入库")
    args = ap.parse_args()

    import glob
    pdfs = sorted(glob.glob(os.path.join(args.dir, "*.pdf")),
                  key=os.path.getmtime)
    if not pdfs:
        print("目录里没有 PDF")
        return

    status_rows = list(csv.DictReader(open(args.status, encoding="utf-8-sig")))
    main_rows = {r["来源编号"].split(";")[0].strip(): r
                 for r in csv.DictReader(open(args.csv, encoding="utf-8-sig"))}
    # 待匹配: 未下载成功的记录
    pending = [r for r in status_rows if r.get("状态") != "下载成功"]
    print(f"PDF {len(pdfs)} 个, 待匹配记录 {len(pending)} 条\n")

    inbox = args.inbox
    os.makedirs(inbox, exist_ok=True)
    matched = {}
    for pdf in pdfs:
        txt = first_page_text(pdf)
        if not txt:
            print(f"? {os.path.basename(pdf)}: 无法提取文本, 跳过")
            continue
        # 取首页前 400 字符做匹配窗口
        head = txt[:400]
        best, bs = None, 0.0
        for r in pending:
            s = similarity(head, r.get("题名", ""))
            if s > bs:
                best, bs = r, s
        name = os.path.basename(pdf)
        if best and bs >= args.threshold:
            rec_id = best["来源编号"].split(";")[0].strip()
            print(f"✔ {name} -> {rec_id} ({bs:.2f}) {best.get('题名','')[:50]}")
            matched[pdf] = (rec_id, best, bs)
        else:
            print(f"✘ {name}: 最高相似度 {bs:.2f} (低于阈值), 未匹配")

    if args.dry_run or not matched:
        return

    # 入库
    for pdf, (rec_id, rec, _) in matched.items():
        dst = os.path.join(inbox, f"{rec_id}.pdf")
        shutil.copy2(pdf, dst)
        h = hashlib.sha256(open(dst, "rb").read()).hexdigest()
        rec["状态"] = "下载成功"
        rec["入口"] = "人工下载"
        rec["sha256"] = h
        print(f"  入库: {dst}")

    # 写回状态表（保持列序）
    fields = list(status_rows[0].keys())
    with open(args.status, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(status_rows)
    print(f"状态表已更新: {args.status}")


if __name__ == "__main__":
    main()
