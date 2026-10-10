import baostock as bs
import json
import re

bs.login()

rs = bs.query_stock_industry()
code_to_industry = {}

while rs.error_code == '0' and rs.next():
    row = rs.get_row_data()
    code = row[1]
    industry = row[3]
    if industry:
        # 生成时就去掉编码前缀
        cleaned = re.sub(r'^[A-Z]\d+', '', industry)
        code_to_industry[code] = cleaned

bs.logout()

with open("industry_map.json", "w", encoding="utf-8") as f:
    json.dump(code_to_industry, f, ensure_ascii=False, indent=2)

print(f"✅ 完成，共 {len(code_to_industry)} 只")
print("验证前3条：")
for i, (k, v) in enumerate(code_to_industry.items()):
    if i >= 3:
        break
    print(f"  {k}: {v}")
