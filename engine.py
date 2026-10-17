#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""

astock-screener engine v7.4

变更:

  - 列表来源: baostock query_all_stock()

  - 行业口径: 申万一级映射 industry_map.json

  - today 回退到最近交易日（跳过周末）

  - 自动补跑 failed_stocks.json（全量建库 + 每日增量双通道）

  - daily_update() 每天顺带补跑5只 failed（兜底消化）

  - update_by_codes(): 增量补K线 + 拉取失败/数据不完整明确反馈（不删缓存全量重拉）

"""



import os

import sys

import json

import time

import argparse

from datetime import datetime, timedelta



try:

    import baostock as bs

except ImportError:

    print("请先安装 baostock: pip install baostock")

    sys.exit(1)



# ========= 配置 =========

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")

HISTORY_DIR = os.path.join(DATA_DIR, "history")

SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshot.json")

TECH_FILE = os.path.join(DATA_DIR, "tech.json")

FAILED_FILE = os.path.join(DATA_DIR, "failed_stocks.json")

NAME_CACHE_FILE = os.path.join(DATA_DIR, "name_cache.json")

FUND_CACHE_FILE = os.path.join(DATA_DIR, "fund_cache.json")

QUERY_RESULT_FILE = os.path.join(DATA_DIR, "query_result.json")

INDUSTRY_MAP_FILE = os.path.join(BASE_DIR, "industry_map.json")



KLINE_DAYS = 90

MAX_RETRIES = 3

BATCH_PAUSE = 0.4

DEFAULT_STALE_DAYS = 7

DAILY_RETRY_BATCH = 5



TECH_SCORES = {"ma": 25, "macd": 25, "kdj": 20, "vol": 30}

FUND_SCORES = {"pe": 30, "pb": 30, "roe": 40}





# ========= 工具函数 =========

def get_last_trade_date(_dt=None):

    d = _dt or datetime.now()

    while d.weekday() >= 5:

        d -= timedelta(days=1)

    return d.strftime("%Y-%m-%d")





def normalize_code(code):

    code = str(code).strip()

    if '.' in code and len(code) == 9:

        return code

    if len(code) == 6:

        if code.startswith(('6', '9')):

            return f"sh.{code}"

        elif code.startswith(('0', '3', '2')):

            return f"sz.{code}"

    return code





def count_missing_days(last_date_str, today_str):

    if not last_date_str:

        return -1

    try:

        last = datetime.strptime(last_date_str, "%Y-%m-%d")

        today = datetime.strptime(today_str, "%Y-%m-%d")

        return (today - last).days

    except:

        return -1





def estimate_trade_days(start_str, end_str):

    """粗估 start~end 之间的交易日数量（跳过周末）"""

    try:

        s = datetime.strptime(start_str, "%Y-%m-%d")

        e = datetime.strptime(end_str, "%Y-%m-%d")

    except Exception:

        return None

    if e < s:

        return 0

    n = 0

    d = s

    while d <= e:

        if d.weekday() < 5:

            n += 1

        d += timedelta(days=1)

    return n





# ========= baostock =========

def bs_login():

    lg = bs.login()

    if lg.error_code != '0':

        print(f"baostock 登录失败: {lg.error_msg}")

        sys.exit(1)





def bs_logout():

    try:

        bs.logout()

    except Exception:

        pass





# ========= 缓存 =========

def load_name_cache():

    if os.path.exists(NAME_CACHE_FILE):

        try:

            with open(NAME_CACHE_FILE, "r", encoding="utf-8") as f:

                return json.load(f)

        except:

            pass

    return {}





def save_name_cache(cache):

    os.makedirs(DATA_DIR, exist_ok=True)

    with open(NAME_CACHE_FILE, "w", encoding="utf-8") as f:

        json.dump(cache, f, ensure_ascii=False)





def load_fund_cache():

    if os.path.exists(FUND_CACHE_FILE):

        try:

            with open(FUND_CACHE_FILE, "r", encoding="utf-8") as f:

                return json.load(f)

        except:

            pass

    return {}





def save_fund_cache(cache):

    os.makedirs(DATA_DIR, exist_ok=True)

    with open(FUND_CACHE_FILE, "w", encoding="utf-8") as f:

        json.dump(cache, f, ensure_ascii=False)





# ========= failed_stocks 读写 =========

def load_failed():

    if not os.path.exists(FAILED_FILE):

        return {}

    try:

        with open(FAILED_FILE, "r", encoding="utf-8") as f:

            raw = json.load(f)

    except:

        return {}

    if isinstance(raw, list):

        out = {}

        for item in raw:

            if isinstance(item, dict) and "code" in item:

                out[item["code"]] = item.get("reason", "unknown")

            elif isinstance(item, str):

                out[item] = "unknown"

        return out

    elif isinstance(raw, dict):

        return raw

    return {}





def save_failed(failed_dict):

    remaining = []

    for c, r in failed_dict.items():

        if isinstance(r, str):

            remaining.append({"code": c, "reason": r})

        elif isinstance(r, dict):

            d = {"code": c}

            d.update(r)

            remaining.append(d)

        else:

            remaining.append({"code": c, "reason": "unknown"})

    with open(FAILED_FILE, "w", encoding="utf-8") as f:

        json.dump(remaining, f, ensure_ascii=False, indent=2)





# ========= 失败票补跑核心 =========

def retry_failed_batch(failed_dict, batch=None, name_cache=None, fund_cache=None, today=None):

    if not failed_dict:

        return [], {}



    if name_cache is None:

        name_cache = load_name_cache()

    if fund_cache is None:

        fund_cache = load_fund_cache()

    if today is None:

        today = get_last_trade_date()



    items = list(failed_dict.items())

    if batch is not None:

        items = items[:batch]



    success_records = []

    still_failed = dict(failed_dict)



    for code, reason in items:

        print(f"  补跑 {code} ({reason})...", end="")

        try:

            record = build_stock_record(code, name_cache, fund_cache, today)

            if record and record.get("price", 0) > 0:

                success_records.append(record)

                del still_failed[code]

                print(f" ✅ (价格 {record['price']})")

            else:

                print(f" ❌ 仍无数据")

        except Exception as e:

            print(f" ❌ 异常: {e}")

        time.sleep(BATCH_PAUSE)



    return success_records, still_failed





# ========= 行业映射 =========

_industry_map = {}





def load_industry_map():

    global _industry_map

    _industry_map = {}

    if not os.path.exists(INDUSTRY_MAP_FILE):

        print(f"⚠️ {INDUSTRY_MAP_FILE} 不存在，建库时行业将用 baostock 兜底")

        return

    try:

        with open(INDUSTRY_MAP_FILE, "r", encoding="utf-8") as f:

            raw = json.load(f)

        for k, v in raw.items():

            nk = normalize_code(k)

            _industry_map[nk] = v

            if '.' in nk:

                _industry_map[nk.split('.')[-1]] = v

        print(f"✅ 申万一级行业映射加载: {len(raw)} 条")

    except Exception as e:

        print(f"⚠️ 行业映射加载失败: {e}")





def get_industry(code):

    if code in _industry_map:

        return _industry_map[code]

    pure = code.split('.')[-1] if '.' in code else code

    if pure in _industry_map:

        return _industry_map[pure]

    try:

        rs = bs.query_stock_industry(code)

        if rs.error_code == '0' and rs.next():

            row = rs.get_row_data()

            if row and len(row) > 1 and row[1]:

                return row[1]

    except:

        pass

    return "未分类"





# ========= K线 =========

def get_cached_kline(code):

    filepath = os.path.join(HISTORY_DIR, f"{code}.json")

    if os.path.exists(filepath):

        try:

            with open(filepath, "r", encoding="utf-8") as f:

                return json.load(f)

        except:

            pass

    return []





def save_cached_kline(code, data):

    os.makedirs(HISTORY_DIR, exist_ok=True)

    filepath = os.path.join(HISTORY_DIR, f"{code}.json")

    with open(filepath, "w", encoding="utf-8") as f:

        json.dump(data, f, ensure_ascii=False)





def is_kline_cache_fresh(kline, max_days=5):

    if not kline or len(kline) == 0:

        return False

    try:

        last_date = datetime.strptime(kline[-1].get("date", ""), "%Y-%m-%d")

        return (datetime.now() - last_date).days <= max_days

    except:

        return False





def merge_kline(old, new):

    merged = {d['date']: d for d in old if d.get("date")}

    for d in new:

        if d.get("date"):

            merged[d['date']] = d

    return sorted(merged.values(), key=lambda x: x['date'])





def realign_kline(kline):

    """合并后排序去重，close为0时前向填充"""

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





def fetch_kline(code):

    try:

        end_date = get_last_trade_date()

        start_date = (datetime.now() - timedelta(days=KLINE_DAYS * 2)).strftime("%Y-%m-%d")

        rs = bs.query_history_k_data_plus(

            code,

            "date,open,high,low,close,volume,amount,turn,pctChg",

            start_date=start_date, end_date=end_date,

            frequency="d", adjustflag="2"

        )

        data = []

        while rs.error_code == '0' and rs.next():

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

    except:

        return None





def fetch_kline_incremental(code, start_date, end_date):

    try:

        rs = bs.query_history_k_data_plus(

            code,

            "date,open,high,low,close,volume,amount,turn,pctChg",

            start_date=start_date, end_date=end_date,

            frequency="d", adjustflag="2"

        )

        data = []

        while rs.error_code == '0' and rs.next():

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

    except:

        return None





# ========= 名称 =========

def get_stock_name(code, name_cache):

    if code in name_cache:

        cached = name_cache[code]

        if cached and not cached.startswith(("sh.", "sz.")) and not cached.isdigit():

            return cached

    for attempt in range(MAX_RETRIES):

        try:

            rs = bs.query_stock_basic(code=code)

            if rs.error_code == '0' and rs.next():

                row = rs.get_row_data()

                name = row[1] if row and len(row) > 1 else None

                if name and not name.startswith(("sh.", "sz.")) and not name.isdigit():

                    name_cache[code] = name

                    return name

        except:

            pass

        if attempt < MAX_RETRIES - 1:

            time.sleep(0.5)

    fallback = code.split('.')[-1]

    name_cache[code] = fallback

    return fallback





# ========= 技术面 =========

def calc_macd(closes, fast=12, slow=26, signal=9):

    if len(closes) < slow:

        return [0], [0]

    ema_f, ema_s = closes[0], closes[0]

    dif = []

    for price in closes:

        ema_f = ema_f * (fast - 1) / (fast + 1) + price * 2 / (fast + 1)

        ema_s = ema_s * (slow - 1) / (slow + 1) + price * 2 / (slow + 1)

        dif.append(ema_f - ema_s)

    dea, dea_val = [], dif[0]

    for d in dif:

        dea_val = dea_val * (signal - 1) / (signal + 1) + d * 2 / (signal + 1)

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





def calc_tech_analysis(code):

    result = {

        "ma_bull": False, "macd_gold": False, "kdj_gold": False, "vol_breakout": False,

        "ma_score": 0, "macd_score": 0, "kdj_score": 0, "vol_score": 0,

        "tech_total": 0, "tech_detail": {}

    }

    cached = get_cached_kline(code)

    fresh = fetch_kline(code)

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



    closes = [d['close'] for d in data]

    volumes = [d['volume'] for d in data]

    highs = [d['high'] for d in data]

    lows = [d['low'] for d in data]



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





# ========= 基本面 =========

def calc_fund_analysis(code, today):

    result = {

        "pe": None, "pb": None, "roe": None,

        "pe_score": 0, "pb_score": 0, "roe_score": 0,

        "fund_total": 0, "fund_detail": {}

    }

    try:

        rs = bs.query_stock_basic(code=code)

        if rs.error_code == '0' and rs.next():

            row = rs.get_row_data()

            if row and len(row) > 3:

                result["fund_detail"]["ipoDate"] = row[2] if len(row) > 2 else ""

                result["fund_detail"]["outDate"] = row[3] if len(row) > 3 else ""



        rs = bs.query_profit_data(code=code, year=datetime.strptime(today, "%Y-%m-%d").year, quarter=4)

        roe_val = None

        if rs.error_code == '0':

            while rs.next():

                row = rs.get_row_data()

                if row and len(row) > 3:

                    try:

                        roe_val = float(row[3])

                        break

                    except:

                        pass

        if roe_val is None:

            for q in [3, 2, 1]:

                rs = bs.query_profit_data(code=code, year=datetime.strptime(today, "%Y-%m-%d").year, quarter=q)

                if rs.error_code == '0':

                    while rs.next():

                        row = rs.get_row_data()

                        if row and len(row) > 3:

                            try:

                                roe_val = float(row[3])

                                break

                            except:

                                pass

                if roe_val is not None:

                    break

        if roe_val is None:

            rs = bs.query_profit_data(code=code, year=datetime.strptime(today, "%Y-%m-%d").year - 1, quarter=4)

            if rs.error_code == '0':

                while rs.next():

                    row = rs.get_row_data()

                    if row and len(row) > 3:

                        try:

                            roe_val = float(row[3])

                            break

                        except:

                            pass



        rs = bs.query_valuation(code=code, start_date=today, end_date=today)

        pe_val, pb_val = None, None

        if rs.error_code == '0' and rs.next():

            row = rs.get_row_data()

            if row:

                for i, col in enumerate(rs.fields):

                    if col == 'peTTM' and row[i]:

                        try:

                            pe_val = float(row[i])

                        except:

                            pass

                    elif col == 'pbMRQ' and row[i]:

                        try:

                            pb_val = float(row[i])

                        except:

                            pass



        result["roe"] = roe_val

        result["pe"] = pe_val

        result["pb"] = pb_val

        result["fund_detail"]["pe"] = pe_val

        result["fund_detail"]["pb"] = pb_val

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

            else:

                result["pe_score"] = 0



        if pb_val is not None:

            if pb_val < 0:

                result["pb_score"] = 0

            elif pb_val < 1:

                result["pb_score"] = FUND_SCORES["pb"]

            elif pb_val < 3:

                result["pb_score"] = int(FUND_SCORES["pb"] * 0.6)

            elif pb_val < 5:

                result["pb_score"] = int(FUND_SCORES["pb"] * 0.3)

            else:

                result["pb_score"] = 0



        if roe_val is not None:

            if roe_val > 20:

                result["roe_score"] = FUND_SCORES["roe"]

            elif roe_val > 15:

                result["roe_score"] = int(FUND_SCORES["roe"] * 0.8)

            elif roe_val > 10:

                result["roe_score"] = int(FUND_SCORES["roe"] * 0.5)

            elif roe_val > 5:

                result["roe_score"] = int(FUND_SCORES["roe"] * 0.2)

            else:

                result["roe_score"] = 0



        result["fund_total"] = result["pe_score"] + result["pb_score"] + result["roe_score"]

    except Exception as e:

        result["fund_detail"]["error"] = str(e)



    return result





# ========= 构建单只股票记录 =========

def build_stock_record(code, name_cache, fund_cache, today):

    name = get_stock_name(code, name_cache)

    if not name or name.startswith("sh.") or name.startswith("sz.") or name.isdigit():

        return None



    tech = calc_tech_analysis(code)

    fund = calc_fund_analysis(code, today)



    price = 0

    kline_data = get_cached_kline(code)

    if kline_data and len(kline_data) > 0:

        price = kline_data[-1].get("close", 0)



    if price == 0:

        try:

            rs = bs.query_valuation(code=code, start_date=today, end_date=today)

            if rs.error_code == '0' and rs.next():

                row = rs.get_row_data()

                if row and len(row) > 0:

                    for i, col in enumerate(rs.fields):

                        if col == 'closePrice' and row[i]:

                            try:

                                price = float(row[i])

                            except:

                                pass

        except:

            pass



    industry = get_industry(code)



    record = {

        "code": code,

        "name": name,

        "industry": industry,

        "price": price,

        "ma_bull": tech["ma_bull"],

        "macd_gold": tech["macd_gold"],

        "kdj_gold": tech["kdj_gold"],

        "vol_breakout": tech["vol_breakout"],

        "tech_score": tech["tech_total"],

        "fund_score": fund["fund_total"],

        "composite": tech["tech_total"] + fund["fund_total"],

        "last_date": today,

        "missing_days": 0,

        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        "tech_detail": tech["tech_detail"],

        "fund_detail": fund["fund_detail"],

    }

    return record





# ========= 全量建库 =========

def main_full_build():

    bs_login()

    load_industry_map()



    today = get_last_trade_date()

    print(f"📅 交易日基准: {today}")



    failed = load_failed()

    if failed:

        print(f"🔄 检测到 {len(failed)} 只历史失败票，开始补跑...")

        success_records, still_failed = retry_failed_batch(

            failed, batch=None, today=today

        )

        if success_records:

            results = []

            done_codes = set()

            if os.path.exists(SNAPSHOT_FILE):

                try:

                    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

                        results = json.load(f)

                    for item in results:

                        done_codes.add(item.get("code"))

                except:

                    pass

            for rec in success_records:

                if rec["code"] not in done_codes:

                    results.append(rec)

                    done_codes.add(rec["code"])

            with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

                json.dump(results, f, ensure_ascii=False)

            print(f"✅ 补跑成功 {len(success_records)} 只，已写入 snapshot")

        if still_failed:

            save_failed(still_failed)

            print(f"⚠️ 仍有 {len(still_failed)} 只补跑失败，保留在 failed_stocks.json")

        else:

            if os.path.exists(FAILED_FILE):

                os.remove(FAILED_FILE)

                print(f"✅ failed_stocks.json 已清空")

        print()



    name_cache = load_name_cache()

    fund_cache = load_fund_cache()

    print(f"📛 名称缓存: {len(name_cache)} 条")

    print(f"💰 基本面缓存: {len(fund_cache)} 条")



    rs = bs.query_all_stock(day=today)

    if rs.error_code != '0':

        print(f"❌ 获取股票列表失败: {rs.error_msg}")

        bs_logout()

        return



    stock_list = []

    while rs.next():

        row = rs.get_row_data()

        if row and len(row) > 0:

            stock_list.append(row[0])



    if not stock_list:

        print("⚠️ 股票列表为空（可能非交易日），优雅退出，保留已有数据")

        save_name_cache(name_cache)

        save_fund_cache(fund_cache)

        bs_logout()

        return



    print(f"📋 共 {len(stock_list)} 只证券")



    results = []

    done_codes = set()

    if os.path.exists(SNAPSHOT_FILE):

        try:

            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

                results = json.load(f)

            for item in results:

                done_codes.add(item.get("code"))

            print(f"📦 已有底库: {len(results)} 只，跳过已完成的")

        except:

            pass



    todo = [c for c in stock_list if c not in done_codes]

    print(f"🆕 待处理: {len(todo)} 只")



    failed_list = []

    start_time = time.time()

    total = len(todo)



    for i, code in enumerate(todo):

        record = build_stock_record(code, name_cache, fund_cache, today)

        if record and record.get("price", 0) > 0:

            results.append(record)

            done_codes.add(code)

            if (i + 1) % 200 == 0:

                print(f"  ✅ {i+1}/{total} 完成")

        else:

            reason = "no_data"

            if record is None:

                reason = "name_failed"

            elif record.get("price", 0) <= 0:

                reason = "price_zero"

            failed_list.append({"code": code, "reason": reason})



        time.sleep(BATCH_PAUSE)



        if (i + 1) % 500 == 0:

            with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

                json.dump(results, f, ensure_ascii=False)

            save_name_cache(name_cache)

            save_fund_cache(fund_cache)



    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

        json.dump(results, f, ensure_ascii=False)

    save_name_cache(name_cache)

    save_fund_cache(fund_cache)



    elapsed = time.time() - start_time

    print(f"\n🎉 全量建库完成!")

    print(f"   ✅ 成功: {len(results)} 只")

    print(f"   ❌ 失败: {len(failed_list)} 只")

    print(f"   ⏱️ 耗时: {elapsed:.0f}s ({elapsed/60:.1f}min)")



    if failed_list:

        with open(FAILED_FILE, "w", encoding="utf-8") as f:

            json.dump(failed_list, f, ensure_ascii=False, indent=2)

        print(f"   📝 失败列表已写入 {FAILED_FILE}")



    bs_logout()





# ========= 每日增量更新 =========

def daily_update(stale_days=DEFAULT_STALE_DAYS):

    bs_login()

    today = get_last_trade_date()

    print(f"📅 交易日基准: {today}")



    failed = load_failed()

    if failed:

        print(f"🔄 顺带补跑 failed（最多{DAILY_RETRY_BATCH}只）...")

        items = list(failed.items())[:DAILY_RETRY_BATCH]

        snapshot = []

        if os.path.exists(SNAPSHOT_FILE):

            try:

                with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

                    snapshot = json.load(f)

            except:

                pass

        done_codes = {item["code"] for item in snapshot if "code" in item}

        added = 0

        for code, reason in items:

            print(f"  补跑 {code}...", end="")

            try:

                name_cache = load_name_cache()

                fund_cache = load_fund_cache()

                record = build_stock_record(code, name_cache, fund_cache, today)

                if record and record.get("price", 0) > 0:

                    snapshot.append(record)

                    del failed[code]

                    added += 1

                    print(f" ✅")

                else:

                    print(f" ❌")

            except Exception as e:

                print(f" ❌ {e}")

            time.sleep(BATCH_PAUSE)

        if added > 0:

            with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

                json.dump(snapshot, f, ensure_ascii=False)

            print(f"  ✅ 补回 {added} 只进底库")

        save_failed(failed)

        print()



    if not os.path.exists(SNAPSHOT_FILE):

        print("❌ 底库不存在，请先全量建库")

        bs_logout()

        return



    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

        snapshot = json.load(f)



    print(f"📦 底库共 {len(snapshot)} 只")



    updated = 0

    skipped = 0

    stale_list = []



    for item in snapshot:

        code = item.get("code")

        if not code:

            continue



        last_date = item.get("last_date", "")

        missing = count_missing_days(last_date, today)



        need_update = False

        if missing < 0:

            need_update = True

        elif missing >= stale_days:

            need_update = True

            stale_list.append(code)



        if not need_update:

            skipped += 1

            continue



        start_str = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KLINE_DAYS)).strftime("%Y-%m-%d")

        fresh = fetch_kline_incremental(code, start_str, today)



        if fresh and len(fresh) > 0:

            cached = get_cached_kline(code)

            merged = merge_kline(cached, fresh) if cached else fresh

            merged = realign_kline(merged)

            save_cached_kline(code, merged)



            tech = calc_tech_analysis(code)

            item["ma_bull"] = tech["ma_bull"]

            item["macd_gold"] = tech["macd_gold"]

            item["kdj_gold"] = tech["kdj_gold"]

            item["vol_breakout"] = tech["vol_breakout"]

            item["tech_score"] = tech["tech_total"]

            item["tech_detail"] = tech["tech_detail"]



            if merged and len(merged) > 0:

                item["price"] = merged[-1].get("close", item.get("price", 0))



            item["last_date"] = today

            item["missing_days"] = 0

            item["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            updated += 1

        else:

            item["missing_days"] = missing

            skipped += 1



        time.sleep(BATCH_PAUSE)



    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

        json.dump(snapshot, f, ensure_ascii=False)



    print(f"\n📊 每日更新完成:")

    print(f"   ✅ 更新: {updated} 只")

    print(f"   ⏭️ 跳过: {skipped} 只")

    if stale_list:

        print(f"   ⚠️ 超期未更新: {len(stale_list)} 只")



    bs_logout()





# ========= 强制刷新超期 =========

def force_refresh_stale(stale_days=DEFAULT_STALE_DAYS):

    bs_login()

    today = get_last_trade_date()

    print(f"📅 交易日基准: {today}")



    if not os.path.exists(SNAPSHOT_FILE):

        print("❌ 底库不存在")

        bs_logout()

        return



    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

        snapshot = json.load(f)



    stale_items = [item for item in snapshot if count_missing_days(item.get("last_date", ""), today) >= stale_days]

    print(f"🔍 超期 {stale_days}+ 天: {len(stale_items)} 只")



    if not stale_items:

        print("✅ 没有需要强制刷新的")

        bs_logout()

        return



    name_cache = load_name_cache()

    fund_cache = load_fund_cache()

    refreshed = 0

    still_failed = []



    for item in stale_items:

        code = item.get("code")

        if not code:

            continue

        print(f"  刷新 {code}...", end="")

        record = build_stock_record(code, name_cache, fund_cache, today)

        if record and record.get("price", 0) > 0:

            for idx, s in enumerate(snapshot):

                if s.get("code") == code:

                    snapshot[idx] = record

                    break

            refreshed += 1

            print(f" ✅")

        else:

            still_failed.append({"code": code, "reason": "refresh_failed"})

            print(f" ❌")



        time.sleep(BATCH_PAUSE)



    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

        json.dump(snapshot, f, ensure_ascii=False)

    save_name_cache(name_cache)

    save_fund_cache(fund_cache)



    if still_failed:

        existing = load_failed()

        for item in still_failed:

            existing[item["code"]] = item["reason"]

        save_failed(existing)

        print(f"⚠️ {len(still_failed)} 只刷新失败，已写入 failed_stocks.json")



    print(f"\n🎉 强制刷新完成: {refreshed}/{len(stale_items)} 成功")

    bs_logout()





# ========= 指定代码更新（增量 + 明确反馈） =========

def update_by_codes(codes, force_refresh=False):

    bs_login()

    today = get_last_trade_date()

    name_cache = load_name_cache()

    fund_cache = load_fund_cache()



    if os.path.exists(SNAPSHOT_FILE):

        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

            snapshot = json.load(f)

    else:

        snapshot = []



    done_codes = {item["code"] for item in snapshot if "code" in item}



    stats = {"success": [], "warn": [], "fail": []}



    window_start = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=KLINE_DAYS)).strftime("%Y-%m-%d")

    expected_trade_days = estimate_trade_days(window_start, today)



    for raw_code in codes:

        code = normalize_code(raw_code)

        print(f"处理 {code}...", end="")



        # 1) 增量补K线：只补缺失区间，不删缓存全量重拉

        fresh = fetch_kline_incremental(code, window_start, today)



        if not fresh:

            # 拉取失败：不能拿旧数据冒充更新成功

            print(f" ❌ 增量K线拉取失败/为空，跳过写入")

            stats["fail"].append({"code": code, "reason": "kline_fetch_empty"})

            time.sleep(BATCH_PAUSE)

            continue



        cached = get_cached_kline(code)

        merged = merge_kline(cached, fresh) if cached else fresh

        merged = realign_kline(merged)

        save_cached_kline(code, merged)



        # 2) 数据完整性校验

        warn_flag = False

        if merged and merged[-1].get("date") != today:

            print(f" ⚠️ 最新K线日期={merged[-1].get('date')}≠{today}", end="")

            warn_flag = True



        if expected_trade_days and len(merged) < expected_trade_days * 0.7:

            print(f" ⚠️ K线天数偏少(预期~{expected_trade_days}天，实际{len(merged)}根)", end="")

            warn_flag = True



        # 3) 用更新后的缓存重算技术指标

        record = build_stock_record(code, name_cache, fund_cache, today)

        if record and record.get("price", 0) > 0:

            if code in done_codes:

                for idx, s in enumerate(snapshot):

                    if s.get("code") == code:

                        snapshot[idx] = record

                        break

            else:

                snapshot.append(record)

                done_codes.add(code)



            if warn_flag:

                print(f" ✅(有告警)")

                stats["warn"].append({"code": code, "reason": "data_incomplete"})

            else:

                print(f" ✅")

                stats["success"].append(code)

        else:

            print(f" ❌ 构建记录失败(价格=0)")

            stats["fail"].append({"code": code, "reason": "build_failed"})



        time.sleep(BATCH_PAUSE)



    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

        json.dump(snapshot, f, ensure_ascii=False)

    save_name_cache(name_cache)

    save_fund_cache(fund_cache)



    # 4) 汇总输出

    print(f"\n{'='*50}")

    print(f"📊 更新汇总:")

    print(f"   ✅ 成功: {len(stats['success'])} 只")

    print(f"   ⚠️ 告警: {len(stats['warn'])} 只")

    print(f"   ❌ 失败: {len(stats['fail'])} 只")

    if stats["fail"]:

        print(f"   失败详情:")

        for f in stats["fail"]:

            print(f"     {f['code']}: {f['reason']}")

    print(f"{'='*50}")



    print(f"🎉 完成")

    bs_logout()





# ========= 主入口 =========

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="astock-screener engine v7.4")

    parser.add_argument("--daily", action="store_true", help="每日增量更新")

    parser.add_argument("--force-refresh-stale", action="store_true", help="强制刷新超期")

    parser.add_argument("--stale-days", type=int, default=DEFAULT_STALE_DAYS, help="超期阈值")

    parser.add_argument("--codes", nargs="+", help="指定代码更新")

    args = parser.parse_args()



    if args.codes:

        update_by_codes(args.codes, force_refresh=True)

    elif args.force_refresh_stale:

        force_refresh_stale(args.stale_days)

    elif args.daily:

        daily_update(args.stale_days)

    else:

        main_full_build()

