#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bootstrap.py - 一次性迁移旧项目数据到新目录
用法:  python bootstrap.py  (自动从 C:/Users/86139/astock-engine 复制到当前目录)
说明:  仅复制存在的数据文件; industry_map.json 缺失时引擎会自动用 baostock 兜底。
"""
import os
import shutil

OLD = r"C:\Users\86139\astock-engine"
NEW = os.path.dirname(os.path.abspath(__file__))

# (相对路径, 是否关键)
FILES = [
    ("data/snapshot.json", True),
    ("data/name_cache.json", False),
    ("data/fund_cache.json", False),
    ("data/failed_stocks.json", False),
    ("industry_map.json", False),
]
DIRS = ["data/history"]


def main():
    os.makedirs(os.path.join(NEW, "data"), exist_ok=True)
    for rel in DIRS:
        os.makedirs(os.path.join(NEW, rel), exist_ok=True)
    for rel, critical in FILES:
        src = os.path.join(OLD, rel)
        dst = os.path.join(NEW, rel)
        if os.path.exists(src):
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)
            print(f"✅ 已复制 {rel}")
        elif critical:
            print(f"⚠️ 缺失关键文件: {rel}")
        else:
            print(f"- 跳过(不存在): {rel}")
    print("\n完成。缺失 industry_map.json 时会自动用 baostock 兜底行业。")


if __name__ == "__main__":
    main()
