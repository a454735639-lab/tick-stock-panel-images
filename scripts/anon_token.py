#!/usr/bin/env python3
"""从 ghcr.io 的匿名 token 响应里取出 token。

用法：  curl -s ".../token?scope=..." | python3 scripts/anon_token.py

输入是 JSON；解析失败时输出空串（调用方据此判定"包仍是私有的"）。
单独放成脚本文件，是为了让 workflow 的 YAML 里不必内嵌多行 Python
（多行内嵌会让 block scalar 的缩进极易出错）。
"""
import json
import sys


def main() -> int:
    raw = sys.stdin.read()
    try:
        print(json.loads(raw).get("token", ""))
    except Exception:
        print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
