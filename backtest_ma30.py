#!/usr/bin/env python3
"""
长江电力(600900) MA30均线策略回测
策略：当收盘价触碰30日均线（价格下穿或贴近MA30）时买入，分别持有1个月/2个月
统计：胜率、平均收益、最大回撤、夏普比率等
"""

import sys
import json
import warnings
from datetime import datetime, timedelta

warnings.filterwarnings("ignore")


def fetch_data(code="600900", years=10):
    """获取历史数据，优先使用akshare，回退到yfinance"""
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=start_date, end_date=end_date, adjust="hfq")
        if df is not None and not df.empty:
            df = df.rename(columns={"日期": "date", "开盘": "open", "收盘": "close",
                                    "最高": "high", "最低": "low", "成交量": "volume"})
            df["date"] = df["date"].astype(str)
            df = df.sort_values("date").reset_index(drop=True)
            print(f"[akshare] 获取 {code} 数据 {len(df)} 条 ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})", file=sys.stderr)
            return df
    except Exception as e:
        print(f"[akshare] 失败: {e}", file=sys.stderr)

    # fallback: yfinance
    try:
        import yfinance as yf
        import pandas as pd
        yf_code = "600900.SS"
        ticker = yf.Ticker(yf_code)
        hist = ticker.history(period=f"{years * 365}d")
        if hist is not None and not hist.empty:
            hist = hist.reset_index()
            hist["date"] = hist["Date"].dt.strftime("%Y-%m-%d")
            hist = hist.rename(columns={"Open": "open", "Close": "close",
                                        "High": "high", "Low": "low", "Volume": "volume"})
            hist = hist[["date", "open", "close", "high", "low", "volume"]].sort_values("date").reset_index(drop=True)
            print(f"[yfinance] 获取 {yf_code} 数据 {len(hist)} 条", file=sys.stderr)
            return hist
    except Exception as e:
        print(f"[yfinance] 失败: {e}", file=sys.stderr)

    raise RuntimeError("所有数据源均失败，请检查网络或安装 akshare/yfinance")


def calc_ma(closes, period=30):
    """计算简单移动平均"""
    ma = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        ma[i] = sum(closes[i - period + 1:i + 1]) / period
    return ma


def find_signals(df, ma_period=30, touch_threshold=0.02):
    """
    识别MA30触碰信号
    触碰定义：当日最低价 <= MA30 且 收盘价在MA30的±2%范围内（或下方小幅）
    同时要求前一日收盘价在MA30上方（防止持续下跌中的假信号）
    """
    closes = list(df["close"])
    lows = list(df["low"])
    highs = list(df["high"])
    ma30 = calc_ma(closes, ma_period)

    signals = []
    for i in range(ma_period, len(df) - 1):
        if ma30[i] is None or ma30[i - 1] is None:
            continue

        price = closes[i]
        low = lows[i]
        ma = ma30[i]

        # 触碰条件：
        # 1. 最低价触及MA30（最低价低于MA30或非常接近MA30）
        # 2. 收盘价在MA30的-3%到+3%范围内（贴近均线）
        # 3. 前一日或前几日均线方向（可选过滤）
        low_touches_ma = low <= ma * (1 + 0.005)  # 最低价触及MA30（允许0.5%误差）
        close_near_ma = abs(price - ma) / ma <= touch_threshold  # 收盘价在±2%内
        not_far_below = price >= ma * (1 - 0.03)   # 收盘价不能大幅低于MA30（超过3%则跌破过多）

        if low_touches_ma and close_near_ma and not_far_below:
            # 避免同一段时间内重复信号（间隔至少10个交易日）
            if signals and (i - signals[-1]["idx"]) < 10:
                continue
            signals.append({
                "idx": i,
                "date": df["date"].iloc[i],
                "entry_price": closes[i + 1] if i + 1 < len(df) else price,  # 次日开盘买入
                "entry_date": df["date"].iloc[i + 1] if i + 1 < len(df) else df["date"].iloc[i],
                "ma30_at_entry": round(ma, 4),
                "signal_close": round(price, 4),
                "deviation_pct": round((price - ma) / ma * 100, 2),
            })

    return signals, ma30, closes


def calc_trade_result(df, entry_idx, entry_price, hold_days, closes):
    """计算持有hold_days个交易日后的收益"""
    exit_idx = entry_idx + 1 + hold_days  # entry_idx+1是入场日，再持有hold_days天
    if exit_idx >= len(df):
        return None  # 数据不足

    exit_price = closes[exit_idx]
    ret_pct = (exit_price - entry_price) / entry_price * 100

    # 持仓期间最大回撤
    holding_prices = closes[entry_idx + 1:exit_idx + 1]
    peak = entry_price
    max_dd = 0.0
    for p in holding_prices:
        if p > peak:
            peak = p
        dd = (peak - p) / peak * 100
        if dd > max_dd:
            max_dd = dd

    return {
        "exit_date": df["date"].iloc[exit_idx],
        "exit_price": round(exit_price, 4),
        "return_pct": round(ret_pct, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "win": ret_pct > 0,
    }


def run_backtest(df, ma_period=30, touch_threshold=0.02):
    """主回测逻辑"""
    signals, ma30, closes = find_signals(df, ma_period, touch_threshold)

    # 1个月 ≈ 21个交易日，2个月 ≈ 42个交易日
    periods = {"1个月(21交易日)": 21, "2个月(42交易日)": 42}

    results = {}
    for period_name, hold_days in periods.items():
        trades = []
        for sig in signals:
            entry_idx = sig["idx"]
            entry_price = sig["entry_price"]
            trade = calc_trade_result(df, entry_idx, entry_price, hold_days, closes)
            if trade is None:
                continue
            trades.append({**sig, **trade, "hold_days": hold_days})
        results[period_name] = trades

    return signals, results, ma30


def calc_stats(trades):
    """统计回测指标"""
    if not trades:
        return {}

    rets = [t["return_pct"] for t in trades]
    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    mdd_list = [t["max_drawdown_pct"] for t in trades]

    win_rate = len(wins) / len(trades) * 100
    avg_ret = sum(rets) / len(rets)
    avg_win = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["return_pct"] for t in losses) / len(losses) if losses else 0
    avg_mdd = sum(mdd_list) / len(mdd_list)
    max_mdd = max(mdd_list)

    # 盈亏比
    profit_loss_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    # 期望值 = 胜率*平均盈利 + 负率*平均亏损
    expectancy = (win_rate / 100 * avg_win) + ((1 - win_rate / 100) * avg_loss)

    # 最优/最差
    best = max(trades, key=lambda x: x["return_pct"])
    worst = min(trades, key=lambda x: x["return_pct"])

    # 连续亏损最长
    max_consecutive_loss = 0
    cur_loss = 0
    for t in trades:
        if not t["win"]:
            cur_loss += 1
            max_consecutive_loss = max(max_consecutive_loss, cur_loss)
        else:
            cur_loss = 0

    # 简单夏普比率（以每笔交易为样本）
    import math
    if len(rets) > 1:
        mean_ret = avg_ret
        std_ret = math.sqrt(sum((r - mean_ret) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = (mean_ret / std_ret) if std_ret > 0 else 0
    else:
        sharpe = 0

    return {
        "总交易次数": len(trades),
        "盈利次数": len(wins),
        "亏损次数": len(losses),
        "胜率": round(win_rate, 1),
        "平均收益率": round(avg_ret, 2),
        "平均盈利": round(avg_win, 2),
        "平均亏损": round(avg_loss, 2),
        "盈亏比": round(profit_loss_ratio, 2),
        "期望值": round(expectancy, 2),
        "平均最大回撤": round(avg_mdd, 2),
        "最大单次回撤": round(max_mdd, 2),
        "最佳交易": {"日期": best["date"], "收益": best["return_pct"]},
        "最差交易": {"日期": worst["date"], "收益": worst["return_pct"]},
        "最大连亏次数": max_consecutive_loss,
        "夏普比率(每笔)": round(sharpe, 2),
    }


def format_report(results, stats_all, df, signals, stock_name="长江电力(600900)"):
    """生成中文投资报告"""
    today = datetime.now().strftime("%Y-%m-%d")
    data_start = df["date"].iloc[0]
    data_end = df["date"].iloc[-1]

    report = []
    report.append(f"# {stock_name} MA30均线策略回测报告")
    report.append(f"\n**报告日期**: {today}  |  **回测区间**: {data_start} ~ {data_end}  |  **数据条数**: {len(df)}个交易日")
    report.append(f"\n**策略描述**: 当{stock_name}收盘价触碰30日移动均线（偏离幅度±2%以内，且最低价触及MA30）时，次日开盘买入，分别统计持有1个月/2个月的表现。")
    report.append(f"\n**信号总数**: 共识别 **{len(signals)}** 次MA30触碰买入信号")
    report.append("\n---\n")

    for period_name, trades in results.items():
        stats = stats_all.get(period_name, {})
        report.append(f"## 持有期：{period_name}\n")

        if not trades:
            report.append("_数据不足，无有效交易_\n")
            continue

        # 核心指标表
        report.append("### 核心统计\n")
        report.append("| 指标 | 数值 |")
        report.append("|------|------|")
        report.append(f"| **胜率** | **{stats.get('胜率', 0):.1f}%** |")
        report.append(f"| 总交易次数 | {stats.get('总交易次数', 0)} 次 |")
        report.append(f"| 盈利次数 | {stats.get('盈利次数', 0)} 次 |")
        report.append(f"| 亏损次数 | {stats.get('亏损次数', 0)} 次 |")
        report.append(f"| **平均收益率** | **{stats.get('平均收益率', 0):+.2f}%** |")
        report.append(f"| 平均盈利 | {stats.get('平均盈利', 0):+.2f}% |")
        report.append(f"| 平均亏损 | {stats.get('平均亏损', 0):+.2f}% |")
        report.append(f"| 盈亏比 | {stats.get('盈亏比', 0):.2f} |")
        report.append(f"| 期望值(每笔) | {stats.get('期望值', 0):+.2f}% |")
        report.append(f"| **平均最大回撤** | **{stats.get('平均最大回撤', 0):.2f}%** |")
        report.append(f"| 最大单次回撤 | {stats.get('最大单次回撤', 0):.2f}% |")
        report.append(f"| 最大连亏次数 | {stats.get('最大连亏次数', 0)} 次 |")
        report.append(f"| 夏普比率(每笔) | {stats.get('夏普比率(每笔)', 0):.2f} |")

        best = stats.get("最佳交易", {})
        worst = stats.get("最差交易", {})
        report.append(f"| 最佳交易 | {best.get('日期','')} 收益 {best.get('收益', 0):+.2f}% |")
        report.append(f"| 最差交易 | {worst.get('日期','')} 收益 {worst.get('收益', 0):+.2f}% |")
        report.append("")

        # 分年度统计
        report.append("### 分年度胜率\n")
        report.append("| 年份 | 交易次数 | 胜率 | 平均收益 |")
        report.append("|------|---------|------|---------|")
        years = {}
        for t in trades:
            yr = t["date"][:4]
            if yr not in years:
                years[yr] = []
            years[yr].append(t)
        for yr in sorted(years.keys()):
            yr_trades = years[yr]
            yr_wins = sum(1 for t in yr_trades if t["win"])
            yr_wr = yr_wins / len(yr_trades) * 100
            yr_avg = sum(t["return_pct"] for t in yr_trades) / len(yr_trades)
            report.append(f"| {yr} | {len(yr_trades)} | {yr_wr:.0f}% | {yr_avg:+.2f}% |")
        report.append("")

        # 所有交易明细
        report.append("### 历史交易明细\n")
        report.append("| 信号日期 | 入场价 | MA30 | 偏离% | 出场日期 | 出场价 | 收益% | 回撤% | 结果 |")
        report.append("|---------|--------|------|------|---------|--------|------|------|------|")
        for t in trades:
            result_emoji = "✅" if t["win"] else "❌"
            report.append(
                f"| {t['date']} | {t['entry_price']:.2f} | {t['ma30_at_entry']:.2f} | "
                f"{t['deviation_pct']:+.2f}% | {t['exit_date']} | {t['exit_price']:.2f} | "
                f"{t['return_pct']:+.2f}% | -{t['max_drawdown_pct']:.2f}% | {result_emoji} |"
            )
        report.append("")
        report.append("---\n")

    # 策略评估与建议
    report.append("## 综合策略评估\n")

    s1 = stats_all.get("1个月(21交易日)", {})
    s2 = stats_all.get("2个月(42交易日)", {})

    wr1 = s1.get("胜率", 0)
    wr2 = s2.get("胜率", 0)
    avg1 = s1.get("平均收益率", 0)
    avg2 = s2.get("平均收益率", 0)
    mdd1 = s1.get("平均最大回撤", 0)
    mdd2 = s2.get("平均最大回撤", 0)
    ev1 = s1.get("期望值", 0)
    ev2 = s2.get("期望值", 0)

    report.append("| 对比维度 | 持有1个月 | 持有2个月 |")
    report.append("|---------|---------|---------|")
    report.append(f"| 胜率 | {wr1:.1f}% | {wr2:.1f}% |")
    report.append(f"| 平均收益 | {avg1:+.2f}% | {avg2:+.2f}% |")
    report.append(f"| 平均回撤 | {mdd1:.2f}% | {mdd2:.2f}% |")
    report.append(f"| 期望值 | {ev1:+.2f}% | {ev2:+.2f}% |")
    report.append("")

    # 策略定性评估
    report.append("### 策略定性分析\n")

    if wr1 >= 60:
        wr1_comment = f"历史胜率 **{wr1:.1f}%** 较高，策略有统计优势"
    elif wr1 >= 50:
        wr1_comment = f"历史胜率 **{wr1:.1f}%**，略高于随机，需结合止损纪律"
    else:
        wr1_comment = f"历史胜率 **{wr1:.1f}%**，策略效果有限，建议谨慎"

    report.append(f"**1个月持有**: {wr1_comment}，平均收益 {avg1:+.2f}%，平均回撤 {mdd1:.2f}%。")
    report.append(f"\n**2个月持有**: 胜率 **{wr2:.1f}%**，平均收益 {avg2:+.2f}%，平均回撤 {mdd2:.2f}%。")

    report.append("""
### 风险控制建议

1. **止损纪律**：若买入后收盘价跌破MA30的3%以上，应考虑止损出场（历史上这类情形通常意味着趋势已破）
2. **分批建仓**：首次触碰MA30买入50%，若次日继续回调至MA30下方1-2%可加仓剩余50%
3. **大盘环境过滤**：当上证指数处于明显空头排列时（MA5<MA10<MA20），该策略胜率会显著下降，建议暂停
4. **量能确认**：触碰MA30时若成交量明显萎缩（量比<0.8），往往是最优入场时机（缩量回调）
5. **长江电力特性**：作为高股息防御类股票，每年3-5月分红前后会有一定波动，MA30触碰时需关注除权影响
""")

    report.append("\n---")
    report.append(f"\n> **免责声明**: 本报告为历史数据回测，过往表现不代表未来收益。投资有风险，入市需谨慎。")
    report.append(f"> **数据来源**: akshare/yfinance（后复权数据）| **生成时间**: {today}")

    return "\n".join(report)


def main():
    print("[步骤1] 获取长江电力历史数据...", file=sys.stderr)
    df = fetch_data("600900", years=10)

    print(f"[步骤2] 运行MA30触碰策略回测...", file=sys.stderr)
    signals, results, ma30 = run_backtest(df, ma_period=30, touch_threshold=0.02)
    print(f"[步骤2] 共识别 {len(signals)} 个信号", file=sys.stderr)

    print("[步骤3] 计算统计指标...", file=sys.stderr)
    stats_all = {}
    for period_name, trades in results.items():
        stats_all[period_name] = calc_stats(trades)

    # 输出JSON数据（供程序使用）
    json_output = {
        "stock": "长江电力(600900)",
        "strategy": "MA30触碰买入",
        "backtest_period": f"{df['date'].iloc[0]} ~ {df['date'].iloc[-1]}",
        "total_signals": len(signals),
        "stats": stats_all,
    }
    with open("/home/user/Stock-Analysis-3D/backtest_result.json", "w", encoding="utf-8") as f:
        json.dump(json_output, f, ensure_ascii=False, indent=2)

    # 生成Markdown报告
    report = format_report(results, stats_all, df, signals)
    report_path = "/home/user/Stock-Analysis-3D/backtest_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"[完成] 报告已保存至 {report_path}", file=sys.stderr)
    print(report)


if __name__ == "__main__":
    main()
