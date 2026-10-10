#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Qlimen 全部测试：不调任何模型、不花钱、不碰你真实的 ~/.claude、~/.codex、~/.dsh 和账本。

做法：在 tests/_沙盒-*/ 里临时装一份 Qlimen（复制 core/ 和 adapters/，用 *.example 模板生成 预算.json 和 保护清单.txt），
然后依次跑三组测试，跑完删掉临时目录。
  1. guard（tests/guard/测guard.py）：100 条命令该放、该问
  2. Codex 格式（tests/codex/跑验收.py）：guard 吃 Codex 的钩子输入
  3. waibao（tests/waibao/跑测试.py）：假工人 + 临时账本，测派工、越界审计、预算提醒、超时、apply/undo 等
  4. 统计脚本（tools/统计/自测.py）：用造出来的假会话记录测 tools/统计/统计.py 的口径和隐私（汇总表不含原文）
只依赖系统自带的 Python 3（3.9 起）。退出码：全过 0，否则 1。
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
PY = sys.executable or "/usr/bin/python3"


def unlock(path):
    """清掉 macOS 的用户锁定标记（只作用于测试沙盒）。不是 macOS 时什么都不做。"""
    if sys.platform == "darwin" and os.path.exists(path):
        subprocess.run(["/usr/bin/chflags", "-R", "nouchg", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def fill(src, dst, home, ws):
    with open(src, encoding="utf-8") as f:
        text = f.read().replace("{{QLIMEN_HOME}}", home).replace("{{WORKSPACE}}", ws)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    # 沙盒建在 tests/ 下，不放系统临时目录：系统沙箱规则本来就放行临时目录，放那里测不出「写不进」
    tmp = tempfile.mkdtemp(prefix="_沙盒-", dir=HERE)
    ws = os.path.join(tmp, "工作区")
    home = os.path.join(ws, "Qlimen")
    try:
        for d in ("core", "adapters"):
            shutil.copytree(os.path.join(REPO, d), os.path.join(home, d), symlinks=True)
        unlock(tmp)   # 仓库加过锁（qlimen-lock）时，copytree 会把 uchg 标记一起复制过来
        fill(os.path.join(home, "core/workers/预算.example.json"), os.path.join(home, "core/workers/预算.json"), home, ws)
        fill(os.path.join(home, "core/bin/保护清单.example.txt"), os.path.join(home, "core/bin/保护清单.txt"), home, ws)
        for d in ("core/data/工人结果",):
            os.makedirs(os.path.join(home, d), exist_ok=True)
        for d in ("工人家.nosync/dsh", "工人家.nosync/kimi/sessions", "测试场地"):
            os.makedirs(os.path.join(ws, d), exist_ok=True)
        env = dict(os.environ, QLIMEN_TEST_WS=ws, PYTHONDONTWRITEBYTECODE="1")
        guard = os.path.join(home, "core/workers/guard")
        runs = [("guard", [PY, os.path.join(HERE, "guard/测guard.py"), "--target", guard]),
                ("Codex 格式", [PY, os.path.join(HERE, "codex/跑验收.py"), "--target", guard]),
                ("waibao", [PY, os.path.join(HERE, "waibao/跑测试.py"), "--target", os.path.join(home, "core/workers/waibao")]),
                ("统计脚本", [PY, os.path.join(REPO, "tools/统计/自测.py")])]
        results = []
        for name, cmd in runs:
            print("\n========== %s ==========" % name, flush=True)
            r = subprocess.run(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
            lines = r.stdout.splitlines()
            # waibao 的报告很长：只打印概要和失败项
            if name == "waibao":
                lines = [l for l in lines if l.startswith(("- 通过", "- 回放", "- ✗", "  - ", "#")) or "Traceback" in l]
            print("\n".join(lines), flush=True)
            results.append((name, r.returncode))
        print("\n========== 汇总 ==========")
        for name, rc in results:
            print("  %s %s" % ("✓" if rc == 0 else "✗", name))
        return 0 if all(rc == 0 for _, rc in results) else 1
    finally:
        unlock(tmp)
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(tmp):
            print("提醒：测试沙盒没删干净，请手动删除 %s" % tmp)


if __name__ == "__main__":
    sys.exit(main())
