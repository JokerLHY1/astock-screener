# ============ 保存函数（已修复 KeyError 容错） ============



def save_snapshot(results):

    os.makedirs(DATA_DIR, exist_ok=True)

    snapshot = []

    for r in results:

        # 过滤不完整记录，确保断点续跑不写入脏数据

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

        # 修复 KeyError: 'tech_detail' / 'fund_detail'，兼容旧快照

        tech_data[r["code"]] = {

            "name": r.get("name", ""),

            "price": r.get("price", 0),

            "industry": r.get("industry", "未分类"),

            "tech_detail": r.get("tech_detail", {}),

            "fund_detail": r.get("fund_detail", {}),

        }

    with open(TECH_FILE, "w", encoding="utf-8") as f:

        json.dump(tech_data, f, ensure_ascii=False, indent=2)





# ============ 主流程（已修复旧快照过滤 + 申万匹配兜底） ============



def main():

    args = parse_args()



    print("=" * 60)

    print("A股选股引擎 v7.0（增量缓存 + 板块筛选 + 稳定性兜底）启动")

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



    fund_cache = load_fund_cache()



    lg = bs.login()

    if lg.error_code != '0':

        print(f"Baostock 登录失败: {lg.error_msg}")

        save_snapshot([])

        save_tech_detail([])

        save_fund_cache(fund_cache)

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

                    # 兼容旧数据，使用 .get()

                    if item.get("industry") in sector_list:

                        filtered_codes.append(item["code"])

                print(f"底库中找到 {len(filtered_codes)} 只属于选中板块的股票")

            except Exception as e:

                print(f"读取底库失败: {e}")



        if filtered_codes:

            stocks = filtered_codes

        else:

            # 申万切换后旧底库可能全不匹配，自动全量拉取建库

            print("⚠️ 底库中没找到匹配股票（可能行业映射已更新），将全量拉取建库")

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

        sys.exit(1)



    print(f"股票池共 {len(stocks)} 只")



    # 加载申万行业映射

    print("\n正在加载行业映射...")

    try:

        with open("industry_map.json", "r", encoding="utf-8") as f:

            CODE_INDUSTRY = json.load(f)

        print(f"板块映射加载完成，共 {len(CODE_INDUSTRY)} 只股票有行业归属")

    except Exception as e:

        print(f"⚠️ 未找到 industry_map.json: {e}")

        CODE_INDUSTRY = {}



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



            stock_name = code

            try:

                rs = bs.query_stock_basic(code=code)

                if rs.error_code == '0' and rs.next():

                    row = rs.get_row_data()

                    stock_name = row[1] if len(row) > 1 and row[1] else code

            except:

                pass



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

                "fund_detail": fund["detail"],

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



    save_snapshot(results)

    save_fund_cache(fund_cache)

    save_tech_detail(results)



    bs.logout()



    print("\n" + "=" * 60)

    print(f"完成! 成功: {success}, 失败: {fail}, 总计: {len(results)}")

    print(f"结果已保存: {SNAPSHOT_FILE}")

    print("=" * 60)





if __name__ == "__main__":

    main()

