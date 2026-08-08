#!/usr/bin/env python3
"""用雅虎财经 adjclose 对若干命中股票做交叉验证（口径：复权收盘价）。"""
import json, sys, time
import requests

def yahoo_check(code):
    sym = f"{int(code):04d}.HK"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    r = requests.get(url, params={"range": "10y", "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    d = r.json()["chart"]["result"][0]
    ts = d["timestamp"]
    adj = d["indicators"]["adjclose"][0]["adjclose"]
    import datetime
    pairs = [(datetime.datetime.utcfromtimestamp(t).date().isoformat(), a)
             for t, a in zip(ts, adj) if a is not None]
    pairs = [p for p in pairs if p[0] >= "2016-08-08"]
    recent = [p for p in pairs if p[0] >= "2026-07-08"]
    prior = [p for p in pairs if p[0] < "2026-07-08"]
    if not recent or not prior:
        return sym, None
    rmin = min(recent, key=lambda x: x[1])
    pmin = min(prior, key=lambda x: x[1])
    return sym, {"recent_min": round(rmin[1], 3), "recent_min_date": rmin[0],
                 "prior_min": round(pmin[1], 3), "prior_min_date": pmin[0],
                 "is_new_low_close": rmin[1] <= pmin[1],
                 "span": (pairs[0][0], pairs[-1][0])}

if __name__ == "__main__":
    codes = sys.argv[1:]
    for c in codes:
        try:
            sym, res = yahoo_check(c)
            print(sym, json.dumps(res, ensure_ascii=False))
        except Exception as e:
            print(c, "ERROR", str(e)[:150])
        time.sleep(1)
