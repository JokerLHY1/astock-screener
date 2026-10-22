#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
astock-screener engine v8.0　修复版

相对 v7.4 的修复:
  1. 数据字段对齐前端  - 顶层输出 tech_total / fund_total / roe / pe / pb / roe_score / composite
  2. 补全 --sectors     - 板块筛选(兼容旧版"之前跑通的版本.py"的逻辑)
  3. 交易日历          - 用 akshare 交易日历精确定位最近交易日(跳过法定节假日,不只周末)
  4. 性能优化          - 名称/行业/基本面走批量缓存, 每日只增量补K线
  5. 断点续跑          - 保留 done_codes / failed_stocks 逻辑
  6. 安全             - 输出不再含任何可执行字段注入

全部数据来源于本地网络接口(baostock/akshare), 纯免费, 不上传第三方服务。
"""
import os
import sys
import json
import time
import argparse
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshot.json")
TECH_FILE = os.path.join(DATA_DIR, "tech.json")
FAILED_FILE = os.path.join(DATA_DIR, "failed_stocks.json")
NAME_CACHE_FILE = os.path.join(DATA_DIR, "name_cache.json")
FUND_CACHE_FILE = os.path.join(DATA_DIR, "fund_cache.json")
INDUSTRY_MAP_FILE = os.path.join(BASE_DIR, "industry_map.json")

KLINE_DAYS = 90
MAX_RETRIES = 3
BATCH_PAUSE = 0.2
DEFAULT_STALE_DAYS = 7
DAILY_RETRY_BATCH = 5

TECH_SCORES = {"ma": 25, "macd": 25, "kdj": 20, "vol": 30}
FUND_SCORES = {"pe": 30, "pb": 30, "roe": 40}


def ensure(data_dir):
    os.makedirs(data_dir, exist_ok=True)


# ============= 交易日历 =============
def get_trade_calendar():
    """尝试用 akshare 拉取交易日历；失败则回退为跳过周末。返回 {date_str} 集合(最近一年)。"""
    try:
        import akshare as ak
        df = ak.tool_trade_date_hist_sina()
        dates = set()
        for val in df["trade_date"].astype(str).tolist():
            # 形如 '2024-01-02' 或 '20240102'
            s = val.replace("-", "").strip()
            if len(s) == 8:
                dates.add(f"{s[0:4]}-{s[4:6]}-{s[6:8]}")
        if dates:
            return dates
    except Exception:
        pass
    return None


def get_last_trade_date(_dt=None):
    """返回最近一个交易日的 YYYY-MM-DD。优先使用交易日历，否则跳过周末。"""
    d = _dt or datetime.now()
    cal = getattr(get_last_trade_date, "_cal", None)
    if cal is None:
        cal = get_trade_calendar()
        get_last_trade_date._cal = cal
    cur = d.replace(hour=0, minute=0, second=0, microsecond=0)
    if cal:
        # 从今天往回找第一个在日历里的日期(同时保证该日是周一~周五多数, 且非未来)
        for _ in range(30):
            ds = cur.strftime("%Y-%m-%d")
            if ds in cal and cur.weekday() < 5:
                return ds
            cur -= timedelta(days=1)
        # 日历异常回退
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur.strftime("%Y-%m-%d")


# ============= 代码规范化 =============
def normalize_code(code):
    code = str(code).strip().lower()
    if "." in code and len(code) == 9:
        return code
    if len(code) == 6:
        if code.startswith(("6", "9", "5")):      # 沪: 60x/68x/90/51x/58x
            return f"sh.{code}"
        elif code.startswith(("0", "3", "2")):    # 深: 00x/30x/20x
            return f"sz.{code}"
        elif code.startswith(("4", "8")):         # 北交所: 43x/83x/87x
            return f"bj.{code}"
    return code


def count_missing_days(last_date_str, today_str):
    if not last_date_str:
        return -1
    try:
        last = datetime.strptime(last_date_str, "%Y-%m-%d")
        today = datetime.strptime(today_str, "%Y-%m-%d")
        return (today - last).days
    except Exception:
        return -1


def estimate_trade_days(start_str, end_str):
    try:
        s = datetime.strptime(start_str, "%Y-%m-%d")
        e = datetime.strptime(end_str, "%Y-%m-%d")
    except Exception:
        return None
    if e < s:
        return 0
    cal = getattr(get_last_trade_date, "_cal", None)
    n = 0
    d = s
    while d <= e:
        if cal:
            if d.strftime("%Y-%m-%d") in cal:
                n += 1
        elif d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


# ============= baostock =============
def bs_login():
    import baostock as bs
    lg = bs.login()
    if lg.error_code != "0":
        print(f"baostock 登录失败: {lg.error_msg}")
        sys.exit(1)
    return bs


def bs_logout(bs):
    try:
        bs.logout()
    except Exception:
        pass


# ============= 缓存 IO =============
def _safe_load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_name_cache():
    return _safe_load(NAME_CACHE_FILE, {})


def save_name_cache(cache):
    ensure(DATA_DIR)
    with open(NAME_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def load_fund_cache():
    return _safe_load(FUND_CACHE_FILE, {})


def save_fund_cache(cache):
    ensure(DATA_DIR)
    with open(FUND_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def load_failed():
    return _safe_load(FAILED_FILE, {}) if os.path.exists(FAILED_FILE) else {}


def save_failed(failed):
    ensure(DATA_DIR)
    with open(FAILED_FILE, "w", encoding="utf-8") as f:
        json.dump(failed, f, ensure_ascii=False, indent=2)


def load_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return []
    return _safe_load(SNAPSHOT_FILE, [])


def save_snapshot(snapshot):
    ensure(DATA_DIR)
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False)


def load_tech():
    return _safe_load(TECH_FILE, {})


def save_tech(tech):
    ensure(DATA_DIR)
    with open(TECH_FILE, "w", encoding="utf-8") as f:
        json.dump(tech, f, ensure_ascii=False)


# ============= 行业映射 =============
_industry_map = {}


def load_industry_map():
    global _industry_map
    _industry_map = {}
    if not os.path.exists(INDUSTRY_MAP_FILE):
        print(f"⚠️ {INDUSTRY_MAP_FILE} 不存在, 行业将用 baostock 兜底")
        return
    try:
        with open(INDUSTRY_MAP_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        for k, v in raw.items():
            nk = normalize_code(k)
            _industry_map[nk] = v
            _industry_map[nk.split(".")[-1]] = v
        print(f"✅ 申万一级行业映射加载: {len(raw)} 条")
    except Exception as e:
        print(f"⚠️ 行业映射加载失败: {e}")


def get_industry(bs, code):
    if code in _industry_map:
        return _industry_map[code]
    pure = code.split(".")[-1] if "." in code else code
    if pure in _industry_map:
        return _industry_map[pure]
    try:
        rs = bs.query_stock_industry(code)
        if rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if row and len(row) > 1 and row[1]:
                return row[1]
    except Exception:
        pass
    return "未分类"


# ============= 名称 =============
def get_stock_name(bs, code, name_cache):
    if code in name_cache:
        cached = name_cache[code]
        if cached and not cached.startswith(("sh.", "sz.", "bj.")) and not cached.isdigit():
            return cached
    for attempt in range(MAX_RETRIES):
        try:
            rs = bs.query_stock_basic(code=code)
            if rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                name = row[1] if row and len(row) > 1 else None
                if name and not name.startswith(("sh.", "sz.", "bj.")) and not name.isdigit():
                    name_cache[code] = name
                    return name
        except Exception:
            pass
        if attempt < MAX_RETRIES - 1:
            time.sleep(0.3)
    fallback = code.split(".")[-1]
    name_cache[code] = fallback
    return fallback


# ============= 技术面 =============
def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow:
        return [0], [0]
    ema_f, ema_s = closes[0], closes[0]
    dif, dea = [], []
    f = float(fast); s = float(slow); sg = float(signal)
    for price in closes:
        ema_f = ema_f * (f - 1) / (f + 1) + price * 2 / (f + 1)
        ema_s = ema_s * (s - 1) / (s + 1) + price * 2 / (s + 1)
        dif.append(ema_f - ema_s)
    dea_val = dif[0]
    for d in dif:
        dea_val = dea_val * (sg - 1) / (sg + 1) + d * 2 / (sg + 1)
        dea.append(dea_val)
    return dif, dea


def calc_kdj(highs, lows, closes, n=9):
    if len(closes) < n:
        return [50], [50]
    k_list, d_list = [50], [50]
    for i in range(n - 1, len(closes)):
        ph = max(highs[i - n + 1:i + 1])
        pl = min(lows[i - n + 1:i + 1])
        rsv = (closes[i] - pl) / (ph - pl) * 100 if ph != pl else 50
        k = k_list[-1] * 2 / 3 + rsv * 1 / 3
        d = d_list[-1] * 2 / 3 + k * 1 / 3
        k_list.append(k)
        d_list.append(d)
    return k_list, d_list


def get_cached_kline(code):
    p = os.path.join(HISTORY_DIR, f"{code}.json")
    return _safe_load(p, [])


def save_cached_kline(code, data):
    ensure(HISTORY_DIR)
    p = os.path.join(HISTORY_DIR, f"{code}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def merge_kline(old, new):
    merged = {d["date"]: d for d in old if d.get("date")}
    for d in new or []:
        if d.get("date"):
            merged[d["date"]] = d
    return sorted(merged.values(), key=lambda x: x["date"])


def realign_kline(kline):
    if not kline:
        return kline
    kline = sorted([d for d in kline if d.get("date")], key=lambda x: x["date"])
    seen = {}
    for d in kline:
        seen[d["date"]] = d
    out = list(seen.values())
    prev_close = None
    for d in out:
        c = d.get("close")
        if c in (None, 0) and prev_close not in (None, 0):
            d["close"] = prev_close
            if d.get("open") in (None, 0):
                d["open"] = prev_close
            if d.get("high") in (None, 0):
                d["high"] = prev_close
            if d.get("low") in (None, 0):
                d["low"] = prev_close
        if d.get("close"):
            prev_close = d["close"]
    return out


def fetch_kline(bs, code):
    for attempt in range(MAX_RETRIES):
        try:
            end_date = get_last_trade_date()
            start_date = (datetime.now() - timedelta(days=KLINE_DAYS * 2)).strftime("%Y-%m-%d")
            rs = bs.query_history_k_data_plus(
                code, "date,open,high,low,close,volume,amount,turn,pctChg",
                start_date=start_date, end_date=end_date,
                frequency="d", adjustflag="2")
            data = []
            while rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                if row[0]:
                    data.append({
                        "date": row[0],
                        "open": float(row[1]) if row[1] else 0,
                        "high": float(row[2]) if row[2] else 0,
                        "low": float(row[3]) if row[3] else 0,
                        "close": float(row[4]) if row[4] else 0,
                        "volume": float(row[5]) if row[5] else 0,
                        "amount": float(row[6]) if row[6] else 0,
                        "turn": float(row[7]) if row[7] else 0,
                        "pctChg": float(row[8]) if row[8] else 0,
                    })
            if data:
                return data
        except Exception:
            pass
        if attempt < MAX_RETRIES - 1:
            time.sleep(0.4)
    return None


def calc_tech_analysis(bs, code):
    result = {
        "ma_bull": False, "macd_gold": False, "kdj_gold": False, "vol_breakout": False,
        "ma_score": 0, "macd_score": 0, "kdj_score": 0, "vol_score": 0,
        "tech_total": 0, "tech_detail": {}
    }
    cached = get_cached_kline(code)
    fresh = fetch_kline(bs, code)
    if not fresh and not cached:
        return result
    if fresh:
        data = merge_kline(cached, fresh) if cached else fresh
        data = data[-KLINE_DAYS:]
        save_cached_kline(code, data)
    else:
        data = cached
    if len(data) < 20:
        return result
    closes = [d["close"] for d in data]
    volumes = [d["volume"] for d in data]
    highs = [d["high"] for d in data]
    lows = [d["low"] for d in data]

    if len(closes) >= 20:
        ma5 = sum(closes[-5:]) / 5
        ma10 = sum(closes[-10:]) / 10
        ma20 = sum(closes[-20:]) / 20
        ma5_prev = sum(closes[-10:-5]) / 5 if len(closes) >= 10 else ma5
        ma_bull = (ma5 > ma10 > ma20) and (ma5 > ma5_prev)
        result["ma_bull"] = ma_bull
        result["ma_score"] = TECH_SCORES["ma"] if ma_bull else (10 if ma5 > ma10 else 0)
        result["tech_detail"]["ma5"] = round(ma5, 2)
        result["tech_detail"]["ma10"] = round(ma10, 2)
        result["tech_detail"]["ma20"] = round(ma20, 2)

    if len(closes) >= 26:
        dif, dea = calc_macd(closes)
        if len(dif) >= 2:
            dif_v, dea_v = dif[-1], dea[-1]
            dif_p, dea_p = dif[-2], dea[-2]
            macd_gold = (dif_v > dea_v) and (dif_p <= dea_p)
            result["macd_gold"] = macd_gold
            result["macd_score"] = TECH_SCORES["macd"] if macd_gold else (10 if dif_v > dea_v else 0)
            result["tech_detail"]["dif"] = round(dif_v, 4)
            result["tech_detail"]["dea"] = round(dea_v, 4)
            result["tech_detail"]["macd_hist"] = round((dif_v - dea_v) * 2, 4)

    if len(closes) >= 9:
        k_vals, d_vals = calc_kdj(highs, lows, closes)
        if len(k_vals) >= 2:
            k_v, d_v = k_vals[-1], d_vals[-1]
            k_p, d_p = k_vals[-2], d_vals[-2]
            kdj_gold = (k_v > d_v) and (k_p <= d_p)
            result["kdj_gold"] = kdj_gold
            result["kdj_score"] = TECH_SCORES["kdj"] if kdj_gold else (8 if k_v > d_v else 0)
            result["tech_detail"]["k"] = round(k_v, 2)
            result["tech_detail"]["d"] = round(d_v, 2)
            result["tech_detail"]["j"] = round(3 * k_v - 2 * d_v, 2)

    if len(volumes) >= 5:
        vol_ma5 = sum(volumes[-5:]) / 5
        vol_breakout = volumes[-1] > vol_ma5 * 1.5
        result["vol_breakout"] = vol_breakout
        result["vol_score"] = TECH_SCORES["vol"] if vol_breakout else (10 if volumes[-1] > vol_ma5 else 0)
        result["tech_detail"]["vol_ma5"] = round(vol_ma5, 0)
        result["tech_detail"]["vol_latest"] = round(volumes[-1], 0)

    result["tech_total"] = result["ma_score"] + result["macd_score"] + result["kdj_score"] + result["vol_score"]
    return result


# ============= 基本面(带缓存) =============
def _legacy_cache_entry(fund_cache, code):
    """读取旧版无日期 key 的缓存项(纯代码 key)。防止格式升级导致历史缓存失效。"""
    raw = fund_cache.get(code)
    if not isinstance(raw, dict):
        return None
    return {
        "pe": raw.get("pe"), "pb": raw.get("pb"), "roe": raw.get("roe"),
        "pe_score": raw.get("pe_score", 0), "pb_score": raw.get("pb_score", 0),
        "roe_score": raw.get("roe_score", 0), "fund_total": raw.get("fund_total", 0),
        "fund_detail": raw.get("fund_detail", {}),
    }


def calc_fund_analysis(bs, code, today, fund_cache):
    key = f"{code}:{today}"
    legacy_key = code  # 兼容旧版无日期 key
    cached = fund_cache.get(key) or _legacy_cache_entry(fund_cache, legacy_key)
    if cached:
        return cached
    result = {
        "pe": None, "pb": None, "roe": None,
        "pe_score": 0, "pb_score": 0, "roe_score": 0,
        "fund_total": 0, "fund_detail": {}
    }
    try:
        rs = bs.query_valuation(code=code, start_date=today, end_date=today)
        pe_val, pb_val = None, None
        if rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if row:
                for i, col in enumerate(rs.fields):
                    if col == "peTTM" and row[i]:
                        try:
                            pe_val = float(row[i])
                        except Exception:
                            pass
                    elif col == "pbMRQ" and row[i]:
                        try:
                            pb_val = float(row[i])
                        except Exception:
                            pass
        result["pe"] = pe_val
        result["pb"] = pb_val
        result["fund_detail"]["pe"] = pe_val
        result["fund_detail"]["pb"] = pb_val

        roe_val = None
        year = datetime.strptime(today, "%Y-%m-%d").year
        for q in [4, 3, 2, 1, -1]:  # -1 表示上年Q4
            try:
                yy = year if q != -1 else year - 1
                qq = 4 if q == -1 else q
                rs = bs.query_profit_data(code=code, year=yy, quarter=qq)
                if rs.error_code == "0":
                    while rs.next():
                        row = rs.get_row_data()
                        if row and len(row) > 3:
                            try:
                                roe_val = float(row[3])
                                break
                            except Exception:
                                pass
            except Exception:
                pass
            if roe_val is not None:
                break
        result["roe"] = roe_val
        result["fund_detail"]["roe"] = roe_val

        if pe_val is not None:
            if pe_val < 0:
                result["pe_score"] = 0
            elif pe_val < 15:
                result["pe_score"] = FUND_SCORES["pe"]
            elif pe_val < 30:
                result["pe_score"] = int(FUND_SCORES["pe"] * 0.6)
            elif pe_val < 50:
                result["pe_score"] = int(FUND_SCORES["pe"] * 0.3)
        if pb_val is not None:
            if pb_val < 0:
                result["pb_score"] = 0
            elif pb_val < 1:
                result["pb_score"] = FUND_SCORES["pb"]
            elif pb_val < 3:
                result["pb_score"] = int(FUND_SCORES["pb"] * 0.6)
            elif pb_val < 5:
                result["pb_score"] = int(FUND_SCORES["pb"] * 0.3)
        if roe_val is not None:
            if roe_val > 20:
                result["roe_score"] = FUND_SCORES["roe"]
            elif roe_val > 15:
                result["roe_score"] = int(FUND_SCORES["roe"] * 0.8)
            elif roe_val > 10:
                result["roe_score"] = int(FUND_SCORES["roe"] * 0.5)
            elif roe_val > 5:
                result["roe_score"] = int(FUND_SCORES["roe"] * 0.2)

        result["fund_total"] = result["pe_score"] + result["pb_score"] + result["roe_score"]
        fund_cache[key] = result
    except Exception as e:
        result["fund_detail"]["error"] = str(e)
    return result


# ============= 构建单只记录 =============
def build_stock_record(bs, code, name_cache, fund_cache, today):
    name = get_stock_name(bs, code, name_cache)
    if not name or name.startswith(("sh.", "sz.", "bj.")) or name.isdigit():
        return None
    tech = calc_tech_analysis(bs, code)
    fund = calc_fund_analysis(bs, code, today, fund_cache)

    price = 0
    kline_data = get_cached_kline(code)
    if kline_data and len(kline_data) > 0:
        price = kline_data[-1].get("close", 0)

    industry = get_industry(bs, code)

    composite = tech["tech_total"] + fund["fund_total"]
    return {
        "code": code,
        "name": name,
        "industry": industry,
        "price": price,
        "ma_bull": tech["ma_bull"],
        "macd_gold": tech["macd_gold"],
        "kdj_gold": tech["kdj_gold"],
        "vol_breakout": tech["vol_breakout"],
        "tech_total": tech["tech_total"],      # 对齐前端
        "tech_score": tech["tech_total"],      # 兼容旧前端
        "fund_total": fund["fund_total"],      # 对齐前端
        "fund_score": fund["fund_total"],      # 兼容旧前端
        "roe": fund["roe"],
        "pe": fund["pe"],
        "pb": fund["pb"],
        "roe_score": fund["roe_score"],
        "pe_score": fund["pe_score"],
        "pb_score": fund["pb_score"],
        "composite": composite,
        "last_date": today,
        "missing_days": 0,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "tech_detail": tech["tech_detail"],
        "fund_detail": fund["fund_detail"],
    }


# ============= 批量获取全市场股票列表 =============
def get_all_a_stocks(bs):
    """用 baostock query_all_stock 拉全市场证券列表(返回 code 列表)。"""
    today = get_last_trade_date()
    rs = bs.query_all_stock(day=today)
    if rs.error_code != "0":
        return []
    out = []
    while rs.next():
        row = rs.get_row_data()
        if row and len(row) > 0:
            code = row[0]
            # 只保留 A 股(排除指数/基金), 沪/深/北交易所前缀
            if code.split(".")[-1][0] in ("0", "3", "6", "2", "4", "8", "9"):
                out.append(code)
    return out


# ============= 全量建库 =============
def main_full_build(bs, sectors=None):
    ensure(DATA_DIR)
    load_industry_map()
    today = get_last_trade_date()
    print(f"📅 交易日基准: {today}")

    snapshot = load_snapshot()
    done_codes = {item["code"] for item in snapshot if "code" in item}
    print(f"📦 已有底库: {len(snapshot)} 只")

    name_cache = load_name_cache()
    fund_cache = load_fund_cache()
    failed = load_failed()
    if failed:
        print(f"🔄 检测到 {len(failed)} 只历史失败票")

    if sectors:
        # 从现有底库按行业筛出候选
        wanted = set(sectors)
        todo = [c for c in done_codes if any(item.get("industry") in wanted
                for item in snapshot if item.get("code") == c)]
        print(f"🎯 板块筛选: {sectors}, 已有 {len(todo)} 只匹配")
    else:
        todo = get_all_a_stocks(bs)
        print(f"📋 全市场 {len(todo)} 只证券(待处理 {len([c for c in todo if c not in done_codes])})")
        todo = [c for c in todo if c not in done_codes]
        failed_dict = failed
        # 把失败的拼进 todo
        todo = todo + list(failed_dict.keys()) if failed_dict else todo
        if not todo:
            print("✅ 全部已完成，无待处理")
            bs_logout(bs)
            return

    start_time = time.time()
    total = len(todo)
    failed_list = []
    for i, code in enumerate(todo):
        record = build_stock_record(bs, code, name_cache, fund_cache, today)
        if record and record.get("price", 0) > 0:
            snapshot = [x for x in snapshot if x.get("code") != code] + [record]
            done_codes.add(code)
            if (i + 1) % 100 == 0:
                print(f"  ✅ {i+1}/{total} 完成")
        else:
            reason = "name_failed" if record is None else "price_zero"
            failed_list.append({"code": code, "reason": reason})
        time.sleep(BATCH_PAUSE)
        if (i + 1) % 200 == 0:
            save_snapshot(snapshot)
            save_name_cache(name_cache)
            save_fund_cache(fund_cache)

    save_snapshot(snapshot)
    save_name_cache(name_cache)
    save_fund_cache(fund_cache)
    if failed_list:
        save_failed({x["code"]: x["reason"] for x in failed_list})
    elapsed = time.time() - start_time
    print(f"\n🎉 建库完成! 成功 {len(snapshot)}, 本次失败 {len(failed_list)}, 耗时 {elapsed:.0f}s")


# ============= 每日增量更新 =============
def daily_update(bs, stale_days=DEFAULT_STALE_DAYS):
    ensure(DATA_DIR)
    today = get_last_trade_date()
    print(f"📅 交易日基准: {today}")
    snapshot = load_snapshot()
    if not snapshot:
        print("❌ 底库为空，请先全量建库")
        return

    failed = load_failed()
    if failed:
        items = list(failed.items())[:DAILY_RETRY_BATCH]
        name_cache = load_name_cache()
        fund_cache = load_fund_cache()
        added = 0
        for code, reason in items:
            print(f"  补跑 {code}...", end="")
            try:
                record = build_stock_record(bs, code, name_cache, fund_cache, today)
                if record and record.get("price", 0) > 0:
                    snapshot = [x for x in snapshot if x.get("code") != code] + [record]
                    failed.pop(code, None)
                    added += 1
                    print(" ✅")
                else:
                    print(" ❌")
            except Exception as e:
                print(f" ❌ {e}")
            time.sleep(BATCH_PAUSE)
        if added:
            save_snapshot(snapshot)
            print(f"  ✅ 补回 {added} 只")
        save_failed(failed)

    name_cache = load_name_cache()
    fund_cache = load_fund_cache()
    updated = 0
    skipped = 0
    stale_list = []
    idx_by_code = {item["code"]: i for i, item in enumerate(snapshot)}

    for code, i in idx_by_code.items():
        item = snapshot[i]
        last_date = item.get("last_date", "")
        missing = count_missing_days(last_date, today)
        need = missing < 0 or missing >= stale_days
        if not need:
            skipped += 1
            continue
        if missing >= stale_days:
            stale_list.append(code)
        start_str = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KLINE_DAYS)).strftime("%Y-%m-%d")
        fresh = fetch_kline_incremental(bs, code, start_str, today)
        if fresh:
            merged = merge_kline(get_cached_kline(code), fresh)
            merged = realign_kline(merged)
            save_cached_kline(code, merged)
            tech = calc_tech_analysis(bs, code)
            item["ma_bull"] = tech["ma_bull"]
            item["macd_gold"] = tech["macd_gold"]
            item["kdj_gold"] = tech["kdj_gold"]
            item["vol_breakout"] = tech["vol_breakout"]
            item["tech_total"] = tech["tech_total"]
            item["tech_score"] = tech["tech_total"]
            item["tech_detail"] = tech["tech_detail"]
            item["price"] = merged[-1].get("close", item.get("price", 0))
            item["last_date"] = today
            item["missing_days"] = 0
            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            updated += 1
        else:
            item["missing_days"] = missing
            skipped += 1
        time.sleep(BATCH_PAUSE)
        if (updated + skipped) % 300 == 0:
            save_snapshot(snapshot)

    save_snapshot(snapshot)
    save_name_cache(name_cache)
    save_fund_cache(fund_cache)
    print(f"\n📊 每日更新: 更新 {updated}, 跳过 {skipped}, 超期 {len(stale_list)}")


# ============= 增量 K 线 =============
def fetch_kline_incremental(bs, code, start_date, end_date):
    try:
        rs = bs.query_history_k_data_plus(
            code, "date,open,high,low,close,volume,amount,turn,pctChg",
            start_date=start_date, end_date=end_date,
            frequency="d", adjustflag="2")
        data = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if row[0]:
                data.append({
                    "date": row[0],
                    "open": float(row[1]) if row[1] else 0,
                    "high": float(row[2]) if row[2] else 0,
                    "low": float(row[3]) if row[3] else 0,
                    "close": float(row[4]) if row[4] else 0,
                    "volume": float(row[5]) if row[5] else 0,
                    "amount": float(row[6]) if row[6] else 0,
                    "turn": float(row[7]) if row[7] else 0,
                    "pctChg": float(row[8]) if row[8] else 0,
                })
        return data if data else None
    except Exception:
        return None


# ============= 指定代码更新 =============
def update_by_codes(bs, raw_codes):
    ensure(DATA_DIR)
    today = get_last_trade_date()
    name_cache = load_name_cache()
    fund_cache = load_fund_cache()
    snapshot = load_snapshot()
    done_codes = {item["code"] for item in snapshot if "code" in item}

    window_start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KLINE_DAYS)).strftime("%Y-%m-%d")
    expected = estimate_trade_days(window_start, today)

    stats = {"success": [], "warn": [], "fail": []}
    for raw in raw_codes:
        code = normalize_code(raw)
        print(f"处理 {code}...", end="")
        fresh = fetch_kline_incremental(bs, code, window_start, today)
        if not fresh:
            print(" ❌ 增量K线拉取失败/为空")
            stats["fail"].append({"code": code, "reason": "kline_fetch_empty"})
            time.sleep(BATCH_PAUSE)
            continue
        merged = merge_kline(get_cached_kline(code), fresh)
        merged = realign_kline(merged)
        save_cached_kline(code, merged)

        warn_flag = False
        if merged and merged[-1].get("date") != today:
            print(f" ⚠️ 最新={merged[-1].get('date')}", end="")
            warn_flag = True
        if expected and len(merged) < expected * 0.7:
            print(f" ⚠️ K线偏少({len(merged)}/{expected})", end="")
            warn_flag = True

        record = build_stock_record(bs, code, name_cache, fund_cache, today)
        if record and record.get("price", 0) > 0:
            snapshot = [x for x in snapshot if x.get("code") != code] + [record]
            if warn_flag:
                stats["warn"].append(code)
                print(" ✅(告警)")
            else:
                stats["success"].append(code)
                print(" ✅")
        else:
            stats["fail"].append({"code": code, "reason": "build_failed"})
            print(" ❌ 构建失败")
        time.sleep(BATCH_PAUSE)

    save_snapshot(snapshot)
    save_name_cache(name_cache)
    save_fund_cache(fund_cache)
    print(f"\n{'='*40}\n✅ 成功 {len(stats['success'])} | ⚠️ 告警 {len(stats['warn'])} | ❌ 失败 {len(stats['fail'])}")


# ============= 主入口 =============
def run(mode="daily", sectors=None, codes=None, stale_days=DEFAULT_STALE_DAYS):
    ensure(DATA_DIR)
    bs = bs_login()
    try:
        if codes:
            update_by_codes(bs, codes)
        elif mode == "full" and not sectors:
            main_full_build(bs)
        elif sectors:
            main_full_build(bs, sectors=sectors)
        elif mode == "force-refresh-stale":
            force_refresh_stale(bs, stale_days)
        else:
            daily_update(bs, stale_days)
    finally:
        bs_logout(bs)


def force_refresh_stale(bs, stale_days=DEFAULT_STALE_DAYS):
    ensure(DATA_DIR)
    today = get_last_trade_date()
    snapshot = load_snapshot()
    ok = 0
    for i, item in enumerate(snapshot):
        code = item.get("code")
        if not code:
            continue
        if count_missing_days(item.get("last_date", ""), today) >= stale_days:
            start_str = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KLINE_DAYS)).strftime("%Y-%m-%d")
            fresh = fetch_kline_incremental(bs, code, start_str, today)
            if fresh:
                merged = realign_kline(merge_kline(get_cached_kline(code), fresh))
                save_cached_kline(code, merged)
                rec = build_stock_record(bs, code, load_name_cache(), load_fund_cache(), today)
                if rec:
                    snapshot[i] = rec
                    ok += 1
            time.sleep(BATCH_PAUSE)
    save_snapshot(snapshot)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="astock-screener engine v8.0 修复版")
    parser.add_argument("--daily", action="store_true", help="每日增量更新")
    parser.add_argument("--force-refresh-stale", action="store_true", help="强制刷新超期")
    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS)
    parser.add_argument("--codes", nargs="+", help="指定代码更新")
    parser.add_argument("--sectors", type=str, default="", help="板块筛选(逗号分隔)")
    parser.add_argument("--mode", type=str, default="daily", choices=["daily", "full", "force-refresh-stale"])
    args = parser.parse_args()

    codes = args.codes
    sectors = [s.strip() for s in args.sectors.split(",") if s.strip()] if args.sectors else None
    mode = "full" if args.mode == "full" else args.mode
    if codes:
        run(mode=mode, codes=codes)
    elif sectors:
        run(mode="full", sectors=sectors)
    elif args.daily:
        run(mode="daily", stale_days=args.stale_days)
    elif args.force_refresh_stale:
        run(mode="force-refresh-stale", stale_days=args.stale_days)
    else:
        run(mode=mode, stale_days=args.stale_days)
