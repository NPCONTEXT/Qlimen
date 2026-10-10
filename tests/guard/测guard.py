#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""guard 测试（2026-10-09 版：拦改成问）。不调任何模型。

  测guard.py                测仓库里的 core/workers/guard
  测guard.py --installed    测已安装的那一份（$QLIMEN_HOME/core/workers/guard；没设 QLIMEN_HOME 就提示后退出，退出码 2）
  测guard.py --target 路径   测指定的 guard（拿旧版来跑，应该大面积未过）
  测guard.py --report 文件   另把结果写成 Markdown

老样例在同目录 样例.json（54 条，2026-10-06 写的）：原来「拦」的现在应「问」，「放」的照旧放；
CHANGED 里的 3 条是 10-09 有意改了预期的。新样例在 NEW 里（后半是命令替换：双引号里、<<EOF 正文里的 $( ) 和
反引号会真的执行，要问；单引号里、<<'EOF' 正文里、转义了的、注释里的只是文字，放）。全过退出码 0，否则 1。
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DRAFT = os.path.join(REPO, "core", "workers", "guard")
OLD_CASES = os.path.join(HERE, "样例.json")

CHANGED = {
    "echo 的引号里出现壳名和触发词": ("放", "引号里的整句话不算（原来是已知多拦）"),
    "heredoc 正文里出现壳名和触发词": ("放", "heredoc 正文不算（原来是已知多拦）"),
    "变量里放壳名": ("放", "壳名放进变量属于故意绕，不管"),
}

NEW = [
    # ---- 写文字：不该问 ----
    {"名": "10-08 被拦那条：heredoc 里的 Python 往交接里写字", "预期": "放",
     "命令": "python3 - <<'PYEOF'\np='交接.md'\nnew='''  - 要用户运行 `~/.kimi-code/bin/kimi login`；报错原文 "
             "`error: Cannot combine --prompt with --auto.`'''\nprint(new)\nPYEOF\ntail -3 日志/C4进度.md"},
    {"名": "echo 单引号追加到交接", "预期": "放", "命令": "echo '记一笔：kimi -p 和 --auto 不能一起用' >> 交接.md"},
    {"名": "printf 双引号写说明", "预期": "放", "命令": "printf \"%s\\n\" \"dsh headless -p 的用法\" > 说明.md"},
    {"名": "git commit 信息里有触发词", "预期": "放", "命令": "git commit -m \"guard 不再拦 kimi -p 的文字\""},
    {"名": "grep 搜触发词", "预期": "放", "命令": "grep -rn \"kimi -p\" ~/work/claude/派工制度"},
    {"名": "kimi login", "预期": "放", "命令": "~/.kimi-code/bin/kimi login"},
    {"名": "kimi --version", "预期": "放", "命令": "~/.kimi-code/bin/kimi --version"},
    {"名": "注释里写触发词", "预期": "放", "命令": "# 以后别用 kimi -p 直连\nls"},
    {"名": "heredoc <<- 带 tab 缩进", "预期": "放", "命令": "cat <<-EOF > a.md\n\tkimi -p hi\n\tEOF"},
    {"名": "heredoc 写完接普通命令", "预期": "放", "命令": "cat > a.md <<EOF\nkimi -p x\nEOF\nls -la"},
    {"名": "waibao 带 --out、--timeout，任务里有触发词", "预期": "放",
     "命令": "{W} k3 --dir ~/work/claude/样例项目 --out ~/work/claude/样例项目/out/K3 "
             "--timeout 18000 --tag t --plan \"p\" --task \"照 kimi -p 的口径\""},
    {"名": "mkdir -p 和 kimi 在不同命令里", "预期": "放", "命令": "mkdir -p out && ~/.kimi-code/bin/kimi login"},
    {"名": "路径参数里有 dsh、另一条命令有 headless", "预期": "放", "命令": "ls ~/work/dsh; echo headless"},
    # ---- 真调用：要问 ----
    {"名": "env 前缀", "预期": "问", "命令": "env KIMI_CODE_HOME=/tmp/x ~/.kimi-code/bin/kimi -p hi"},
    {"名": "nohup 后台", "预期": "问", "命令": "nohup ~/.kimi-code/bin/kimi -p hi > log.txt 2>&1 &"},
    {"名": "timeout 前缀", "预期": "问", "命令": "timeout 60 kimi -p hi"},
    {"名": "caffeinate 包 dsh", "预期": "问", "命令": "caffeinate -i dsh headless -p hi"},
    {"名": "10-08 给用户的 perl 包一层", "预期": "问",
     "命令": "cd ~/work/claude/样例项目 && perl -e 'alarm 18000; exec @ARGV' ~/.kimi-code/bin/kimi "
             "-p \"$(cat 任务书/K3.md)\" -m kimi-code/k3 > 日志/K3b.jsonl 2> 日志/K3b.err"},
    {"名": "sandbox-exec 包一层", "预期": "问", "命令": "sandbox-exec -p '(version 1)' ~/.kimi-code/bin/kimi -p hi"},
    {"名": "$( ) 命令替换", "预期": "问", "命令": "out=$(kimi -p hi)"},
    {"名": "反引号命令替换", "预期": "问", "命令": "out=`kimi -p hi`"},
    {"名": "--prompt 长参数", "预期": "问", "命令": "cd /tmp && kimi --prompt \"hi\""},
    {"名": "--prompt= 写法", "预期": "问", "命令": "kimi --prompt=\"hi\""},
    {"名": "zsh -lc 包一层", "预期": "问", "命令": "zsh -lc \"kimi -p hi\""},
    {"名": "eval 包一层", "预期": "问", "命令": "eval \"kimi -p hi\""},
    {"名": "heredoc 写完真的直连", "预期": "问", "命令": "cat > a.md <<EOF\n说明\nEOF\nkimi -p hi"},
    {"名": "触发参数加了引号", "预期": "问", "命令": "kimi \"-p\" hi"},
    # ---- 命令替换：双引号里、<<EOF 正文里的 $( ) 和反引号会真的执行 ----
    {"名": "双引号里的 $( )", "预期": "问", "命令": "printf \"%s\\n\" \"$(kimi -p hi)\""},
    {"名": "双引号里的反引号", "预期": "问", "命令": "echo \"结果：`kimi -p hi`\""},
    {"名": "<<EOF 正文里的 $( )", "预期": "问", "命令": "cat <<EOF\n$(kimi -p hi)\nEOF"},
    {"名": "<<EOF 正文里的反引号", "预期": "问", "命令": "cat > a.md <<EOF\n结果：`kimi -p hi`\nEOF"},
    {"名": "<<-EOF 正文里的 $( )（tab 缩进）", "预期": "问", "命令": "cat <<-EOF\n\t$(kimi -p hi)\n\tEOF"},
    {"名": "嵌套：双引号里的 $( ) 里再套双引号和 $( )", "预期": "问", "命令": "echo \"$(echo \"$(kimi -p hi)\")\""},
    {"名": "git commit -m 里用反引号包 kimi -p", "预期": "问", "命令": "git commit -m \"修了 `kimi -p hi` 的问题\""},
    {"名": "双引号里 ${x:-$( )}", "预期": "问", "命令": "echo \"${x:-$(kimi -p hi)}\""},
    {"名": "bash -c 双引号里转义的 \\$( )（交给子 shell 执行）", "预期": "问", "命令": "bash -c \"echo \\$(kimi -p hi)\""},
    {"名": "<<'EOF' 正文里的 $( )", "预期": "放", "命令": "cat <<'EOF'\n$(kimi -p hi)\nEOF"},
    {"名": "<<\"EOF\" 正文里的 $( )", "预期": "放", "命令": "cat <<\"EOF\"\n$(kimi -p hi)\nEOF"},
    {"名": "<<\\EOF 正文里的 $( )", "预期": "放", "命令": "cat <<\\EOF\n$(kimi -p hi)\nEOF"},
    {"名": "<<EOF 正文里转义的 \\$( )", "预期": "放", "命令": "cat <<EOF\n\\$(kimi -p hi)\nEOF"},
    {"名": "双引号里转义的 \\$( )", "预期": "放", "命令": "echo \"\\$(kimi -p hi)\""},
    {"名": "双引号里转义的反引号", "预期": "放", "命令": "echo \"\\`kimi -p hi\\`\""},
    {"名": "双引号里纯文字", "预期": "放", "命令": "echo \"kimi -p hi\""},
    {"名": "单引号里的 $( )", "预期": "放", "命令": "echo '$(kimi -p hi)'"},
    {"名": "注释里的 $( )", "预期": "放", "命令": "# echo \"$(kimi -p hi)\"\nls"},
    {"名": "双引号里的 $( ) 只是查帮助", "预期": "放", "命令": "echo \"$(dsh headless --help)\""},
]


def verdict(hook, case):
    w = os.path.join(os.path.dirname(os.path.realpath(hook)), "waibao")
    c = json.loads(json.dumps(case, ensure_ascii=False).replace("{W}", w))
    payload = {"hook_event_name": "PreToolUse", "tool_name": c.get("工具", "Bash"),
               "tool_input": c.get("输入") or {"command": c["命令"]}}
    r = subprocess.run([hook], input=json.dumps(payload, ensure_ascii=False), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
    if r.returncode != 0:
        return "异常(退出码 %d)" % r.returncode
    out = r.stdout
    if not out.strip():
        return "放"
    if '"permissionDecision": "ask"' in out:
        return "问"
    if '"permissionDecision": "deny"' in out:
        return "拦"
    return "异常(输出 %r)" % out[:80]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--installed", action="store_true")
    ap.add_argument("--target")
    ap.add_argument("--report")
    a = ap.parse_args()
    if a.target:
        hook = a.target
    elif a.installed:
        home = os.environ.get("QLIMEN_HOME")
        if not home:
            print("请先 export QLIMEN_HOME（设成 Qlimen 的安装位置），再跑 --installed。", file=sys.stderr)
            return 2
        hook = os.path.join(os.path.expanduser(home), "core", "workers", "guard")
    else:
        hook = DRAFT
    hook = os.path.realpath(hook)
    with open(OLD_CASES, encoding="utf-8") as f:
        old = json.load(f)["guard"]
    cases = []
    for c in old:
        c = dict(c)
        if c["名"] in CHANGED:
            c["预期"], c["备注"] = CHANGED[c["名"]]
        else:
            c["预期"] = {"拦": "问"}.get(c["预期"], c["预期"])
        c["来源"] = "老样例"
        cases.append(c)
    for c in NEW:
        cases.append(dict(c, 来源="新样例"))
    rows, bad = [], 0
    for c in cases:
        got = verdict(hook, c)
        ok = got == c["预期"]
        bad += not ok
        rows.append((c["来源"], c["名"], got, c["预期"], "通过" if ok else "未过", c.get("备注", "")))
    head = ["# guard 测试结果（%s）" % time.strftime("%Y-%m-%d %H:%M"), "", "被测：`%s`" % hook, "",
            "- 共 %d 条（老样例 %d、新样例 %d），通过 %d，未过 %d" % (len(rows), len(old), len(NEW), len(rows) - bad, bad), ""]
    table = ["| 来源 | 样例 | 实际 | 预期 | 结果 | 备注 |", "|---|---|---|---|---|---|"]
    table += ["| %s |" % " | ".join(str(x).replace("|", "\\|").replace("\n", " ") for x in r) for r in rows]
    text = "\n".join(head + table) + "\n"
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            f.write(text)
    print("\n".join(head))
    for r in rows:
        if r[4] != "通过":
            print("  未过：%s | 实际 %s | 预期 %s" % (r[1], r[2], r[3]))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
