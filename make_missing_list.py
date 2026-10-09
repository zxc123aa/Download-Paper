# -*- coding: utf-8 -*-
"""生成未找到清单：从状态表筛出未成功的记录，附 doi.org 链接与
Semantic Scholar 存档页备用入口，便于人工处理。

用法:
  python make_missing_list.py
  python make_missing_list.py --out 未找到清单.csv
"""
import argparse
import csv
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description="生成未找到文献清单（DOI 链接 + 备用入口）")
    ap.add_argument("--status", default=os.path.join(BASE, "下载状态_输出.csv"))
    ap.add_argument("--main", default=os.path.join(BASE, "待下载清单.csv"))
    ap.add_argument("--out", default=os.path.join(BASE, "未找到清单.csv"))
    args = ap.parse_args()

    status = list(csv.DictReader(open(args.status, encoding="utf-8-sig")))
    main_rows = {r["来源编号"].split(";")[0].strip(): r
                 for r in csv.DictReader(open(args.main, encoding="utf-8-sig"))}

    out_rows = []
    for r in status:
        if r.get("状态") == "下载成功":
            continue
        rec_id = r["来源编号"].split(";")[0].strip()
        rec = main_rows.get(rec_id, {})
        doi = (r.get("DOI") or "").strip()
        arc = (rec.get("无DOI存档入口") or "").strip()
        out_rows.append({
            "来源编号": rec_id,
            "题名": (rec.get("题名") or "").strip(),
            "DOI链接": f"https://doi.org/{doi}" if doi else "",
            "备用入口": arc if arc.startswith("http") else "",
            "状态": r.get("状态", ""),
        })

    fields = ["来源编号", "题名", "DOI链接", "备用入口", "状态"]
    with open(args.out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(out_rows)
    have_doi = sum(1 for r in out_rows if r["DOI链接"])
    have_alt = sum(1 for r in out_rows if r["备用入口"])
    print(f"未找到 {len(out_rows)} 条 -> {args.out}")
    print(f"  DOI直链 {have_doi} | 仅备用入口 {have_alt - (have_doi and 0)}"
          f" | 双无 {len(out_rows) - max(have_doi, have_alt)}")
    print("处理建议: DOI直链 -> 浏览器打开手动点下载(部分社需校园网/订阅权限); "
          "备用入口 -> Semantic Scholar 页找免费全文")


if __name__ == "__main__":
    main()
