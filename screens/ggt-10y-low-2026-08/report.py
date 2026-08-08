#!/usr/bin/env python3
"""从 results.json 生成创十年新低筛选报告。"""
import json

results = json.load(open("results.json"))
ok = [r for r in results if r["status"] == "ok"]

def fmt(r):
    rebound = (r["last_close"] / r["recent_low"] - 1) * 100 if r["recent_low"] > 0 else 0.0
    return {
        "code": r["code"], "name": r.get("name_qt") or r["name_en"],
        "recent_low": r["recent_low"], "recent_low_date": r["recent_low_date"],
        "prior_low": r["prior_low"], "last_close": r["last_close"],
        "last_date": r["last_date"], "first_date": r["first_date"],
        "rebound_pct": rebound,
        "strict": r["recent_low"] < r["prior_low"],
        "new_low_by_close": r["new_low_by_close"],
    }

hits_full = sorted((fmt(r) for r in ok if r["full_history"] and r["new_low_by_low"]),
                   key=lambda x: (not x["strict"], x["code"]))
young = sorted((fmt(r) for r in ok if not r["full_history"] and r["new_low_by_low"]),
               key=lambda x: x["first_date"])
young_old = [r for r in young if r["first_date"] <= "2021-08-08"]   # 上市≥5年
young_new = [r for r in young if r["first_date"] > "2021-08-08"]

n_strict = sum(r["strict"] for r in hits_full)
print(f"满十年创新低: {len(hits_full)} (严格 {n_strict} / 追平 {len(hits_full)-n_strict})")
print(f"不足十年创历史新低: {len(young)} (上市≥5年 {len(young_old)} / <5年 {len(young_new)})")

with open("report.md", "w") as f:
    f.write("# 港股通标的：最近一个月前复权价格创十年新低筛选\n\n")
    f.write("**筛选口径**\n\n")
    f.write("- 数据窗口：2016-08-08 ~ 2026-08-07，前复权日线（腾讯行情接口，前复权口径与东方财富一致）\n")
    f.write("- “最近一个月”：2026-07-08 ~ 2026-08-07\n")
    f.write("- 判定标准：近一月盘中最低价（前复权）低于（或追平）此前十年（2016-08-08 ~ 2026-07-07）盘中最低价\n")
    f.write("- 样本：港交所披露易“港股通（南向 SH & SZ）持股纪录”全部证券 %d 只，成功取数 %d 只\n" % (len(results), len(ok)))
    f.write("- 注：大额特别派息/分拆会使早年前复权价为负（如长和），此类个股按前复权口径天然难创新低，属口径固有特性\n\n")

    f.write("## 一、上市满十年、近一月创十年新低（%d 只，其中严格新低 %d 只）\n\n" % (len(hits_full), n_strict))
    f.write("| 代码 | 名称 | 近一月最低 | 创新低日期 | 此前十年最低 | 最新收盘 | 距最低反弹 | 类型 | 收盘口径也新低 |\n")
    f.write("|------|------|----------:|-----------|----------:|--------:|--------:|:----:|:---:|\n")
    for r in hits_full:
        f.write("| %s | %s | %.3f | %s | %.3f | %.3f | %+.1f%% | %s | %s |\n" % (
            r["code"], r["name"], r["recent_low"], r["recent_low_date"],
            r["prior_low"], r["last_close"], r["rebound_pct"],
            "严格新低" if r["strict"] else "追平前低",
            "✅" if r["new_low_by_close"] else "—"))

    f.write("\n## 二、上市不足十年、近一月创上市以来新低\n\n")
    f.write("### 2.1 上市已满5年（%d 只，历史新低含义较强）\n\n" % len(young_old))
    f.write("| 代码 | 名称 | 数据起点 | 近一月最低 | 创新低日期 | 此前最低 | 最新收盘 | 距最低反弹 |\n")
    f.write("|------|------|---------|----------:|-----------|--------:|--------:|--------:|\n")
    for r in young_old:
        f.write("| %s | %s | %s | %.3f | %s | %.3f | %.3f | %+.1f%% |\n" % (
            r["code"], r["name"], r["first_date"], r["recent_low"], r["recent_low_date"],
            r["prior_low"], r["last_close"], r["rebound_pct"]))
    f.write("\n### 2.2 上市不足5年（%d 只，多为次新股破发，参考意义有限）\n\n" % len(young_new))
    f.write("| 代码 | 名称 | 数据起点 | 近一月最低 | 创新低日期 | 最新收盘 |\n")
    f.write("|------|------|---------|----------:|-----------|--------:|\n")
    for r in young_new:
        f.write("| %s | %s | %s | %.3f | %s | %.3f |\n" % (
            r["code"], r["name"], r["first_date"], r["recent_low"],
            r["recent_low_date"], r["last_close"]))

    skipped = [r for r in results if r["status"] != "ok"]
    if skipped:
        f.write("\n## 备注：未纳入判定的标的（%d 只）\n\n" % len(skipped))
        f.write("近一月无成交（长期停牌）或无数据：")
        f.write(", ".join(f"{r['code']}" for r in sorted(skipped, key=lambda x: x['code'])) + "\n")

json.dump({"full_ten_year": hits_full, "young_ge5y": young_old, "young_lt5y": young_new},
          open("hits.json", "w"), ensure_ascii=False, indent=1)
print("written report.md / hits.json")
