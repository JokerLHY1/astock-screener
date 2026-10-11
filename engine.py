#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A股选股引擎 v6.0 - 增量缓存版
- stocks.txt 为空时自动拉全市场
- K线缓存：data/history/<code>.json，只补最近缺失交易日
- 基本面缓存：已算过的跳过，避免重复拉取
- 失败重试：单只失败重试3次
- 断点续跑：每100只存一次，中断后重跑会自动跳过已完成的
"""

import baostock as bs
import akshare as ak
import json
import os
import time
import re
from datetime import datetime, timedelta

# ============ 配置 ============
DATA_DIR = "data"
HISTORY_DIR = os.path.join(DATA_DIR, "history")  # K线缓存目录
FUND_CACHE_FILE = os.path.join(DATA_DIR, "fund_cache.json")  # 基本面缓存
SNAPSHOT_FILE = os.path.join(DATA_DIR, "snapshot.json")
TECH_FILE = os.path.join(DATA_DIR, "tech.json")
STOCKS_FILE = "stocks.txt"

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

# 每只最大重试次数
MAX_RETRY = 3
# 每次拉取的K线天数（往前多拉一些，覆盖周末/停牌）
KLINE_DAYS = 90


# ============ 工具函数 ============
def ensure_data_dir():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)
    if not os.path.exists(HISTORY_DIR):
        os.makedirs(HISTORY_DIR)


def load_stocks_from_txt():
    if not os.path.exists(STOCKS_FILE):
        return []
    with open(STOCKS_FILE, "r", encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]
    return lines


def get_all_a_stocks():
    print("  正在拉取全市场股票列表...")
    try:
        df = ak.stock_info_a_code_name()
        codes = df['code'].tolist()
        print(f"  共获取 {len(codes)} 只股票")
        return codes
    except Exception as e:
        print(f"  拉取全市场列表失败: {e}")
        return []


def load_fund_cache():
    """加载基本面缓存"""
    if os.path.exists(FUND_CACHE_FILE):
        try:
            with open(FUND_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}


def save_fund_cache(cache):
    with open(FUND_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


def get_cached_kline(code):
    """读取K线缓存"""
    cache_file = os.path.join(HISTORY_DIR, f"{code}.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []


def save_cached_kline(code, kline):
    """保存K线缓存"""
    cache_file = os.path.join(HISTORY_DIR, f"{code}.json")
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(kline, f, ensure_ascii=False)


def merge_kline(old, new):
    """合并新旧K线，按日期去重，保留最新的"""
    merged = {d['date']: d for d in old}
    for d in new:
        merged[d['date']] = d
    result = sorted(merged.values(), key=lambda x: x['date'])
    return result


# ============ 技术面分析 ============
def fetch_kline(code, count=KLINE_DAYS):
    """拉取K线（带重试）"""
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

    # 读缓存
    cached = get_cached_kline(code)
    fresh = fetch_kline(code)

    if not fresh and not cached:
        return result

    # 合并：有新数据就用新的，否则用缓存
    if fresh:
        data = merge_kline(cached, fresh)
        # 只保留最近 KLINE_DAYS 个交易日
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

    # --- 1. 5/20均线多头 ---
    if len(closes) >= 20:
        ma5 = sum(closes[-5:]) / 5
        ma20 = sum(closes[-20:]) / 20
        ma5_prev = sum(closes[-10:-5]) / 5 if len(closes) >= 10 else ma5
        ma_bull = (ma5 > ma20) and (ma5 > ma5_prev)
        result["ma_bull"] = ma_bull
        result["ma_score"] = TECH_SCORES["ma"] if ma_bull else 0
        result["detail"]["ma5"] = round(ma5, 2)
        result["detail"]["ma20"] = round(ma20, 2)

    # --- 2. MACD ---
    if len(closes) >= 26:
        dif, dea, macd_hist = calc_macd(closes)
        if len(dif) >= 2 and len(dea) >= 2:
            macd_gold = (dif[-1] > dea[-1]) and (dif[-2] <= dea[-2])
            result["macd_gold"] = macd_gold
            result["macd_score"] = TECH_SCORES["macd"] if macd_gold else 0
            result["detail"]["dif"] = round(dif[-1], 4)
            result["detail"]["dea"] = round(dea[-1], 4)

    # --- 3. KDJ ---
    if len(closes) >= 14:
        k, d, j = calc_kdj(highs, lows, closes)
        if len(k) >= 2 and len(d) >= 2:
            kdj_gold = (k[-1] > d[-1]) and (k[-2] <= d[-2]) and (k[-1] < 80)
            result["kdj_gold"] = kdj_gold
            result["kdj_score"] = TECH_SCORES["kdj"] if kdj_gold else 0
            result["detail"]["k"] = round(k[-1], 2)
            result["detail"]["d"] = round(d[-1], 2)
            result["detail"]["j"] = round(j[-1], 2)

    # --- 4. 放量突破 ---
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
    # 检查缓存：已有且不是过期数据则直接返回
    today = datetime.now().strftime("%Y-%m-%d")
    if code in fund_cache:
        cached_date = fund_cache[code].get("cache_date", "")
        # 同一天跑过就复用
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
            # PE/PB
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

            # ROE
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
            break  # 成功，跳出重试循环

        except Exception as e:
            if attempt < MAX_RETRY - 1:
                time.sleep(2)
            else:
                result["detail"]["error"] = str(e)

    # 写入缓存
    fund_cache[code] = result
    return result


# ============ 主流程 ============
def main():
    print("=" * 60)
    print("A股选股引擎 v6.0（增量缓存版）启动")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    ensure_data_dir()
    fund_cache = load_fund_cache()

    # 登录 Baostock
    lg = bs.login()
    if lg.error_code != '0':
        print(f"Baostock 登录失败: {lg.error_msg}")
        return
    print("Baostock 登录成功")

    # 确定股票池
    stocks = load_stocks_from_txt()
    if not stocks:
        print("stocks.txt 为空，自动拉取全市场 A 股列表...")
        stocks = get_all_a_stocks()

    if not stocks:
        print("股票池为空，退出")
        bs.logout()
        save_fund_cache(fund_cache)
        return

    print(f"股票池共 {len(stocks)} 只")

    # 加载行业映射
    print("\n正在加载行业映射...")
    try:
        with open("industry_map.json", "r", encoding="utf-8") as f:
            CODE_INDUSTRY = json.load(f)
        print(f"板块映射加载完成，共 {len(CODE_INDUSTRY)} 只股票有行业归属")
    except Exception as e:
        print(f"⚠️ 未找到 industry_map.json: {e}")
        CODE_INDUSTRY = {}

    # 尝试加载已有快照（断点续跑）
    existing_results = {}
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                for item in json.load(f):
                    existing_results[item["code"]] = item
            print(f"已加载已有快照 {len(existing_results)} 条（断点续跑）")
        except:
            pass

    results = list(existing_results.values())
    done_codes = set(existing_results.keys())
    success = len(done_codes)
    fail = 0

    for i, code in enumerate(stocks):
        # 跳过已完成的
        if code in done_codes:
            continue

        try:
            if (i + 1) % 50 == 0:
                print(f"  进度: {i+1}/{len(stocks)} (已完成 {success}, 失败 {fail})")

            # 技术面
            tech = get_tech_analysis(code)

            # 基本面（带缓存）
            fund = get_fundamental_analysis(code, fund_cache)

            # 股票名称
            stock_name = code
            try:
                rs = bs.query_stock_basic(code=code)
                if rs.error_code == '0' and rs.next():
                    row = rs.get_row_data()
                    stock_name = row[1] if len(row) > 1 and row[1] else code
            except:
                pass

            # 当前价格
            current_price = 0
            cached_k = get_cached_kline(code)
            if cached_k:
                current_price = cached_k[-1]['close']

            industry = CODE_INDUSTRY.get(code, "未分类")

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
                "tech_total": tech["tech_total"],
                "roe": fund["roe"],
                "pe": fund["pe"],
                "pb": fund["pb"],
                "roe_score": fund["roe_score"],
                "pe_score": fund["pe_score"],
                "pb_score": fund["pb_score"],
                "fund_total": fund["fund_total"],
                "tech_detail": tech["detail"],
                "fund_detail": fund["fund_detail"],
            }

            results.append(result)
            done_codes.add(code)
            success += 1

            if success % 100 == 0:
                save_snapshot(results)
                save_fund_cache(fund_cache)

        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  {code} 处理失败: {e}")

    # 最终保存
    save_snapshot(results)
    save_fund_cache(fund_cache)
    save_tech_detail(results)

    bs.logout()

    print("\n" + "=" * 60)
    print(f"完成! 成功: {success}, 失败: {fail}, 总计: {len(results)}")
    print(f"结果已保存: {SNAPSHOT_FILE}")
    print("=" * 60)


def save_snapshot(results):
    snapshot = []
    for r in results:
        snapshot.append({
            "code": r["code"],
            "name": r["name"],
            "price": r["price"],
            "industry": r["industry"],
            "ma_bull": r["ma_bull"],
            "macd_gold": r["macd_gold"],
            "kdj_gold": r["kdj_gold"],
            "vol_breakout": r["vol_breakout"],
            "tech_total": r["tech_total"],
            "roe": r["roe"],
            "pe": r["pe"],
            "pb": r["pb"],
            "roe_score": r["roe_score"],
            "pe_score": r["pe_score"],
            "pb_score": r["pb_score"],
            "fund_total": r["fund_total"],
        })
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def save_tech_detail(results):
    tech_data = {}
    for r in results:
        tech_data[r["code"]] = {
            "name": r["name"],
            "price": r["price"],
            "industry": r["industry"],
            "tech_detail": r["tech_detail"],
            "fund_detail": r["fund_detail"],
        }
    with open(TECH_FILE, "w", encoding="utf-8") as f:
        json.dump(tech_data, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
