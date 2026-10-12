# -*- coding: utf-8 -*-
"""
set_secrets.py — 一次性录入护照号码与手机号码

特点：
  * 输入时不回显（终端不显示字符，也不会进入命令历史）
  * 使用 Windows DPAPI 加密后写入 secrets/secrets.dat
  * 明文不落盘、不进日志、不进代码仓库

用法（在本项目目录下双击运行本文件对应的命令，或在命令行执行）：
  .venv\\Scripts\\python.exe set_secrets.py
"""

from __future__ import annotations

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tdac_sec import (SECRETS_PATH, load_secrets, mask, save_secrets,
                      load_group_secrets, save_group_secrets)  # noqa: E402
import tdac_paths  # noqa: E402


def ask(label: str, current: str = "") -> str:
    hint = f"（当前 {mask(current)}，直接回车保持不变）" if current else ""
    while True:
        try:
            v = getpass.getpass(f"{label}{hint}: ").strip()
        except Exception:
            v = input(f"{label}{hint}: ").strip()
        if v == "" and current:
            return current
        if v:
            return v
        print("  不能为空，请重新输入。")


def group_main() -> int:
    """按 config/group.json 中的旅客 ID 录入加密护照和手机号。"""
    from tdac_group import load_group_config
    config_dir = tdac_paths.config_dir()
    try:
        group = load_group_config(config_dir, lambda: {})
    except Exception as e:
        print(f"读取 config\\group.json 失败：{e}")
        return 1
    if not group.travelers or len(group.travelers) > 10:
        print("group.json 必须包含 1 至 10 名旅客。")
        return 1
    try:
        current = load_group_secrets()
    except Exception:
        current = {}
    records = {}
    for traveler in group.travelers:
        print(f"\n旅客 {traveler.id}（{traveler.familyName} {traveler.firstName}）")
        old = current.get(traveler.id, {})
        records[traveler.id] = {
            "passportNo": ask("护照号码", old.get("passportNo", "")),
            "phoneNo": ask("手机号码（只填数字，不含区号）", old.get("phoneNo", "")),
        }
    path = save_group_secrets(records)
    print(f"\n已加密保存 {len(records)} 位旅客资料：{path}")
    return 0


def main() -> int:
    if "--group" in sys.argv[1:]:
        return group_main()
    print("=" * 62)
    print(" TDAC 敏感信息录入（本地加密保存，仅当前 Windows 用户可解密）")
    print("=" * 62)

    cur = {}
    if os.path.exists(SECRETS_PATH):
        try:
            cur = load_secrets()
            print("已检测到旧的加密文件，可直接回车沿用。\n")
        except Exception as e:
            print(f"旧文件无法解密（{str(e)[:80]}），将重新录入。\n")

    passport = ask("护照号码", cur.get("passportNo", ""))
    phone = ask("手机号码（只填数字，不含 +86，例如 13800138000）", cur.get("phoneNo", ""))

    path = save_secrets({"passportNo": passport, "phoneNo": phone})
    print()
    print("已加密保存：", path)
    print(f"  护照号码 -> {mask(passport)}")
    print(f"  手机号码 -> {mask(phone)}")
    print("\n可用以下命令自检：")
    print("  .venv\\Scripts\\python.exe tdac_sec.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
