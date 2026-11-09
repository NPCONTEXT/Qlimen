#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""doccheck 测试（2026-10-18 起；2026-11-04 照放宽后的规矩改：TODO、交接不查总字数；「## 现在」约 1000 字；交接提醒迁移；方案超 1.2 万提醒拆批）。
不调任何模型，只在临时目录里造文件，测完删掉。

  测doccheck.py                测仓库里的 core/workers/doccheck
  测doccheck.py --installed    测已安装的那一份（$QLIMEN_HOME/core/workers/doccheck；没设 QLIMEN_HOME 就提示后退出，退出码 2）
  测doccheck.py --target 路径   测指定的 doccheck

每条用例造好文件，按钩子输入喂给 doccheck，看提醒里有没有该有的话（预期为空 = 不该提醒；第五项 = 不该出现的话）。全过退出码 0，否则 1。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DRAFT = os.path.join(REPO, "core", "workers", "doccheck")
PY = sys.executable or "/usr/bin/python3"

HEAD = "编号 | 步骤 | 产出物 | 预计耗时 | 依赖/阻塞\n--- | --- | --- | --- | ---\n"
LONG_ROW = HEAD + "1 | %s | 产出 | 1 分钟 | — |\n" % ("长" * 320)
MIGRATE = "照核心 §6 迁"


def row(num, step="[自动] 做一件事"):
    return "%s | %s | 产出 | 1 分钟 | — |\n" % (num, step)


def now_todo(n):
    return "# 任务 · TODO\n\n## 现在（11-04 04:1x）\n- 停因：" + "字" * n + "\n\n## 卷一（11-04 · 在做）\n\n" + HEAD + row(1)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def edit(path):
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


def bash(cmd, cwd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}, "cwd": cwd}


def cases(ws):
    t = os.path.join(ws, "任务")
    good_todo = "# TODO\n\n" + HEAD + row(1) + row(2) + row(3)
    index_ok = ("# 索引\n\n- 停在哪 → `TODO.md` §现在\n- 做法 → `方案.md` §2\n- 规矩 → `说明.md` §三、§四\n"
                "- 脚本 → `脚本/a.py`；格式见 `别的任务/格式.md`\n- 改皮肤只动 `squirrel.custom.yaml`\n")
    base_files = {
        "TODO.md": "# TODO\n\n## 现在\n- 好了\n",
        "方案.md": "# 方案\n\n## 1. 目标\n## 2. 做法\n",
        "说明.md": "# 说明\n\n## 三、规矩\n## 四、数据\n",
        "脚本/a.py": "print(1)\n",
        "../别的任务/格式.md": "# 格式\n",
    }
    return [
        ("正常的 TODO 不提醒", {"TODO.md": good_todo}, edit(os.path.join(t, "TODO.md")), ""),
        ("TODO 不查总字数（9000 字不提醒）", {"TODO.md": "字" * 9000}, edit(os.path.join(t, "TODO.md")), ""),
        ("「## 现在」超过约 1000 字：提醒在追加历史", {"TODO.md": now_todo(1100)}, edit(os.path.join(t, "TODO.md")), "「## 现在」有"),
        ("「## 现在」900 字不提醒", {"TODO.md": now_todo(900)}, edit(os.path.join(t, "TODO.md")), ""),
        ("「## 现在」只算到下一个标题：后面的段再长也不算", {"TODO.md": now_todo(100) + "说明" * 1500 + "\n"}, edit(os.path.join(t, "TODO.md")), ""),
        ("还有 交接.md：提醒迁移，不提字数", {"交接.md": "字" * 10100}, edit(os.path.join(t, "交接.md")), MIGRATE, "超过上限"),
        ("方案超过 1.2 万字：提醒拆批", {"方案.md": "字" * 12100}, edit(os.path.join(t, "方案.md")), "拆成两批"),
        ("方案 1.1 万字不提醒", {"方案.md": "字" * 11000}, edit(os.path.join(t, "方案.md")), ""),
        ("提醒里不再叫人挪归档", {"TODO.md": LONG_ROW}, edit(os.path.join(t, "TODO.md")), "别为字数挪归档", "挪进同目录 归档/"),
        ("TODO 表格一行超过 300 字", {"TODO.md": LONG_ROW}, edit(os.path.join(t, "TODO.md")), "表格一行不超过 300 字"),
        ("一格里塞了列表，表格从中间断开（10-17 GIF 那种）",
         {"TODO.md": HEAD + row("2.1", "[自动] 做了") + "  - 第一件\n  - 第二件\n剩下的格 | 产出 | 1 分钟 | — |\n" + row("2.2")},
         edit(os.path.join(t, "TODO.md")), "夹在表格中间"),
        ("编号重复", {"TODO.md": HEAD + row("2.3") + row("2.4") + row("2.3")}, edit(os.path.join(t, "TODO.md")), "编号 2.3 重复"),
        ("某行格数和表头不同", {"TODO.md": HEAD + row(1) + "2 | 少了几格\n"}, edit(os.path.join(t, "TODO.md")), "表头是 5 格"),
        ("代码块里的表格不查", {"TODO.md": "```\n" + LONG_ROW + "```\n"}, edit(os.path.join(t, "TODO.md")), ""),
        ("归档里的不查", {"归档/交接.md": "字" * 9000}, edit(os.path.join(t, "归档", "交接.md")), ""),
        ("Bash：Python 里写的绝对路径", {"TODO.md": LONG_ROW},
         bash("python3 - <<'EOF'\np='%s'\nprint(open(p).read()[:5])\nEOF" % os.path.join(t, "TODO.md"), ws), "表格一行不超过 300 字"),
        ("Bash：$D/交接.md 带变量", {"交接.md": "# 交接\n"}, bash('D=%s; tail -3 "$D/交接.md"' % t, ws), MIGRATE),
        ("Bash：相对路径按 cwd 算", {"方案.md": "字" * 12100}, bash("wc -m 任务/方案.md", ws), "拆成两批"),
        ("Bash：只写文件名，目录在别的字符串里", {"TODO.md": LONG_ROW},
         bash("python3 - <<'EOF'\nd='%s/'\nt=open(d + 'TODO.md').read()\nEOF" % t, ws), "表格一行不超过 300 字"),
        ("Bash：只读的 grep 不提醒（文件 10 分钟没改过）", {"交接.md": ("字" * 10100, 600)},
         bash("grep -c 字 %s" % os.path.join(t, "交接.md"), ws), ""),
        ("Bash：认不出路径的不管", {"TODO.md": LONG_ROW}, bash("ls -la", ws), ""),
        ("索引引用都对，不提醒", dict(base_files, **{"索引.md": index_ok}), edit(os.path.join(t, "索引.md")), ""),
        ("索引引用的文件不存在", dict(base_files, **{"索引.md": "- 脚本 → `脚本/没有.py`\n"}), edit(os.path.join(t, "索引.md")), "`脚本/没有.py` 不存在"),
        ("索引引用的小节找不到", dict(base_files, **{"索引.md": "- 停在哪 → `TODO.md` §不存在的节\n"}), edit(os.path.join(t, "索引.md")), "§不存在的节 找不到"),
        ("同一行接着写的 §小节 按前面的文件查", dict(base_files, **{"索引.md": "- 规矩 → `说明.md` §三、§九\n"}), edit(os.path.join(t, "索引.md")), "说明.md §九 找不到"),
        ("占位符和网址式路径不查", dict(base_files, **{"索引.md": "原来在 `~/Documents/<目录>/`；接口 `/api/log/self`；`*.md`\n"}),
         edit(os.path.join(t, "索引.md")), ""),
        ("索引超过 3000 字", dict(base_files, **{"索引.md": "字" * 3100}), edit(os.path.join(t, "索引.md")), "超过上限 3000"),
        ("接手索引.md 也按索引查", dict(base_files, **{"接手索引.md": "- `没有.md`\n"}), edit(os.path.join(t, "接手索引.md")), "`没有.md` 不存在"),
        ("不认识的工具不管", {"TODO.md": LONG_ROW}, {"tool_name": "Read", "tool_input": {"file_path": os.path.join(t, "TODO.md")}}, ""),
    ]


def run(target, stdin_text, state):
    env = dict(os.environ, QLIMEN_DOCCHECK_STATE=state, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([PY, target], input=stdin_text, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=20)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def context(out):
    return json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--installed", action="store_true")
    ap.add_argument("--target")
    a = ap.parse_args()
    target = a.target or DRAFT
    if a.installed:
        home = os.environ.get("QLIMEN_HOME")
        if not home:
            print("没设 QLIMEN_HOME，不知道装在哪。")
            return 2
        target = os.path.join(home, "core", "workers", "doccheck")
    print("测：%s" % target)
    passed, failed = 0, []
    tmp = tempfile.mkdtemp(prefix="doccheck-测试-")
    try:
        for case in cases(os.path.join(tmp, "工作区")):
            name, files, data, want = case[:4]
            bad = case[4] if len(case) > 4 else None
            ws = os.path.join(tmp, "工作区")
            shutil.rmtree(ws, ignore_errors=True)
            for rel, text in files.items():
                age = 0
                if isinstance(text, tuple):   # (内容, 多少秒前改的)：造一个旧文件
                    text, age = text
                fp = os.path.join(ws, "任务", rel)
                write(fp, text)
                if age:
                    os.utime(fp, (time.time() - age, time.time() - age))
            st = os.path.join(tmp, "提醒记录.json")
            rc, out, err = run(target, json.dumps(data, ensure_ascii=False), st)
            if os.path.exists(st):
                os.remove(st)
            got = context(out) if rc == 0 and not err else "（退出码 %d，stderr：%s）" % (rc, err[:200])
            # 钩子模式出错会放行、不输出，「预期不提醒」的用例会被白白算过；用不吞异常的命令行模式再查一遍
            if want == "" and data["tool_name"] == "Edit":
                c = subprocess.run([PY, target, data["tool_input"]["file_path"]], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, universal_newlines=True)
                if c.returncode != 0 or c.stdout or c.stderr:
                    got = "（命令行模式：退出码 %d，%s）" % (c.returncode, (c.stdout + c.stderr)[:200])
            ok = (want == "" and got == "") or (want != "" and want in got)
            if ok and bad and bad in got:
                ok = False
            if ok:
                passed += 1
                print("✓ %s" % name)
            else:
                failed.append(name)
                print("✗ %s\n    预期：%s%s\n    实际：%s" % (name, want or "（不提醒）", "，且没有「%s」" % bad if bad else "", got or "（没提醒）"))
        # 30 分钟内同一个文件只提醒一次
        ws = os.path.join(tmp, "工作区")
        shutil.rmtree(ws, ignore_errors=True)
        p = os.path.join(ws, "任务", "TODO.md")
        write(p, LONG_ROW)
        state = os.path.join(tmp, "提醒记录.json")
        first = context(run(target, json.dumps(edit(p)), state)[1])
        second = context(run(target, json.dumps(edit(p)), state)[1])
        os.remove(state)
        third = context(run(target, json.dumps(edit(p)), state)[1])
        name = "同一个文件 30 分钟内只提醒一次，记录清掉后再提醒"
        if first and not second and third:
            passed += 1
            print("✓ %s" % name)
        else:
            failed.append(name)
            print("✗ %s：第一次 %s，第二次 %s，清掉后 %s" % (name, bool(first), bool(second), bool(third)))
        # 输入坏了放行
        rc, out, err = run(target, "这不是 JSON", state)
        name = "输入坏了放行（退出码 0、没有输出）"
        if rc == 0 and out == "":
            passed += 1
            print("✓ %s" % name)
        else:
            failed.append(name)
            print("✗ %s：退出码 %d，输出 %r" % (name, rc, out[:100]))
        # 命令行模式
        good = os.path.join(ws, "好", "TODO.md")
        write(good, "# TODO\n\n" + HEAD + row(1))
        r1 = subprocess.run([PY, target, p], stdout=subprocess.PIPE, universal_newlines=True)
        r0 = subprocess.run([PY, target, os.path.dirname(good)], stdout=subprocess.PIPE, universal_newlines=True)
        name = "命令行：有问题退出码 1 并列出，没问题退出码 0"
        if r1.returncode == 1 and "表格一行不超过 300 字" in r1.stdout and r0.returncode == 0 and r0.stdout == "":
            passed += 1
            print("✓ %s" % name)
        else:
            failed.append(name)
            print("✗ %s：有问题的退出码 %d，没问题的退出码 %d" % (name, r1.returncode, r0.returncode))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    total = passed + len(failed)
    print("\n通过 %d / %d%s" % (passed, total, "" if not failed else "；没过：" + "、".join(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
