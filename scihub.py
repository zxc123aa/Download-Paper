# -*- coding: utf-8 -*-
"""Sci-Hub 多镜像下载模块。

镜像现状（2026-10 校园网直连环境实测）:
  sci-hub.ru  可用（未收录的返回元数据结果页）
  sci-hub.ee  部分可用
  sci-hub.se  DNS 污染, 需代理（开代理后库最大, 优先试）
  sci-hub.st  DDoS-Guard 403

用法:
  作为库:   from scihub import try_scihub
  命令行:   python scihub.py --csv 待下载清单.csv --inbox _inbox --resume 下载状态_输出.csv
"""
import argparse
import hashlib
import os
import re
import time
from urllib.parse import urlparse

import requests

MIRRORS = ["https://sci-hub.se", "https://sci-hub.ru", "https://sci-hub.st",
           "https://sci-hub.ee", "https://sci-hub.ren"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

sess = requests.Session()
sess.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def find_pdf_url(html, mirror):
    """从 Sci-Hub 页面抠 PDF 直链（iframe/embed/downloads 路径）。"""
    pats = [
        r'<iframe[^>]+src="([^"]+)"',
        r'<embed[^>]+src="([^"]+)"',
        r"<iframe[^>]+src='([^']+)'",
        r'location\.href\s*=\s*"([^"]+\.pdf[^"]*)"',
        r"//sci-hub\.[a-z.]+/(downloads/[^\"']+)",
    ]
    for pat in pats:
        for m in re.finditer(pat, html, re.I):
            u = m.group(1)
            if any(k in u.lower() for k in (".pdf", "/downloads/", "downloads")):
                if u.startswith("//"):
                    u = "https:" + u
                elif u.startswith("/"):
                    u = mirror + u
                return u
    for m in re.finditer(r'src="(//[^"]+|/[^"]+)"', html):
        u = m.group(1)
        if "/downloads/" in u.lower() or u.lower().endswith(".pdf"):
            if u.startswith("//"):
                u = "https:" + u
            elif u.startswith("/"):
                u = mirror + u
            return u
    return None


def try_scihub(doi, timeout_page=30, timeout_pdf=90):
    """轮询镜像下载。返回 (pdf_bytes, mirror) 或 (None, 失败原因)。"""
    for mirror in MIRRORS:
        host = urlparse(mirror).netloc
        try:
            r = sess.get(f"{mirror}/{doi}", timeout=timeout_page)
            if r.status_code != 200 or len(r.text) < 500:
                continue
            if "article not found" in r.text.lower() or \
                    "не найдена" in r.text.lower():
                return None, "not_found"
            # 结果页特征: 只有元数据+相关文章, 无全文
            pdf_url = find_pdf_url(r.text, mirror)
            if not pdf_url:
                continue
            pd = sess.get(pdf_url, timeout=timeout_pdf,
                          headers={"Referer": f"{mirror}/{doi}"})
            if pd.content[:5] == b"%PDF-":
                return pd.content, mirror
        except Exception:
            continue
    return None, "all_mirrors_fail"


def phase_scihub(rows, inbox, hashes, delay=2.5, log=print):
    """对带 DOI 且未成功的记录跑 Sci-Hub。返回 results 列表（与主流程同格式）。"""
    results = []
    todo = [r for r in rows if r.get("DOI", "").strip()]
    log(f"Sci-Hub: {len(todo)} 条带 DOI")
    for i, rec in enumerate(todo):
        rec_id = rec["来源编号"].strip()
        doi = rec["DOI"].strip()
        out = os.path.join(inbox, f"{rec_id}.pdf")
        if os.path.exists(out):
            continue
        body, info = try_scihub(doi)
        if body:
            h = hashlib.md5(body).hexdigest()
            if h in hashes:
                log(f"[{i+1}/{len(todo)}] {rec_id} -> 重复内容, 丢弃")
                results.append({"记录ID": rec.get("记录ID", ""), "来源编号": rec_id,
                                "DOI": doi, "状态": "重复内容", "入口": info,
                                "sha256": ""})
            else:
                with open(out, "wb") as f:
                    f.write(body)
                hashes.add(h)
                log(f"[{i+1}/{len(todo)}] {rec_id} -> 下载成功 ({len(body)}B, {info})")
                results.append({"记录ID": rec.get("记录ID", ""), "来源编号": rec_id,
                                "DOI": doi, "状态": "下载成功", "入口": info,
                                "sha256": hashlib.sha256(body).hexdigest()})
        else:
            log(f"[{i+1}/{len(todo)}] {rec_id} -> Sci-Hub 无全文 ({info})")
            results.append({"记录ID": rec.get("记录ID", ""), "来源编号": rec_id,
                            "DOI": doi, "状态": "Sci-Hub无全文", "入口": "",
                            "sha256": ""})
        time.sleep(delay)
    return results


def main():
    ap = argparse.ArgumentParser(description="Sci-Hub 批量下载（独立运行）")
    ap.add_argument("--csv", required=True)
    ap.add_argument("--inbox", required=True)
    ap.add_argument("--resume", help="状态 CSV，跳过已 下载成功 的记录")
    args = ap.parse_args()

    os.makedirs(args.inbox, exist_ok=True)
    import download_paper as dp
    rows = dp.read_rows(args.csv)
    if args.resume and os.path.exists(args.resume):
        done = {r["来源编号"].strip() for r in dp.read_rows(args.resume)
                if r.get("状态") == "下载成功"}
        rows = [r for r in rows if r["来源编号"].strip() not in done]
        print(f"resume: 跳过已完成 {len(done)}")
    hashes = dp.inbox_hashes(args.inbox)
    phase_scihub(rows, args.inbox, hashes)


if __name__ == "__main__":
    main()
