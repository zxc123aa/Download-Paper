# Download-Paper

学术论文 PDF 批量下载工作流。两条链路：requests 直连 + Playwright 有头 Edge 浏览器（走机构订阅 IP 授权 + 真实浏览器指纹）。

## 工作流

```
清单 CSV ──► 阶段1 直连 ──► 阶段2 有头浏览器 ──► _inbox/{来源编号}-{sha256前12}.pdf
             │                    │
             │ 候选URL            │ DOI 落地页（自动过 Cloudflare/Radware 等待）
             │ arXiv              │ PDF字节获取三连:
             │ Unpaywall OA       │   1. 页内 fetch(同源带cookie)
             │ Semantic Scholar   │   2. <a download> 锚点点击
             │                    │   3. ctx.request + 浏览器真实UA(共享cf_clearance)
             ▼                    ▼
        下载成功/需浏览器    下载成功/付费墙/需人工验证/无落地页
```

- **阶段1（direct）**：无需浏览器，并发 6 线程，OA 链路直下。
- **阶段2（browser）**：有头 Edge 真窗口。校园网/机构 IP 有订阅时，付费墙论文可直接下；遇人机验证窗口停留等待人工点击。单条失败自动重建页面，不连坐。

## 用法

```bash
pip install -r requirements.txt
playwright install msedge   # 若本机无 Edge 则需要；有 Edge 可跳过

# 全流程（直连 + 浏览器）
python download_paper.py --csv 清单.csv --inbox D:\path\_inbox

# 只跑浏览器阶段
python download_paper.py --csv 清单.csv --inbox D:\path\_inbox --phase browser

# 断点续传（跳过上次已成功的）
python download_paper.py --csv 清单.csv --inbox D:\path\_inbox --resume 下载状态_输出.csv
```

## 输入 CSV 格式

需包含以下列（兼容 LADIA 调研清单导出格式）：

| 列名 | 必需 | 说明 |
|---|---|---|
| 记录ID | ✓ | 唯一键，断点续传依据 |
| 来源编号 | ✓ | 文件名前缀，如 `Sch24` |
| DOI | 推荐 | 落地页与 OA 回退查询 |
| 无DOI存档入口 | 可选 | arXiv 链接 |
| 正式PDF入口候选_未验证 | 可选 | `;` 分隔的候选 PDF URL |

## 输出

- PDF：`{inbox}/{来源编号}-{sha256前12位}.pdf`（与 LADIA literature 入库命名一致）
- 状态表：清单同目录 `下载状态_输出.csv`（记录ID/来源编号/DOI/状态/入口/sha256）

状态取值：`下载成功` / `需浏览器` / `付费墙_无公开权限` / `需人工验证` / `无落地页` / `未找到PDF入口`

## 经验备忘

- AIP(pubs.aip.org) 被 Cloudflare 拦 requests 直连；`ctx.request` 共享浏览器 cookie 后带浏览器真实 UA 可下。
- IOP 有 Radware Bot Manager，API 请求指纹会被拦；页内 fetch / 锚点下载走真网络栈可过。
- Springer 对非浏览器请求返回 Client Challenge；同样用页内通道。
- 断点续传只跳过 `下载成功`，其余状态一律重试。
