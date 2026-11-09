#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""假工人：按剧本吐事件，不调任何模型。给 waibao 的零花费测试用。

waibao 用环境变量 WAIBAO_DSH / WAIBAO_KIMI 把它当成壳来启动。
剧本是 FAKE_SCENARIO 指向的 JSON：{"events": [...], "hang": 秒, "after": [...], "exit": 0, "touch": [路径]}
events 里的字典按一行 JSON 吐出，字符串原样吐出（用来造畸形行）。
hang 是吐完以后赖着不退出的秒数；没被杀掉的话再吐 after。
touch（2026-10-09 加，测系统沙箱）：吐事件之前，挨个往这些路径写一个文件，结果（写成 / 被挡）记进 FAKE_LOG + ".touch.json"。
wire（2026-11-03 加，测按派工编号补记用量）：当 kimi 时，吐事件之前像 kimi 那样在 WAIBAO_KIMI_SESSIONS 下写一份会话记录，
开头是任务文字，后面每个字典是一条 usage.record。
"""
import json
import os
import sys
import time

argv = sys.argv[1:]
as_dsh = argv[:1] == ["headless"]
if as_dsh:
    text = sys.stdin.read()
else:
    text = argv[argv.index("-p") + 1] if "-p" in argv else ""
    argv = ["<任务>" if x == text else x for x in argv]

log = os.environ.get("FAKE_LOG")
if log:
    with open(log, "w", encoding="utf-8") as f:
        json.dump({"as": "dsh" if as_dsh else "kimi", "argv": argv, "cwd": os.getcwd(), "text": text,
                   "pid": os.getpid(),
                   "env": {k: os.environ.get(k) for k in ("DSH_PERMISSION_MODE", "DSH_HOME", "KIMI_CODE_HOME")}},
                  f, ensure_ascii=False)

with open(os.environ["FAKE_SCENARIO"], encoding="utf-8") as f:
    spec = json.load(f)

if spec.get("touch") and log:
    got = {}
    for p in spec["touch"]:
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write("假工人写的\n")
            got[p] = "写成"
        except OSError as e:
            got[p] = "被挡：%s" % e.strerror
    with open(log + ".touch.json", "w", encoding="utf-8") as f:
        json.dump(got, f, ensure_ascii=False)


if spec.get("wire") is not None and not as_dsh:
    import uuid
    d = os.path.join(os.environ["WAIBAO_KIMI_SESSIONS"], "wd_假", "session_" + uuid.uuid4().hex, "agents", "main")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "wire.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "turn.prompt", "input": [{"type": "text", "text": text}]}, ensure_ascii=False) + "\n")
        for g in spec["wire"]:
            f.write(json.dumps({"type": "usage.record", "usage": g}) + "\n")


def emit(evs):
    for ev in evs:
        sys.stdout.write((ev if isinstance(ev, str) else json.dumps(ev, ensure_ascii=False)) + "\n")
        sys.stdout.flush()


emit(spec.get("events", []))
if spec.get("hang"):
    time.sleep(spec["hang"])
emit(spec.get("after", []))
sys.exit(spec.get("exit", 0))
