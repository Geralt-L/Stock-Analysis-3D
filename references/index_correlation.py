#!/usr/bin/env python3
"""
Index Correlation / Inverse-Relationship Analyzer
道琼指数 vs AI 标的指数 —— 相关性 / 反向性统计分析

Pure data + math. No AI/LLM calls. Fetches daily OHLC from Yahoo Finance's
public chart endpoint (works behind a CA-terminating proxy via `requests`,
avoiding yfinance/curl_cffi TLS issues).

它回答一个问题："道指和 AI 标的指数到底有多少反向性？"
用一组统计指标量化：Pearson / Spearman 相关系数、Beta、R²、同向/反向天数占比、
滚动相关系数、以及不同时间窗口下相关性的变化。

Usage:
    python3 index_correlation.py                       # 默认 DJI vs AIQ，5 年
    python3 index_correlation.py --ai AIQ --range 2y
    python3 index_correlation.py --ai BASKET           # 自建等权 AI 篮子
    python3 index_correlation.py --json                # 输出机器可读 JSON

Notes on the "AI 标的指数" proxy:
    没有唯一官方"AI 指数"。本脚本默认用 AIQ(Global X AI & Technology ETF)
    作为可交易的 AI 宽基代理，并可选 BOTZ / IRBO / 自建等权篮子做稳健性交叉验证。
"""

import os
import sys
import json
import math
import argparse
import datetime as dt
from urllib.parse import quote

import requests

# 让 requests 信任代理 CA（若环境已配置则无副作用）
_CA = "/root/.ccr/ca-bundle.crt"
if os.path.exists(_CA):
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _CA)

YF_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
_HDR = {"User-Agent": "Mozilla/5.0"}

# 自建等权 AI 篮子（纯 AI/算力/应用龙头）
AI_BASKET = ["NVDA", "MSFT", "GOOGL", "META", "AVGO", "AMD", "PLTR", "TSM"]


def _log(msg):
    print(f"[INFO] {msg}", file=sys.stderr)


# ------------------------------------------------------------------
# 数据获取
# ------------------------------------------------------------------
def fetch_series(symbol: str, rng: str = "5y") -> dict:
    """返回 {date_str: close} 的有序 dict。"""
    url = YF_CHART.format(sym=quote(symbol, safe=""))
    r = requests.get(url, params={"range": rng, "interval": "1d"},
                     headers=_HDR, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    out = {}
    for t, c in zip(ts, closes):
        if c is None:
            continue
        d = dt.datetime.utcfromtimestamp(t).strftime("%Y-%m-%d")
        out[d] = float(c)
    if not out:
        raise ValueError(f"no data for {symbol}")
    return out


def fetch_basket_equal_weight(symbols, rng="5y") -> dict:
    """等权价格指数：每只归一化到起点=100，再取均值。"""
    series = {s: fetch_series(s, rng) for s in symbols}
    # 公共交易日
    common = set.intersection(*[set(v.keys()) for v in series.values()])
    dates = sorted(common)
    if not dates:
        raise ValueError("no common dates for basket")
    base = {s: series[s][dates[0]] for s in symbols}
    out = {}
    for d in dates:
        vals = [series[s][d] / base[s] * 100.0 for s in symbols]
        out[d] = sum(vals) / len(vals)
    return out


# ------------------------------------------------------------------
# 统计工具（无 numpy/scipy 依赖，纯 Python）
# ------------------------------------------------------------------
def align(a: dict, b: dict):
    """对齐两条序列的公共交易日，返回 (dates, xa, xb)。"""
    common = sorted(set(a.keys()) & set(b.keys()))
    xa = [a[d] for d in common]
    xb = [b[d] for d in common]
    return common, xa, xb


def pct_returns(prices):
    """日收益率（简单收益）。"""
    return [(prices[i] / prices[i - 1] - 1.0) for i in range(1, len(prices))]


def mean(x):
    return sum(x) / len(x)


def std(x, ddof=1):
    m = mean(x)
    n = len(x)
    if n - ddof <= 0:
        return 0.0
    return math.sqrt(sum((v - m) ** 2 for v in x) / (n - ddof))


def pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = mean(x), mean(y)
    num = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    dx = math.sqrt(sum((v - mx) ** 2 for v in x))
    dy = math.sqrt(sum((v - my) ** 2 for v in y))
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


def _rank(x):
    """平均秩（处理并列）。"""
    order = sorted(range(len(x)), key=lambda i: x[i])
    ranks = [0.0] * len(x)
    i = 0
    while i < len(x):
        j = i
        while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(x, y):
    return pearson(_rank(x), _rank(y))


def ols_beta(x, y):
    """y = alpha + beta*x，返回 (beta, alpha, r2)。x=市场(DJI), y=AI。"""
    n = len(x)
    mx, my = mean(x), mean(y)
    sxx = sum((x[i] - mx) ** 2 for i in range(n))
    sxy = sum((x[i] - mx) * (y[i] - my) for i in range(n))
    if sxx == 0:
        return None, None, None
    beta = sxy / sxx
    alpha = my - beta * mx
    ss_tot = sum((y[i] - my) ** 2 for i in range(n))
    ss_res = sum((y[i] - (alpha + beta * x[i])) ** 2 for i in range(n))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else None
    return beta, alpha, r2


def direction_agreement(rx, ry):
    """同向 / 反向天数统计。"""
    same = opp = zero = 0
    for a, b in zip(rx, ry):
        sa = (a > 0) - (a < 0)
        sb = (b > 0) - (b < 0)
        if sa == 0 or sb == 0:
            zero += 1
        elif sa == sb:
            same += 1
        else:
            opp += 1
    total = same + opp  # 只在双方都非零时判定方向
    return {
        "same_days": same,
        "opposite_days": opp,
        "flat_days": zero,
        "same_pct": round(same / total * 100, 2) if total else None,
        "opposite_pct": round(opp / total * 100, 2) if total else None,
    }


def rolling_corr(rx, ry, window):
    out = []
    for i in range(window, len(rx) + 1):
        out.append(pearson(rx[i - window:i], ry[i - window:i]))
    out = [c for c in out if c is not None]
    if not out:
        return None
    return {
        "window": window,
        "n": len(out),
        "min": round(min(out), 3),
        "max": round(max(out), 3),
        "mean": round(mean(out), 3),
        "pct_negative": round(sum(1 for c in out if c < 0) / len(out) * 100, 2),
        "last": round(out[-1], 3),
    }


def annualized_vol(rets):
    return std(rets) * math.sqrt(252)


# ------------------------------------------------------------------
# 主分析
# ------------------------------------------------------------------
def analyze(dow_sym, ai_sym, rng, ai_is_basket=False):
    dow = fetch_series(dow_sym, rng)
    if ai_is_basket:
        ai = fetch_basket_equal_weight(AI_BASKET, rng)
        ai_label = "等权AI篮子(" + ",".join(AI_BASKET) + ")"
    else:
        ai = fetch_series(ai_sym, rng)
        ai_label = ai_sym

    dates, pd_, pa_ = align(dow, ai)
    if len(dates) < 30:
        raise ValueError(f"公共交易日过少: {len(dates)}")

    rd = pct_returns(pd_)   # Dow 日收益
    ra = pct_returns(pa_)   # AI 日收益

    # 价格水平相关（含虚假相关警告）
    lvl_pearson = pearson(pd_, pa_)
    lvl_spearman = spearman(pd_, pa_)

    # 收益率相关（真正衡量联动/反向的核心指标）
    ret_pearson = pearson(rd, ra)
    ret_spearman = spearman(rd, ra)

    beta, alpha, r2 = ols_beta(rd, ra)
    dir_stat = direction_agreement(rd, ra)

    # 累计涨幅
    dow_total = (pd_[-1] / pd_[0] - 1) * 100
    ai_total = (pa_[-1] / pa_[0] - 1) * 100

    result = {
        "dow_symbol": dow_sym,
        "ai_symbol": ai_label,
        "range": rng,
        "trading_days": len(dates),
        "start_date": dates[0],
        "end_date": dates[-1],
        "cumulative_return_pct": {
            "dow": round(dow_total, 2),
            "ai": round(ai_total, 2),
        },
        "annualized_vol_pct": {
            "dow": round(annualized_vol(rd) * 100, 2),
            "ai": round(annualized_vol(ra) * 100, 2),
        },
        "level_correlation": {
            "pearson": round(lvl_pearson, 4) if lvl_pearson is not None else None,
            "spearman": round(lvl_spearman, 4) if lvl_spearman is not None else None,
            "warning": "价格水平相关易受共同趋势影响，可能虚假，仅供参考",
        },
        "return_correlation": {
            "pearson": round(ret_pearson, 4) if ret_pearson is not None else None,
            "spearman": round(ret_spearman, 4) if ret_spearman is not None else None,
            "beta_ai_on_dow": round(beta, 4) if beta is not None else None,
            "r_squared": round(r2, 4) if r2 is not None else None,
        },
        "direction_agreement": dir_stat,
        "rolling_return_corr": {
            "w21": rolling_corr(rd, ra, 21),   # ~1 个月
            "w63": rolling_corr(rd, ra, 63),   # ~1 季度
        },
    }
    return result


def _interpret(rp):
    if rp is None:
        return "无法判定"
    if rp <= -0.5:
        return "强反向"
    if rp <= -0.2:
        return "中度反向"
    if rp < 0:
        return "弱反向"
    if rp < 0.2:
        return "基本无关"
    if rp < 0.5:
        return "弱正相关"
    if rp < 0.8:
        return "中度正相关"
    return "强正相关"


def print_report(res):
    rc = res["return_correlation"]
    lv = res["level_correlation"]
    da = res["direction_agreement"]
    print("=" * 62)
    print(f"  道琼指数({res['dow_symbol']})  vs  AI标的指数({res['ai_symbol']})")
    print(f"  区间: {res['start_date']} → {res['end_date']}  "
          f"({res['trading_days']} 交易日, range={res['range']})")
    print("=" * 62)
    print(f"  累计涨幅       道指 {res['cumulative_return_pct']['dow']:+.2f}%   "
          f"AI {res['cumulative_return_pct']['ai']:+.2f}%")
    print(f"  年化波动率     道指 {res['annualized_vol_pct']['dow']:.2f}%    "
          f"AI {res['annualized_vol_pct']['ai']:.2f}%")
    print("-" * 62)
    print("  【核心：日收益率相关性 —— 衡量联动/反向的正确口径】")
    print(f"    Pearson  相关系数 : {rc['pearson']:+.4f}   ({_interpret(rc['pearson'])})")
    print(f"    Spearman 秩相关   : {rc['spearman']:+.4f}")
    print(f"    Beta(AI~道指)     : {rc['beta_ai_on_dow']:+.4f}")
    print(f"    R²(解释度)        : {rc['r_squared']:.4f}  "
          f"(道指涨跌可解释 AI {rc['r_squared']*100:.1f}% 的方差)")
    print("-" * 62)
    print("  【同向 / 反向天数】")
    print(f"    同向天数 : {da['same_days']}  ({da['same_pct']}%)")
    print(f"    反向天数 : {da['opposite_days']}  ({da['opposite_pct']}%)")
    print(f"    平盘天数 : {da['flat_days']}")
    print("-" * 62)
    print("  【滚动相关系数（反向性随时间的变化）】")
    for key, label in (("w21", "21日(≈1月)"), ("w63", "63日(≈1季)")):
        w = res["rolling_return_corr"][key]
        if w:
            print(f"    {label}: 均值 {w['mean']:+.3f}  区间[{w['min']:+.3f}, {w['max']:+.3f}]  "
                  f"当前 {w['last']:+.3f}  出现负相关的时间占比 {w['pct_negative']}%")
    print("-" * 62)
    print("  【价格水平相关（仅参考，含虚假相关风险）】")
    print(f"    Pearson : {lv['pearson']:+.4f}   Spearman : {lv['spearman']:+.4f}")
    print("=" * 62)
    verdict = _interpret(rc["pearson"])
    if rc["pearson"] is not None and rc["pearson"] > 0:
        print(f"  结论: 二者为『{verdict}』——反向性很低。日收益 Pearson={rc['pearson']:+.4f}，")
        print(f"       约 {da['opposite_pct']}% 的交易日方向相反，谈不上稳定对冲关系。")
    else:
        print(f"  结论: 二者呈『{verdict}』。")
    print("=" * 62)


def main():
    ap = argparse.ArgumentParser(description="道琼 vs AI 标的指数 相关性/反向性分析")
    ap.add_argument("--dow", default="^DJI", help="道琼指数代码 (默认 ^DJI)")
    ap.add_argument("--ai", default="AIQ",
                    help="AI 标的代理: AIQ/BOTZ/IRBO 等 ETF，或 BASKET(自建等权篮子)")
    ap.add_argument("--range", default="5y",
                    help="时间窗口: 1mo/3mo/6mo/1y/2y/5y/max")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    ai_is_basket = args.ai.upper() == "BASKET"
    res = analyze(args.dow, args.ai, args.range, ai_is_basket=ai_is_basket)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print_report(res)


if __name__ == "__main__":
    main()
