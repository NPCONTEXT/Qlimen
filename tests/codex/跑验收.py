#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""guard 吃 Codex 格式的 PreToolUse 输入（tool_name、turn_id 等字段和 Claude Code 不同）。不调模型、不改文件。

  跑验收.py                 测仓库里的 core/workers/guard
  跑验收.py --target 路径    测指定的 guard

样例在同目录 guard样例.jsonl：{W} 换成被测 guard 同目录的 waibao。
2026-10-08 写的时候 guard 直接拦（deny），10-09 起改成问用户（ask），样例的期望已按新行为改。
本机安装后的自查（入口是不是链到核心、钩子有没有被信任）见 adapters/codex/挂载说明.md。
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=os.path.join(REPO, "core", "workers", "guard"))
    guard = os.path.realpath(ap.parse_args().target)
    waibao = os.path.join(os.path.dirname(guard), "waibao")
    bad = 0
    for line in open(os.path.join(HERE, "guard样例.jsonl"), encoding="utf-8"):
        case = json.loads(line.replace("{W}", waibao))
        r = subprocess.run([guard], input=json.dumps(case["输入"], ensure_ascii=False),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
        out = r.stdout
        got = ("问" if '"ask"' in out else "拦" if '"deny"' in out else "放行" if not out.strip() else "其他:" + out[:80])
        ok = got == case["期望"] and r.returncode == 0
        bad += not ok
        cmd = case["输入"]["tool_input"]["command"]
        print("  %s %s %s → 期望 %s，实际 %s" % ("✓" if ok else "✗", case["输入"]["tool_name"], cmd[:50], case["期望"], got))
    print("结果：%s" % ("全部通过" if not bad else "未通过 %d 项" % bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
