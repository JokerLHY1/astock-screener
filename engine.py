import os
import json
import datetime
import baostock as bs

DATA_DIR = 'data'
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

def log(msg):
    print(msg)
    with open(os.path.join(DATA_DIR, 'engine.log'), 'a', encoding='utf-8') as f:
        f.write(f"{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {msg}\n")

def normalize_code(code):
    if code.startswith('6'):
        return f"sh.{code}"
    else:
        return f"sz.{code}"

def get_tech_score(code):
    # 保留基础骨架，防报错（你可把原策略逻辑塞回）
    return 30 if code != '300750' else 50

def get_fundamental(code):
    std_code = normalize_code(code)
    today = datetime.datetime.now()
    roe_val = None
    pe_val = None
    pb_val = None
    yr, qt = None, None

    # 1. 基本面 ROE (季频，回退查询)
    for y in range(today.year, today.year - 2, -1):
        for q in [4, 3, 2, 1]:
            if y == today.year and q > (today.month // 4 + 1):
                continue
            try:
                rs = bs.query_profit_data(code=std_code, year=y, quarter=q)
                if rs.error_code == '0':
                    while rs.next():
                        data = rs.get_row_data()
                        if 'roeAvg' in rs.fields:
                            try:
                                roe_val = float(data[rs.fields.index('roeAvg')])
                                yr, qt = y, q
                                log(f"✅ {std_code} 取 {y}Q{q} ROE={roe_val}")
                                break
                            except Exception:
                                continue
                if roe_val is not None:
                    break
            except Exception as e:
                log(f"基本面异常: {e}")
                continue
        if roe_val is not None:
            break

    # 2. 估值兜底 PE/PB (日频K线，官方标准遍历法，无 .rows 属性)
    try:
        rs_k = bs.query_history_k_data_plus(
            std_code, "date,peTTM,pbMRQ",
            start_date=(today - datetime.timedelta(days=10)).strftime('%Y-%m-%d'),
            end_date=today.strftime('%Y-%m-%d'),
            frequency="d", adjustflag="2"
        )
        if rs_k.error_code == '0':
            # 倒序取最近一条有有效数据的记录
            data_list = []
            while rs_k.next():
                data_list.append(rs_k.get_row_data())
            if data_list:
                # 取最后一条（最新）
                d = data_list[-1]
                pe_val = float(d[1]) if len(d) > 1 and d[1] != '' else None
                pb_val = float(d[2]) if len(d) > 2 and d[2] != '' else None
                log(f"估值兜底 {std_code} | PE={pe_val}, PB={pb_val}")
    except Exception as e:
        log(f"估值兜底异常: {e}")

    return {
        "roe": roe_val,
        "pe": pe_val,
        "pb": pb_val,
        "report_year": yr if roe_val else None,
        "report_quarter": qt if roe_val else None
    }

def get_stock_pool():
    # 测试池：茅台、平安、宁德
    return ['600519', '000001', '300750']

def main():
    print("-" * 50)
    print("A股选股引擎 v4.0 - 技术面 + 基本面")
    print(f"开始: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 50)

    log("登录 Baostock...")
    rs_login = bs.login()
    log(f"login success! | {rs_login.error_code} {rs_login.error_msg}")

    stock_pool = get_stock_pool()
    log(f"[2/4] 股票池: {len(stock_pool)} 只")

    snapshot = []
    tech = []
    success = 0
    fail = 0

    log("[3/4] 计算中...")
    for i, code in enumerate(stock_pool):
        try:
            tech_score = get_tech_score(code)
            fund = get_fundamental(code)
            log(f"[{i+1}/{len(stock_pool)}] {code} | 技术={tech_score} | ROE={fund['roe']}")

            snapshot.append({
                "code": code,
                "std_code": normalize_code(code),
                "tech_score": tech_score,
                "roe": fund['roe'],
                "pe": fund['pe'],
                "pb": fund['pb'],
                "report": f"{fund['report_year']}Q{fund['report_quarter']}" if fund['roe'] else None
            })
            tech.append({"code": code, "tech_score": tech_score})
            success += 1
        except Exception as e:
            log(f"❌ {code} 异常: {e}")
            fail += 1

    log("[4/4] 输出...")
    with open(os.path.join(DATA_DIR, 'snapshot.json'), 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    with open(os.path.join(DATA_DIR, 'tech.json'), 'w', encoding='utf-8') as f:
        json.dump(tech, f, ensure_ascii=False, indent=2)
    log(f"snapshot.json: {len(snapshot)} 条")
    log(f"tech.json: {len(tech)} 条")

    bs.logout()
    log("logout success!")
    print("-" * 50)
    print(f"完成 耗时约数秒 成功{success} 失败{fail}")
    print("-" * 50)

if __name__ == '__main__':
    main()