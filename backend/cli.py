#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令行工具：修改登录密码
用法：
  docker exec -it cctv-dl-docker python3 /app/backend/cli.py password              # 生成新随机密码
  docker exec -it cctv-dl-docker python3 /app/backend/cli.py password --set 新密码  # 指定新密码
"""
import sys
import secrets
import string
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auth


def gen_random(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def main():
    if len(sys.argv) < 2 or sys.argv[1] != "password":
        print("用法:")
        print("  python3 cli.py password              生成新随机密码")
        print("  python3 cli.py password --set 新密码  指定新密码")
        sys.exit(1)

    if "--set" in sys.argv:
        idx = sys.argv.index("--set")
        if idx + 1 >= len(sys.argv):
            print("错误: --set 后需跟新密码")
            sys.exit(1)
        new_pw = sys.argv[idx + 1]
        if len(new_pw) < 6:
            print("错误: 密码至少6位")
            sys.exit(1)
    else:
        new_pw = gen_random()

    auth.set_password(new_pw)
    print("=" * 60)
    print("密码修改成功！")
    print(f"账号: {auth.USERNAME}")
    print(f"新密码: {new_pw}")
    print("旧登录将全部失效，请用新密码重新登录。")
    print("=" * 60)


if __name__ == "__main__":
    main()
