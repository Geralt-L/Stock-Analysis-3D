#!/usr/bin/env python3
"""抓取全部港股通标的的10年前复权日线（腾讯接口），并计算近一月是否创十年新低。"""
import json, time, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
import requests

BASE = "https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TODAY = date(2026, 8, 8)
START = date(2016, 8, 8)          # 十年窗口起点
RECENT_START = date(2026, 7, 8)   # “最近一个月”起点
PAGE = 800

session_local = threading.local()

def get_session():
    if not hasattr(session_local, "s"):
        s = requests.Session()
        s.headers.update(HEADERS)
        session_local.s = s
    return session_local.s

def fetch_page(code, end_str, retries=4):
    param = f"hk{code},day,{START.isoformat()},{end_str},{PAGE},qfq"
    for i in range(retries):
        try:
            r = get_session().get(BASE, params={"param": param}, timeout=30)
            d = r.json()
            node = d.get("data", {}).get(f"hk{code}")
            if isinstance(node, dict):
                rows = node.get("qfqday") or node.get("day") or []
                qt = node.get("qt", {}).get(f"hk{code}")
                name = qt[1] if qt and len(qt) > 1 else None
                return rows, name
            return [], None  # 无数据（已退市/代码无效等）
        except Exception:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))

def fetch_history(code):
    """往前翻页拉全 [START, TODAY] 的日线。返回 (rows, name)，rows 按日期升序。"""
    all_rows, name = [], None
    end = TODAY.isoformat()
    seen_first = None
    for _ in range(8):  # 最多8页，防御死循环
        rows, nm = fetch_page(code, end)
        if nm and not name:
            name = nm
        if not rows:
            break
        if seen_first is not None:
            rows = [r for r in rows if r[0] < seen_first]
            if not rows:
                break
        all_rows = rows + all_rows
        seen_first = rows[0][0]
        first_date = date.fromisoformat(rows[0][0])
        if first_date <= START or len(rows) < PAGE:
            break
        end = (first_date - timedelta(days=1)).isoformat()
    return all_rows, name

def analyze(code):
    rows, name = fetch_history(code)
    if not rows:
        return {"code": code, "status": "no_data"}
    listed_first = rows[0][0]           # 上市以来最早可得日期（用于判断是否满十年）
    rows = [r for r in rows if r[0] >= START.isoformat()]   # 裁剪到十年窗口
    if not rows:
        return {"code": code, "status": "no_data"}
    recent = [r for r in rows if r[0] >= RECENT_START.isoformat()]
    prior = [r for r in rows if r[0] < RECENT_START.isoformat()]
    out = {
        "code": code, "status": "ok", "name_qt": name,
        "first_date": listed_first, "last_date": rows[-1][0],
        "n_rows": len(rows), "last_close": float(rows[-1][2]),
    }
    if not recent or not prior:
        out["status"] = "insufficient"  # 近一月无交易(停牌) 或 刚上市
        return out
    # r = [date, open, close, high, low, volume, ...]
    rec_low = min(float(r[4]) for r in recent)
    rec_low_date = min((float(r[4]), r[0]) for r in recent)[1]
    prior_low = min(float(r[4]) for r in prior)
    rec_close_min = min(float(r[2]) for r in recent)
    prior_close_min = min(float(r[2]) for r in prior)
    out.update({
        "recent_low": rec_low, "recent_low_date": rec_low_date,
        "prior_low": prior_low,
        "new_low_by_low": rec_low <= prior_low,
        "new_low_by_close": rec_close_min <= prior_close_min,
        "full_history": listed_first <= "2016-09-30",  # 是否覆盖完整十年
    })
    return out

def main():
    stocks = json.load(open("ggt_list.json"))
    results, errors = [], []
    lock = threading.Lock()
    done = [0]
    def work(st):
        try:
            res = analyze(st["code"])
            res["name_en"] = st["name"]
            return res
        except Exception as e:
            return {"code": st["code"], "status": "error", "err": str(e)[:200], "name_en": st["name"]}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(work, st): st for st in stocks}
        for fut in as_completed(futs):
            res = fut.result()
            with lock:
                results.append(res)
                done[0] += 1
                if done[0] % 50 == 0:
                    print(f"progress {done[0]}/{len(stocks)}", flush=True)
    json.dump(results, open("results.json", "w"), ensure_ascii=False, indent=1)
    ok = [r for r in results if r["status"] == "ok"]
    hits = [r for r in ok if r.get("new_low_by_low")]
    print(f"done. ok={len(ok)} no_data={sum(r['status']=='no_data' for r in results)} "
          f"insufficient={sum(r['status']=='insufficient' for r in results)} "
          f"error={sum(r['status']=='error' for r in results)} newlow_hits={len(hits)}")

if __name__ == "__main__":
    main()
