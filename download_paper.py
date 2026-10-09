# -*- coding: utf-8 -*-
"""
Download-Paper: 学术论文 PDF 批量下载工作流（直连 + 有头浏览器双阶段）

用法:
  python download_paper.py --csv 清单.csv --inbox D:\\path\\to\\_inbox
  python download_paper.py --csv 清单.csv --phase browser   # 只跑浏览器阶段
  python download_paper.py --csv 清单.csv --resume status.csv

输入 CSV 需包含列: 记录ID, 来源编号, DOI, 无DOI存档入口, 正式PDF入口候选_未验证
输出: 每篇 PDF 命名为 {来源编号}-{sha256前12位}.pdf 存入 --inbox；
      状态表 {csv同目录}/下载状态_输出.csv

阶段说明:
  direct : requests 直连链 (候选URL -> arXiv -> Unpaywall -> Semantic Scholar)
  browser: Playwright 有头 Edge (校园网 IP 授权 + 真实指纹)
           落地页 -> 页内fetch / <a download>锚点 / ctx.request(带浏览器UA)
"""
import argparse, base64, csv, hashlib, io, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

UA_HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
EMAIL = "ladia.research@outlook.com"
CHALLENGE_TITLES = ("captcha", "just a moment", "client challenge", "attention required")
PAYWALL = re.compile(
    r"purchase pdf|sign in to|subscribe to|access through your institution|"
    r"buy article|add to cart|login required|institutional login", re.I)
FETCH_JS = """
async (urls) => {
  for (const u of urls) {
    try {
      const r = await fetch(u, {credentials: 'include'});
      if (!r.ok) continue;
      const buf = await r.arrayBuffer();
      const head = new Uint8Array(buf.slice(0, 5));
      if (String.fromCharCode(...head) !== '%PDF-') continue;
      if (buf.byteLength <= 20480) continue;
      const bytes = new Uint8Array(buf);
      let bin = '';
      for (let i = 0; i < bytes.length; i += 0x8000)
        bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      return {url: u, b64: btoa(bin)};
    } catch (e) {}
  }
  return null;
}
"""
ANCHOR_JS = """
(u) => {
  const a = document.createElement('a');
  a.href = u; a.download = ''; a.style.display = 'none';
  document.body.appendChild(a); a.click();
  setTimeout(() => a.remove(), 3000);
}
"""


# ---------------------------------------------------------------- CSV / 基础

def read_rows(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gbk", "utf-16"):
        try:
            text = raw.decode(enc)
            rows = list(csv.DictReader(io.StringIO(text)))
            if rows:
                return rows
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise RuntimeError("无法解码 CSV: " + path)


def is_pdf(body):
    return len(body) > 20480 and (body[:5] == b"%PDF-" or b"%PDF-" in body[:2048])


def inbox_hashes(inbox):
    hashes = set()
    if os.path.isdir(inbox):
        for fn in os.listdir(inbox):
            if fn.endswith(".pdf"):
                h = hashlib.sha256()
                try:
                    with open(os.path.join(inbox, fn), "rb") as f:
                        for c in iter(lambda: f.read(1 << 20), b""):
                            h.update(c)
                    hashes.add(h.hexdigest())
                except OSError:
                    pass
    return hashes


def save_pdf(inbox, rec_id, body):
    h = hashlib.sha256(body).hexdigest()
    path = os.path.join(inbox, f"{rec_id}-{h[:12]}.pdf")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(body)
    return h


def candidate_urls(rec):
    urls = []
    for u in (rec.get("正式PDF入口候选_未验证") or "").split(";"):
        u = u.strip()
        if u:
            urls.append(u)
    src = rec.get("无DOI存档入口") or ""
    for m in re.finditer(r"arxiv\.org/(?:abs|pdf)/([^\s;]+?)(?:\.pdf)?$", src.strip(), re.I):
        urls.append(f"https://arxiv.org/pdf/{m.group(1)}")
    return urls


# ---------------------------------------------------------------- 阶段1: 直连

def phase_direct(rows, inbox, hashes):
    import requests
    from requests.adapters import HTTPAdapter

    def make():
        s = requests.Session()
        s.headers.update(UA_HDRS)
        s.mount("https://", HTTPAdapter(max_retries=1))
        return s

    tls = {}
    import threading
    lock = threading.local()

    def sess():
        if not hasattr(lock, "s"):
            lock.s = make()
        return lock.s

    def try_url(url):
        try:
            r = sess().get(url, timeout=(15, 60), allow_redirects=True)
        except requests.RequestException as e:
            return None, f"neterr:{type(e).__name__}"
        if r.status_code != 200:
            return None, f"http{r.status_code}"
        low = r.content[:4096].lower()
        if b"cloudflare" in low or b"captcha" in low or b"just a moment" in low:
            return None, "antibot"
        if is_pdf(r.content):
            return r.content, "ok"
        return None, "html"

    def unpaywall(doi):
        try:
            r = sess().get(f"https://api.unpaywall.org/v2/{doi}?email={EMAIL}", timeout=30)
            data = r.json() if r.status_code == 200 else {}
        except Exception:
            return []
        loc = data.get("best_oa_location") or {}
        out = [loc.get(k) for k in ("url_for_pdf", "url") if loc.get(k)]
        for l in data.get("oa_locations") or []:
            if l.get("url_for_pdf") and l["url_for_pdf"] not in out:
                out.append(l["url_for_pdf"])
        return out[:4]

    def s2(doi):
        try:
            r = sess().get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
                           f"?fields=openAccessPdf", timeout=30)
            pdf = (r.json().get("openAccessPdf") or {}).get("url") if r.status_code == 200 else None
        except Exception:
            pdf = None
        return [pdf] if pdf else []

    results = []

    def work(rec):
        rid_key = rec["记录ID"].strip()
        rec_id = rec["来源编号"].strip()
        doi = rec.get("DOI", "").strip()
        urls = candidate_urls(rec) + (unpaywall(doi) if doi else []) + (s2(doi) if doi else [])
        seen = set()
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            body, note = try_url(u)
            if body:
                h = save_pdf(inbox, rec_id, body)
                return {"记录ID": rid_key, "来源编号": rec_id, "DOI": doi,
                        "状态": "下载成功", "入口": u, "sha256": h}
            time.sleep(0.3)
        return {"记录ID": rid_key, "来源编号": rec_id, "DOI": doi,
                "状态": "需浏览器", "入口": "", "sha256": ""}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(work, r) for r in rows]
        for i, fut in enumerate(as_completed(futs)):
            results.append(fut.result())
            if (i + 1) % 20 == 0:
                print(f"  [direct {i+1}/{len(rows)}]", flush=True)
    return results


# ---------------------------------------------------------------- 阶段2: 浏览器

def phase_browser(rows, inbox, hashes, profile_dir):
    from playwright.sync_api import sync_playwright

    def is_challenge(page):
        t = (page.title() or "").lower()
        return any(k in t for k in CHALLENGE_TITLES)

    def wait_clear(page, sec=45):
        end = time.time() + sec
        while time.time() < end:
            if not is_challenge(page):
                return True
            time.sleep(3)
        return not is_challenge(page)

    def safe_goto(page, url, timeout=45000):
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return True
        except Exception:
            return False

    def landing(page, rec, doi):
        cands = []
        if doi:
            cands.append("https://doi.org/" + doi)
            if doi.startswith("10.1088/"):
                cands.append(f"https://iopscience.iop.org/article/{doi}")
        for u in candidate_urls(rec):
            if "/pdf" not in u.lower() and "arxiv" not in u:
                cands.append(u)
        for cu in cands:
            if not safe_goto(page, cu):
                continue
            if is_challenge(page) and not wait_clear(page):
                return None, "challenge"
            if not safe_goto(page, cu):
                continue
            if is_challenge(page):
                return None, "challenge"
            if any(k in (page.title() or "").lower() for k in CHALLENGE_TITLES):
                return None, "challenge"
            return page.url, None
        return None, "no_landing"

    def pdf_urls(page, rec, doi):
        urls = candidate_urls(rec)
        if doi:
            if doi.startswith("10.1088/"):
                urls.insert(0, f"https://iopscience.iop.org/article/{doi}/pdf")
            if doi.startswith("10.1007/"):
                urls.insert(0, f"https://link.springer.com/content/pdf/{doi}.pdf")
        try:
            meta = page.query_selector("meta[name='citation_pdf_url']")
            if meta and meta.get_attribute("content"):
                urls.insert(0, meta.get_attribute("content"))
            for el in page.query_selector_all("a[href]"):
                h = el.get_attribute("href") or ""
                if re.search(r"\.pdf($|\?)|/pdf($|\?|/)|article-pdf|/docserver", h, re.I):
                    if h.startswith("/"):
                        pp = urlparse(page.url)
                        h = f"{pp.scheme}://{pp.netloc}{h}"
                    if h not in urls:
                        urls.append(h)
        except Exception:
            pass
        out, seen = [], set()
        for u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out[:8]

    def try_fetch(page, u):
        try:
            res = page.evaluate(FETCH_JS, [u])
            if res:
                return base64.b64decode(res["b64"])
        except Exception:
            pass
        return None

    def try_anchor(page, u):
        try:
            with page.expect_download(timeout=20000) as di:
                page.evaluate(ANCHOR_JS, u)
            dl = di.value
            tmp = os.path.join(inbox, "_tmp_" + dl.suggested_filename)
            dl.save_as(tmp)
            body = open(tmp, "rb").read()
            os.remove(tmp)
            if body[:5] == b"%PDF-":
                return body
        except Exception:
            pass
        return None

    def try_ctx_ua(ctx, page, u):
        try:
            ua = page.evaluate("navigator.userAgent")
            r = ctx.request.get(u, headers={"User-Agent": ua, "Referer": page.url},
                                timeout=60000)
            if r.status == 200:
                body = r.body()
                if body[:5] == b"%PDF-":
                    return body
        except Exception:
            pass
        return None

    results = []
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            profile_dir, channel="msedge", headless=False,
            viewport={"width": 1400, "height": 900}, accept_downloads=True,
            args=["--disable-blink-features=AutomationControlled"])
        page = ctx.new_page()

        for i, rec in enumerate(rows):
            rid_key = rec["记录ID"].strip()
            rec_id = rec["来源编号"].strip()
            doi = rec.get("DOI", "").strip()
            try:
                land, err = landing(page, rec, doi)
                if err:
                    status = "需人工验证" if err == "challenge" else "无落地页"
                    results.append({"记录ID": rid_key, "来源编号": rec_id, "DOI": doi,
                                    "状态": status, "入口": "", "sha256": ""})
                    print(f"[{i+1}/{len(rows)}] {rec_id} -> {status}", flush=True)
                    continue
                body = None
                used = ""
                for u in pdf_urls(page, rec, doi):
                    body = try_fetch(page, u) or try_anchor(page, u) or try_ctx_ua(ctx, page, u)
                    if body:
                        used = u
                        break
                if body and is_pdf(body):
                    h = save_pdf(inbox, rec_id, body)
                    status = "下载成功"
                else:
                    html = ""
                    try:
                        html = page.content()[:40000]
                    except Exception:
                        pass
                    status = "付费墙_无公开权限" if PAYWALL.search(html) else "未找到PDF入口"
                    h, used = "", land
            except Exception as e:
                # 浏览器页崩溃恢复: 重建 page 继续，不再连坐失败
                name = type(e).__name__
                if "TargetClosed" in name:
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = ctx.new_page()
                status = f"error:{name}"
                h, used = "", ""
            results.append({"记录ID": rid_key, "来源编号": rec_id, "DOI": doi,
                            "状态": status, "入口": used, "sha256": h})
            ok = sum(1 for x in results if x["状态"] == "下载成功")
            print(f"[{i+1}/{len(rows)}] {rec_id} -> {status} (ok={ok})", flush=True)
        ctx.close()
    return results


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(description="论文 PDF 批量下载 (直连+浏览器)")
    ap.add_argument("--csv", required=True, help="清单 CSV（含 记录ID/来源编号/DOI 等列）")
    ap.add_argument("--inbox", required=True, help="PDF 保存目录")
    ap.add_argument("--phase", choices=["direct", "browser", "all"], default="all")
    ap.add_argument("--resume", help="上次状态 CSV，跳过已 下载成功 的记录")
    ap.add_argument("--profile", default=None, help="Edge profile 目录（默认 ./edge_profile）")
    args = ap.parse_args()

    os.makedirs(args.inbox, exist_ok=True)
    rows = read_rows(args.csv)
    print(f"清单记录: {len(rows)}", flush=True)

    done = {}
    if args.resume and os.path.exists(args.resume):
        for r in read_rows(args.resume):
            if r.get("状态") == "下载成功":
                done[r["记录ID"].strip()] = r
        print(f"resume: 跳过已完成 {len(done)}", flush=True)
    rows = [r for r in rows if r["记录ID"].strip() not in done]

    hashes = inbox_hashes(args.inbox)
    results = []

    if args.phase in ("direct", "all"):
        print("== 阶段1: 直连 ==", flush=True)
        results += phase_direct(rows, args.inbox, hashes)

    if args.phase in ("browser", "all"):
        todo = rows if args.phase == "browser" else \
            [r for r in rows if any(x["记录ID"] == r["记录ID"].strip()
                                    and x["状态"] == "需浏览器" for x in results)]
        if args.phase == "browser" and args.resume:
            # browser-only + resume: exclude anything already downloaded per hash
            have = {r["来源编号"].strip() for r in done.values()}
            todo = [r for r in todo if r["来源编号"].strip() not in have]
        print(f"== 阶段2: 浏览器 ({len(todo)} 条) ==", flush=True)
        profile = args.profile or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "edge_profile")
        results += phase_browser(todo, args.inbox, hashes, profile)

    # 合并 resume 结果并写出状态
    results = list(done.values()) + results
    status_path = os.path.join(os.path.dirname(os.path.abspath(args.csv)),
                               "下载状态_输出.csv")
    fields = ["记录ID", "来源编号", "DOI", "状态", "入口", "sha256"]
    with open(status_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(results)
    from collections import Counter
    stat = Counter(x["状态"] for x in results)
    print("DONE", dict(stat), flush=True)
    print("状态表:", status_path, flush=True)


if __name__ == "__main__":
    main()
