#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""统计.py 的自测：在系统临时目录里造几份假的 Claude Code / Codex / DSH 会话记录，跑一遍统计，核对数字和隐私。

不读你真实的会话记录，不联网，跑完删掉临时目录。DSH 那部分要 Node.js 22.15 以上（用它压 zstd），没有就跳过并说明。
用法：python3 tools/统计/自测.py      退出码：全过 0，否则 1。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '统计.py')
PY = sys.executable or '/usr/bin/python3'
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)
import 统计 as T  # noqa: E402

SECRET = 'SECRET_MARK'
RESULTS = []


def check(name, ok, info=''):
    RESULTS.append(ok)
    print(('  ✓ ' if ok else '  ✗ ') + name + ('' if ok or not info else '\n      ' + info))


def jl(path, recs):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + '\n')


def cu(uuid, ts, content, **kw):
    r = {'type': 'user', 'uuid': uuid, 'timestamp': ts, 'message': {'role': 'user', 'content': content}}
    r.update(kw)
    return r


def ca(uuid, ts, mid, blocks):
    return {'type': 'assistant', 'uuid': uuid, 'timestamp': ts, 'message': {'id': mid, 'role': 'assistant', 'content': blocks}}


def tool(name, **inp):
    return {'type': 'tool_use', 'name': name, 'input': inp}


def text(s):
    return {'type': 'text', 'text': s}


def make_claude(root):
    proj = os.path.join(root, 'projects', '-fake-proj')
    s1 = [
        {'type': 'queue-operation', 'timestamp': '2000-01-10T00:00:00Z'},
        cu('u1', '2000-01-10T00:00:01Z', '请帮我整理 %s 资料' % SECRET),
        ca('a1', '2000-01-10T00:00:02Z', 'm1', [text('【进行中】第 1/3 步：先看目录，预计 2 分钟。'), tool('Read', file_path='/fake/%s/a' % SECRET)]),
        cu('r1', '2000-01-10T00:00:03Z', [{'type': 'tool_result', 'content': 'x'}]),
        ca('a2', '2000-01-10T00:00:04Z', 'm2', [tool('Read', file_path='/fake/b')]),
        ca('a3', '2000-01-10T00:00:05Z', 'm2', [tool('Bash', command="cat >> /fake/任务-%s/TODO.md <<'X'\n- [x] 1\nX" % SECRET)]),
        cu('u2', '2000-01-10T00:00:06Z', T.NOTIFY_TAG + '>后台结束'),
        ca('a4', '2000-01-10T00:00:07Z', 'm3', [tool('Read', file_path='/fake/c'), tool('Write', file_path='/fake/任务-%s/方案.md' % SECRET, content='x')]),
        ca('a5', '2000-01-10T00:10:00Z', 'm4', [text('【已完成】已核实 A，未验证 B。\n现在在等你回复，后台没有任务在跑。')]),
    ]
    jl(os.path.join(proj, 'fakesess-0001.jsonl'), s1)
    # 续接会话：开头是新记录，接着整段复制会话 1（同 uuid），再接一个新回合。复制的部分不能重复计。
    s3 = [{'type': 'queue-operation', 'timestamp': '2000-01-11T00:00:00Z'}] + s1[1:] + [
        cu('u9', '2000-01-11T00:01:00Z', '接着做 %s' % SECRET),
        ca('a9', '2000-01-11T00:01:01Z', 'm5', [text('【进行中】好的。')]),
    ]
    jl(os.path.join(proj, 'fakesess-0003.jsonl'), s3)
    s2 = [
        cu('v1', '2000-02-10T00:00:00Z', '第二个任务 %s' % SECRET),
        ca('b1', '2000-02-10T00:00:01Z', 'm6', [text('看一下。'), tool('Read', file_path='/fake/d')]),
        {'type': 'system', 'subtype': 'compact_boundary', 'uuid': 'c1', 'timestamp': '2000-02-10T00:00:02Z'},
        cu('v2', '2000-02-10T00:00:03Z', '继续'),
        ca('b2', '2000-02-10T01:00:00Z', 'm7', [tool('Grep', pattern='x') for _ in range(6)]),
        cu('v3', '2000-03-05T00:00:00Z', '截止以后的话 %s' % SECRET),   # 截止之后，不计
        ca('b3', '2000-03-05T00:00:01Z', 'm8', [tool('Read', file_path='/fake/e') for _ in range(5)]),
    ]
    jl(os.path.join(proj, 'fakesess-0002.jsonl'), s2)
    # 子代理记录放在 <会话>/subagents/ 下，不计
    jl(os.path.join(proj, 'fakesess-0002', 'subagents', 'agent-1.jsonl'), [
        cu('w1', '2000-02-10T00:00:05Z', '子代理任务 %s' % SECRET),
        ca('w2', '2000-02-10T00:00:06Z', 'm9', [tool('Read', file_path='/fake/f') for _ in range(5)]),
    ])
    return os.path.join(root, 'projects')


def make_codex(root):
    base = os.path.join(root, 'codex', '2000', '01', '05')

    def item(ts, payload):
        return {'type': 'response_item', 'timestamp': ts, 'payload': payload}

    def msg(ts, role, s):
        return item(ts, {'type': 'message', 'role': role, 'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': s}]})
    calls = [item('2000-01-05T02:00:0%dZ' % i, {'type': 'function_call', 'name': 'exec_command', 'arguments': '{"cmd": "ls"}'}) for i in range(2, 6)]
    patch = item('2000-01-05T02:00:07Z', {'type': 'custom_tool_call', 'name': 'apply_patch',
                                          'input': '*** Begin Patch\n*** Update File: /fake/codex-%s/交接.md\n+x\n*** End Patch' % SECRET})
    jl(os.path.join(base, 'rollout-2000-01-05T10-00-00-fakecodex-main.jsonl'), [
        {'type': 'session_meta', 'timestamp': '2000-01-05T02:00:00Z', 'payload': {'source': 'cli'}},
        msg('2000-01-05T02:00:00Z', 'user', '<environment_context>x</environment_context>'),
        msg('2000-01-05T02:00:01Z', 'user', '整理 %s' % SECRET),
        msg('2000-01-05T02:00:01Z', 'assistant', '【进行中】第 1/2 步：改交接。'),
    ] + calls + [patch, msg('2000-01-05T02:00:08Z', 'assistant', '【已完成】改好了。在等你确认。')])
    jl(os.path.join(base, 'rollout-2000-01-05T11-00-00-fakecodex-sub.jsonl'), [
        {'type': 'session_meta', 'timestamp': '2000-01-05T03:00:00Z', 'payload': {'source': {'subagent': 'review'}}},
        msg('2000-01-05T03:00:01Z', 'user', '审查 %s' % SECRET),
    ])
    jl(os.path.join(root, 'codex', '2000', '02', '05', 'rollout-2000-02-05T10-00-00-fakecodex-exec.jsonl'), [
        {'type': 'session_meta', 'timestamp': '2000-02-05T02:00:00Z', 'payload': {'source': 'exec'}},
        msg('2000-02-05T02:00:01Z', 'user', '派的活 %s' % SECRET),
        msg('2000-02-05T02:00:02Z', 'assistant', '做完了。'),
    ])
    return os.path.join(root, 'codex')


def make_dsh(root, node):
    ms = 949536000000   # 2000-02-03T00:00:00Z
    sess = {
        'session-dsh0001': [
            {'type': 'request/header', 'time': ms, 'data': {'header': {'config': {'model': 'fake-model'}}}},
            {'type': 'user/message', 'time': ms + 1000, 'data': {'source': {'kind': 'user'}, 'content': [{'type': 'text', 'text': '整理 %s' % SECRET}]}},
            {'type': 'assistant/message', 'time': ms + 2000, 'data': {'message': {'content': [{'type': 'text', 'text': '【进行中】第 1 步，预计 3 分钟。'}]}}},
            {'type': 'tool/call', 'time': ms + 3000, 'data': {'name': 'read', 'arguments': '{"file_path": "/fake/x"}'}},
            {'type': 'tool/call', 'time': ms + 4000, 'data': {'name': 'read', 'arguments': '{"file_path": "/fake/y"}'}},
            {'type': 'tool/call', 'time': ms + 5000, 'data': {'name': 'Edit', 'arguments': json.dumps({'path': '/fake/dsh-%s/TODO.md' % SECRET})}},
            {'type': 'tool/call', 'time': ms + 6000, 'data': {'name': 'bash', 'arguments': '{"command": "ls"}'}},
            {'type': 'tool/call', 'time': ms + 7000, 'data': {'name': 'write', 'arguments': json.dumps({'file_path': '/fake/dsh-%s/交接.md' % SECRET})}},
            {'type': 'assistant/message', 'time': ms + 8000, 'data': {'message': {'content': [{'type': 'text', 'text': '后台还在跑，跑完会通知我。'}]}}},
        ],
        'session-dsh0002': [
            {'type': 'request/header', 'time': ms, 'data': {'header': {'config': {'model': 'fake-model'}}}},
            {'type': 'user/message', 'time': ms + 1000, 'data': {'source': {'kind': 'user'}, 'content': [{'type': 'text', 'text': T.WORKER_HEAD + '你是工人 %s' % SECRET}]}},
            {'type': 'tool/call', 'time': ms + 2000, 'data': {'name': 'read', 'arguments': '{}'}},
        ],
    }
    js = ("const fs=require('node:fs'),z=require('node:zlib');"
          "const L=fs.readFileSync(process.argv[1],'utf8').split('\\n').filter(Boolean);const h=Math.ceil(L.length/2);"
          "fs.writeFileSync(process.argv[2],Buffer.concat([z.zstdCompressSync(Buffer.from(L.slice(0,h).join('\\n')+'\\n')),"
          "z.zstdCompressSync(Buffer.from(L.slice(h).join('\\n')+'\\n'))]));")
    for name, recs in sess.items():
        d = os.path.join(root, 'dsh', 'fake-proj', name)
        raw = os.path.join(root, name + '.jsonl')
        jl(raw, recs)
        os.makedirs(d, exist_ok=True)
        subprocess.run([node, '-e', js, raw, os.path.join(d, 'session.v4.jsonl.zstd')], check=True)   # 两个 zstd 帧首尾相接，和 DSH 一样
    return os.path.join(root, 'dsh')


def run(args):
    r = subprocess.run([PY, '-B', SCRIPT] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.returncode, r.stdout.decode('utf-8', 'replace'), r.stderr.decode('utf-8', 'replace')


def row(out, label):
    for line in out.splitlines():
        if line.startswith('| %s |' % label) or line.startswith('| %s † |' % label):
            return [c.strip() for c in line.strip().strip('|').split('|')]
    return None


def section(out, title):
    s = out.split('## ' + title, 1)
    return s[1].split('\n## ', 1)[0] if len(s) > 1 else ''


def main():
    tmp = tempfile.mkdtemp(prefix='qlimen-统计自测-')
    try:
        print('造假数据……')
        claude = make_claude(tmp)
        codex = make_codex(tmp)
        node, why = T.find_node(None)
        dsh_args = ['--no-dsh']
        if node:
            dsh_args = ['--dsh', make_dsh(tmp, node)]
        else:
            print('  （跳过 DSH 部分：%s）' % why)
        cfg = os.path.join(tmp, '阶段.json')
        with open(cfg, 'w', encoding='utf-8') as fh:
            json.dump({'截止': '2000-03-01T00:00:00Z', '环境': {
                'claude': [{'名称': '旧', '起': None}, {'名称': '新', '起': '2000-02-01T00:00:00Z'}],
                'dsh': [{'名称': '全段', '起': '2000-01-01T00:00:00Z'}],
                'codex': [{'名称': '旧', '起': '2000-01-01T00:00:00Z'}, {'名称': '新', '起': '2000-02-01T00:00:00+08:00'}]}}, fh, ensure_ascii=False)

        print('1. 汇总表的数字')
        rc, out, err = run(['--stages', cfg, '--claude', claude, '--codex', codex] + dsh_args)
        check('正常退出', rc == 0, err[-500:])
        t1 = section(out, '1. 按回合'); t2 = section(out, '2. 回合末交代'); t3 = section(out, '3. 按会话')
        r = row(t1, 'Claude · 旧')
        check('Claude 旧：续接会话复制的记录不重复计（2 个回合），状态标记 / 阶段预告 / 三件套 / 区分核实都命中，先查后动 = 4（命令行写三件套不算写文件工具）',
              r == ['Claude · 旧 †', '2', '2.5', '100%（2/2）', '1', '100%（1/1） / 100%', '100%（1/1）', '100%（1/1）', '4（1）'], str(r))
        r = row(t1, 'Claude · 新')
        check('Claude 新：截止之后的回合、subagents 下的子代理记录都不计', r == ['Claude · 新 †', '2', '3.5', '0%（0/1）', '1', '0%（0/1） / 0%', '0%（0/1）', '0%（0/1）', '—'], str(r))
        r = row(t2, 'Claude · 旧')
        check('Claude 旧：末尾交代三种口径都命中，先说耗时再动手命中', r == ['Claude · 旧 †', '1', '1', '100%（1/1） / 100% / 100%', '100%（1/1）', '100%（1/1）'], str(r))
        r = row(t2, 'Claude · 新')
        check('Claude 新：以工具调用收尾的回合不进末尾交代的分母', r is not None and r[2] == '0' and r[3] == '— / — / —', str(r))
        r = row(t3, 'Claude · 旧')
        check('按会话：只含多步回合的单位算数（1 个），三件套写入 2 次', r is not None and r[1] == '1' and r[4] == '100%（1/1）' and r[-1] == '2', str(r))
        r = row(t1, 'Codex 主会话 · 旧')
        check('Codex 主会话：环境前缀消息不算回合，apply_patch 写交接算三件套，先查后动 = 4',
              r == ['Codex 主会话 · 旧 †', '1', '5.0', '100%（1/1）', '1', '100%（1/1） / 100%', '100%（1/1）', '0%（0/1）', '4（1）'], str(r))
        r = row(t1, 'Codex exec · 新')
        check('Codex exec 单列，归到「新」阶段', r is not None and r[1] == '1' and r[4] == '0', str(r))
        check('Codex 子代理会话不计', '子代理和审查（不计） 1' in out and row(t1, 'Codex exec · 旧') is None)
        if node:
            r = row(t1, 'DSH fake-model · 全段')
            check('DSH：两帧 zstd 能解开，大写 Edit（参数 path）算写文件和三件套，先查后动 = 2',
                  r == ['DSH fake-model · 全段 †', '1', '5.0', '100%（1/1）', '1', '100%（1/1） / 100%', '100%（1/1）', '0%（0/1）', '2（1）'], str(r))
            check('DSH：【调度说明】开头的工人会话不计', '工人会话（不计） 1' in out)
            r = row(t2, 'DSH fake-model · 全段')
            check('DSH：末尾「后台还在跑」算交代；预计 3 分钟算先说耗时', r is not None and r[3].startswith('100%（1/1）') and r[5] == '100%（1/1）', str(r))
        shape = row(section(out, '4. 会话有多长'), '全部会话')
        check('Claude 会话形状：3 个会话；后台通知 1 次、压缩 1 次被认出来', shape is not None and shape[1] == '3' and shape[3].endswith('（1）') and shape[6].endswith('（1）'), str(shape))
        r = row(section(out, '5. 三件套'), 'Claude Code')
        check('三件套目录：Claude 1 个目录、写 2 次', r == ['Claude Code', '1', '2（2）', '0', '0', '0'], str(r))

        print('2. 隐私：标准输出不含原文、路径、会话编号')
        leaks = [s for s in (SECRET, 'fakesess', 'fakecodex', 'dsh0001', '/fake/', tmp, '在等你回复', '请帮我整理') if s in out]
        check('汇总表里找不到任何原文、路径、会话编号', not leaks, '泄露：%s' % leaks)
        rc2, out2, err2 = run(['--stages', cfg, '--claude', claude, '--codex', codex, '--no-dsh', '--sample', '3'])
        check('--sample 不带 --detail 会被拒绝，什么都不输出', rc2 == 2 and out2 == '' and SECRET not in err2, err2[-300:])
        det = os.path.join(tmp, '明细.json')
        rc3, out3, err3 = run(['--stages', cfg, '--claude', claude, '--codex', codex, '--no-dsh', '--detail', det, '--sample', '3'])
        dtext = open(det, encoding='utf-8').read() if os.path.exists(det) else ''
        check('--detail 才写明细：含会话编号、三件套路径、抽样原文和提醒', rc3 == 0 and 'fakesess-0001' in dtext and '/fake/任务-%s/方案.md' % SECRET in dtext
              and '在等你回复' in dtext and '不要提交' in dtext, err3[-300:])
        check('加了 --detail，标准输出照样不含原文和编号', SECRET not in out3 and 'fakesess' not in out3)
        check('写明细时在标准错误里提醒别提交、别公开', '别提交' in err3)

        print('3. 阶段配置')
        bad = os.path.join(tmp, '乱序.json')
        with open(bad, 'w', encoding='utf-8') as fh:
            json.dump({'环境': {'claude': [{'名称': 'a', '起': '2000-02-01T00:00:00Z'}, {'名称': 'b', '起': '2000-01-01T00:00:00Z'}]}}, fh)
        rc4, _, err4 = run(['--stages', bad, '--claude', claude, '--no-dsh', '--no-codex'])
        check('阶段没按时间排会报错', rc4 != 0 and '先后' in err4, err4[-300:])
        check('Z 和 +08:00 是同一时刻', T.parse_cfg_time('2000-01-01T08:00:00+08:00', 'x') == T.parse_cfg_time('2000-01-01T00:00:00Z', 'x'))
        import datetime
        check('不带时区后缀的按本机本地时间', T.parse_cfg_time('2000-01-01T08:00:00', 'x') == datetime.datetime(2000, 1, 1, 8, 0).timestamp())
        rc5, out5, err5 = run(['--claude', claude, '--no-dsh', '--no-codex'])
        check('不给 --stages 也能跑，整个算「全部」', rc5 == 0 and row(section(out5, '1. 按回合'), 'Claude · 全部') is not None, err5[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    n = len(RESULTS); ok = sum(RESULTS)
    print('\n自测：通过 %d/%d' % (ok, n))
    return 0 if ok == n else 1


if __name__ == '__main__':
    sys.exit(main())
