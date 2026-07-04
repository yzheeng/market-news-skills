#!/usr/bin/env python3
"""
news-search skill — 固化的资讯检索流水线。

数据源与分工:
  Tavily  (api.tavily.com, 需 TAVILY_API_KEY) : 科技/AI 资讯、地缘、宏观 —— 境外内容主通道
  arXiv   (export.arxiv.org/api, 直连)        : 论文块,结构化 Atom XML
  Anthropic (www.anthropic.com, 直连)         : Research 板块动态,best-effort 抓取
  Hacker News (hn.algolia.com/api/v1, 直连)   : 科技圈热点补充

硬性原则:
  - Tavily 失败必须显式报错(tavily_status 字段),不得静默返回空。
  - 每条资讯必带原始链接;搜不到就返回空列表,不编造。
  - skill 只做提取/整理,不做主观评论。

用法:
  python3 news.py --dimensions all              # 全部维度(晨报)
  python3 news.py --dimensions 1,8,11           # 指定维度编号
  python3 news.py --dimensions 8 --hours 168    # 周报窗口
输出: stdout 打印 JSON(见 SKILL.md)。
"""

import argparse
import html as html_mod
import json
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET_XML
from datetime import datetime, timedelta, timezone

import requests

UA = {"User-Agent": "Mozilla/5.0 (compatible; openclaw-news-skill/1.0)"}

DEFAULT_CONFIG_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "briefing.json"),
    os.path.expanduser("~/.openclaw/workspace/config/briefing.json"),
]

# 维度定义: id -> (key, 中文标签, 数据源, Tavily 查询列表生成器占位)
DIMENSIONS = {
    1:  ("llm_releases",    "大模型发布/更新",   "tavily"),
    2:  ("ai_agents",       "AI Agent 产品动态", "tavily"),
    3:  ("open_source",     "重要开源项目",      "tavily"),
    4:  ("funding_ma",      "大额融资/收购",     "tavily"),
    5:  ("chips_compute",   "芯片/算力行业",     "tavily"),
    6:  ("bigtech_strategy","大厂 AI 战略",      "tavily"),
    7:  ("ai_policy",       "AI 监管/政策",      "tavily"),
    8:  ("research_papers", "研究前沿/论文",     "arxiv+anthropic"),
    9:  ("geopolitics",     "地缘政治",          "tavily"),
    10: ("macro",           "宏观经济",          "tavily"),
    11: ("cn_llm",          "国产大模型专项",    "tavily"),
}

TAVILY_QUERIES = {
    1:  ["new large language model release OR update announcement"],
    2:  ["AI agent product launch OR update news"],
    3:  ["major open source AI project release trending"],
    4:  ["AI startup funding round OR acquisition announcement"],
    5:  ["semiconductor AI chip industry news NVIDIA TSMC datacenter GPU"],
    6:  ["big tech AI strategy news Google Microsoft Meta OpenAI Anthropic Amazon"],
    7:  ["AI regulation policy government news"],
    9:  ["geopolitics news Middle East OR Russia Ukraine OR US China"],
    10: ["macroeconomy news Federal Reserve OR CPI OR nonfarm payrolls OR global stock markets"],
    # 11 由 config 的 llm_watch 动态生成
}


def load_config(path=None):
    candidates = [path] if path else DEFAULT_CONFIG_CANDIDATES
    for p in candidates:
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def domain_of(url):
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""


def one_line(text, limit=200):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------------------------------------------------------------- Tavily

def tavily_search(query, hours, api_key, max_results=8):
    """调 Tavily /search。失败抛异常给上层统一处理。"""
    days = max(1, round(hours / 24))
    r = requests.post(
        "https://api.tavily.com/search",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {api_key}"},
        json={
            "query": query,
            "topic": "news",
            "days": days,
            "search_depth": "basic",
            "max_results": max_results,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def run_tavily_dimension(dim_id, label, queries, hours, api_key):
    items = []
    for q in queries:
        for res in tavily_search(q, hours, api_key):
            items.append({
                "title": res.get("title", ""),
                "summary": one_line(res.get("content", "")),
                "domain": domain_of(res.get("url", "")),
                "url": res.get("url", ""),
                "published": res.get("published_date"),
                "dimension": label,
            })
        time.sleep(0.5)
    return items


# ---------------------------------------------------------------- arXiv

ATOM = "{http://www.w3.org/2005/Atom}"


def fetch_arxiv(categories, hours, max_results=25):
    """arXiv 官方 API,按提交时间倒序;窗口内无结果时自动放宽到 72h 并标注。"""
    query = "+OR+".join(f"cat:{c}" for c in categories)
    url = ("https://export.arxiv.org/api/query?search_query=" + query +
           f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}")
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    root = ET_XML.fromstring(r.text)

    entries = []
    for e in root.findall(ATOM + "entry"):
        published = (e.findtext(ATOM + "published") or "").strip()
        try:
            pub_dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            continue
        authors = [a.findtext(ATOM + "name") for a in e.findall(ATOM + "author")][:5]
        link = (e.findtext(ATOM + "id") or "").strip()
        entries.append({
            "title": one_line(e.findtext(ATOM + "title") or "", 300),
            "summary": one_line(e.findtext(ATOM + "summary") or ""),
            "authors": authors,
            "domain": "arxiv.org",
            "url": link,
            "published": published,
            "dimension": "研究前沿/论文",
            "_dt": pub_dt,
        })

    now = datetime.now(timezone.utc)
    note = None
    within = [x for x in entries if now - x["_dt"] <= timedelta(hours=hours)]
    if not within and entries:
        within = [x for x in entries if now - x["_dt"] <= timedelta(hours=72)][:10]
        note = f"过去 {hours}h 无新论文,已放宽到 72h 窗口"
    for x in within:
        x.pop("_dt", None)
    return within, note


# ---------------------------------------------------------------- Anthropic Research

def fetch_anthropic_research(max_items=8):
    """抓 www.anthropic.com/research 的文章链接。页面结构可能变化,best-effort。"""
    r = requests.get("https://www.anthropic.com/research", headers=UA, timeout=30)
    r.raise_for_status()
    html = r.text
    # 提取 <a href="/research/..."> / <a href="/news/..."> 及其可见文本
    seen, items = set(), []
    for m in re.finditer(r'href="(/(?:research|news)/[a-z0-9\-]+)"[^>]*>(.*?)</a>',
                         html, re.S | re.I):
        path, inner = m.group(1), m.group(2)
        if path in seen:
            continue
        seen.add(path)
        title = one_line(html_mod.unescape(re.sub(r"<[^>]+>", " ", inner)), 150)
        if not title or len(title) < 8:
            continue
        # 卡片文本混有日期与栏目名,顺序不固定(如 "Jun 8, 2026 Science <标题>"
        # 或 "Economic Research Jun 26, 2026 <标题>")—— 抽出日期、剥掉栏目前缀
        published = None
        m2 = re.search(r"([A-Z][a-z]{2} \d{1,2}, \d{4})", title)
        if m2:
            try:
                published = datetime.strptime(m2.group(1), "%b %d, %Y").date().isoformat()
                title = (title[:m2.start()] + " " + title[m2.end():]).strip()
            except ValueError:
                pass
        for cat in ("Frontier Red Team", "Economic Research", "Societal Impacts",
                    "Interpretability", "Red Team", "Alignment", "Science",
                    "Research", "Policy", "Product"):
            if title.startswith(cat + " "):
                title = title[len(cat):].strip()
                break
        title = one_line(title, 150)
        items.append({
            "title": title,
            "summary": "",
            "domain": "www.anthropic.com",
            "url": "https://www.anthropic.com" + path,
            "published": published,
            "dimension": "研究前沿/论文",
        })
        if len(items) >= max_items:
            break
    return items


# ---------------------------------------------------------------- Hacker News

def fetch_hn(hours, min_points=80, max_items=10):
    """HN Algolia:窗口内高热度 story,科技热点补充。
    注:/search 端点不支持 points 过滤,须用 /search_by_date,再客户端按热度排序。"""
    cutoff = int(time.time()) - hours * 3600
    r = requests.get(
        "https://hn.algolia.com/api/v1/search_by_date",
        params={"tags": "story",
                "numericFilters": f"created_at_i>{cutoff},points>{min_points}",
                "hitsPerPage": 50},
        headers=UA, timeout=30,
    )
    r.raise_for_status()
    hits = sorted(r.json().get("hits", []),
                  key=lambda h: h.get("points", 0), reverse=True)[:max_items]
    items = []
    for h in hits:
        url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
        items.append({
            "title": h.get("title", ""),
            "summary": f"HN {h.get('points', 0)} points, {h.get('num_comments', 0)} comments",
            "domain": domain_of(url),
            "url": url,
            "published": h.get("created_at"),
            "dimension": "科技热点(HN)",
        })
    return items


# ---------------------------------------------------------------- main

def dedupe(items):
    seen, out = set(), []
    for it in items:
        key = (it.get("url") or "").rstrip("/").lower()
        if key and key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def main():
    ap = argparse.ArgumentParser(description="Structured news retrieval")
    ap.add_argument("--dimensions", default="all",
                    help="'all' 或逗号分隔的维度编号(1-11),另接受 'hn' 拉取 HN 热点补充")
    ap.add_argument("--hours", type=int, help="回溯窗口小时数,缺省读 config(默认 24)")
    ap.add_argument("--config", help="briefing.json 路径")
    args = ap.parse_args()

    cfg = load_config(args.config)
    hours = args.hours or cfg.get("news_lookback_hours", 24)
    llm_watch = cfg.get("llm_watch", ["GLM", "DeepSeek", "Kimi", "MiniMax"])
    arxiv_cats = cfg.get("arxiv_categories", ["cs.AI", "cs.CL", "cs.LG"])

    want_hn = False
    if args.dimensions.strip().lower() == "all":
        dim_ids = sorted(DIMENSIONS)
        want_hn = True
    else:
        parts = [p.strip() for p in args.dimensions.split(",") if p.strip()]
        want_hn = "hn" in [p.lower() for p in parts]
        dim_ids = sorted({int(p) for p in parts if p.isdigit() and int(p) in DIMENSIONS})

    api_key = os.environ.get("TAVILY_API_KEY", "")
    tavily_status = "ok" if api_key else "error: TAVILY_API_KEY 未设置,境外资讯维度全部不可用"

    queries_11 = [" OR ".join(f'"{name}"' for name in llm_watch) + " AI model news"]

    out_dims = []
    for did in dim_ids:
        key, label, source = DIMENSIONS[did]
        block = {"id": did, "key": key, "label": label, "source": source,
                 "status": "ok", "items": []}

        if source == "tavily":
            if not api_key:
                block["status"] = "error: no TAVILY_API_KEY"
            else:
                queries = queries_11 if did == 11 else TAVILY_QUERIES[did]
                try:
                    block["items"] = dedupe(
                        run_tavily_dimension(did, label, queries, hours, api_key))
                except Exception as e:
                    block["status"] = f"error: tavily 调用失败: {e}"
                    tavily_status = f"error: {e}"
        elif source == "arxiv+anthropic":
            try:
                papers, note = fetch_arxiv(arxiv_cats, hours)
                block["items"] += papers
                if note:
                    block["note"] = note
            except Exception as e:
                block["status"] = f"error: arxiv 调用失败: {e}"
            try:
                block["items"] += fetch_anthropic_research()
            except Exception as e:
                block.setdefault("warnings", []).append(f"anthropic 抓取失败(不影响论文块): {e}")

        if block["status"] == "ok" and not block["items"]:
            block["status"] = "empty"  # 如实说明该维度当日无内容
        out_dims.append(block)

    if want_hn:
        block = {"id": "hn", "key": "hn_hot", "label": "科技热点(HN)",
                 "source": "hackernews", "status": "ok", "items": []}
        try:
            block["items"] = fetch_hn(hours)
            if not block["items"]:
                block["status"] = "empty"
        except Exception as e:
            block["status"] = f"error: {e}"
        out_dims.append(block)

    print(json.dumps({
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "lookback_hours": hours,
        "tavily_status": tavily_status,
        "dimensions": out_dims,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
