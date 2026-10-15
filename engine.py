#!/usr/bin/env python3



# -*- coding: utf-8 -*-



"""

A股选股引擎 v7.1 - 增量缓存版 + 板块筛选 + 名称缓存 + 评分硬封顶100分

变更记录:

  - 新增 name_cache.json 持久化股票名称，避免反复请求 baostock 失败导致名称显示为代码

  - 修复断点续跑时旧记录 name 字段为代码的问题

  - 技术面总分硬封顶 70 分，基本面总分硬封顶 30 分，综合分硬封顶 100 分

  - 修复综合分溢出（原 700/962.5 等问题）

"""



import baostock as bs

import akshare as ak

import json

import os

import sys

import time

import argparse

from datetime import datetime, timedelta



# ============ 路径配置（绝对路径，防止从别处调用找不到文件） ============



BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATA_DIR = os.path.join(BASE_DIR, "data")

HISTORY_DIR = os.path.join(DATA_DIR, "history")

FUND_CACHE_FILE = os.path.join(DATA_DIR, "fund_cache.json")

NAME_CACHE_FILE = os.path.join(DATA_DIR, "name_cache.json")

SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshot.json")

TECH_FILE = os.path.join(DATA_DIR, "tech.json")

STOCKS_FILE = os.path.join(BASE_DIR, "stocks.txt")

INDUSTRY_MAP_FILE = os.path.join(BASE_DIR, "industry_map.json")



TECH_SCORES = {

    "ma": 20,

    "macd": 20,

    "kdj": 15,

    "vol": 15,

}

FUND_SCORES = {

    "roe": 12,

    "pe": 10,

    "pb": 8,

}

ROE_MIN = 0.05

PE_MAX = 50

PB_MAX = 10



MAX_RETRY = 3

KLINE_DAYS = 90



# 评分硬封顶常量

TECH_MAX = 70

FUND_MAX = 30

TOTAL_MAX = 100



# ============ 参数解析 ============



def parse_args():

    parser = argparse.ArgumentParser(description='A股选股引擎')

    parser.add_argument('--sectors', type=str, default='',

                        help='板块名称，逗号分隔（如：电子,医药生物）')

    return parser.parse_args()



# ============ 工具函数 ============



def ensure_data_dir():

    if not os.path.exists(DATA_DIR):

        os.makedirs(DATA_DIR)

    if not os.path.exists(HISTORY_DIR):

        os.makedirs(HISTORY_DIR)



def normalize_code(code):

    """统一股票代码格式为 baostock 9位格式（sh.600000 / sz.000001）"""

    code = str(code).strip()

    if '.' in code and len(code) == 9:

        return code

    if len(code) == 6:

        if code.startswith('6') or code.startswith('9'):

            return f"sh.{code}"

        elif code.startswith('0') or code.startswith('3') or code.startswith('2'):

            return f"sz.{code}"

        else:

            return code

    return code



def load_stocks_from_txt():

    if not os.path.exists(STOCKS_FILE):

        return []

    with open(STOCKS_FILE, "r", encoding="utf-8") as f:

        lines = [l.strip() for l in f if l.strip()]

    return [normalize_code(c) for c in lines]



def get_all_a_stocks():

    print("  正在拉取全市场股票列表...")

    try:

        df = ak.stock_info_a_code_name()

        codes = []

        for c in df['code'].tolist():

            codes.append(normalize_code(c))

        print(f"  akshare 获取 {len(codes)} 只股票（已加前缀）")

        return codes

    except Exception as e:

        print(f"  akshare 拉取失败: {e}")

        return []



def load_industry_map():

    """加载申万行业映射，key 统一 normalize"""

    try:

        with open(INDUSTRY_MAP_FILE, "r", encoding="utf-8") as f:

            raw = json.load(f)

        mapped = {}

        for k, v in raw.items():

            mapped[normalize_code(k)] = v

        print(f"✅ 申万行业映射加载完成，共 {len(mapped)} 只股票有行业归属")

        return mapped

    except Exception as e:

        print(f"⚠️ 未找到 industry_map.json ({INDUSTRY_MAP_FILE}): {e}")

        return {}



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



def load_name_cache():

    """加载股票名称缓存"""

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



def get_cached_kline(code):

    cache_file = os.path.join(HISTORY_DIR, f"{code}.json")

    if os.path.exists(cache_file):

        try:

            with open(cache_file, "r", encoding="utf-8") as f:

                return json.load(f)

        except:

            pass

    return []



def save_cached_kline(code, kline):

    cache_file = os.path.join(HISTORY_DIR, f"{code}.json")

    with open(cache_file, "w", encoding="utf-8") as f:

        json.dump(kline, f, ensure_ascii=False)



def merge_kline(old, new):

    merged = {d['date']: d for d in old}

    for d in new:

        merged[d['date']] = d

    result = sorted(merged.values(), key=lambda x: x['date'])

    return result



# ============ 技术面分析 ============



def fetch_kline(code, count=KLINE_DAYS):

    for attempt in range(MAX_RETRY):

        try:

            end_date = datetime.now().strftime("%Y-%m-%d")

            start_date = (datetime.now() - timedelta(days=count * 2)).strftime("%Y-%m-%d")

            rs = bs.query_history_k_data_plus(

                code,

                "date,open,high,low,close,volume,amount,turn",

                start_date=start_date,

                end_date=end_date,

                frequency="d",

                adjustflag="2"

            )

            data = []

            while (rs.error_code == '0') and rs.next():

                row = rs.get_row_data()

                data.append({

                    'date': row[0],

                    'open': float(row[1]) if row[1] else 0,

                    'high': float(row[2]) if row[2] else 0,

                    'low': float(row[3]) if row[3] else 0,

                    'close': float(row[4]) if row[4] else 0,

                    'volume': float(row[5]) if row[5] else 0,

                    'amount': float(row[6]) if row[6] else 0,

                    'turn': float(row[7]) if row[7] else 0,

                })

            return data

        except Exception as e:

            if attempt < MAX_RETRY - 1:

                time.sleep(2)

            else:

                return []

    return []



def get_tech_analysis(code):

    result = {

        "ma_bull": False, "macd_gold": False, "kdj_gold": False,

        "vol_breakout": False, "ma_score": 0, "macd_score": 0,

        "kdj_score": 0, "vol_score": 0, "tech_total": 0, "detail": {}

    }

    cached = get_cached_kline(code)

    fresh = fetch_kline(code)

    if not fresh and not cached:

        return result

    if fresh:

        data = merge_kline(cached, fresh)

        data = data[-KLINE_DAYS:]

        save_cached_kline(code, data)

    else:

        data = cached

    if len(data) < 30:

        return result

    closes = [d['close'] for d in data]

    volumes = [d['volume'] for d in data]

    highs = [d['high'] for d in data]

    lows = [d['low'] for d in data]



    # 1. 5/20均线多头

    if len(closes) >= 20:

        ma5 = sum(closes[-5:]) / 5

        ma20 = sum(closes[-20:]) / 20

        ma5_prev = sum(closes[-10:-5]) / 5 if len(closes) >= 10 else ma5

        ma_bull = (ma5 > ma20) and (ma5 > ma5_prev)

        result["ma_bull"] = ma_bull

        result["ma_score"] = TECH_SCORES["ma"] if ma_bull else 0

        result["detail"]["ma5"] = round(ma5, 2)

        result["detail"]["ma20"] = round(ma20, 2)



    # 2. MACD

    if len(closes) >= 26:

        dif, dea, macd_hist = calc_macd(closes)

        if len(dif) >= 2 and len(dea) >= 2:

            macd_gold = (dif[-1] > dea[-1]) and (dif[-2] <= dea[-2])

            result["macd_gold"] = macd_gold

            result["macd_score"] = TECH_SCORES["macd"] if macd_gold else 0

            result["detail"]["dif"] = round(dif[-1], 4)

            result["detail"]["dea"] = round(dea[-1], 4)



    # 3. KDJ

    if len(closes) >= 14:

        k, d, j = calc_kdj(highs, lows, closes)

        if len(k) >= 2 and len(d) >= 2:

            kdj_gold = (k[-1] > d[-1]) and (k[-2] <= d[-2]) and (k[-1] < 80)

            result["kdj_gold"] = kdj_gold

            result["kdj_score"] = TECH_SCORES["kdj"] if kdj_gold else 0

            result["detail"]["k"] = round(k[-1], 2)

            result["detail"]["d"] = round(d[-1], 2)

            result["detail"]["j"] = round(j[-1], 2)



    # 4. 放量突破

    if len(volumes) >= 20 and len(closes) >= 20:

        avg_vol_20 = sum(volumes[-20:]) / 20

        recent_vol = volumes[-1]

        recent_close = closes[-1]

        recent_high = max(highs[-20:])

        vol_breakout = (recent_vol > avg_vol_20 * 1.5) and (recent_close >= recent_high * 0.98)

        result["vol_breakout"] = vol_breakout

        result["vol_score"] = TECH_SCORES["vol"] if vol_breakout else 0

        result["detail"]["volume_ratio"] = round(recent_vol / avg_vol_20, 2) if avg_vol_20 > 0 else 0

        result["detail"]["recent_high"] = round(recent_high, 2)



    result["tech_total"] = result["ma_score"] + result["macd_score"] + result["kdj_score"] + result["vol_score"]

    # 技术面总分硬封顶 70

    result["tech_total"] = min(result["tech_total"], TECH_MAX)

    return result



def calc_macd(closes, fast=12, slow=26, signal=9):

    if len(closes) < slow + signal:

        return [0], [0], [0]

    ema_fast = []

    ema_slow = []

    ema_f = closes[0]

    ema_s = closes[0]

    for price in closes:

        ema_f = ema_f * (fast - 1) / (fast + 1) + price * 2 / (fast + 1)

        ema_s = ema_s * (slow - 1) / (slow + 1) + price * 2 / (slow + 1)

        ema_fast.append(ema_f)

        ema_slow.append(ema_s)

    dif = [f - s for f, s in zip(ema_fast, ema_slow)]

    dea = []

    dea_val = dif[0]

    for d in dif:

        dea_val = dea_val * (signal - 1) / (signal + 1) + d * 2 / (signal + 1)

        dea.append(dea_val)

    macd_hist = [(d - a) * 2 for d, a in zip(dif, dea)]

    return dif, dea, macd_hist



def calc_kdj(highs, lows, closes, n=9, m1=3, m2=3):

    if len(closes) < n:

        return [50], [50], [50]

    k_list = [50]

    d_list = [50]

    j_list = [50]

    for i in range(n - 1, len(closes)):

        period_high = max(highs[i - n + 1:i + 1])

        period_low = min(lows[i - n + 1:i + 1])

        if period_high == period_low:

            rsv = 50

        else:

            rsv = (closes[i] - period_low) / (period_high - period_low) * 100

        k = k_list[-1] * (m1 - 1) / (m1 + 1) + rsv * 2 / (m1 + 1)

        d = d_list[-1] * (m2 - 1) / (m2 + 1) + k * 2 / (m2 + 1)

        j = 3 * k - 2 * d

        k_list.append(k)

        d_list.append(d)

        j_list.append(j)

    return k_list, d_list, j_list



# ============ 基本面分析 ============



def get_fundamental_analysis(code, fund_cache):

    today = datetime.now().strftime("%Y-%m-%d")

    if code in fund_cache:

        cached_date = fund_cache[code].get("cache_date", "")

        if cached_date == today:

            return fund_cache[code]

    result = {

        "roe": 0, "pe": 0, "pb": 0,

        "roe_score": 0, "pe_score": 0, "pb_score": 0,

        "fund_total": 0, "detail": {},

        "cache_date": today,

    }

    for attempt in range(MAX_RETRY):

        try:

            end_dt = datetime.now()

            start_dt = end_dt - timedelta(days=10)

            rs = bs.query_history_k_data_plus(

                code, "date,peTTM,pbMRQ",

                start_date=start_dt.strftime("%Y-%m-%d"),

                end_date=end_dt.strftime("%Y-%m-%d"),

                frequency="d"

            )

            if rs.error_code == '0':

                while rs.next():

                    row = rs.get_row_data()

                    pe = float(row[1]) if row[1] else 0

                    pb = float(row[2]) if row[2] else 0

                    if pe > 0 or pb > 0:

                        result["pe"] = pe

                        result["pb"] = pb

                        if 0 < pe < PE_MAX:

                            result["pe_score"] = FUND_SCORES["pe"] * (1 - pe / PE_MAX)

                        else:

                            result["pe_score"] = 0

                        if 0 < pb < PB_MAX:

                            result["pb_score"] = FUND_SCORES["pb"] * (1 - pb / PB_MAX)

                        else:

                            result["pb_score"] = 0

                        result["detail"]["pe"] = pe

                        result["detail"]["pb"] = pb

                        break

            year = datetime.now().year

            quarter = (datetime.now().month - 1) // 3 + 1

            for offset in range(4):

                q = quarter - offset

                y = year

                while q <= 0:

                    q += 4

                    y -= 1

                rs = bs.query_profit_data(code=code, year=y, quarter=q)

                if rs.error_code == '0':

                    while rs.next():

                        row = rs.get_row_data()

                        try:

                            roe = float(row[3]) if row[3] else 0

                        except (ValueError, TypeError, IndexError):

                            roe = 0

                        result["roe"] = roe

                        if roe > ROE_MIN:

                            result["roe_score"] = FUND_SCORES["roe"] * min(roe / 0.25, 1)

                        result["detail"]["roe"] = roe

                        result["detail"]["roe_year"] = f"{y}Q{q}"

                        break

                if result["roe"] > 0:

                    break

            result["fund_total"] = result["roe_score"] + result["pe_score"] + result["pb_score"]

            # 基本面总分硬封顶 30

            result["fund_total"] = min(result["fund_total"], FUND_MAX)

            break

        except Exception as e:

            if attempt < MAX_RETRY - 1:

                time.sleep(2)

            else:

                result["detail"]["error"] = str(e)

    fund_cache[code] = result

    return result



# ============ 保存函数 ============



def save_snapshot(results):

    os.makedirs(DATA_DIR, exist_ok=True)

    snapshot = []

    for r in results:

        if "code" not in r or "tech_total" not in r:

            continue

        snapshot.append({

            "code": r["code"],

            "name": r.get("name", ""),

            "price": r.get("price", 0),

            "industry": r.get("industry", "未分类"),

            "ma_bull": r.get("ma_bull", False),

            "macd_gold": r.get("macd_gold", False),

            "kdj_gold": r.get("kdj_gold", False),

            "vol_breakout": r.get("vol_breakout", False),

            "tech_total": r["tech_total"],

            "roe": r.get("roe", 0),

            "pe": r.get("pe", 0),

            "pb": r.get("pb", 0),

            "roe_score": r.get("roe_score", 0),

            "pe_score": r.get("pe_score", 0),

            "pb_score": r.get("pb_score", 0),

            "fund_total": r.get("fund_total", 0),

            "total_score": round(r.get("tech_total", 0) + r.get("fund_total", 0), 2),

        })

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

        json.dump(snapshot, f, ensure_ascii=False, indent=2)



def save_tech_detail(results):

    os.makedirs(DATA_DIR, exist_ok=True)

    tech_data = {}

    for r in results:

        if "code" not in r:

            continue

        tech_data[r["code"]] = {

            "name": r.get("name", ""),

            "price": r.get("price", 0),

            "industry": r.get("industry", "未分类"),

            "tech_detail": r.get("tech_detail", {}),

            "fund_detail": r.get("fund_detail", {}),

        }

    with open(TECH_FILE, "w", encoding="utf-8") as f:

        json.dump(tech_data, f, ensure_ascii=False, indent=2)



# ============ 主流程 ============



def main():

    args = parse_args()



    print("=" * 60)

    print("A股选股引擎 v7.1（增量缓存 + 板块筛选 + 名称缓存 + 评分封顶）启动")

    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if args.sectors:

        print(f"筛选板块: {args.sectors}")

    print("=" * 60)



    ensure_data_dir()

    os.makedirs(DATA_DIR, exist_ok=True)



    if not os.path.exists(SNAPSHOT_FILE):

        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:

            json.dump([], f)

    if not os.path.exists(TECH_FILE):

        with open(TECH_FILE, "w", encoding="utf-8") as f:

            json.dump({}, f)



    # 加载申万行业映射

    global CODE_INDUSTRY

    CODE_INDUSTRY = load_industry_map()



    fund_cache = load_fund_cache()

    name_cache = load_name_cache()

    print(f"📋 名称缓存已加载: {len(name_cache)} 条")



    lg = bs.login()

    if lg.error_code != '0':

        print(f"Baostock 登录失败: {lg.error_msg}")

        save_snapshot([])

        save_tech_detail([])

        save_fund_cache(fund_cache)

        save_name_cache(name_cache)

        sys.exit(1)

    print("Baostock 登录成功")



    stocks = []



    if args.sectors:

        sector_list = [s.strip() for s in args.sectors.split(',')]

        print(f"筛选板块: {sector_list}")

        filtered_codes = []

        if os.path.exists(SNAPSHOT_FILE):

            try:

                with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

                    existing = json.load(f)

                for item in existing:

                    if item.get("industry") in sector_list:

                        filtered_codes.append(item["code"])

                print(f"底库中找到 {len(filtered_codes)} 只属于选中板块的股票")

            except Exception as e:

                print(f"读取底库失败: {e}")

        if filtered_codes:

            stocks = filtered_codes

        else:

            print("⚠️ 底库中没找到匹配股票，将全量拉取建库")

            stocks = get_all_a_stocks()

            if not stocks:

                print("akshare 失败，尝试 baostock...")

                try:

                    rs = bs.query_stock_basic()

                    while rs.error_code == '0' and rs.next():

                        row = rs.get_row_data()

                        if row[0].startswith(('sh.6', 'sz.0', 'sz.3')):

                            stocks.append(row[0])

                    print(f"从 baostock 获取 {len(stocks)} 只")

                except Exception as e:

                    print(f"baostock 也失败: {e}")

    else:

        stocks = load_stocks_from_txt()

        if not stocks:

            print("stocks.txt 为空，自动拉取全市场 A 股列表...")

            stocks = get_all_a_stocks()

            if not stocks:

                print("akshare 失败，尝试 baostock...")

                try:

                    rs = bs.query_stock_basic()

                    while rs.error_code == '0' and rs.next():

                        row = rs.get_row_data()

                        if row[0].startswith(('sh.6', 'sz.0', 'sz.3')):

                            stocks.append(row[0])

                    print(f"从 baostock 获取 {len(stocks)} 只")

                except Exception as e:

                    print(f"baostock 也失败: {e}")



    if not stocks:

        print("所有数据源都失败，写空结果并退出")

        save_snapshot([])

        save_tech_detail([])

        bs.logout()

        save_fund_cache(fund_cache)

        save_name_cache(name_cache)

        sys.exit(1)



    print(f"股票池共 {len(stocks)} 只")



    # 断点续跑：只保留完整记录

    existing_results = {}

    if os.path.exists(SNAPSHOT_FILE):

        try:

            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:

                for item in json.load(f):

                    if "code" in item and "tech_total" in item:

                        existing_results[item["code"]] = item

            print(f"已加载已有快照 {len(existing_results)} 条（断点续跑，已过滤不完整记录）")

        except Exception as e:

            print(f"读取快照失败，将全量重跑: {e}")

            existing_results = {}



    # 修复旧记录中 name 是代码的问题

    for item in existing_results.values():

        if item.get("name") and item["name"].startswith(("sh.", "sz.")):

            cached_name = name_cache.get(item["code"])

            if cached_name and not cached_name.startswith(("sh.", "sz.")):

                item["name"] = cached_name

                print(f"  🔧 修复旧记录名称: {item['code']} → {cached_name}")



    results = list(existing_results.values())

    done_codes = set(existing_results.keys())

    success = len(done_codes)

    fail = 0



    for i, code in enumerate(stocks):

        if code in done_codes:

            continue

        try:

            if (i + 1) % 50 == 0:

                print(f"  进度: {i+1}/{len(stocks)} (已完成 {success}, 失败 {fail})")

            tech = get_tech_analysis(code)

            fund = get_fundamental_analysis(code, fund_cache)



            # ===== 名称获取（优先缓存）=====

            stock_name = name_cache.get(code)

            if not stock_name or stock_name == code or stock_name.startswith(("sh.", "sz.")):

                try:

                    rs = bs.query_stock_basic(code=code)

                    if rs.error_code == '0' and rs.next():

                        row = rs.get_row_data()

                        fetched = row[1] if len(row) > 1 and row[1] else None

                        if fetched and not fetched.startswith(("sh.", "sz.")):

                            stock_name = fetched

                            name_cache[code] = fetched

                        else:

                            stock_name = code.split('.')[-1]

                            name_cache[code] = stock_name

                    else:

                        stock_name = code.split('.')[-1]

                except:

                    stock_name = code.split('.')[-1]

            # ===== 名称获取结束 =====



            current_price = 0

            cached_k = get_cached_kline(code)

            if cached_k:

                current_price = cached_k[-1]['close']

            industry = CODE_INDUSTRY.get(code, CODE_INDUSTRY.get(code[3:] if '.' in code else code, "未分类"))



            # 评分硬封顶

            tech_total = min(tech["tech_total"], TECH_MAX)

            fund_total = min(fund["fund_total"], FUND_MAX)

            total_score = min(tech_total + fund_total, TOTAL_MAX)



            result = {

                "code": code,

                "name": stock_name,

                "price": current_price,

                "industry": industry,

                "ma_bull": tech["ma_bull"],

                "macd_gold": tech["macd_gold"],

                "kdj_gold": tech["kdj_gold"],

                "vol_breakout": tech["vol_breakout"],

                "ma_score": tech["ma_score"],

                "macd_score": tech["macd_score"],

                "kdj_score": tech["kdj_score"],

                "vol_score": tech["vol_score"],

                "tech_total": tech_total,

                "roe": fund["roe"],

                "pe": fund["pe"],

                "pb": fund["pb"],

                "roe_score": fund["roe_score"],

                "pe_score": fund["pe_score"],

                "pb_score": fund["pb_score"],

                "fund_total": fund_total,

                "total_score": round(total_score, 2),

                "tech_detail": tech["detail"],

                "fund_detail": fund["detail"],

            }

            results.append(result)

            done_codes.add(code)

            success += 1

            if success % 100 == 0:

                save_snapshot(results)

                save_fund_cache(fund_cache)

                save_name_cache(name_cache)

        except Exception as e:

            fail += 1

            if fail <= 5:

                print(f"  {code} 处理失败: {e}")



    save_snapshot(results)

    save_fund_cache(fund_cache)

    save_name_cache(name_cache)

    save_tech_detail(results)

    bs.logout()



    # 行业分布统计

    industry_count = {}

    for r in results:

        ind = r.get("industry", "未分类")

        industry_count[ind] = industry_count.get(ind, 0) + 1

    print("\n行业分布统计:")

    for ind, cnt in sorted(industry_count.items(), key=lambda x: x[1], reverse=True)[:10]:

        print(f"  {ind}: {cnt} 只")



    print("\n" + "=" * 60)

    print(f"完成! 成功: {success}, 失败: {fail}, 总计: {len(results)}")

    print(f"名称缓存: {len(name_cache)} 条")

    print(f"结果已保存: {SNAPSHOT_FILE}")

    print("=" * 60)



if __name__ == "__main__":

    main()

