# Download-Paper

学术论文 PDF 批量下载工作流。四阶段流水线：requests 直连 → Playwright 有头 Edge 浏览器 → Sci-Hub 兜底 → 人工下载收件。

当前战绩：260 条清单 218 篇成功（83.8%）。

## 工作流

```
清单 CSV ──► 阶段1 直连 ──► 阶段2 有头浏览器 ──► 阶段3 Sci-Hub ──► 未找到清单.csv ──► 人工下载
             │                    │                    │                │
             │ 候选URL            │ DOI 落地页          │ 多镜像轮询      │ 生成 doi.org 直链
             │ arXiv              │ (自动过 Cloudflare) │ ru/ee 可用     │ + S2 存档页备用入口
             │ Unpaywall OA       │ PDF字节获取三连:     │ se 需代理       │
             │ Semantic Scholar   │  1. 页内 fetch      │                │ pickup_manual.py
             │                    │  2. <a> 锚点点击     │                │ 扫描下载目录, 按首页
             ▼                    ▼  3. ctx.request     ▼                ▼ 文本自动匹配入库
                          _inbox/{来源编号}.pdf          下载状态_输出.csv
```

- **阶段1（direct）**：无需浏览器，并发 6 线程，OA 链路直下。
- **阶段2（browser）**：有头 Edge 真窗口。校园网/机构 IP 有订阅时，付费墙论文可直接下；遇人机验证自动点击 + 等待人工；页面/浏览器崩溃自动重建，不连坐。
- **阶段3（scihub）**：`scihub.py` 模块，对剩余带 DOI 的记录轮询镜像。
- **人工兜底**：`make_missing_list.py` 生成未找到清单（doi.org 直链 + S2 备用入口），人工下载后 `pickup_manual.py` 一键收件。

## 模块

| 文件 | 职责 |
|---|---|
| `download_paper.py` | 主流程：阶段1+2+3 编排、状态表合并写入 |
| `scihub.py` | Sci-Hub 多镜像下载（可独立运行） |
| `pickup_manual.py` | 手动下载 PDF 收件：pypdf 提取首页文本 → 题名模糊匹配 → 改名入库 + 更新状态表 |
| `make_missing_list.py` | 从状态表生成未找到清单（DOI 链接 + 备用入口） |

## 用法

```bash
pip install -r requirements.txt pypdf
playwright install msedge   # 若本机无 Edge 则需要；有 Edge 可跳过

# 全流程（直连 + 浏览器 + Sci-Hub）
python download_paper.py --csv 待下载清单.csv --inbox _inbox

# 断点续传（跳过已 下载成功 的）
python download_paper.py --csv 待下载清单.csv --inbox _inbox --resume 下载状态_输出.csv

# 只跑 Sci-Hub
python download_paper.py --csv 待下载清单.csv --inbox _inbox --phase scihub --resume 下载状态_输出.csv

# 生成未找到清单 -> 人工处理
python make_missing_list.py

# 人工下载完, 扫描下载目录收件（先 --dry-run 看匹配）
python pickup_manual.py --dir D:/下载 --dry-run
python pickup_manual.py --dir D:/下载
```

## 输入 CSV 格式

需包含以下列（兼容 LADIA 调研清单导出格式）：

| 列名 | 必需 | 说明 |
|---|---|---|
| 记录ID | ✓ | 唯一键，断点续传依据 |
| 来源编号 | ✓ | 文件名前缀，如 `Sch24` |
| DOI | 推荐 | 落地页与 OA 回退查询、Sci-Hub 查询 |
| 无DOI存档入口 | 可选 | arXiv / Semantic Scholar 链接（备用入口来源） |
| 正式PDF入口候选_未验证 | 可选 | `;` 分隔的候选 PDF URL |

## 输出

- PDF：`_inbox/{来源编号}.pdf`
- 状态表：`下载状态_输出.csv`（记录ID/来源编号/DOI/状态/入口/sha256）

状态取值：`下载成功` / `需浏览器` / `付费墙_无公开权限` / `需人工验证` / `无落地页` / `未找到PDF入口` / `Sci-Hub无全文` / `重复内容`

## 经验备忘（出版社反爬逐条）

- **AIP**(pubs.aip.org)：Cloudflare 拦 requests；`ctx.request` 共享浏览器 cookie + 真实 UA 可下。
- **IOP**：Radware Bot Manager 拦 API 指纹；页内 fetch / 锚点下载走真网络栈可过。
- **Springer**：非浏览器请求返回 Client Challenge；页内通道。
- **IEEE**：stamp.jsp 是 HTML 中转页，需跳过去从 iframe 抠 `getPDF.jsp` 真直链。
- **ScienceDirect**：pdfft 的 fetch 必被 CF JS challenge 拦，仅放行浏览器导航；signed URL 需 viewer 同源 fetch 或 ctx download 事件捕获。
- **Wiley/MedPhys**：`/doi/pdfdirect/{DOI}?download=true` 同源 fetch 直出；`/doi/pdf/` 是 viewer 壳页；`10.1002/mp.*` 在 aapm 子域（跨域 fetch 被拒）。
- **SPIE**：Incapsula 单独设防 `/doi/pdf/` 端点（文章页可过）；**自动化信誉一旦破坏全站封锁且难自愈** —— 不要反复试，人工在浏览器点 PDF 后用 `pickup_manual.py` 收件。
- **Sci-Hub**：`sci-hub.ru`/`ee` 可用；`se` DNS 污染需代理（开代理后库最大优先试）；`st` DDoS-Guard 403；未收录文章返回元数据结果页（~26-38KB 无 iframe）。
- Edge 内置 PDF viewer 会杀死 Playwright context（TargetClosed）—— landing 阶段绝不碰 PDF 直链。
- 断点续传只跳过 `下载成功`，其余状态一律重试。
