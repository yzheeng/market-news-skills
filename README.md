# ArkClaw Skill 交付包 — market-quotes + news-search

本地开发并实测通过,产出日期:2026-07-04(北京时间)。
按 SKILL_DEV_SPEC.md 开发;所有外部依赖均在 ArkClaw 白名单实测可达清单内,未引入任何清单外域名。

## 1. 包含什么

| Skill | 用途 | 脚本 | 依赖 |
|-------|------|------|------|
| `market-quotes` | 美股指数/个股/加密的**准确**结构化行情(价格、涨跌额、涨跌幅、休市标记),根治"搜网页猜数字"问题 | `quotes.py` | `yfinance`, `requests` |
| `news-search` | 11 个固定维度的资讯检索(Tavily)+ arXiv 论文块 + Anthropic Research + HN 热点,输出带链接的结构化条目 | `news.py` | `requests` |

另有 `config/briefing.json`:关注标的、国产大模型名单、arXiv 分类、回溯窗口等,用户日后直接改它,不用动代码。

## 2. 安装(用户手动操作)

```bash
# 1) 拷贝两个 skill 目录
cp -r skills/market-quotes skills/news-search ~/.openclaw/workspace/skills/

# 2) 拷贝配置(两个脚本都会按此路径查找;也可用 --config 指定其他位置)
mkdir -p ~/.openclaw/workspace/config
cp config/briefing.json ~/.openclaw/workspace/config/

# 3) 安装依赖(Ubuntu 24.04 需 --break-system-packages)
pip install --break-system-packages -r ~/.openclaw/workspace/skills/market-quotes/requirements.txt
pip install --break-system-packages -r ~/.openclaw/workspace/skills/news-search/requirements.txt
```

配置查找顺序:`<skill目录>/../../config/briefing.json` → `~/.openclaw/workspace/config/briefing.json`。按上面的装法,第二条路径会命中。

## 3. 环境变量 / API key

| 变量 | 必需性 | 用途 |
|------|--------|------|
| `TAVILY_API_KEY` | **news-search 必需**(除论文块外所有维度都靠它) | Tavily 搜索,境外资讯唯一通道 |
| `POLYGON_API_KEY` | 可选 | market-quotes 备用源 1(yfinance 失败时优先兜底指数;免费层每分钟 5 次,实测可取 `^NDX`/`^IXIC`/`^SOX`) |
| `FINNHUB_API_KEY` | 可选 | market-quotes 备用源 2(普通股票和部分加密可用,`^NDX`/`^VIX` 等指数可能要求额外订阅) |
| `ALPHAVANTAGE_API_KEY` | 可选 | market-quotes 备用源 3(主要兜底普通股票) |

配置方式(任选):写入 ArkClaw 上运行 Agent 的用户环境,例如 `~/.bashrc` / systemd 服务的 Environment / OpenClaw 的 env 配置:

```bash
export TAVILY_API_KEY="tvly-..."
```

不设可选 key 不影响主流程,只是少了兜底。

## 4. 本地测试结果(真实调用,2026-07-04 北京时间 ≈ 美东 7/3 深夜)

### market-quotes(数字准确性验收)

7/3 为美国独立日观察日休市,脚本正确返回最近交易日(7/2)收盘价并标注 `closed_holiday`:

| 标的 | 实测值 | 验收基准 | 结果 |
|------|--------|----------|------|
| `^NDX` | **29,329.21**(-1.61%) | 29,000–30,500 区间 | ✅(绝非 19,888 类离谱值) |
| `^VIX` | 15.81(-2.11%) | — | ✅ |
| `NVDA` | 194.83(-1.39%) | — | ✅(与规格书示例值一致) |
| 全 watchlist 15 个标的 | 一次批量请求全部成功,`failed_symbols: []` | — | ✅ |
| 无效 ticker `FAKETICKER123` | `price: null, status: "error", note: "取数失败,数值待核实,严禁编造"`,不影响同批其他标的 | 不得编造 | ✅ |

批量取数走单次 `yf.download`(15 标的 1 个请求),失败重试带指数退避(3s/6s/12s),本地未触发 429。

### news-search

| 数据块 | 结果 |
|--------|------|
| arXiv 官方 API(cs.AI/cs.CL/cs.LG) | ✅ 拉回结构化条目(标题/摘要/作者/日期/链接);24h 窗口无新论文时自动放宽到 72h 并在 `note` 中如实标注 |
| Anthropic Research 官网 | ✅ 抓到 8 条,含标题/日期/链接(best-effort,页面改版时自动降级且不影响论文块) |
| Hacker News Algolia | ✅ 窗口内高热度 story 10 条(注:`/search` 端点已不支持 points 过滤,代码用 `/search_by_date` + 客户端排序) |
| Tavily | ⚠️ 本机无 `TAVILY_API_KEY`,只完成了**连通性与错误路径验证**:端点可达、无效 key 返回 401(请求结构被服务端正常受理);无 key 时 11 个 Tavily 维度全部显式返回 `error: no TAVILY_API_KEY`,`tavily_status` 顶层报错,**无静默空结果** |

## 5. 待用户在 ArkClaw 上验证的事项(重要)

1. **Tavily 真实检索**(本地没有 key,未跑过真实检索):设好 `TAVILY_API_KEY` 后运行
   `python3 ~/.openclaw/workspace/skills/news-search/news.py --dimensions 1,11`
   确认 `tavily_status: "ok"` 且条目带链接。若结构异常,把输出发回来调。
2. **yfinance 在白名单环境的 429 表现**:本地网络与 ArkClaw 出口 IP 不同,限流阈值可能不同。运行
   `python3 ~/.openclaw/workspace/skills/market-quotes/quotes.py`
   看 `failed_symbols` 是否为空。若频繁 429,可在 cron 里错峰,或提供 Polygon/Finnhub/Alpha Vantage key 兜底。
3. **Anthropic Research 抓取**:本地实测 `www.anthropic.com` 200,但该块是 HTML 解析(best-effort),ArkClaw 上跑一次维度 8 确认条目正常。
4. **hn.algolia.com 的 `/search_by_date` 路径**:规格书实测的是 `/api/v1` 根路径可达,本代码用其下的 `search_by_date` 端点,理论同域无问题,跑一次 `--dimensions hn` 确认。
5. arXiv 重定向:规格书实测 `export.arxiv.org` 为 301→通,代码直接用 `https://`,requests 会自动跟随重定向,跑一次维度 8 确认即可(本地已通)。

## 6. 让 Agent 识别新 skill

装好后,在 OpenClaw 会话里任选其一:

- 直接对 Agent 说:"读一下 `~/.openclaw/workspace/skills/` 目录,里面有两个新 skill(market-quotes、news-search),阅读各自的 SKILL.md 并按其规则使用";
- 或重启/重载 OpenClaw 实例,让其重新扫描 skills 目录;
- 验证路由是否生效:问一句"现在纳指100多少点?"——正确行为是 Agent 调 `quotes.py` 而不是去搜网页。

## 7. 范围说明

- 未包含任何部署/cron/推送脚本(按规格书属用户手动管理)。
- skill 不调用任何 LLM;只负责取准确数据,组织语言是 Agent 层的事。
- 全部出站请求仅指向:`query1.finance.yahoo.com`(yfinance)、`api.polygon.io`(可选)、`finnhub.io`(可选)、`www.alphavantage.co`(可选)、`api.tavily.com`、`export.arxiv.org`、`www.anthropic.com`、`hn.algolia.com` —— 均在白名单实测清单内。
