---
name: news-search
description: 按 11 个固定维度检索科技/AI/地缘/宏观资讯与最新 AI 论文,返回带原始链接的结构化条目。凡需要"今天/最近发生了什么"类资讯(晨报、AI 动态、论文速递、地缘、宏观),必须调用本 skill,禁止临场自由发挥搜索。
---

# news-search — 固化的资讯检索流水线

## 何时必须使用本 skill(触发场景)

需要以下任一内容时,调用本 skill,**不要自行随意搜索**(临场发挥会导致质量飘忽):

- 晨报/日报的资讯板块(用 `--dimensions all`)
- AI/科技动态:大模型发布、Agent 产品、开源项目、融资收购、芯片算力、大厂战略、监管政策
- 最新 AI 论文/研究前沿(arXiv 结构化数据 + Anthropic Research)
- 地缘政治(中东/俄乌/中美)、宏观(美联储/CPI/非农/全球股市)
- 国产大模型专项(GLM/DeepSeek/Kimi/MiniMax,名单在 config 的 `llm_watch`)

**注意**:行情数字不归本 skill 管,报价格请用 `market-quotes`。

## 调用方式

```bash
# 全部 11 个维度 + HN 热点(晨报用)
python3 {skill_dir}/news.py --dimensions all

# 指定维度(编号见下),可附加 'hn' 拉 HN 热点
python3 {skill_dir}/news.py --dimensions 1,8,11,hn

# 周报窗口(过去 7 天)
python3 {skill_dir}/news.py --dimensions all --hours 168
```

维度编号:1 大模型发布/更新 · 2 AI Agent 产品 · 3 重要开源项目 · 4 大额融资/收购 · 5 芯片/算力 · 6 大厂 AI 战略 · 7 AI 监管/政策 · 8 研究前沿/论文 · 9 地缘政治 · 10 宏观 · 11 国产大模型专项

依赖首次安装:`pip install --break-system-packages -r {skill_dir}/requirements.txt`
必需环境变量:`TAVILY_API_KEY`(维度 8 之外的所有维度都依赖它)。

## 输出字段说明(stdout,JSON)

```json
{
  "generated_at": "2026-07-04T11:51:56+08:00",
  "lookback_hours": 24,
  "tavily_status": "ok",              // 非 ok = 境外资讯这次没取到,必须向用户明示!
  "dimensions": [
    {
      "id": 8, "key": "research_papers", "label": "研究前沿/论文",
      "source": "arxiv+anthropic",
      "status": "ok",                 // ok / empty(当日无内容,如实说明) / error: ...
      "note": "过去 24h 无新论文,已放宽到 72h 窗口",   // 仅论文块可能出现
      "items": [
        {
          "title": "...",
          "summary": "一句话摘要(原文提取,无主观评论)",
          "domain": "arxiv.org",
          "url": "http://arxiv.org/abs/...",   // 每条必带,供用户核实
          "published": "2026-07-02T17:59:56Z",
          "dimension": "研究前沿/论文",
          "authors": ["..."]          // 仅论文条目有
        }
      ]
    }
  ]
}
```

## 使用输出时的硬性规则

1. `tavily_status` 非 `ok` 时,**必须**在回复中告知用户"境外资讯本次未获取到",不得假装没事。
2. `status: "empty"` 的维度如实说"该维度今日无内容",**禁止编造新闻凑数**。
3. 引用条目时保留原始链接。summary 是原文提取,主观点评是 Agent 层的事。
4. 论文块(维度 8)来自 arXiv 官方 API + Anthropic Research 官网直连,不经 Tavily。

## 数据源与分工

| 源 | 角色 | 依赖 |
|----|------|------|
| api.tavily.com | 境外资讯唯一通道(维度 1-7、9-11) | `TAVILY_API_KEY` |
| export.arxiv.org/api | 论文块,结构化 Atom XML | 无 |
| www.anthropic.com/research | 研究动态 best-effort 抓取(页面改版时自动降级,不影响论文) | 无 |
| hn.algolia.com/api/v1 | 科技热点补充(窗口内高热度 story) | 无 |
