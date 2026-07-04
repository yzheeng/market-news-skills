---
name: market-quotes
description: 获取准确的美股指数、个股、加密货币实时/最近收盘行情(价格、涨跌额、涨跌幅)。任何需要报出行情数字的场合必须调用本 skill,禁止用网页搜索或凭记忆作答。
---

# market-quotes — 结构化行情取数

## 何时必须使用本 skill(触发场景)

只要回答中需要出现**任何行情数值**,就必须调用本 skill 取数,包括但不限于:

- 美股指数点位/涨跌:纳指100(^NDX)、道琼斯(^DJI)、标普500(^GSPC)、纳斯达克综合(^IXIC)、VIX 恐慌指数(^VIX)、费城半导体(^SOX)、十年期美债收益率(^TNX)
- 个股价格/涨跌:NVDA、MSFT、AAPL、GOOGL、META、TSLA 等任意 ticker
- 加密货币:BTC-USD、ETH-USD
- 大宗商品期货:GC=F(黄金)、CL=F(原油)
- 晨报/简报中的"市场概况"板块

**严格禁止**:
1. 禁止用搜索引擎搜网页文本来"猜"行情数字(历史教训:曾把 ^NDX 真实值 ~29,800 错报成 19,888)。
2. 禁止凭训练记忆报价格。
3. 若本 skill 对某标的返回 `"status": "error"`,必须如实告知用户"该标的取数失败、待核实",**绝不允许编造或沿用旧数字**。

## 调用方式

```bash
# 取 config/briefing.json 中全部 watchlist(指数+个股+加密)
python3 {skill_dir}/quotes.py

# 指定标的(逗号分隔,Yahoo Finance ticker 格式)
python3 {skill_dir}/quotes.py --symbols ^NDX,^VIX,NVDA,BTC-USD

# 指定配置文件路径
python3 {skill_dir}/quotes.py --config ~/.openclaw/workspace/config/briefing.json
```

依赖首次安装:`pip install --break-system-packages -r {skill_dir}/requirements.txt`

## 输出字段说明(stdout,JSON)

```json
{
  "as_of_et": "2026-07-03T23:49:54-04:00",     // 取数时刻(美东)
  "as_of_beijing": "2026-07-04T11:49:54+08:00", // 取数时刻(北京)
  "market_status": "closed_holiday",            // 整体市场状态,见下
  "quotes": [
    {
      "symbol": "^NDX",
      "name": "Nasdaq-100",
      "price": 29329.21,          // 当前价或最近收盘价
      "change": -479.92,          // 相对前一交易日收盘的涨跌额
      "change_pct": -1.61,        // 涨跌幅 %
      "last_trade_day_et": "2026-07-02",  // 数据所属交易日(美东)
      "source": "yfinance",       // 数据源: yfinance / polygon / finnhub / alphavantage
      "status": "ok",             // ok = 数据可信; error = 取数失败(数值为 null)
      "market_status": "closed_holiday"
    }
  ],
  "failed_symbols": []            // 所有源都失败的标的列表
}
```

`market_status` 取值:`open`(盘中)、`closed_today`(当日已收盘)、`closed_weekend`(周末)、`closed_holiday`(节假日休市)、`closed`。休市时 `price` 为**最近交易日收盘价**,报告时请注明 `last_trade_day_et` 日期。

注意:`^TNX` 是十年期美债收益率,实测返回值即收益率百分数(如 4.372 表示 4.372%);若某天取到 40+ 的值,则是 Yahoo 旧的 ×10 惯例,请除以 10 后再报。

## 数据源与降级

主源 yfinance(带控频与指数退避重试,规避 429)。失败时按序降级:Polygon(需环境变量 `POLYGON_API_KEY`)→ Finnhub(需 `FINNHUB_API_KEY`)→ Alpha Vantage(需 `ALPHAVANTAGE_API_KEY`)。Polygon 免费层每分钟 5 次,优先兜底关键指数,实测可取 `^NDX`/`^IXIC`/`^SOX`;`^VIX`/`^GSPC`/`^DJI` 可能需更高权限或其他源。Finnhub 可兜底普通股票与部分加密,但指数可能要求额外市场数据订阅。Alpha Vantage 主要兜底普通股票。全部源失败才返回 `status: error`。
