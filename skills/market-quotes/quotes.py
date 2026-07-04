#!/usr/bin/env python3
"""
market-quotes skill — 结构化行情取数(美股指数 / 个股 / 加密)。

数据源:
  主: yfinance (query1.finance.yahoo.com)
  备: polygon.io (需 POLYGON_API_KEY), finnhub.io (需 FINNHUB_API_KEY),
      alphavantage (需 ALPHAVANTAGE_API_KEY)

硬性原则: 绝不编造数字。取数失败的标的返回 {"status": "error", "price": null},
上层 Agent 必须将其报告为「待核实」,不得用任何占位数字冒充。

用法:
  python3 quotes.py                          # 读 config/briefing.json 的全部 watchlist
  python3 quotes.py --symbols ^NDX,NVDA      # 指定标的
  python3 quotes.py --config /path/to/briefing.json
输出: stdout 打印一个 JSON 对象(见 SKILL.md 的字段说明)。
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BJ = ZoneInfo("Asia/Shanghai")

# 常见标的的显示名(避免为取名称而额外请求 Yahoo 触发限流)
NAME_MAP = {
    "^NDX": "Nasdaq-100",
    "^DJI": "Dow Jones Industrial Average",
    "^GSPC": "S&P 500",
    "^IXIC": "Nasdaq Composite",
    "^VIX": "CBOE Volatility Index",
    "^SOX": "PHLX Semiconductor",
    "^TNX": "US 10-Year Treasury Yield (x10)",
    "NVDA": "NVIDIA",
    "MSFT": "Microsoft",
    "AAPL": "Apple",
    "GOOGL": "Alphabet (Class A)",
    "META": "Meta Platforms",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "BTC-USD": "Bitcoin / USD",
    "ETH-USD": "Ethereum / USD",
    "GC=F": "Gold Futures",
    "CL=F": "WTI Crude Oil Futures",
}

FINNHUB_SYMBOL_CANDIDATES = {
    "^NDX": ["^NDX", "NDX", ".NDX"],
    "^DJI": ["^DJI", "DJI", ".DJI", "DJIA"],
    "^GSPC": ["^GSPC", "GSPC", ".GSPC", "SPX", ".SPX"],
    "^IXIC": ["^IXIC", "IXIC", ".IXIC"],
    "^VIX": ["^VIX", "VIX", ".VIX"],
    "^SOX": ["^SOX", "SOX", ".SOX"],
    "^TNX": ["^TNX", "TNX", ".TNX"],
    "BTC-USD": ["BINANCE:BTCUSDT", "COINBASE:BTC-USD"],
    "ETH-USD": ["BINANCE:ETHUSDT", "COINBASE:ETH-USD"],
}

POLYGON_SYMBOL_CANDIDATES = {
    "^NDX": ["I:NDX"],
    "^IXIC": ["I:COMP"],
    "^SOX": ["I:SOX"],
}

DEFAULT_CONFIG_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "briefing.json"),
    os.path.expanduser("~/.openclaw/workspace/config/briefing.json"),
]


def load_config(path=None):
    candidates = [path] if path else DEFAULT_CONFIG_CANDIDATES
    for p in candidates:
        if p and os.path.isfile(p):
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
    return {}


def symbols_from_config(cfg):
    syms = []
    for key in ("watchlist_indices", "watchlist_stocks", "watchlist_crypto"):
        syms += cfg.get(key, [])
    return syms


def market_status_for(last_bar_et: datetime, symbol: str, now_et: datetime) -> str:
    """根据最后一根日线的日期与当前美东时间判断市场状态。加密 7x24 视为 open。"""
    if symbol.endswith("-USD"):
        return "open"
    last_day = last_bar_et.date()
    today = now_et.date()
    if last_day == today:
        # 当天有数据:盘中或刚收盘
        if now_et.hour < 16:
            return "open"
        return "closed_today"
    # 最后交易日不是今天 → 休市中(周末/节假日/盘前)
    if now_et.weekday() >= 5 or (now_et.weekday() == 0 and now_et.hour < 9):
        return "closed_weekend"
    gap = (today - last_day).days
    if gap >= 1 and now_et.weekday() < 5 and now_et.hour >= 9:
        # 工作日盘中却没有当日数据 → 大概率节假日休市
        return "closed_holiday"
    return "closed"


def quote_from_history(symbol, hist):
    """从日线 DataFrame(单标的)提取最新价/涨跌。返回 dict 或 None。"""
    closes = hist["Close"].dropna()
    if len(closes) < 1:
        return None
    price = float(closes.iloc[-1])
    prev = float(closes.iloc[-2]) if len(closes) >= 2 else None
    ts = closes.index[-1]
    ts = ts.tz_convert(ET) if ts.tzinfo else ts.tz_localize(ET)
    out = {
        "symbol": symbol,
        "name": NAME_MAP.get(symbol, symbol),
        "price": round(price, 4),
        "change": round(price - prev, 4) if prev is not None else None,
        "change_pct": round((price - prev) / prev * 100, 2) if prev else None,
        "last_trade_day_et": str(ts.date()),
        "source": "yfinance",
        "status": "ok",
    }
    return out, ts.to_pydatetime()


def fetch_yfinance(symbols, retries=3):
    """批量取数,失败标的返回 None。带指数退避以规避 429。"""
    import yfinance as yf

    results = {}
    last_ts = {}
    remaining = list(symbols)
    delay = 3
    for attempt in range(retries):
        if not remaining:
            break
        if attempt > 0:
            time.sleep(delay)
            delay *= 2
        try:
            data = yf.download(
                remaining, period="10d", interval="1d",
                group_by="ticker", auto_adjust=False,
                progress=False, threads=False,
            )
        except Exception as e:
            sys.stderr.write(f"[yfinance] batch attempt {attempt+1} failed: {e}\n")
            continue
        if data is None or data.empty:
            continue
        still = []
        for sym in remaining:
            try:
                # group_by="ticker" 时列是 (ticker, field) 两级,单标的时也可能如此
                hist = data[sym] if data.columns.nlevels > 1 else data
                q = quote_from_history(sym, hist)
            except Exception:
                q = None
            if q:
                results[sym], last_ts[sym] = q
            else:
                still.append(sym)
        remaining = still
    return results, last_ts, remaining


def polygon_symbol_candidates(symbol):
    return POLYGON_SYMBOL_CANDIDATES.get(symbol, [])


def parse_polygon_aggs(symbol, provider_symbol, data):
    rows = data.get("results") or []
    if len(rows) < 1:
        return None
    rows = sorted(rows, key=lambda x: x.get("t", 0))
    latest = rows[-1]
    prev = rows[-2] if len(rows) >= 2 else None
    price = latest.get("c")
    if price in (None, 0):
        return None
    prev_close = prev.get("c") if prev else None
    ts = datetime.fromtimestamp(latest["t"] / 1000, tz=ET) if latest.get("t") else None
    return {
        "symbol": symbol,
        "name": NAME_MAP.get(symbol, symbol),
        "price": round(float(price), 4),
        "change": round(float(price - prev_close), 4) if prev_close else None,
        "change_pct": round(float((price - prev_close) / prev_close * 100), 2) if prev_close else None,
        "last_trade_day_et": ts.date().isoformat() if ts else None,
        "source": "polygon",
        "provider_symbol": provider_symbol,
        "provider_status": data.get("status"),
        "status": "ok",
    }


def fetch_polygon(symbol):
    """备用源 1:Polygon。免费层每分钟 5 次,仅用于优先兜底关键指数。"""
    key = os.environ.get("POLYGON_API_KEY")
    candidates = polygon_symbol_candidates(symbol)
    if not key or not candidates:
        return None
    import requests
    end = datetime.now(ET).date()
    start = end - timedelta(days=14)
    for provider_symbol in candidates:
        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/{provider_symbol}/range/1/day/{start}/{end}"
            r = requests.get(
                url,
                params={"adjusted": "true", "sort": "asc", "limit": 10, "apiKey": key},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            q = parse_polygon_aggs(symbol, provider_symbol, r.json())
            if q:
                return q
        except Exception:
            continue
        time.sleep(0.2)
    return None


def finnhub_symbol_candidates(symbol):
    return FINNHUB_SYMBOL_CANDIDATES.get(symbol, [symbol])


def parse_finnhub_quote(symbol, provider_symbol, data):
    price = data.get("c")
    if price in (None, 0):
        return None
    return {
        "symbol": symbol,
        "name": NAME_MAP.get(symbol, symbol),
        "price": round(float(price), 4),
        "change": round(float(data.get("d") or 0), 4),
        "change_pct": round(float(data.get("dp") or 0), 2),
        "last_trade_day_et": datetime.fromtimestamp(data["t"], tz=ET).date().isoformat() if data.get("t") else None,
        "source": "finnhub",
        "provider_symbol": provider_symbol,
        "status": "ok",
    }


def fetch_finnhub(symbol):
    """备用源 1:finnhub。对指数/加密尝试 Finnhub 常见 symbol 写法。"""
    key = os.environ.get("FINNHUB_API_KEY")
    if not key or "=" in symbol:
        return None
    import requests
    for provider_symbol in finnhub_symbol_candidates(symbol):
        try:
            r = requests.get("https://finnhub.io/api/v1/quote",
                             params={"symbol": provider_symbol, "token": key}, timeout=15)
            if r.status_code != 200:
                continue
            q = parse_finnhub_quote(symbol, provider_symbol, r.json())
            if q:
                return q
        except Exception:
            continue
        time.sleep(0.2)
    return None


def fetch_alphavantage(symbol):
    """备用源 2:Alpha Vantage GLOBAL_QUOTE(免费档每分钟 5 次,只在兜底时用)。"""
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key or symbol.startswith("^") or "=" in symbol:
        return None
    import requests
    try:
        r = requests.get("https://www.alphavantage.co/query",
                         params={"function": "GLOBAL_QUOTE", "symbol": symbol, "apikey": key},
                         timeout=15)
        g = r.json().get("Global Quote") or {}
        if not g.get("05. price"):
            return None
        return {
            "symbol": symbol,
            "name": NAME_MAP.get(symbol, symbol),
            "price": round(float(g["05. price"]), 4),
            "change": round(float(g.get("09. change") or 0), 4),
            "change_pct": round(float((g.get("10. change percent") or "0%").rstrip("%")), 2),
            "last_trade_day_et": g.get("07. latest trading day"),
            "source": "alphavantage",
            "status": "ok",
        }
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="Fetch structured market quotes")
    ap.add_argument("--symbols", help="逗号分隔的 ticker,如 ^NDX,NVDA,BTC-USD;缺省读 briefing.json")
    ap.add_argument("--config", help="briefing.json 路径")
    args = ap.parse_args()

    cfg = load_config(args.config)
    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = symbols_from_config(cfg)
    if not symbols:
        print(json.dumps({"error": "no symbols: pass --symbols or provide briefing.json"}, ensure_ascii=False))
        sys.exit(1)

    now_et = datetime.now(ET)
    results, last_ts, failed = fetch_yfinance(symbols)

    # 备用源兜底
    for sym in list(failed):
        q = fetch_polygon(sym) or fetch_finnhub(sym) or fetch_alphavantage(sym)
        if q:
            results[sym] = q
            failed.remove(sym)
        time.sleep(1)

    quotes = []
    for sym in symbols:
        if sym in results:
            q = results[sym]
            if sym in last_ts:
                q["market_status"] = market_status_for(last_ts[sym], sym, now_et)
            elif q.get("last_trade_day_et"):
                last_dt = datetime.fromisoformat(q["last_trade_day_et"]).replace(tzinfo=ET)
                q["market_status"] = market_status_for(last_dt, sym, now_et)
            quotes.append(q)
        else:
            quotes.append({
                "symbol": sym,
                "name": NAME_MAP.get(sym, sym),
                "price": None, "change": None, "change_pct": None,
                "source": None,
                "status": "error",
                "note": "取数失败,数值待核实,严禁编造",
            })

    overall = "unknown"
    non_crypto = [q.get("market_status") for q in quotes
                  if q.get("market_status") and not q["symbol"].endswith("-USD")]
    if non_crypto:
        overall = max(set(non_crypto), key=non_crypto.count)

    out = {
        "as_of_et": now_et.isoformat(timespec="seconds"),
        "as_of_beijing": now_et.astimezone(BJ).isoformat(timespec="seconds"),
        "market_status": overall,
        "quotes": quotes,
        "failed_symbols": failed,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
