#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""waibao 的零花费测试：假工人 + 临时账本，不调任何模型，不碰真的账本和结果目录。

  一般不直接运行，由 tests/跑全部测试.py 在临时沙盒里装一份 Qlimen 后调用：
  跑测试.py --target <沙盒>/core/workers/waibao   需要环境变量 QLIMEN_TEST_WS（沙盒工作区，即 预算.json 的 allow_roots）
  跑测试.py --report-to 文件   把结果写到指定文件
可选：QLIMEN_REPLAY_DIR 指到你自己的 core/data/工人结果/原始，会把历史真实记录逐份过一遍审计；不给就跳过这一组。
退出码：全过 0，有失败 1。
"""
import argparse
import collections
import csv
import datetime
import glob
import importlib.machinery
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HERE = os.path.dirname(os.path.realpath(__file__))
TASK = os.path.dirname(HERE)
WS = os.environ.get("QLIMEN_TEST_WS", "")             # 沙盒工作区（预算.json 的 allow_roots）
OLD = ""                                              # 拿旧版对照时用：设成旧版 waibao 的路径
FAKE = os.path.join(HERE, "假工人.py")
FIELD = os.path.join(WS, "测试场地") if WS else os.path.join(HERE, "场地")
WORK = os.path.join(FIELD, "工作目录")
OUTSIDE_DIR = os.path.join(FIELD, "目录外")
OUTSIDE = os.path.join(OUTSIDE_DIR, "x.txt")
TIDY = os.path.join(FIELD, "整理")
RAW_DIR = os.environ.get("QLIMEN_REPLAY_DIR", "")      # 历史原始记录，不给就跳过回放
DENY = ""                                             # main() 里按被测 waibao 的位置填：<Qlimen>/core/data
PREV = ""                                             # 拿小修前的旧版逐字对照联网回执时用
ANSWER = "## 结论\n假工人的回答\n## 证据\n## 不确定\n## 没做"
BASE_ARGS = ["--tag", "测试", "--plan", "零花费测试"]
PY = "/usr/bin/python3"
TMP = None
RES = []
TESTS = []


# ---------- 工具 ----------

def check(group, name, ok, detail=""):
    RES.append((group, name, bool(ok), detail if not ok else ""))


def test(group):
    def deco(fn):
        TESTS.append((group, fn))
        return fn
    return deco


def k_call(*calls):
    tcs = []
    for i, (name, args) in enumerate(calls):
        fn = {"arguments": args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)}
        if name is not None:
            fn["name"] = name
        tcs.append({"id": "c%d" % i, "type": "function", "function": fn})
    return {"role": "assistant", "tool_calls": tcs}


def k_final(text=ANSWER):
    return {"role": "assistant", "content": text}


K_META = {"role": "meta", "type": "session.resume_hint", "session_id": "fake"}


def d_call(tool, inp):
    return {"type": "tool_call", "callId": uuid.uuid4().hex, "tool": tool, "input": inp}


def d_step(i=100, o=50):
    return {"type": "status", "phase": "step_end", "usage": {"inputTokens": i, "cacheReadTokens": 0, "outputTokens": o}}


def d_final(text=ANSWER):
    return {"type": "final", "text": text}


class Run(object):
    pass


def run(target, args, scen=None, rows=None, fields=None, driver=None, wait=90):
    d = tempfile.mkdtemp(dir=TMP)
    ledger = os.path.join(d, "账本.csv")
    env = dict(os.environ, WAIBAO_DSH=FAKE, WAIBAO_KIMI=FAKE, WAIBAO_LEDGER=ledger,
               WAIBAO_OUT=os.path.join(d, "结果"), WAIBAO_KIMI_SESSIONS=os.path.join(d, "会话"),
               FAKE_LOG=os.path.join(d, "假工人记录.json"), FAKE_SCENARIO=os.path.join(d, "剧本.json"))
    with open(env["FAKE_SCENARIO"], "w", encoding="utf-8") as f:
        json.dump(scen or {"events": []}, f, ensure_ascii=False)
    if rows:
        with open(ledger, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    cmd = ([PY, "-c", driver, target] if driver else [PY, target]) + args
    t0 = time.time()
    p = subprocess.run(cmd, env=env, input="", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, encoding="utf-8", errors="replace", timeout=wait)
    r = Run()
    r.rc, r.out, r.err, r.secs, r.dir = p.returncode, p.stdout, p.stderr, time.time() - t0, d
    r.log = None
    if os.path.exists(env["FAKE_LOG"]):
        with open(env["FAKE_LOG"], encoding="utf-8") as f:
            r.log = json.load(f)
    r.touch = None
    if os.path.exists(env["FAKE_LOG"] + ".touch.json"):
        with open(env["FAKE_LOG"] + ".touch.json", encoding="utf-8") as f:
            r.touch = json.load(f)
    r.rows = []
    if os.path.exists(ledger):
        with open(ledger, encoding="utf-8-sig", newline="") as f:
            r.rows = list(csv.DictReader(f))
    r.files = sorted(glob.glob(os.path.join(d, "结果", "*.md")))
    m = re.search(r"^\[waibao\] \S+ · (\S+) · ", r.out, re.M)
    r.status = m.group(1) if m else None
    return r


def brief(r):
    return "退出码 %s，状态 %s，输出尾 %r，错误尾 %r" % (r.rc, r.status, r.out[-240:], r.err[-240:])


def marked(r):
    """回执里有没有带 ‼ 的条目（表头的图例不算）。"""
    return bool(re.search(r"^‼", r.out, re.M))


def dead(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


def tools_block(path, key):
    out, on = [], False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith(key + ":"):
                on = True
                continue
            if on and not line.startswith(" "):
                break
            if on:
                out.append(line.strip()[2:].strip())
    return sorted(out)


def setup_field():
    os.makedirs(os.path.join(WORK, "sub"), exist_ok=True)
    os.makedirs(OUTSIDE_DIR, exist_ok=True)
    for path, text in ((os.path.join(WORK, "a.txt"), "工作目录里的测试文件\n"),
                       (os.path.join(WORK, "sub", "b.md"), "子目录里的测试文件\n"),
                       (OUTSIDE, "工作目录外面、派工范围里面的测试文件\n")):
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    link = os.path.join(WORK, "链接出去")
    if not os.path.islink(link):
        os.symlink(os.path.join("..", "目录外"), link)
    if os.path.isdir(TIDY):
        shutil.rmtree(TIDY)
    os.makedirs(TIDY)
    with open(os.path.join(TIDY, "f.txt"), "w", encoding="utf-8") as f:
        f.write("整理测试用\n")


def kimi(target, calls, extra=None, web=False, hang=0, timeout=None, model="k28"):
    """派一个假的 kimi 工人：先做 calls 里的工具调用，再给出回答。"""
    scen = {"events": [k_call(*c) for c in calls], "hang": hang, "after": [k_final(), K_META]}
    args = [model] + (["--web"] if web else ["--dir", WORK]) + BASE_ARGS + ["--task", "测试任务"]
    if timeout:
        args += ["--timeout", str(timeout)]
    return run(target, args + (extra or []), scen)


def flash(target, events, hang=0, timeout=None):
    scen = {"events": events, "hang": hang, "after": [d_step(), d_final()]}
    args = ["flash", "--dir", WORK] + BASE_ARGS + ["--task", "测试任务"]
    if timeout:
        args += ["--timeout", str(timeout)]
    return run(target, args, scen)


# ---------- 甲、乙：档案、守则、--web ----------

G1 = "档案、守则和 --web"


@test(G1)
def a_local(T, M):
    r = kimi(T, [[("Read", {"path": "a.txt"})]])
    check(G1, "k28 不加 --web：正常完成", r.rc == 0 and r.status == "完成", brief(r))
    av = r.log["argv"]
    check(G1, "k28 不加 --web：用本地只读档案",
          av[av.index("--agent-file") + 1] == os.path.join(os.path.dirname(T), "工人-kimi.md"), str(av))
    check(G1, "k28 不加 --web：守则是「不联网」那一份，任务接在后面",
          "不联网。" in r.log["text"] and r.log["text"].rstrip().endswith("测试任务"), r.log["text"][:200])
    check(G1, "k28 不加 --web：工作目录是给的那个", os.path.samefile(r.log["cwd"], WORK), r.log["cwd"])


@test(G1)
def a_web(T, M):
    webdir = os.path.join(os.path.dirname(os.path.dirname(T)), "联网工作目录.nosync")
    r = kimi(T, [[("WebSearch", {"query": "Python 3.13 release date"})]], web=True)
    check(G1, "k28 --web 不给 --dir：正常完成", r.rc == 0 and r.status == "完成", brief(r))
    av = (r.log or {}).get("argv", [])
    check(G1, "k28 --web：用联网档案",
          "--agent-file" in av and av[av.index("--agent-file") + 1] == os.path.join(os.path.dirname(T), "工人-kimi-联网.md"),
          str(av))
    check(G1, "k28 --web：守则是联网那一份", "一次性联网工人" in (r.log or {}).get("text", ""))
    check(G1, "k28 --web：工作目录是联网工作目录",
          r.log and os.path.isdir(webdir) and os.path.samefile(r.log["cwd"], webdir), str(r.log and r.log["cwd"]))
    check(G1, "k28 --web：回执写明是联网档案，并列出搜索词",
          "档案：联网" in r.out and "本次联网 1 次" in r.out and "Python 3.13 release date" in r.out, brief(r))
    r = kimi(T, [[("FetchURL", {"url": "https://example.com/"})]], web=True, model="k3")
    av = (r.log or {}).get("argv", [])
    check(G1, "k3 也能加 --web", r.rc == 0 and "kimi-code/k3" in av and any(x.endswith("工人-kimi-联网.md") for x in av),
          brief(r))
    r = kimi(T, [[("WebSearch", {"query": "x"})]], web=True, extra=["--dir", WORK])
    check(G1, "--web 同时给了 --dir：用给的目录", r.rc == 0 and r.log and os.path.samefile(r.log["cwd"], WORK), brief(r))


@test(G1)
def a_refuse(T, M):
    r = run(T, ["flash", "--web"] + BASE_ARGS + ["--task", "x"])
    check(G1, "flash 加 --web：拒绝（退出码 2），不启动工人", r.rc == 2 and "不接受 --web" in r.err and r.log is None, brief(r))
    r = run(T, ["k28"] + BASE_ARGS + ["--task", "x"])
    check(G1, "不加 --web 又不给 --dir：拒绝", r.rc == 2 and "缺少 --dir" in r.err, brief(r))


@test(G1)
def a_flash(T, M):
    r = flash(T, [d_call("read", {"file_path": "a.txt"}), d_call("read_image", {"file_path": "a.png"})])
    check(G1, "flash：正常完成", r.rc == 0 and r.status == "完成", brief(r))
    check(G1, "flash：守则是「不联网」那一份", r.log and r.log["as"] == "dsh" and "不联网。" in r.log["text"])
    check(G1, "flash：仍是只读模式并带补丁",
          r.log and r.log["env"]["DSH_PERMISSION_MODE"] == "read-only" and "--patch" in r.log["argv"], str(r.log))


@test(G1)
def a_files(T, M):
    base = os.path.dirname(T)
    loc, web = os.path.join(base, "工人-kimi.md"), os.path.join(base, "工人-kimi-联网.md")
    check(G1, "本地档案的白名单只有读类工具，和 waibao 里的名单一致",
          tools_block(loc, "tools") == sorted(["Read", "Grep", "Glob", "ReadMediaFile"]) == sorted(M.PROFILES["kimi"]["tools"]),
          str(tools_block(loc, "tools")))
    check(G1, "联网档案的白名单只有搜索和读网页，和 waibao 里的名单一致",
          tools_block(web, "tools") == sorted(["WebSearch", "FetchURL"]) == sorted(M.PROFILES["kimi-web"]["tools"]),
          str(os.path.exists(web) and tools_block(web, "tools")))
    check(G1, "两份档案的黑名单里都写了对方的工具",
          set(["WebSearch", "FetchURL", "Bash", "Write", "Edit"]) <= set(tools_block(loc, "disallowedTools"))
          and set(["Read", "Grep", "Glob", "ReadMediaFile", "Bash", "Write", "Edit"]) <= set(tools_block(web, "disallowedTools")))
    wr = os.path.join(base, "工人-kimi-写.md")
    if os.path.exists(wr) or "kimi-write" in M.PROFILES:              # 2026-10-09 起有第三份档案
        check(G1, "写文件档案的白名单是读类工具加 Write，和 waibao 里的名单一致",
              os.path.exists(wr) and tools_block(wr, "tools") == sorted(["Read", "Grep", "Glob", "ReadMediaFile", "Write"])
              == sorted(M.PROFILES["kimi-write"]["tools"]), str(os.path.exists(wr) and tools_block(wr, "tools")))
        check(G1, "写文件档案的黑名单里有命令行、改文件、联网",
              os.path.exists(wr) and set(["Bash", "Edit", "WebSearch", "FetchURL"]) <= set(tools_block(wr, "disallowedTools")))
    patch = os.path.join(os.path.dirname(os.path.dirname(base)), "adapters/dsh", "dsh-flash.patch.yml")
    with open(patch, encoding="utf-8") as f:
        off = set(re.findall(r"^- id: (\S+)\n  disabled: true$", f.read(), re.M))
    want = set("tool-bash tool-pwsh tool-jobs tool-web tool-subagent tool-subagent-fork tool-subagent-control "
               "tool-subagent-list-agents tool-workflow tool-skill tool-goal tool-todo".split())
    check(G1, "补丁关掉的正好是那 12 个工具，读文件和搜文件没关", off == want, str(sorted(off ^ want)))


# ---------- 派错的提醒 ----------

G2 = "忘加 --web 的提醒"
ASK_WEB = "请联网搜索一下这个库的官网"


@test(G2)
def b_hint(T, M):
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", ASK_WEB], {"events": [k_final(), K_META]})
    check(G2, "任务像要联网但没加 --web：派出去之前就提醒",
          "提醒：" in r.out and r.out.index("提醒：") < r.out.index("[waibao] k28 ·"), brief(r))
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", ASK_WEB, "--dry-run"])
    check(G2, "--dry-run 也提醒，并写明档案", r.rc == 0 and "提醒：" in r.out and "档案：本地只读" in r.out, brief(r))
    r = run(T, ["k28", "--web"] + BASE_ARGS + ["--task", ASK_WEB], {"events": [k_final(), K_META]})
    check(G2, "加了 --web：不提醒", r.rc == 0 and "提醒：" not in r.out, brief(r))
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "读 a.txt 写摘要"], {"events": [k_final(), K_META]})
    check(G2, "普通本地任务：不提醒", r.rc == 0 and "提醒：" not in r.out, brief(r))
    r = run(T, ["flash", "--dir", WORK] + BASE_ARGS + ["--task", "看看 https://example.com 写了什么"],
            {"events": [d_step(), d_final()]})
    check(G2, "flash 的任务里有网址：也提醒", "提醒：" in r.out, brief(r))


# ---------- 丙-1：事后审计 ----------

G3 = "事后审计（列出越界读取）"


@test(G3)
def c_inside(T, M):
    w = os.path.realpath(WORK)
    swapped = "".join(c.swapcase() if c.isascii() else c for c in os.path.join(w, "a.txt"))
    r = kimi(T, [[("Read", {"path": "a.txt"})], [("Read", {"path": os.path.join(w, "sub", "b.md")})],
                 [("Grep", {"pattern": "测试", "output_mode": "content"})], [("Glob", {"pattern": "**/*.md"})],
                 [("Glob", {"pattern": "sub/[ab]*.md"})], [("Glob", {"pattern": "s?b/*.md"})],
                 [("ReadMediaFile", {"path": "sub/图.png"})], [("Read", {"path": swapped})]])
    check(G3, "目录内的各种读法（相对、绝对、搜内容不给路径、通配符、大小写写错）：不算越界",
          r.rc == 0 and r.status == "完成" and "越界记录" not in r.out and "假工人的回答" in r.out, brief(r))


@test(G3)
def c_mild(T, M):
    r = kimi(T, [[("Read", {"path": OUTSIDE})], [("Read", {"path": "../目录外/x.txt"})],
                 [("Read", {"path": "链接出去/x.txt"})], [("Grep", {"pattern": "x", "path": OUTSIDE_DIR})],
                 [("Glob", {"pattern": FIELD + "/{目录外,整理}/*.txt"})]])
    n = r.out.count("普通越界")
    check(G3, "读了目录外、派工范围内的文件（绝对路径、..、软链接、搜内容、通配符）：5 条都列出",
          n == 5 and "越界记录 5 条" in r.out, "列出 %d 条；%s" % (n, brief(r)))
    check(G3, "普通越界：不中止、状态仍是完成、回答照常打印",
          r.rc == 0 and r.status == "完成" and not marked(r) and "假工人的回答" in r.out, brief(r))


@test(G3)
def c_serious(T, M):
    r = kimi(T, [[("Read", {"path": "/tmp/waibao-测试-不存在.txt"})]])
    check(G3, "读了 /tmp（派工范围以外）：标 ‼、状态「越界」、退出码 3",
          r.rc == 3 and r.status == "越界" and "‼ 严重越界 Read: /tmp/waibao-测试-不存在.txt" in r.out, brief(r))
    check(G3, "严重越界：回执里不打印回答，写明不可信", "假工人的回答" not in r.out and "不可信" in r.out, brief(r))
    body = ""
    if r.files:
        with open(r.files[0], encoding="utf-8") as f:
            body = f.read()
    check(G3, "严重越界：结果文件照常保存，里面有回答和越界清单",
          "假工人的回答" in body and "调度器记录的越界" in body and "严重越界" in body, body[-300:])
    check(G3, "严重越界：账本里状态记「越界」", r.rows and r.rows[-1]["状态"] == "越界", str(r.rows))
    r = kimi(T, [[("Read", {"path": os.path.join(WS, "工人家.nosync", "kimi", "不存在的文件")})],
                 [("Read", {"path": "~/.某个隐藏目录/x"})], [("Grep", {"pattern": "x", "path": "/etc"})],
                 [("Glob", {"pattern": "/tmp/a?c/*.txt"})]])
    check(G3, "禁区里的、家目录隐藏目录里的、/etc、带 ? 的 /tmp 通配：4 条都标 ‼",
          r.rc == 3 and r.out.count("‼ 严重越界") == 4, brief(r))


@test(G3)
def c_unknown(T, M):
    r = kimi(T, [[("Read", "{不是合法的 JSON")], [("Read", {"path": 123})], [("Read", {})],
                 [("Glob", {"pattern": "{/tmp,/etc}/**"})], [("Glob", {"pattern": "*/../../x"})],
                 [("Read", {"path": "a\u0000b"})], [("Glob", {"pattern": ["不是字符串"]})]])
    n = r.out.count("？ 没判出来")
    check(G3, "判不出来的 7 种写法（参数不是 JSON、路径不是字符串、没给路径、花括号开头、通配符后有 ..、路径带空字符、通配符不是字符串）：单列，不当成没事",
          n == 7, "列出 %d 条；%s" % (n, brief(r)))
    check(G3, "只有判不出来的：不中止、状态完成", r.rc == 0 and r.status == "完成" and not marked(r), brief(r))


@test(G3)
def c_multi(T, M):
    r = kimi(T, [[("Read", {"path": "a.txt"}), ("Read", {"path": OUTSIDE}), ("Read", {"path": "/tmp/x"})]])
    check(G3, "一条消息里 3 个调用：目录内的不列，另外两个都列",
          r.out.count("普通越界") == 1 and r.out.count("‼ 严重越界") == 1 and "越界记录 2 条" in r.out, brief(r))


@test(G3)
def c_flash(T, M):
    r = flash(T, [d_call("read", {"file_path": "a.txt"}), d_call("glob", {"pattern": "*.md"}),
                  d_call("glob", {"pattern": "**/*.png", "path": OUTSIDE_DIR}),
                  d_call("read_image", {"file_path": "/tmp/x.png"}), d_step()])
    check(G3, "flash 的读、读图、找文件：同样分类（普通 1、严重 1），状态「越界」",
          r.rc == 3 and r.status == "越界" and r.out.count("普通越界") == 1 and r.out.count("‼ 严重越界") == 1, brief(r))
    check(G3, "flash 正常跑完只是事后标记时：花费照实际用量记，不按上限记",
          r.rows and float(r.rows[-1]["估算花费元"]) < 1, str(r.rows))


@test(G3)
def c_timeout(T, M):
    r = kimi(T, [[("Read", {"path": "/tmp/x"})]], hang=30, timeout=2)
    check(G3, "先读了 /tmp 后来超时：状态记先发生的「超时」，越界照样标出",
          r.rc == 3 and r.status == "超时" and "‼ 严重越界" in r.out and r.secs < 15, brief(r))


DRIVER = r'''
import importlib.machinery, sys
m = importlib.machinery.SourceFileLoader("w", sys.argv[1]).load_module()
def boom(*a, **k):
    raise RuntimeError("故意出错")
m.read_audit = boom
sys.argv = ["waibao"] + sys.argv[2:]
sys.exit(m.main())
'''


@test(G3)
def c_broken(T, M):
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "测试任务"],
            {"events": [k_call(("Read", {"path": "a.txt"})), k_final(), K_META]}, driver=DRIVER)
    check(G3, "审计自己出错：回执写「越界审计没跑成」、退出码 3，结果和账本照常",
          r.rc == 3 and "越界审计没跑成" in r.out and r.status == "完成" and "假工人的回答" in r.out
          and r.rows and r.rows[-1]["状态"] == "完成" and r.files, brief(r))


# ---------- 丙-2：工具报警 ----------

G4 = "工具报警（用了不该有的工具当场杀）"


@test(G4)
def d_kimi_local(T, M):
    r = kimi(T, [[("WebSearch", {"query": "x"})]], hang=30)
    check(G4, "本地档案的工人调了搜索：没等它跑完就被杀（假工人赖着 30 秒不退）",
          r.secs < 15 and r.log and dead(r.log["pid"]), "用时 %.1f 秒；%s" % (r.secs, brief(r)))
    check(G4, "本地档案调了搜索：状态「越界」、退出码 3、回执写明是哪个工具、不打印回答",
          r.rc == 3 and r.status == "越界" and "‼ 不该有的工具 WebSearch" in r.out and "假工人的回答" not in r.out, brief(r))
    r = kimi(T, [[("Read", {"path": "a.txt"}), ("Bash", {"command": "ls"}), ("FetchURL", {"url": "https://example.com"})]],
             hang=30)
    check(G4, "一条消息里 3 个调用、2 个不该有：两个都记下再杀",
          r.secs < 15 and "不该有的工具 Bash" in r.out and "不该有的工具 FetchURL" in r.out, brief(r))
    r = kimi(T, [[(None, {"path": "a.txt"})]], hang=30)
    check(G4, "工具调用没有名字：按不该有的工具处理", r.secs < 15 and r.status == "越界", brief(r))
    r = kimi(T, [[("Write", {"path": "a.txt", "content": "x"})]], hang=30)
    check(G4, "本地只读档案调了 Write：被杀，状态「越界」",
          r.secs < 15 and r.rc == 3 and r.status == "越界" and "不该有的工具 Write" in r.out, brief(r))


@test(G4)
def d_kimi_web(T, M):
    r = kimi(T, [[("Read", {"path": "a.txt"})]], web=True, hang=30)
    check(G4, "联网档案的工人调了读文件：被杀，状态「越界」",
          r.secs < 15 and r.rc == 3 and r.status == "越界" and "‼ 不该有的工具 Read" in r.out, brief(r))
    r = kimi(T, [[("WebSearch", {"query": "a"})], [("FetchURL", {"url": "https://example.com/"})]], web=True)
    check(G4, "联网档案只用搜索和读网页：正常完成，没有越界记录",
          r.rc == 0 and r.status == "完成" and "越界记录" not in r.out and "本次联网 2 次" in r.out, brief(r))


@test(G4)
def d_flash(T, M):
    r = flash(T, [d_call("bash", {"command": "curl https://example.com", "description": "x"})], hang=30)
    check(G4, "flash 调了命令行：被杀，状态「越界」",
          r.secs < 15 and r.rc == 3 and r.status == "越界" and "‼ 不该有的工具 bash" in r.out, brief(r))
    check(G4, "flash 被中途杀掉：花费按已记到的用量记（2026-10-09 起没有单次上限可按）",
          r.rows and float(r.rows[-1]["估算花费元"]) < 0.01, str(r.rows))
    for tool in ("write", "edit", "web_search", "subagent"):
        r = flash(T, [d_call(tool, {"x": 1})], hang=30)
        check(G4, "flash 调了 %s：被杀" % tool, r.secs < 15 and r.status == "越界", brief(r))


# ---------- 健壮性 ----------

G5 = "出错保护和先后顺序"


@test(G5)
def e_alarm(T, M):
    flag, killed = {}, []
    orig = M._calls
    try:
        def boom(ev):
            raise RuntimeError("故意出错")
        M._calls = boom
        M._alarm({"x": 1}, ("Read",), killed.append, flag)
    finally:
        M._calls = orig
    check(G5, "工具报警自己出错：不往外抛、不乱杀，记一笔",
          not killed and len(flag.get("notes", [])) == 1 and "why" not in flag, str(flag))
    check(G5, "事件不是对象、工具调用不是列表、列表里混了别的东西：都不出错",
          M._calls([1, 2]) == [] and M._calls("x") == [] and M._calls(None) == []
          and M._calls({"tool_calls": "字符串"}) == [] and M._calls({"tool_calls": [1, None, "x"]}) == [])


@test(G5)
def e_first(T, M):
    p = subprocess.Popen(["/bin/sleep", "30"], start_new_session=True)
    try:
        flag = {}
        kill = M._killer(p, flag)
        kill("越界")
        kill("超时")
        p.wait(timeout=10)
        check(G5, "越界之后又到了超时：状态记先发生的「越界」", flag.get("why") == "越界", str(flag))
    finally:
        if p.poll() is None:
            p.kill()


@test(G5)
def e_noise(T, M):
    scen = {"events": ["这一行不是 JSON", k_call(("Read", "{坏的参数")), k_call(("Read", {"path": "a.txt"})), "",
                       k_final(), K_META]}
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "测试任务"], scen)
    check(G5, "输出里夹着不是 JSON 的行和坏参数：照常跑完", r.rc == 0 and r.status == "完成", brief(r))


# ---------- 原有功能 ----------

G6 = "原有功能不变"


def row(M, model, shell, money="0.0000"):
    r = dict((k, "") for k in M.FIELDS)
    r.update({"id": uuid.uuid4().hex[:12], "日期": datetime.date.today().isoformat(), "开始时间": "00:00:00",
              "模型": model, "壳": shell, "状态": "完成", "估算花费元": money, "pid": "0"})
    return r


@test(G6)
def f_basic(T, M):
    r = run(T, ["--status"])
    check(G6, "--status 能用", r.rc == 0 and "今天" in r.out, brief(r))
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "读 a.txt", "--dry-run"])
    check(G6, "--dry-run：检查通过，不启动工人，不记账", r.rc == 0 and "检查通过" in r.out and r.log is None and not r.rows,
          brief(r))
    for d, word in ((DENY, "禁区"), ("/tmp", "不在允许"), (os.path.join(FIELD, "没有这个目录"), "目录不存在"),
                    (WS, "包含禁区")):
        r = run(T, ["k28", "--dir", d] + BASE_ARGS + ["--task", "x"])
        check(G6, "目录检查：%s → 拒绝" % word, r.rc == 2 and word in r.err and r.log is None, brief(r))
    r = run(T, ["k28", "--dir", WORK, "--tag", "测试", "--task", "x"])
    check(G6, "缺 --plan → 拒绝", r.rc == 2 and "缺少 --plan" in r.err, brief(r))


@test(G6)
def f_budget(T, M):
    n = int(M._CFG["kimi_daily_calls"]["k28"])
    full = [row(M, "k28", "kimi") for _ in range(n)]
    # 2026-10-09 起：日上限只提醒，不拦
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--dry-run"], rows=full, fields=M.FIELDS)
    check(G6, "k28 当天次数用满，--dry-run：通过，提醒超过日上限", r.rc == 0 and "超过日上限" in r.out, brief(r))
    r = run(T, ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "x"], {"events": [k_final(), K_META]},
            rows=full, fields=M.FIELDS)
    check(G6, "k28 当天次数用满，真派：照跑、完成、派前和回执都提醒、账本照记",
          r.rc == 0 and r.status == "完成" and r.out.count("超过日上限") >= 2 and len(r.rows) == n + 1
          and r.out.index("超过日上限") < r.out.index("[waibao] k28 ·"), brief(r))
    r = run(T, ["k3", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--dry-run"], rows=full, fields=M.FIELDS)
    check(G6, "k28 用满不影响 k3：不提醒", r.rc == 0 and "日上限" not in r.out, brief(r))
    daily = float(M._CFG["deepseek_daily_cny"])
    over = [row(M, "flash", "dsh", "%.4f" % (daily + 0.5))]
    r = run(T, ["flash", "--dir", WORK] + BASE_ARGS + ["--task", "x"], {"events": [d_step(), d_final()]},
            rows=over, fields=M.FIELDS)
    check(G6, "DeepSeek 当天已超日上限，真派 flash：照跑、完成、提醒",
          r.rc == 0 and r.status == "完成" and "已到日上限" in r.out and "超过日上限" in r.out, brief(r))
    r = run(T, ["flash", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--dry-run"],
            rows=[row(M, "flash", "dsh", "%.4f" % (daily - 1))], fields=M.FIELDS)
    check(G6, "DeepSeek 没到日上限：不提醒", r.rc == 0 and "日上限" not in r.out, brief(r))
    r = run(T, ["--status"], rows=full, fields=M.FIELDS)
    check(G6, "--status：列出三个模型的用量和超长上限；刚好用满不算超、不提醒",
          r.rc == 0 and "今日 k28 %d / %d 次\n" % (n, n) in r.out and "今日 DeepSeek" in r.out
          and "今日 k3 0 /" in r.out and "超长上限" in r.out and "⚠" not in r.out, brief(r))
    r = run(T, ["--status"], rows=full + [row(M, "k28", "kimi")], fields=M.FIELDS)
    check(G6, "--status：超过日上限的那个带提醒", r.rc == 0 and "今日 k28 %d / %d 次  ⚠ 超过日上限" % (n + 1, n) in r.out,
          brief(r))


@test(G6)
def f_kill(T, M):
    r = kimi(T, [[("Read", {"path": "a.txt"})]], hang=30, timeout=2)
    check(G6, "超时：被杀，状态「超时」，退出码 3", r.rc == 3 and r.status == "超时" and r.secs < 15, brief(r))
    r = flash(T, [d_call("read", {"file_path": "a.txt"}), d_step(i=2000000, o=500000)])
    money = float(r.rows[-1]["估算花费元"]) if r.rows else -1
    check(G6, "flash 一次花费超过 ¥3：不再熔断，照常完成，按实际用量记账（2026-10-09）",
          r.rc == 0 and r.status == "完成" and 8.0 <= money < 8.01, "%.4f；%s" % (money, brief(r)))


G10 = "时限（超长上限只提醒）"


@test(G10)
def j_timeout(T, M):
    limit = int(M._CFG["timeout_sec"])
    r = run(T, ["k3", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--dry-run"])
    check(G10, "不给 --timeout：时限等于超长上限，不提醒", r.rc == 0 and "时限：%ds" % limit in r.out and "超长" not in r.out,
          brief(r))
    r = run(T, ["k3", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--dry-run", "--timeout", "18000"])
    check(G10, "--timeout 18000：照给的算，提醒超过超长上限",
          r.rc == 0 and "时限：18000s" in r.out and "超过超长上限 %d 秒" % limit in r.out, brief(r))
    r = run(T, ["k3", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--dry-run", "--timeout", "60"])
    check(G10, "--timeout 比上限短：照给的算，不提醒", r.rc == 0 and "时限：60s" in r.out and "超长" not in r.out, brief(r))
    r = run(T, ["k3", "--dir", WORK] + BASE_ARGS + ["--task", "x", "--timeout", "0"])
    check(G10, "--timeout 0：拒绝", r.rc == 2 and "大于 0" in r.err, brief(r))
    r = kimi(T, [[("Read", {"path": "a.txt"})]], timeout=limit + 100, model="k3")
    check(G10, "真派、时限超过上限：照跑、完成，派前和回执都提醒",
          r.rc == 0 and r.status == "完成" and r.out.count("超过超长上限") == 2, brief(r))


@test(G6)
def f_record(T, M):
    r = kimi(T, [[("Read", {"path": "a.txt"})]])
    body = ""
    if r.files:
        with open(r.files[0], encoding="utf-8") as f:
            body = f.read()
    check(G6, "正常完成：账本一行「完成」、结果文件有回答、原始记录留着",
          r.rows and r.rows[-1]["状态"] == "完成" and "假工人的回答" in body
          and glob.glob(os.path.join(r.dir, "结果", "原始", "*.jsonl")), brief(r))
    if os.path.exists(OLD) and os.path.realpath(T) != os.path.realpath(OLD):
        for name, new, old in (("kimi", kimi(T, [[("Read", {"path": "a.txt"})]]), kimi(OLD, [[("Read", {"path": "a.txt"})]])),
                               ("flash", flash(T, [d_call("read", {"file_path": "a.txt"})]),
                                flash(OLD, [d_call("read", {"file_path": "a.txt"})]))):
            def tail(x):
                return x.out.split("---", 1)[-1]
            check(G6, "同一个正常任务，新旧两版的状态、退出码、回执正文一样（%s）" % name,
                  (new.rc, new.status, tail(new)) == (old.rc, old.status, tail(old)), brief(new) + " ｜旧版 " + brief(old))


@test(G6)
def f_apply(T, M):
    lst = os.path.join(TMP, "清单.json")
    with open(lst, "w", encoding="utf-8") as f:
        json.dump({"ops": [{"op": "mkdir", "to": "新目录"}, {"op": "move", "from": "f.txt", "to": "新目录/f.txt"}]}, f,
                  ensure_ascii=False)
    r = run(T, ["apply", "--dir", TIDY, "--list", lst, "--dry-run"])
    check(G6, "apply --dry-run：校验通过，不动文件",
          r.rc == 0 and "校验通过" in r.out and os.path.exists(os.path.join(TIDY, "f.txt")), brief(r))
    r = run(T, ["apply", "--dir", TIDY, "--list", lst])
    moved = os.path.exists(os.path.join(TIDY, "新目录", "f.txt"))
    undo = glob.glob(os.path.join(r.dir, "结果", "撤销", "*.json"))
    check(G6, "apply：照清单执行", r.rc == 0 and moved and undo, brief(r))
    if undo:
        r = run(T, ["undo", undo[0]])
        check(G6, "undo：恢复原样",
              r.rc == 0 and os.path.exists(os.path.join(TIDY, "f.txt")) and not os.path.exists(os.path.join(TIDY, "新目录")),
              brief(r))
    bad = os.path.join(TMP, "坏清单.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump({"ops": [{"op": "move", "from": "f.txt", "to": "../逃出去.txt"}]}, f, ensure_ascii=False)
    r = run(T, ["apply", "--dir", TIDY, "--list", bad])
    check(G6, "apply：清单里有 .. → 整份拒绝", r.rc == 2, brief(r))


# ---------- 回放真实记录 ----------

G7 = "回放历史上的真实记录"


@test(G7)
def g_replay(T, M):
    raws = sorted(glob.glob(os.path.join(RAW_DIR, "*.jsonl"))) if RAW_DIR else []
    if not raws:
        g_replay.note = "跳过（没有给 QLIMEN_REPLAY_DIR，或那里没有记录）"
        return
    n = collections.Counter()
    errs, kinds = [], collections.Counter()
    bash_want, bash_got, calls_want, calls_got = set(), set(), 0, 0
    for raw in raws:
        name = os.path.basename(raw)
        is_flash = "-flash-" in name
        n["flash" if is_flash else "kimi"] += 1
        cwd = "/"
        try:
            with open(os.path.join(os.path.dirname(RAW_DIR), name[:-6] + ".md"), encoding="utf-8") as f:
                m = re.search(r"目录 (.+?) -->", f.readline())
                cwd = m.group(1) if m else "/"
        except OSError:
            pass
        with open(raw, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict):
                    continue
                if ev.get("type") == "tool_call":
                    calls_want += 1
                    if ev.get("tool") == "bash":
                        bash_want.add(name)
                calls_want += len(ev.get("tool_calls") or [])
                calls_got += len(M._calls(ev))
        try:
            lines, _ = M.read_audit(raw, cwd, M._CFG, M.PROFILES["dsh" if is_flash else "kimi"]["tools"])
        except Exception as e:
            errs.append("%s：%r" % (name, e))
            continue
        for l in lines:
            kinds[l[2:].split(" ")[0]] += 1
            if "不该有的工具 bash" in l:
                bash_got.add(name)
    check(G7, "线上结果目录里的原始记录（flash %d 份、kimi %d 份）逐份过审计：没有一份出错"
          % (n["flash"], n["kimi"]), raws and not errs, "；".join(errs[:3]) or "没有找到记录")
    check(G7, "取出的工具调用数和另一种数法一致（%d 次）" % calls_want, calls_got == calls_want and calls_want > 0,
          "取出 %d，应为 %d" % (calls_got, calls_want))
    check(G7, "历史上跑过命令行的 flash 记录（%d 份）全部被认出" % len(bash_want), bash_got == bash_want and bash_want,
          str(sorted(bash_got ^ bash_want)))
    g_replay.note = "审计结果里各类的行数：%s" % ("，".join("%s %d" % kv for kv in sorted(kinds.items())) or "无")


# ---------- 2026-10-06 小修：读输出出错不丢下工人 ----------

G8 = "读输出出错不丢下工人"
BAD_USAGE = {"type": "status", "phase": "step_end", "usage": {"inputTokens": "很多"}}
BAD_REASON = {"type": "status", "phase": "turn_end", "reason": "oops"}
LOCAL = ["--dir", WORK] + BASE_ARGS + ["--task", "测试任务"]


@test(G8)
def h_skip(T, M):
    r = run(T, ["k28"] + LOCAL, {"events": ["[1, 2]", "123", '"一个字符串"', "null", "true",
                                           k_call(("Read", {"path": "a.txt"})), k_final(), K_META]})
    check(G8, "kimi 的输出里夹着数组、数字、字符串、空值的行：照常跑完",
          r.rc == 0 and r.status == "完成" and "假工人的回答" in r.out, brief(r))
    r = run(T, ["flash"] + LOCAL, {"events": ["[1, 2]", "123", "null", d_call("read", {"file_path": "a.txt"}),
                                             d_step(), d_final()]})
    check(G8, "flash 的输出里夹着这些行：照常跑完", r.rc == 0 and r.status == "完成" and "假工人的回答" in r.out, brief(r))


@test(G8)
def h_crash(T, M):
    cases = (("flash", "用量不是数字", BAD_USAGE),
             ("flash", "用量是列表", {"type": "status", "phase": "step_end", "usage": {"inputTokens": [1]}}),
             ("flash", "结束原因不是对象", BAD_REASON),
             ("k28", "回答列表里的文字不是字符串", {"role": "assistant", "content": [{"text": 123}]}))
    for model, name, bad in cases:
        after = [d_step(), d_final()] if model == "flash" else [k_final(), K_META]
        r = run(T, [model] + LOCAL, {"events": [bad], "hang": 15, "after": after})
        check(G8, "%s，%s，假工人赖着不退：waibao 返回时工人已经被杀" % (model, name),
              r.secs < 10 and r.log and dead(r.log["pid"]), "用时 %.1f 秒；%s" % (r.secs, brief(r)))
        check(G8, "%s，%s：不是报错退出，状态「中断」、退出码 3、回执写明原因、结果文件写了" % (model, name),
              r.rc == 3 and "Traceback" not in r.err and r.status == "中断" and "读工人输出时出错" in r.out
              and r.files and r.rows and r.rows[-1]["状态"] == "中断", brief(r))


@test(G8)
def h_keep(T, M):
    r = run(T, ["flash"] + LOCAL, {"events": [d_step(100, 50), d_final(), BAD_REASON]})
    body = ""
    if r.files:
        with open(r.files[0], encoding="utf-8") as f:
            body = f.read()
    check(G8, "工人已经给出回答之后才出错：回答照样在结果文件和回执里，状态「中断」",
          r.rc == 3 and r.status == "中断" and "假工人的回答" in body and "假工人的回答" in r.out
          and "读工人输出时出错" in body, brief(r))
    money = float(r.rows[-1]["估算花费元"]) if r.rows else -1
    check(G8, "出错中断时 flash 的花费按已记到的用量记，不是 0 也不是上限",
          0 < money < 0.01 and r.rows[-1]["输入未缓存"] == "100" and r.rows[-1]["输出"] == "50", str(r.rows))
    both = json.dumps(d_step(2000000, 500000), ensure_ascii=False) + "\n" + json.dumps(BAD_REASON, ensure_ascii=False)
    r = run(T, ["flash"] + LOCAL, {"events": [both], "hang": 15, "after": [d_final()]})
    money = float(r.rows[-1]["估算花费元"]) if r.rows else -1
    check(G8, "大用量（¥8）紧接着出错：不熔断，状态「中断」，工人已被杀，花费照实记（2026-10-09 起没有熔断）",
          r.rc == 3 and r.status == "中断" and r.secs < 10 and r.log and dead(r.log["pid"]) and abs(money - 8.0) < 1e-6,
          brief(r))


# ---------- 2026-10-06 小修：联网审计不再弄丢结果 ----------

G9 = "联网审计不再弄丢结果"
WEB = ["k28", "--web", "--dir", WORK] + BASE_ARGS + ["--task", "测试任务"]


def bare(name, **fn):
    return {"role": "assistant", "tool_calls": [{"function": dict(fn, name=name)}]}


@test(G9)
def i_shapes(T, M):
    # 都用 --web 派：本地档案的工人一调搜索就会先被工具报警杀掉，走不到联网审计
    evs = [{"role": "assistant", "tool_calls": "字符串"}, {"role": "assistant", "tool_calls": [1, None]},
           k_call(("WebSearch", "[1, 2]")), bare("WebSearch", arguments={"query": "参数直接是对象"}),
           bare("WebSearch", arguments=123), bare("FetchURL"), k_call(("WebSearch", "{坏的 JSON")),
           k_call(("WebSearch", {"query": "正常的搜索"})), k_final(), K_META]
    r = run(T, WEB, {"events": evs})
    check(G9, "工具调用不是列表、列表里混了数字、参数是数组 / 对象 / 数字 / 缺失 / 坏的：照常跑完，结果文件在",
          r.rc == 0 and r.status == "完成" and r.files and "假工人的回答" in r.out, brief(r))
    want = ["  WebSearch: [1, 2]", "  WebSearch: 参数直接是对象", "  WebSearch: 123", "  FetchURL: ",
            "  WebSearch: {坏的 JSON", "  WebSearch: 正常的搜索"]
    got = [l for l in r.out.splitlines() if l.startswith(("  WebSearch", "  FetchURL", "‼ "))]
    check(G9, "能认的 6 条照常列出，认不出参数的把原文列出来", "本次联网 6 次" in r.out and got == want, str(got))


@test(G9)
def i_unit(T, M):
    p = os.path.join(TMP, "怪形状.jsonl")
    lines = ["[1, 2]", '"字符串"', json.dumps({"tool_calls": {"a": 1}}),
             json.dumps({"tool_calls": [{"function": "不是对象"}]}),
             json.dumps({"tool_calls": [{"function": {"name": ["列表"], "arguments": "{}"}}]}),
             json.dumps({"tool_calls": [{"function": {"name": "WebSearch", "arguments": "{\"query\": \"能认的\"}"}}]},
                        ensure_ascii=False)]
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    got = M.web_audit(p)
    check(G9, "直接喂更怪的形状（事件不是对象、函数不是对象、名字是列表）：不出错，能认的那条照常列出",
          got == ["  WebSearch: 能认的"], str(got))


@test(G9)
def i_broken(T, M):
    r = run(T, WEB, {"events": [k_call(("WebSearch", {"query": "x"})), k_final(), K_META]},
            driver=DRIVER.replace("m.read_audit", "m.web_audit"))
    check(G9, "联网审计自己出错：回执写「联网审计没跑成」、退出码 3，结果文件和账本照常",
          r.rc == 3 and "联网审计没跑成" in r.out and r.status == "完成" and "假工人的回答" in r.out
          and r.files and r.rows and r.rows[-1]["状态"] == "完成", brief(r))


@test(G9)
def i_same(T, M):
    if not os.path.exists(PREV) or os.path.realpath(PREV) == os.path.realpath(T):
        return
    long_q = "很长的搜索词" * 20
    long_u = "https://example.com/s?q=" + "a" * 120
    scen = {"events": [k_call(("WebSearch", {"query": "短的"})), k_call(("WebSearch", {"query": long_q})),
                       k_call(("FetchURL", {"url": "https://example.com/a?b=1"}), ("FetchURL", {"url": long_u})),
                       k_final(), K_META]}

    def part(x):
        i = x.out.find("[waibao] 本次联网")
        return x.out[i:] if i >= 0 else "（没有联网记录）" + x.out

    new, old = run(T, WEB, scen), run(PREV, WEB, scen)
    check(G9, "正常联网（含过长的搜索词和过长的网址参数）：回执里联网记录和正文，新版和小修前的线上版本逐字相同",
          new.rc == old.rc == 0 and part(new) == part(old) and len(re.findall(r"^‼ ", part(new), re.M)) == 2
          and "本次联网 4 次" in new.out,
          brief(new) + " ｜小修前 " + brief(old))


# ---------- 2026-10-09：写文件夹（--out） ----------

G11 = "写文件夹（--out）"


def kimi_out(T, calls, out, hang=0, touch=None):
    scen = {"events": [k_call(*c) for c in calls], "hang": hang, "after": [k_final(), K_META]}
    if touch:
        scen["touch"] = touch
    return run(T, ["k3", "--dir", WORK, "--out", out] + BASE_ARGS + ["--task", "测试任务"], scen)


@test(G11)
def k_refuse(T, M):
    out = os.path.join(WORK, "结果夹")
    r = run(T, ["flash", "--dir", WORK, "--out", out] + BASE_ARGS + ["--task", "x"])
    check(G11, "flash 加 --out：拒绝，不启动工人", r.rc == 2 and "不接受 --out" in r.err and r.log is None, brief(r))
    r = run(T, ["k28", "--web", "--out", out] + BASE_ARGS + ["--task", "x"])
    check(G11, "--out 和 --web 一起：拒绝", r.rc == 2 and "不能一起用" in r.err, brief(r))
    r = run(T, ["k3", "--dir", WORK, "--out", OUTSIDE_DIR] + BASE_ARGS + ["--task", "x"])
    check(G11, "--out 不在 --dir 里面：拒绝", r.rc == 2 and "必须在 --dir 里面" in r.err and r.log is None, brief(r))
    r = run(T, ["k3", "--dir", WORK, "--out", os.path.join(WORK, "链接出去")] + BASE_ARGS + ["--task", "x"])
    check(G11, "--out 是指到外面的软链接：拒绝", r.rc == 2 and "必须在 --dir 里面" in r.err, brief(r))
    r = run(T, ["k3", "--dir", WORK, "--out", os.path.join(WORK, "a.txt")] + BASE_ARGS + ["--task", "x"])
    check(G11, "--out 是个文件：拒绝", r.rc == 2 and "不是文件夹" in r.err, brief(r))
    new = os.path.join(WORK, "还没有的结果夹")
    try:
        r = run(T, ["k3", "--dir", WORK, "--out", new] + BASE_ARGS + ["--task", "x", "--dry-run"])
        check(G11, "--dry-run：写明档案和结果文件夹，不建文件夹",
              r.rc == 0 and "档案：本地读写" in r.out and "结果文件夹：" in r.out and not os.path.exists(new), brief(r))
        r = kimi_out(T, [[("Read", {"path": "a.txt"})]], new)
        check(G11, "真派、结果文件夹还没有：新建，照常完成，写了 0 个文件",
              r.rc == 0 and r.status == "完成" and os.path.isdir(new) and "写了 0 个文件" in r.out, brief(r))
    finally:
        shutil.rmtree(new, ignore_errors=True)


@test(G11)
def k_write(T, M):
    out = tempfile.mkdtemp(prefix="结果夹-", dir=WORK)
    bad_cwd, bad_far = os.path.join(WORK, "沙箱该挡的.txt"), os.path.join(OUTSIDE_DIR, "沙箱该挡的.txt")
    try:
        inside = os.path.join(out, "第1批.md")
        rel = os.path.relpath(os.path.join(out, "第2批.md"), WORK)
        r = kimi_out(T, [[("Read", {"path": "a.txt"})], [("Write", {"path": inside, "content": "x"})],
                         [("Write", {"path": rel, "content": "y"})]], out, touch=[inside, bad_cwd, bad_far])
        check(G11, "写进结果文件夹（绝对路径、相对路径）：完成，回执列出写了 2 个文件，没有越界记录",
              r.rc == 0 and r.status == "完成" and "写了 2 个文件" in r.out and "越界记录" not in r.out, brief(r))
        av = (r.log or {}).get("argv", [])
        check(G11, "用写文件档案，守则里的结果文件夹换成了真路径",
              "--agent-file" in av and av[av.index("--agent-file") + 1] == os.path.join(os.path.dirname(T), "工人-kimi-写.md")
              and out in r.log["text"] and "{结果文件夹}" not in r.log["text"], str(av))
        t = r.touch or {}
        check(G11, "系统沙箱：结果文件夹里写得进", t.get(inside) == "写成", str(t))
        check(G11, "系统沙箱：工作目录里、结果文件夹外写不进",
              str(t.get(bad_cwd, "")).startswith("被挡") and not os.path.exists(bad_cwd), str(t))
        check(G11, "系统沙箱：工作目录外写不进",
              str(t.get(bad_far, "")).startswith("被挡") and not os.path.exists(bad_far), str(t))
        check(G11, "账本说明里记了结果文件夹", r.rows and out in r.rows[-1]["说明"], str(r.rows))
        r = kimi_out(T, [[("Write", {"path": os.path.join(WORK, "sub", "新.md"), "content": "x"})]], out, hang=30)
        check(G11, "Write 写到结果文件夹以外（还在工作目录里）：当场停掉，状态「越界」，回执标 ‼",
              r.secs < 15 and r.rc == 3 and r.status == "越界" and "写到结果文件夹以外" in r.out and marked(r), brief(r))
        r = kimi_out(T, [[("Write", {"content": "没给路径"})]], out, hang=30)
        check(G11, "Write 没给路径：当场停掉", r.secs < 15 and r.status == "越界", brief(r))
        r = kimi_out(T, [[("Write", {"path": os.path.join(out, "..", "a.txt"), "content": "x"})]], out, hang=30)
        check(G11, "Write 用 .. 绕出结果文件夹：当场停掉", r.secs < 15 and r.status == "越界", brief(r))
        r = kimi_out(T, [[("Bash", {"command": "ls"})]], out, hang=30)
        check(G11, "写文件档案调了命令行：当场停掉",
              r.secs < 15 and r.status == "越界" and "不该有的工具 Bash" in r.out, brief(r))
    finally:
        shutil.rmtree(out, ignore_errors=True)
        for p in (bad_cwd, bad_far):
            if os.path.exists(p):
                os.remove(p)


# ---------- 2026-10-10：撤销记录先整份预审 ----------

G12 = "撤销记录先整份预审"


def snap(*paths):
    """这些路径下面现在有什么（软链接只记本身，不跟进去），用来比对撤销前后有没有动过。"""
    out = []
    for top in paths:
        if not os.path.lexists(top):
            out.append((top, None))
        elif os.path.islink(top) or not os.path.isdir(top):
            out.append((top, "文件"))
        else:
            for d, dirs, files in os.walk(top):
                out.append((d, tuple(sorted(dirs + files))))
    return out


def undo_rec(T, rec, name):
    """把一份（可能被改坏的）撤销记录写进临时文件，再跑 waibao undo。"""
    p = os.path.join(TMP, "撤销记录-%s.json" % name)
    with open(p, "w", encoding="utf-8") as f:
        if isinstance(rec, str):
            f.write(rec)
        else:
            json.dump(rec, f, ensure_ascii=False)
    return run(T, ["undo", p]), p


def apply_list(T, root, ops, name):
    lst = os.path.join(TMP, "清单-%s.json" % name)
    with open(lst, "w", encoding="utf-8") as f:
        json.dump({"ops": ops}, f, ensure_ascii=False)
    r = run(T, ["apply", "--dir", root, "--list", lst])
    return r, sorted(glob.glob(os.path.join(r.dir, "结果", "撤销", "*.json")))


@test(G12)
def l_undo_review(T, M):
    pre = os.path.join(FIELD, "撤销预审")
    root, outer, lnk = os.path.join(pre, "根"), os.path.join(pre, "外面"), os.path.join(pre, "根的链接")
    adp = os.path.join(os.path.dirname(os.path.dirname(DENY)), "adapters", "dsh")   # 里面有保护清单里的 dsh-flash.patch.yml
    patch, renamed = os.path.join(adp, "dsh-flash.patch.yml"), os.path.join(adp, "改名.yml")
    far = tempfile.mkdtemp(prefix="范围外-", dir=TMP)                            # 系统临时目录：不在 allow_roots 里
    deny_dir = os.path.join(DENY, "撤销预审-" + uuid.uuid4().hex[:6])
    adp_dir = os.path.join(adp, "撤销预审空目录-" + uuid.uuid4().hex[:6])
    shutil.rmtree(pre, ignore_errors=True)
    try:
        for d in (root, outer, deny_dir, adp_dir):
            os.makedirs(d)
        for p in (os.path.join(root, "f.txt"), os.path.join(root, "g.txt"), os.path.join(outer, "v.txt")):
            with open(p, "w", encoding="utf-8") as f:
                f.write("撤销预审测试用\n")
        os.symlink(os.path.join("..", "外面"), os.path.join(root, "链接"))
        os.symlink("根", lnk)
        r, recs = apply_list(T, root, [{"op": "mkdir", "to": "A"}, {"op": "move", "from": "f.txt", "to": "A/f.txt"},
                                       {"op": "rename", "from": "g.txt", "to": "h.txt"}], "预审")
        check(G12, "先 apply 一份清单，拿到正常的撤销记录", r.rc == 0 and len(recs) == 1, brief(r))
        if not recs:
            return
        with open(recs[0], encoding="utf-8") as f:
            good = json.load(f)
        ok, R = good["done"], good["root"]      # R：记录里的根目录（apply 存的是解析过软链接的真实路径）

        def via(p):
            return lnk + p[len(R):]
        j = os.path.join
        # 坏的那条放在最前面：撤销是倒着来的，没有预审的话，后面那几条正常的会先被执行
        cases = [
            ("from/to 用 .. 跑到根目录外", "不许含",
             {"root": R, "done": [{"op": "move", "from": j(R, "..", "外面", "v2.txt"),
                                      "to": j(R, "..", "外面", "v.txt")}] + ok}),
            ("绝对路径在 allow_roots 外", "不在记录的根目录",
             {"root": R, "done": [{"op": "mkdir", "to": far}] + ok}),
            ("绝对路径指进禁区", "不在记录的根目录",
             {"root": R, "done": [{"op": "mkdir", "to": deny_dir}] + ok}),
            ("经根目录里的软链接出去", "软链接",
             {"root": R, "done": [{"op": "move", "from": j(R, "链接", "v2.txt"), "to": j(R, "链接", "v.txt")}]
              + ok}),
            ("op 是 delete", "不是 apply 记下的操作",
             {"root": R, "done": [{"op": "delete", "to": j(R, "h.txt")}] + ok}),
            ("条目多出字段", "不是 apply 记下的操作",
             {"root": R, "done": [{"op": "mkdir", "to": j(R, "A"), "force": True}] + ok}),
            ("记录多出顶层字段", "结构不对", dict(good, extra=1)),
            ("根目录在禁区", "禁区", {"root": DENY, "done": [{"op": "mkdir", "to": deny_dir}]}),
            ("根目录在 allow_roots 外", "不在允许外发的范围内",
             {"root": os.path.dirname(far), "done": [{"op": "mkdir", "to": far}]}),
            ("根目录经过软链接", "软链接",
             {"root": lnk, "done": [dict((k, via(v) if k != "op" else v) for k, v in t.items()) for t in ok]}),
            ("碰保护清单里的文件", "受保护",
             {"root": adp, "done": [{"op": "rename", "from": renamed, "to": patch}, {"op": "mkdir", "to": adp_dir}]}),
            ("不是 JSON", "读不出来", "这不是撤销记录"),
        ]
        for i, (name, word, rec) in enumerate(cases, 1):
            before = snap(pre, far, deny_dir, adp_dir, patch, renamed)
            r, p = undo_rec(T, rec, "坏%d" % i)
            after = snap(pre, far, deny_dir, adp_dir, patch, renamed)
            check(G12, "被改坏的撤销记录（%s）：整份拒绝，一条不执行" % name,
                  r.rc == 2 and "拒绝" in r.err and word in r.err and before == after
                  and "预审通过" not in r.out and not os.path.exists(p + ".已撤销"),
                  brief(r) + ("" if before == after else " ｜文件被动过"))
        r = run(T, ["undo", recs[0]])
        check(G12, "正常的撤销记录：预审通过，照常恢复，记录改名 .已撤销",
              r.rc == 0 and "预审通过" in r.out and "已撤销 3 条" in r.out
              and os.path.exists(j(root, "f.txt")) and os.path.exists(j(root, "g.txt"))
              and not os.path.exists(j(root, "A")) and not os.path.exists(j(root, "h.txt"))
              and os.path.exists(recs[0] + ".已撤销") and os.path.exists(j(outer, "v.txt")), brief(r))

        # 撤到半路停下：照旧停在那里、后面的不执行，并在记录旁写明撤到了哪一步
        r, recs = apply_list(T, root, [{"op": "mkdir", "to": "B"}, {"op": "move", "from": "f.txt", "to": "B/f.txt"},
                                       {"op": "mkdir", "to": "C"}, {"op": "move", "from": "g.txt", "to": "C/g.txt"}],
                             "半路")
        if r.rc != 0 or len(recs) != 1:
            check(G12, "撤到半路停下（准备）", False, brief(r))
            return
        with open(j(root, "C", "挡路.txt"), "w", encoding="utf-8") as f:
            f.write("让 rmdir C 失败\n")
        r = run(T, ["undo", recs[0]])
        note = recs[0] + ".撤销进度.txt"
        text = ""
        if os.path.exists(note):
            with open(note, encoding="utf-8") as f:
                text = f.read()
        check(G12, "撤到半路停下：退出码 3，停在第 3 条，前面的第 2、1 条没执行，记录不改名",
              r.rc == 3 and "已撤销 1 条" in r.err and "后面的没执行" in r.err
              and os.path.exists(j(root, "g.txt")) and os.path.isdir(j(root, "C"))
              and os.path.exists(j(root, "B", "f.txt")) and not os.path.exists(recs[0] + ".已撤销"), brief(r))
        check(G12, "撤到半路停下：记录旁的进度文件写明已撤销第 4 条、停在第 3 条、第 2 条到第 1 条没执行",
              "已撤销：第 4 条 move" in text and "停在：第 3 条 mkdir" in text and "没执行：第 2 条到第 1 条" in text
              and note in r.err, repr(text[:300]))
        r = run(T, ["undo", recs[0]])
        text2 = ""
        if os.path.exists(note):
            with open(note, encoding="utf-8") as f:
                text2 = f.read()
        check(G12, "再跑一遍：停在已撤销过的第 4 条，进度文件追加一段、前一段还在",
              r.rc == 3 and text2.startswith(text) and "停在：第 4 条 move" in text2[len(text):]
              and os.path.exists(j(root, "g.txt")) and os.path.exists(j(root, "B", "f.txt")), brief(r))
    finally:
        if os.path.exists(renamed) and not os.path.exists(patch):
            os.rename(renamed, patch)
        for d in (deny_dir, adp_dir):
            if os.path.isdir(d):
                os.rmdir(d)
        shutil.rmtree(pre, ignore_errors=True)


@test(G12)
def l_protected_via_link(T, M):
    """保护清单里写的路径经过软链接时，apply / undo 共用的逐级检查也要认得出（沙盒放在 /tmp 下时曾漏掉）。"""
    fake, real = os.path.join(TMP, "假Qlimen"), os.path.join(TMP, "真目录")
    via = os.path.join(TMP, "链到真目录")
    for d in (os.path.join(fake, "core", "workers"), os.path.join(fake, "core", "bin"), real):
        os.makedirs(d)
    os.symlink(real, via)
    with open(os.path.join(real, "受保护.txt"), "w", encoding="utf-8") as f:
        f.write("x\n")
    with open(os.path.join(fake, "core", "bin", "保护清单.txt"), "w", encoding="utf-8") as f:
        f.write(os.path.join(via, "受保护.txt") + "\n")       # 系统临时目录本身也经过软链接（/var → /private/var）
    old, M.BASE = M.BASE, os.path.join(fake, "core", "workers")
    try:
        M.Plan(os.path.realpath(real), M._CFG).path("受保护.txt", 1, "to")
        got = "没拦"
    except M.Refused as e:
        got = str(e)
    finally:
        M.BASE = old
    check(G12, "保护清单里的路径经过软链接：apply / undo 的逐级检查照样认出受保护文件", "受保护" in got, got)


# ---------- 2026-10-10：同一秒的结果文件、原始记录、撤销记录不互相覆盖 ----------

G13 = "同一秒的文件不互相覆盖"

# 把时间钉在同一秒，在一个进程里按 "::" 分段连跑几次 waibao；"#撞名" 那行可以换成下面的 COLLIDE
SAME_SECOND = r'''
import datetime, importlib.machinery, sys, types
m = importlib.machinery.SourceFileLoader("w", sys.argv[1]).load_module()
fixed = datetime.datetime.now().replace(microsecond=0)


class Frozen(datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return fixed


m.datetime = types.SimpleNamespace(datetime=Frozen, date=datetime.date)
#撞名
groups = [[]]
for x in sys.argv[2:]:
    if x == "::":
        groups.append([])
    else:
        groups[-1].append(x)
rc = 0
for g in groups:
    sys.argv = ["waibao"] + g
    try:
        rc = max(rc, m.main() or 0)
    except m.Refused as e:
        print("[waibao] 拒绝：%s" % e, file=sys.stderr)
        rc = max(rc, 2)
sys.exit(rc)
'''
# 前 4 次取随机串给同一个开头 aaaaaa（后面正常），逼第二次撞上第一次的文件名，看独占创建会不会换名
COLLIDE = r'''
n = [0]


def uuid4():
    n[0] += 1
    head = "aaaaaa" if n[0] <= 4 else "%06x" % n[0]
    return types.SimpleNamespace(hex=head + "%06x" % n[0] + "0" * 20)


m.uuid = types.SimpleNamespace(uuid4=uuid4)
'''


@test(G13)
def m_same_second(T, M):
    scen = {"events": [k_call(("Read", {"path": "a.txt"}))], "after": [k_final(), K_META]}
    one = ["k28", "--dir", WORK] + BASE_ARGS + ["--task", "测试任务"]
    pat = re.compile(r"^(\d{8}-\d{6})-[0-9a-f]{6}-k28-测试\.(md|jsonl)$")
    for how, driver in (("随机串照常", SAME_SECOND), ("逼出同一个随机串", SAME_SECOND.replace("#撞名", COLLIDE))):
        r = run(T, one + ["::"] + one, scen, driver=driver)
        raws = sorted(glob.glob(os.path.join(r.dir, "结果", "原始", "*.jsonl")))
        names = [os.path.basename(p) for p in r.files + raws]
        stamps = set(x.group(1) for x in map(pat.match, names) if x)
        check(G13, "同一秒、同模型、同标签连派两次（%s）：两份结果文件、两份原始记录，名字是时间戳加六位随机串" % how,
              r.rc == 0 and len(r.files) == 2 and len(raws) == 2 and len(stamps) == 1
              and all(pat.match(x) for x in names), brief(r) + " ｜" + str(names))
        bodies = []
        for p in r.files:
            with open(p, encoding="utf-8") as f:
                bodies.append(f.read())
        check(G13, "……两份结果文件都有完整回答，账本两行的「结果文件」是这两份的完整路径（%s）" % how,
              len(bodies) == 2 and all("假工人的回答" in b for b in bodies) and len(r.rows) == 2
              and sorted(x["结果文件"] for x in r.rows) == r.files and all(os.path.isabs(x["结果文件"]) for x in r.rows),
              str([x.get("结果文件") for x in r.rows]))
    base = os.path.join(FIELD, "同秒apply")
    shutil.rmtree(base, ignore_errors=True)
    os.makedirs(base)
    try:
        lists = []
        for name in ("甲", "乙"):
            lst = os.path.join(TMP, "同秒清单-%s.json" % name)
            with open(lst, "w", encoding="utf-8") as f:
                json.dump({"ops": [{"op": "mkdir", "to": name}]}, f, ensure_ascii=False)
            lists.append(lst)
        r = run(T, ["apply", "--dir", base, "--list", lists[0], "::", "apply", "--dir", base, "--list", lists[1]],
                driver=SAME_SECOND.replace("#撞名", COLLIDE))
        recs = sorted(glob.glob(os.path.join(r.dir, "结果", "撤销", "*.json")))
        tos = []
        for p in recs:
            with open(p, encoding="utf-8") as f:
                tos.append([t["to"] for t in json.load(f)["done"]])
        want = sorted([os.path.join(os.path.realpath(base), x)] for x in ("甲", "乙"))    # 记录里存的是真实路径
        check(G13, "同一秒连跑两次 apply（逼出同一个随机串）：两份撤销记录，各记各的",
              r.rc == 0 and len(recs) == 2 and sorted(tos) == want
              and all(re.match(r"^\d{8}-\d{6}-[0-9a-f]{6}-apply\.json$", os.path.basename(p)) for p in recs),
              brief(r) + " ｜" + str([os.path.basename(p) for p in recs]))
    finally:
        shutil.rmtree(base, ignore_errors=True)


# ---------- 入口 ----------

def main():
    global TMP
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    ap.add_argument("--report-to", help="把结果写到指定文件（别的任务用这个，免得盖掉工人加固的测试结果）")
    a = ap.parse_args()
    global DENY
    target = os.path.realpath(a.target)
    DENY = os.path.join(os.path.dirname(os.path.dirname(target)), "data")
    if not WS:
        sys.exit("缺环境变量 QLIMEN_TEST_WS：请用 tests/跑全部测试.py 运行")
    sys.dont_write_bytecode = True      # 别在被测脚本旁边留下缓存目录（线上那个目录是加锁的）
    TMP = tempfile.mkdtemp(prefix="waibao-测试-")
    try:
        setup_field()
        os.environ.update(WAIBAO_DSH=FAKE, WAIBAO_KIMI=FAKE, WAIBAO_LEDGER=os.path.join(TMP, "模块账本.csv"),
                          WAIBAO_OUT=os.path.join(TMP, "模块结果"), WAIBAO_KIMI_SESSIONS=os.path.join(TMP, "模块会话"))
        mod = importlib.machinery.SourceFileLoader("waibao_under_test", target).load_module()
        for group, fn in TESTS:
            try:
                fn(target, mod)
            except Exception as e:
                check(group, "%s（测试没跑完）" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    ok = sum(1 for r in RES if r[2])
    bad = len(RES) - ok
    out = ["# 测试结果（零花费：假工人 + 临时账本，没有调用任何模型）", "",
           "- 时间：%s" % datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "- 被测：`%s`" % target,
           "- 通过 %d，失败 %d" % (ok, bad)]
    note = getattr(g_replay, "note", "")
    if note:
        out.append("- 回放：%s" % note)
    last = None
    for group, name, good, detail in RES:
        if group != last:
            out += ["", "## %s" % group, ""]
            last = group
        out.append("- %s %s%s" % ("✓" if good else "✗", name, "" if good else "\n  - " + detail))
    text = "\n".join(out) + "\n"
    print(text)
    if a.report_to:
        with open(a.report_to, "w", encoding="utf-8") as f:
            f.write(text)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
