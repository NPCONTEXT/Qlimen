#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""Qlimen 行为统计：从你本机的 Claude Code、DSH、Codex 会话记录里，按同一套口径数出和协议条款对应的行为，分阶段汇总。

只读，不改任何会话文件，不联网，不调模型。只依赖 Python 3.9+ 标准库；
读 DSH 会话另需 Node.js 22.15 以上（用它自带的 zstd 解压，见同目录 dsh_read.mjs），没有就跳过 DSH。

用法（详见同目录 说明.md）：
  python3 tools/统计/统计.py --stages 我的阶段.json > 汇总.md
  python3 tools/统计/统计.py --stages 我的阶段.json --no-dsh --codex /别处/.codex/sessions
  python3 tools/统计/统计.py --stages 我的阶段.json --detail /本机某处/统计明细.json --sample 20

隐私：标准输出只有汇总表（Markdown），不含对话原文、文件路径、会话编号。
  只有加 --detail 才把明细写到你指定的本地文件：含会话编号、三件套文件路径；再加 --sample 还含回合末尾的原文。
  这个文件只在本机看，别提交到仓库、别公开。

识别规则是照 Qlimen 中文协议（core/rules/AGENTS.md）的说法写的正则，集中在下面「识别规则」一节。
你的协议换了措辞（状态标记不叫【进行中】、三件套换了文件名、阶段预告不说「第 N 步」……），先改那一节的正则，
否则数出来会是 0 或偏低。全部是正则，属于粗口径。
"""
import argparse
import collections
import datetime
import glob
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DSH_READER = os.path.join(HERE, 'dsh_read.mjs')

# ============================== 识别规则 ==============================
# 每条都对应 Qlimen 中文协议（core/rules/AGENTS.md）里的一种说法。换了协议措辞就改这里。

MULTI_STEP = 5          # 多步回合：一个回合里工具调用 ≥ 5 次

# 第 5 节「每条回复都以【进行中】或【已完成】开头」→ 首条带状态标记
MARKS = ('【进行中】', '【已完成】')

# 第 6 节任务目录三件套的文件名
TRIO = ('方案.md', 'TODO.md', '交接.md')
RE_TRIO_NAME = re.compile(r'(方案|TODO|交接)\.md')

# 第 2 节阶段预告「第几步/共几步」。
# 窄 = 第 N 步、第 N/M 步、共 N 步；宽 = 再加 第 N/M 项、第一步（中文数字）、阶段 N / 步骤 N 后接冒号或「做完 / 完成 / 开始」、Step N/M、N/M 步。
RE_STEP_STRICT = re.compile(r'第\s*\d+\s*(/\s*\d+\s*)?步|共\s*\d+\s*步')
RE_STEP_BROAD = re.compile(
    r'第\s*\d+\s*(/\s*\d+\s*)?步|共\s*\d+\s*步|第\s*\d+\s*/\s*\d+\s*项|第\s*[一二三四五六七八九十]+\s*步'
    r'|(阶段|步骤)\s*\d+\s*(/\s*\d+\s*)?([：:（(，,]|做完|完成|开始)|[Ss]tep\s*\d+\s*(/|of)\s*\d+|\b\d+\s*/\s*\d+\s*步')

# 第 1 节「未验证……均须如实说明」、第 2 节「分开写已核实和未核实」
RE_VERIFY = re.compile(r'已核实|未核实|未验证|没验证|没核实')

# 第 5 节「每次回合结束说清状态：在等你回复，还是后台仍在跑」。三组，命中任一组就算交代了。
STATUS_WAIT = [  # 在等用户
    r'等你', r'等您', r'在等', r'待你', r'请你(定|选|确认|决定|批准|过目|看|回复|回|说|拍板|试|验收|运行|执行|操作|点)',
    r'你(来)?(定|选|拍板)', r'由你(定|决定|选)', r'需要你', r'要你', r'你确认', r'你批准', r'你同意', r'你回(复|一句|个|我)',
    r'回复我', r'告诉我', r'(批准|同意|确认|回复|定了|拍板)(之?后|了?我就|我再)', r'要不要', r'是否(要|需要|继续|执行|开始|同意|可以)',
    r'(你|您)看(看|一下|下)?(行不行|可以吗|怎么样|是否)', r'可以吗[？?]', r'行吗[？?]', r'好吗[？?]', r'你说(一声|了算)',
    r'请(确认|批准|回复|选择|决定|定夺|指示)', r'等(待)?(你的|您的)?(回复|批准|确认|决定|指示|答复)', r'说一声', r'跟我说', r'随时(说|叫我)',
    r'(?i:waiting (on|for) (you|your))', r'(?i:waiting on .{0,30}from you)', r'(?i:let me know)', r'(?i:your (call|decision))',
]
STATUS_BG = [  # 后台状态
    r'后台(还|仍|正|也)?(在|继续)?跑', r'后台(任务|进程)', r'没有?后台', r'无后台', r'后台.{0,12}(结束|跑完|完成|停|关|清理)',
    r'跑完(会|后)?(会)?(自动)?(通知|叫醒|叫我|告诉)', r'(会|再)?通知我', r'(自动)?(叫醒|唤醒)我', r'完成(后|时)(会)?(通知|叫醒)',
    r'(没有|无)(任何)?(任务|进程|东西|命令|工人|脚本)(在|还在)?(跑|运行)', r'(都|已|已经)(停|关|结束|清理)(掉|了|好)', r'没有在跑',
    r'还在跑', r'仍在跑', r'正在跑', r'在后台', r'已停止', r'(任务|本轮|脚本)已结束',
    r'(?i:running in the background)', r'(?i:nothing (is )?(still )?running)', r'(?i:no background)',
]
STATUS_GO = [  # 我接着动 / 真的停了
    r'我(先|就|会|再)?接着', r'接着(做|干|跑|改|修|往下|下一|盯)', r'继续盯', r'我(这就|马上|现在)(去|开始|动手|继续)', r'继续(做|跑|往下|下一)',
    r'(真的|就|先)?停(在这|了|下|这里)', r'本(轮|回合)(结束|到此|就到)', r'收工', r'到此为止', r'告一段落',
]
RE_WAIT = re.compile('|'.join(STATUS_WAIT))
RE_BG = re.compile('|'.join(STATUS_BG))
RE_GO = re.compile('|'.join(STATUS_GO))

# 第 2 节阶段预告「预计多久」、第 4 节「单条命令很长又无法后台化时，说明这段时间没法插话」
NUM = r'(\d+(\.\d+)?|[一二两三四五六七八九十几半]+)'
RE_ETA = re.compile(
    r'(预计|预估|估计|大约|大概|约莫|约)\s*(要|需要|需|花|用时?|耗时|跑|再)?\s*' + NUM +
    r'\s*([-–～~到至—]\s*' + NUM + r'\s*)?(个)?\s*(分钟|分(?!之)|秒钟|秒|小时|钟头|min\b|mins\b|s\b|h\b)')   # 分(?!之)：排除「约三分之二」
RE_PAST = re.compile(r'(用了|用时|耗时|花了|实际|实测|跑了|共计|总共|走完|卡了|只用|才用|降到|从|快|慢|多了|少了|提速)\s*\S{0,4}$')
RE_NOINT = re.compile(
    r'插不(了|进|上)话|(没法|无法|不能)插话|插不进来'
    r'|(这段时间|期间|等待期间|跑的时候|渲染时).{0,25}(插话|不需要你|你可以|别操作|不要操作|别碰|不要碰|不碰|保持安静|收不到|回不了|没法回|无法回|不用管|照常用|正常用)'
    r'|(没法|无法|不能)(及时|主动)?(回复|回应|汇报)你?|没法主动汇报')

# 用命令行改写三件套（重定向、tee、sed -i / perl -i 原地改）。Claude Code 和 DSH 的 Bash 调用用它。
RE_SH_REDIR = re.compile(r'(?:>>?|\btee\b(?:\s+-a)?)\s*["\']?([^\s"\'<>|;&]*?(?:方案|TODO|交接)\.md)')
RE_SH_INPLACE = re.compile(r'\b(?:sed|perl)\s+-i')
RE_SH_NAME = re.compile(r'([^\s"\'<>|;&]*?(?:方案|TODO|交接)\.md)')
# Codex 的工具调用是一段段代码，读写分不开，只能从文本里猜写入（偏粗）。Python 的 open(...,'w') 写入识别不了。
RE_CODEX_WRITE = re.compile(r'writeFile|apply_patch|\*\*\* (Update|Add) File|cat\s*>|>\s*\S+\.md')
RE_CODEX_TRIO = re.compile(r'(\*\*\* (?:Update|Add) File:\s*|(?:cat|tee)\s*(?:-a\s*)?>?\s*|>\s*)(\S*?(?:方案|TODO|交接)\.md)')

# Qlimen 派工脚本发给工人的任务以这句开头（core/workers/工人守则*.md）。DSH 里有用户消息这样开头的会话是工人，整个不计。
WORKER_HEAD = '【调度说明】'

# 各工具自己插进对话的系统消息（不是人打的字），不算用户发言，也不开新回合。
# NOTIFY_TAG 是 Claude Code 后台任务结束时插进来的通知；拆成两段写，只是免得被仓库的密钥样式扫描（sk-…）误报。
NOTIFY_TAG = '<task-' 'notification'
CLAUDE_SKIP = ('<command-', '<local-command', '<system-reminder', 'Caveat:', '[Request interrupted', NOTIFY_TAG, '<bash-')
CODEX_SKIP = ('<environment_context', '# AGENTS.md', '<user_instructions', '<INSTRUCTIONS', '<turn_aborted', '<subagent',
              '<external_codex_apps', '<recommended_plugins')
RE_CODEX_PREFIX = re.compile(r'^\s*(<[^>]+>.*?</[^>]+>\s*)+', re.S)    # 用户消息前面成段的 <标签>…</标签>，正文在后面
RE_CITE = re.compile(r'<oai-mem-citation>.*?</oai-mem-citation>', re.S)  # Codex 末尾自动附的记忆引用，不算正文

CLAUDE_WRITE_TOOLS = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')
DSH_WRITE_TOOLS = ('write', 'edit', 'multiedit')    # 不分大小写
MAX_LINE = {'claude': 3_000_000, 'codex': 2_000_000}  # 超长的单行记录（多半是大段工具输出）跳过，汇总里报跳过了几行

ENVS = ('claude', 'dsh', 'codex')
ENV_NAME = {'claude': 'Claude Code', 'dsh': 'DSH', 'codex': 'Codex'}

# ============================== 时间与阶段 ==============================


def _iso(s):
    s = s.strip()
    s = re.sub(r'[Zz]$', '+00:00', s)
    s = re.sub(r'\.(\d+)', lambda m: '.' + (m.group(1) + '000000')[:6], s, count=1)   # Python 3.9 只认 3 位或 6 位小数秒
    return datetime.datetime.fromisoformat(s)


def ts_of(s):
    """会话记录里的 ISO 时间 → 时间戳；读不懂返回 None。"""
    if not isinstance(s, str):
        return None
    try:
        return _iso(s).timestamp()
    except ValueError:
        return None


def parse_cfg_time(s, where):
    """阶段配置里的时间。带 Z 或 +08:00 这类后缀的按该时区；不带后缀的按运行脚本这台电脑的本地时间。"""
    if s is None:
        return None
    try:
        return _iso(str(s)).timestamp()
    except ValueError:
        sys.exit('阶段配置里 %s 的时间看不懂：%r。写成 2026-01-31T19:00:00+08:00 或 2026-01-31T11:00:00Z 这种。' % (where, s))


class Stages:
    """阶段边界。每个环境一串 (名称, 起)：起之前的回合不计；截止之后的记录一律不看。没配的环境整个算一个阶段「全部」。"""

    def __init__(self, cfg=None):
        self.end = None
        self.by_env = {e: [('全部', None)] for e in ENVS}
        if not cfg:
            return
        if not isinstance(cfg, dict):
            sys.exit('阶段配置的最外层要是一个 JSON 对象，照 阶段.example.json 写。')
        self.end = parse_cfg_time(cfg.get('截止'), '截止')
        envs = cfg.get('环境') or {}
        if not isinstance(envs, dict):
            sys.exit('阶段配置的「环境」要是一个对象：{"claude": [...], "dsh": [...], "codex": [...]}。')
        for env in envs:
            if env not in ENVS:
                sys.exit('阶段配置「环境」里的 %r 不认识，只能是 %s。' % (env, ' / '.join(ENVS)))
        for env in ENVS:
            lst = envs.get(env)
            if not lst:
                continue
            out = []
            if not isinstance(lst, list):
                sys.exit('阶段配置：%s 要写成一个列表，每项是 {"名称": ..., "起": ...}。' % env)
            for i, st in enumerate(lst):
                if not isinstance(st, dict):
                    sys.exit('阶段配置：%s 第 %d 个阶段要写成 {"名称": ..., "起": ...}。' % (env, i + 1))
                name = st.get('名称')
                start = parse_cfg_time(st.get('起'), '%s 第 %d 个阶段' % (env, i + 1))
                if not name:
                    sys.exit('阶段配置：%s 第 %d 个阶段没写「名称」。' % (env, i + 1))
                if start is None and i > 0:
                    sys.exit('阶段配置：%s 只有第一个阶段可以不写「起」（= 从最早的记录算起）。' % env)
                if out and out[-1][1] is not None and start <= out[-1][1]:
                    sys.exit('阶段配置：%s 的阶段要按时间先后排，第 %d 个的「起」不晚于前一个。' % (env, i + 1))
                if name in [n for n, _ in out]:
                    sys.exit('阶段配置：%s 有两个阶段都叫 %r。' % (env, name))
                out.append((name, start))
            self.by_env[env] = out

    def after_end(self, t):
        return t is not None and self.end is not None and t >= self.end

    def before_start(self, env, t):
        s = self.by_env[env][0][1]
        return t is not None and s is not None and t < s

    def of(self, env, t):
        """回合开始时间 → 阶段名；在第一个阶段之前或截止之后返回 None（不计）。"""
        if self.after_end(t) or self.before_start(env, t):
            return None
        cur = None
        for name, start in self.by_env[env]:
            if start is None or t >= start:
                cur = name
        return cur

    def order(self, env, name):
        return [n for n, _ in self.by_env[env]].index(name)


def fmt_time(t):
    return '—' if t is None else datetime.datetime.fromtimestamp(t).astimezone().strftime('%Y-%m-%d %H:%M %z')


# ============================== 解析 ==============================


class Turn:
    """回合 = 两条用户发言之间的全部记录。
    ev 里按顺序放 ('text', 文字) 或 ('tool', 工具名, 是不是写文件工具, 写到的三件套路径或 None)。
    「是不是写文件工具」只看工具本身（Codex 只能从代码文本猜），用命令行改写三件套不算，只记在第四项；「先查后动」只看第三项。"""
    __slots__ = ('t', 'sid', 'env', 'group', 'stage', 'ev', 'f')

    def __init__(self, t, sid, env, group, stage):
        self.t = t; self.sid = sid; self.env = env; self.group = group; self.stage = stage
        self.ev = []; self.f = None


def iter_jsonl(path, env, stats):
    try:
        fh = open(path, encoding='utf-8', errors='replace')
    except OSError:
        stats['读不了的文件'] += 1
        return
    with fh:
        for line in fh:
            if len(line) > MAX_LINE[env]:
                stats['超长单行跳过'] += 1
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                yield o


def sh_trio(cmd):
    """Bash 命令里用重定向 / tee / sed -i / perl -i 改写三件套 → 返回文件路径，否则 None。"""
    if not isinstance(cmd, str) or not RE_TRIO_NAME.search(cmd):
        return None
    m = RE_SH_REDIR.search(cmd)
    if m:
        return m.group(1)
    if RE_SH_INPLACE.search(cmd):
        m = RE_SH_NAME.search(cmd)
        return m.group(1) if m else None
    return None


def claude_files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d != 'subagents']    # 子代理的记录在 <会话>/subagents/ 下，不计
        out += [os.path.join(dp, f) for f in fn if f.endswith('.jsonl')]
    return out


def parse_claude(root, S, stats):
    """Claude Code：<root>/<项目>/<会话>.jsonl（root 也可以直接给某个项目目录）。返回 (回合列表, 会话形状列表)。"""
    files = claude_files(root)
    stats['会话文件'] = len(files)

    def first_ts(f):
        for o in iter_jsonl(f, 'claude', collections.Counter()):
            if o.get('timestamp'):
                return o['timestamp']
        return '9'
    # 续接会话会把旧会话的记录整段复制过来：按 uuid 去重，归属最早开始的那个文件
    seen = set(); turns = []; shape = []
    for f in sorted(files, key=first_ts):
        sid = os.path.basename(f)[:-6]
        cur = None; c = collections.Counter(); ids = set(); tmin = tmax = None
        for o in iter_jsonl(f, 'claude', stats):
            u = o.get('uuid')
            if u:
                if u in seen:
                    continue
                seen.add(u)
            if o.get('isSidechain'):      # 老版本把子代理记录混在主文件里
                continue
            t = ts_of(o.get('timestamp'))
            if S.after_end(t):
                continue
            early = S.before_start('claude', t)
            if not early and t is not None:
                tmin = t if tmin is None else min(tmin, t); tmax = t if tmax is None else max(tmax, t)
            ty = o.get('type'); m = o.get('message') or {}
            if ty == 'system' and o.get('subtype') == 'compact_boundary' and not early:
                c['compact'] += 1
            if ty == 'user' and not o.get('isMeta') and not o.get('isCompactSummary'):
                cc = m.get('content')
                kinds = ['text'] if isinstance(cc, str) else [x.get('type') for x in (cc or []) if isinstance(x, dict)]
                text = cc if isinstance(cc, str) else ''.join(x.get('text', '') for x in (cc or []) if isinstance(x, dict) and x.get('type') == 'text')
                p = text.lstrip()
                if not early and 'tool_result' not in kinds:     # 会话形状：人发的消息 / 后台通知
                    if p.startswith(NOTIFY_TAG):
                        c['notify'] += 1
                    elif (p and not p.startswith(CLAUDE_SKIP)) or (not p and 'image' in kinds):
                        c['user'] += 1
                if text.strip() and not p.startswith(CLAUDE_SKIP) and t is not None:
                    st = S.of('claude', t)
                    cur = Turn(t, sid, 'claude', '', st) if st else None
                    if cur:
                        turns.append(cur)
            elif ty == 'assistant':
                blocks = [x for x in (m.get('content') or []) if isinstance(x, dict)]
                if not early:
                    if m.get('id'):
                        ids.add(m['id'])    # 一次模型调用会拆成好几行记录，按 message.id 去重
                    c['tool'] += sum(1 for x in blocks if x.get('type') == 'tool_use')
                if cur is None:
                    continue
                for x in blocks:
                    if x.get('type') == 'text' and str(x.get('text', '')).strip():
                        cur.ev.append(('text', x['text']))
                    elif x.get('type') == 'tool_use':
                        nm = x.get('name'); inp = x.get('input') or {}
                        if not isinstance(inp, dict):
                            inp = {}
                        fp = str(inp.get('file_path', ''))
                        w = nm in CLAUDE_WRITE_TOOLS
                        tp = fp if (w and fp.endswith(TRIO)) else (sh_trio(inp.get('command')) if nm == 'Bash' else None)
                        cur.ev.append(('tool', nm, w, tp))
        if tmin is not None:
            shape.append({'sid': sid, 'user': c['user'], 'notify': c['notify'], 'calls': len(ids), 'tools': c['tool'],
                          'compact': c['compact'], 'hours': round((tmax - tmin) / 3600, 1)})
    return turns, shape


def find_node(given):
    node = given or shutil.which('node')
    if not node:
        return None, '找不到 node（读 DSH 会话要 Node.js 22.15 以上）'
    try:
        r = subprocess.run([node, '-e', 'process.stdout.write(typeof require("node:zlib").zstdDecompressSync)'],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, 'node 运行不了：%s' % e
    if r.stdout.decode('utf-8', 'replace').strip() != 'function':
        return None, '这个 node 太旧，没有内置 zstd（要 22.15 以上）'
    return node, ''


def parse_dsh(root, S, stats, node):
    """DSH：<root>/<工作目录>/session-*/session.v4.jsonl.zstd，多个 zstd 帧首尾相接，用 dsh_read.mjs 解压。"""
    files = sorted(glob.glob(os.path.join(glob.escape(root), '*', 'session-*', 'session.v4.jsonl.zstd')))
    stats['会话文件'] = len(files)
    turns = []
    for f in files:
        sid = os.path.basename(os.path.dirname(f)).replace('session-', '')
        try:
            proc = subprocess.run([node, DSH_READER, f], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
        except (OSError, subprocess.SubprocessError):
            stats['解压失败'] += 1
            continue
        if proc.returncode != 0:
            stats['解压失败'] += 1
            continue
        recs = []
        for line in proc.stdout.decode('utf-8', 'replace').splitlines():
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                recs.append(o)
        models = {((r.get('data') or {}).get('header') or {}).get('config', {}).get('model') for r in recs if r.get('type') == 'request/header'}
        models |= {(r.get('data') or {}).get('model') for r in recs if r.get('type') == 'model/selection'}
        models.discard(None)
        group = next(iter(models)) if len(models) == 1 else ('混用' if models else '模型未知')   # 按会话实际用的模型分组
        cur = None; mine = []; worker = False
        for rec in recs:
            ty = rec.get('type'); d = rec.get('data') or {}
            tm = rec.get('time'); t = tm / 1000 if isinstance(tm, (int, float)) else None
            if S.after_end(t):
                continue
            if ty == 'user/message' and (d.get('source') or {}).get('kind') == 'user':
                text = ''.join(c.get('text', '') for c in d.get('content', []) if isinstance(c, dict))
                if text.startswith(WORKER_HEAD):
                    worker = True; mine = []
                    break
                if t is None:
                    continue
                st = S.of('dsh', t)
                cur = Turn(t, sid, 'dsh', group, st) if st else None
                if cur:
                    mine.append(cur)
            elif ty == 'assistant/message' and cur is not None:
                for c in (d.get('message') or {}).get('content', []):
                    if isinstance(c, dict) and c.get('type') == 'text' and str(c.get('text', '')).strip():
                        cur.ev.append(('text', c['text']))
            elif ty == 'tool/call' and cur is not None:
                nm = d.get('name')
                try:
                    a = json.loads(d.get('arguments') or '{}')
                except (ValueError, TypeError):
                    a = {}
                if not isinstance(a, dict):
                    a = {}
                fp = str(a.get('file_path') or a.get('path') or '')    # 小写 write/edit 用 file_path，大写 Write/Edit 用 path
                low = str(nm).lower()
                w = low in DSH_WRITE_TOOLS
                tp = fp if (w and fp.endswith(TRIO)) else (sh_trio(a.get('command')) if low == 'bash' else None)
                cur.ev.append(('tool', nm, w, tp))
        if worker:
            stats['工人会话（不计）'] += 1
        turns += mine
    return turns


def parse_codex(root, S, stats):
    """Codex：<root>/年/月/日/rollout-*.jsonl。子代理、安全审查（session_meta.source 不是字符串）不计；source == exec 的单列。"""
    files = []
    for dp, dn, fn in os.walk(root):
        files += [os.path.join(dp, f) for f in fn if f.endswith('.jsonl')]
    stats['会话文件'] = len(files)
    turns = []
    for f in sorted(files):
        sid = os.path.basename(f)[:-6]
        meta = None; cur = None; group = None
        for o in iter_jsonl(f, 'codex', stats):
            p = o.get('payload') or {}
            if not isinstance(p, dict):
                continue
            if o.get('type') == 'session_meta' and meta is None:
                meta = p; src = p.get('source')
                if not isinstance(src, str):
                    stats['子代理和审查（不计）'] += 1
                    break
                group = 'exec' if src == 'exec' else '主会话'
                stats[group] += 1
                continue
            if meta is None or o.get('type') != 'response_item':
                continue
            t = ts_of(o.get('timestamp'))
            if S.after_end(t):
                continue
            pt = p.get('type')
            if pt == 'message' and p.get('role') == 'user':
                text = ''.join(c.get('text', '') for c in p.get('content', []) if isinstance(c, dict))
                body = RE_CODEX_PREFIX.sub('', text)
                if not body.strip() or body.lstrip().startswith(CODEX_SKIP) or t is None:
                    continue
                st = S.of('codex', t)
                cur = Turn(t, sid, 'codex', group, st) if st else None
                if cur:
                    turns.append(cur)
            elif pt == 'message' and p.get('role') == 'assistant' and cur is not None:
                text = ''.join(c.get('text', '') for c in p.get('content', []) if isinstance(c, dict))
                if text.strip():
                    cur.ev.append(('text', text))
            elif pt in ('function_call', 'custom_tool_call', 'local_shell_call') and cur is not None:
                body = str(p.get('input') or p.get('arguments') or '')
                w = bool(RE_CODEX_WRITE.search(body))
                m = RE_CODEX_TRIO.search(body) if w else None
                cur.ev.append(('tool', p.get('name'), w, m.group(2) if m else None))
    return turns


# ============================== 指标 ==============================


def status_kind(s):
    k = []
    if RE_WAIT.search(s):
        k.append('等用户')
    if RE_BG.search(s):
        k.append('后台')
    if RE_GO.search(s):
        k.append('接着/停')
    return k


def tail(s, n=2):
    return '\n'.join([l for l in s.strip().splitlines() if l.strip()][-n:])


def ask_end(s):
    lines = [l for l in s.strip().splitlines() if l.strip()][-3:]
    return any('？' in l or '?' in l for l in lines)


def eta_hits(s, forward_only=False):
    for m in RE_ETA.finditer(s):
        if forward_only and RE_PAST.search(s[max(0, m.start() - 8):m.start()]):
            continue
        return True
    return bool(RE_NOINT.search(s))


def final_text(ev):
    """最后一次工具调用之后的全部文字 = 回合末段。回合以工具调用收尾（被打断、报错）时返回 ('', False)。"""
    last_tool = max((i for i, e in enumerate(ev) if e[0] == 'tool'), default=-1)
    after = [e[1] for e in ev[last_tool + 1:] if e[0] == 'text']
    if after:
        return RE_CITE.sub('', '\n'.join(after)).strip(), True
    return '', False


def eta_before_tool(ev):
    """前瞻的耗时 / 插话说明出现在某段文字里，且这段文字之后还有工具调用（= 先说再动手）。"""
    seen_tool_after = False
    for e in reversed(ev):
        if e[0] == 'tool':
            seen_tool_after = True
        elif seen_tool_after and eta_hits(e[1], forward_only=True):
            return True
    return False


def turn_flags(x):
    texts = [e[1] for e in x.ev if e[0] == 'text']
    tools = [e for e in x.ev if e[0] == 'tool']
    ft, natural = final_text(x.ev)
    kinds = status_kind(ft) if natural else []
    return {
        'tools': len(tools),
        'multi': len(tools) >= MULTI_STEP,
        'has_text': bool(texts),
        'mark': bool(texts) and texts[0].lstrip().startswith(MARKS),
        'first_write': next((i for i, e in enumerate(tools) if e[2]), None),
        'trio_paths': [e[3] for e in tools if e[3]],
        'step_s': any(RE_STEP_STRICT.search(s) for s in texts),
        'step_b': any(RE_STEP_BROAD.search(s) for s in texts),
        'verify': any(RE_VERIFY.search(s) for s in texts),
        'natural': natural,
        'end': bool(kinds),
        'end_q': bool(kinds) or (natural and ask_end(ft)),
        'end_tail': natural and bool(status_kind(tail(ft))),
        'eta': any(eta_hits(s) for s in texts),
        'eta_pre': eta_before_tool(x.ev),
        'noint': any(RE_NOINT.search(s) for s in texts),
        'kinds': kinds,
    }


def cell_key(x):
    return (x.env, x.group, x.stage)


def cell_label(key):
    env, group, stage = key
    if env == 'claude':
        return 'Claude · %s' % stage
    if env == 'dsh':
        return 'DSH %s · %s' % (group, stage)
    return 'Codex %s · %s' % (group, stage)


def sort_cells(keys, S):
    gorder = {'主会话': 0, 'exec': 1}
    return sorted(keys, key=lambda k: (ENVS.index(k[0]), gorder.get(k[1], 2), k[1], S.order(k[0], k[2])))


def med(v):
    if not v:
        return None
    m = statistics.median(v)
    return int(m) if float(m).is_integer() else round(m, 2)


def pct(a, b):
    return '—' if not b else '%d%%（%d/%d）' % (round(100 * a / b), a, b)


def pct_only(a, b):
    return '—' if not b else '%d%%' % round(100 * a / b)


def mean_rate(units, num, den):
    v = [100 * u[num] / u[den] for u in units if u[den]]
    return '—' if not v else '%d%%' % round(statistics.mean(v))


def by_turn(turns):
    cells = collections.defaultdict(list)
    for x in turns:
        cells[cell_key(x)].append(x.f)
    res = {}
    for k, fl in cells.items():
        mf = [f for f in fl if f['multi']]
        nat = [f for f in mf if f['natural']]
        fw = [f['first_write'] for f in fl if f['first_write'] is not None]
        res[k] = {
            'n': len(fl), 'tools': sum(f['tools'] for f in fl), 'with_text': sum(f['has_text'] for f in fl),
            'mark': sum(f['mark'] for f in fl), 'M': len(mf), 'N': len(nat),
            'step_s': sum(f['step_s'] for f in mf), 'step_b': sum(f['step_b'] for f in mf),
            'trio': sum(bool(f['trio_paths']) for f in mf), 'verify': sum(f['verify'] for f in mf),
            'fw_med': med(fw), 'fw_n': len(fw),
            'end': sum(f['end'] for f in nat), 'end_q': sum(f['end_q'] for f in nat), 'end_tail': sum(f['end_tail'] for f in nat),
            'eta': sum(f['eta'] for f in mf), 'eta_pre': sum(f['eta_pre'] for f in mf),
        }
    return res


def by_unit(turns):
    """单位 = 一个会话在一个阶段里的那一段。只计含 ≥1 个多步回合的单位。"""
    units = collections.defaultdict(list)
    for x in turns:
        units[(x.sid,) + cell_key(x)].append(x)
    cells = collections.defaultdict(list)
    for (sid, env, group, stage), ts in units.items():
        fl = [x.f for x in ts]
        mf = [f for f in fl if f['multi']]
        if not mf:
            continue
        nat = [f for f in mf if f['natural']]
        cells[(env, group, stage)].append({
            'sid': sid, 'turns': len(ts), 'multi': len(mf), 'natural': len(nat), 'tools': sum(f['tools'] for f in fl),
            'trio': any(f['trio_paths'] for f in fl), 'trio_writes': sum(len(f['trio_paths']) for f in fl),
            'trio_files': sorted({p for f in fl for p in f['trio_paths']}),
            'step_s': any(f['step_s'] for f in fl), 'step_b': any(f['step_b'] for f in fl),
            'end_n': sum(f['end'] for f in nat), 'endq_n': sum(f['end_q'] for f in nat), 'endt_n': sum(f['end_tail'] for f in nat),
            'eta_n': sum(f['eta'] for f in mf), 'eta_pre_n': sum(f['eta_pre'] for f in mf), 'noint_n': sum(f['noint'] for f in mf),
            't0': min(x.t for x in ts),
        })
    return cells


def session_lengths(turns):
    per = collections.defaultdict(list)
    for x in turns:
        per[(x.env, x.sid)].append(x)
    rows = collections.defaultdict(list)
    for (env, sid), ts in per.items():
        if not any(x.f['multi'] for x in ts):
            continue
        rows[env].append({'sid': sid, 'turns': len(ts), 'tools': sum(x.f['tools'] for x in ts),
                          'hours': round((max(x.t for x in ts) - min(x.t for x in ts)) / 3600, 1),
                          'stages': sorted({x.stage for x in ts})})
    return rows


def is_abs(p):
    return p.startswith(('/', '~')) or bool(re.match(r'[A-Za-z]:[\\/]', p))


def trio_dirs(turns):
    d = collections.defaultdict(lambda: {'sids': set(), 'stages': set(), 'writes': 0})
    for x in turns:
        for p in x.f['trio_paths']:
            k = (x.env, os.path.dirname(p), is_abs(p))
            d[k]['sids'].add(x.sid); d[k]['stages'].add(x.stage); d[k]['writes'] += 1
    return d


# ============================== 输出 ==============================


def md_table(head, rows):
    out = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    for r in rows:
        out.append('| ' + ' | '.join('—' if c is None else str(c) for c in r) + ' |')
    return '\n'.join(out)


def mm(v):
    return '—' if not v else '%s（%s）' % (med(v), max(v))


def report(S, envs_info, turns, shape, small_units=5, small_turns=20):
    L = []
    w = L.append
    now = datetime.datetime.now().astimezone()
    w('# Qlimen 行为统计汇总\n')
    w('生成：%s（本机时间），`tools/统计/统计.py`。只有汇总数字：不含对话原文、文件路径、会话编号。' % now.strftime('%Y-%m-%d %H:%M %z'))
    w('口径和局限见 `tools/统计/说明.md`。全部是正则匹配的粗口径，不是对照实验；各阶段做的任务不同，阶段差异里混着任务差异。\n')

    w('## 数据来源\n')
    for env in ENVS:
        info = envs_info.get(env)
        if info is None:
            w('- %s：已关闭（命令行参数）' % ENV_NAME[env]); continue
        if info.get('skip'):
            w('- %s：没读，%s' % (ENV_NAME[env], info['skip'])); continue
        st = info['stats']
        extra = '，'.join('%s %d' % (k, v) for k, v in sorted(st.items()) if k != '会话文件' and v)
        n_sess = len({x.sid for x in turns if x.env == env})
        w('- %s：找到 %d 个会话文件，%d 个会话有落在阶段范围内的回合%s' % (ENV_NAME[env], st.get('会话文件', 0), n_sess, ('；' + extra) if extra else ''))
    w('')

    w('## 阶段\n')
    rows = []
    for env in ENVS:
        if envs_info.get(env) is None:
            continue
        for name, start in S.by_env[env]:
            rows.append((ENV_NAME[env], name, '从最早的记录' if start is None else fmt_time(start)))
    w(md_table(('环境', '阶段', '起'), rows))
    w('\n截止：%s。阶段按每个用户回合的开始时间划分。DSH 按会话实际用的模型分组；Codex 分主会话和 exec（多为被脚本或别的模型调起）。\n'
      % ('不设' if S.end is None else fmt_time(S.end) + '（之后的记录一律不看）'))

    bt = by_turn(turns)
    keys = sort_cells(bt.keys(), S)
    w('## 1. 按回合\n')
    w('回合 = 两条用户发言之间的全部记录；多步回合 = 工具调用 ≥ %d 次。百分比后面括号里是 命中数/分母。首条带状态标记的分母是有文字的回合，'
      '「先查后动」是回合里第一次写文件之前的工具调用数（中位，括号里是有写文件的回合数），其余列的分母是多步回合。'
      '带 † 的行多步回合不到 %d 个，只看方向。\n' % (MULTI_STEP, small_turns))
    rows = []
    for k in keys:
        r = bt[k]
        rows.append((cell_label(k) + (' †' if r['M'] < small_turns else ''), r['n'], round(r['tools'] / r['n'], 1) if r['n'] else None,
                     pct(r['mark'], r['with_text']), r['M'],
                     '%s / %s' % (pct(r['step_s'], r['M']), pct_only(r['step_b'], r['M'])),
                     pct(r['trio'], r['M']), pct(r['verify'], r['M']),
                     '—' if r['fw_med'] is None else '%s（%d）' % (r['fw_med'], r['fw_n'])))
    w(md_table(('环境 · 阶段', '回合', '工具调用/回合', '首条带状态标记', '多步回合', '阶段预告 窄 / 宽', '写三件套', '区分已核实/未验证', '先查后动'), rows))
    w('')

    w('## 2. 回合末交代与耗时说明（按回合）\n')
    w('末尾交代的分母是自然收尾（最后是文字、不是工具调用）的多步回合；「全末段」在最后一次工具调用之后的全部文字里找，'
      '「含问句」另把末尾 3 行里有问号的也算，「末两行」只看最后两行。耗时说明含「用了约 3 分钟」这类事后说法；'
      '「先说耗时再动手」只算说完后面还有工具调用、而且不是过去式的。\n')
    rows = []
    for k in keys:
        r = bt[k]
        rows.append((cell_label(k) + (' †' if r['M'] < small_turns else ''), r['M'], r['N'],
                     '%s / %s / %s' % (pct(r['end'], r['N']), pct_only(r['end_q'], r['N']), pct_only(r['end_tail'], r['N'])),
                     pct(r['eta'], r['M']), pct(r['eta_pre'], r['M'])))
    w(md_table(('环境 · 阶段', '多步回合', '其中自然收尾', '末尾交代 全末段 / 含问句 / 末两行', '耗时或插话说明', '先说耗时再动手'), rows))
    w('')

    bu = by_unit(turns)
    ukeys = sort_cells(bu.keys(), S)
    w('## 3. 按会话 × 阶段\n')
    w('单位 = 一个会话在一个阶段里的那一段，只计含多步回合的单位。长会话里三件套建一次、之后反复更新，按回合算会被稀释，这张表看「这段工作里出现过没有」。'
      '「写过三件套」「阶段预告」「至少一次……」= 单位里出现过一次就算，除以单位数；「末尾交代率」先算每个单位自己的交代率，再对单位取平均。'
      '出现过一次不等于一直在做。带 † 的行单位数不到 %d 个，只看方向。\n' % small_units)
    rows = []
    for k in ukeys:
        us = bu[k]; n = len(us)
        rows.append((cell_label(k) + (' †' if n < small_units else ''), n,
                     mm([u['turns'] for u in us]), mm([u['tools'] for u in us]),
                     pct(sum(u['trio'] for u in us), n),
                     '%s / %s' % (pct(sum(u['step_s'] for u in us), n), pct_only(sum(u['step_b'] for u in us), n)),
                     '%s / %s' % (mean_rate(us, 'end_n', 'natural'), mean_rate(us, 'endt_n', 'natural')),
                     pct(sum(u['eta_n'] > 0 for u in us), n), pct(sum(u['eta_pre_n'] > 0 for u in us), n),
                     pct(sum(u['noint_n'] > 0 for u in us), n),
                     med([u['trio_writes'] for u in us if u['trio']])))
    w(md_table(('环境 · 阶段', '单位数', '用户回合/单位 中位（最大）', '工具调用/单位 中位（最大）', '写过三件套', '阶段预告 窄 / 宽',
                '末尾交代率 全末段 / 末两行', '至少一次耗时说明', '至少一次先说耗时再动手', '至少一次插话说明', '三件套写入次数（中位）'), rows))
    w('\n最后一列只算写过三件套的单位。\n')

    w('## 4. 会话有多长\n')
    sl = session_lengths(turns)
    rows = []
    for env in ENVS:
        rs = sl.get(env) or []
        if not rs:
            continue
        rows.append((ENV_NAME[env], len(rs), mm([r['turns'] for r in rs]), mm([r['tools'] for r in rs]),
                     mm([r['hours'] for r in rs]), sum(1 for r in rs if len(r['stages']) > 1)))
    w('只算含多步回合的会话，范围内的回合。\n')
    w(md_table(('环境', '会话数', '用户回合 中位（最大）', '工具调用 中位（最大）', '首尾跨度小时 中位（最大）', '跨阶段的会话'), rows))
    if shape:
        multi_sids = {x.sid for x in turns if x.env == 'claude' and x.f['multi']}
        w('\nClaude Code 会话形状（同一个会话的几种「长度」并列）：用户发言 = 人发的消息（含只发图片）；后台通知 = 后台任务结束触发的续跑；'
          '模型调用 = 按 message.id 去重的助手消息；压缩 = 上下文压缩次数。\n')
        rows = []
        for lab, rs in (('全部会话', shape), ('含多步回合的会话', [r for r in shape if r['sid'] in multi_sids])):
            if not rs:
                continue
            rows.append((lab, len(rs)) + tuple(mm([r[k] for r in rs]) for k in ('user', 'notify', 'calls', 'tools', 'compact', 'hours'))
                        + (sum(1 for r in rs if r['calls'] >= 100), sum(1 for r in rs if r['calls'] >= 300), sum(1 for r in rs if r['compact'] >= 1)))
        w(md_table(('范围', '会话数', '用户发言', '后台通知', '模型调用', '工具调用', '压缩', '跨度小时', '模型调用≥100', '模型调用≥300', '压缩过'), rows))
        w('\n中间六列是 中位（最大）；最后三列是会话个数。')
    w('')

    w('## 5. 三件套写在哪些任务目录\n')
    w('只数个数，不列路径。看「建一次、在同一个会话里反复更新」还是「多个会话接力」。相对路径、带变量的路径归不到具体目录，单列。\n')
    td = trio_dirs(turns)
    rows = []
    for env in ENVS:
        ab = [v for (e, _, a), v in td.items() if e == env and a]
        rel = [v for (e, _, a), v in td.items() if e == env and not a]
        if not ab and not rel:
            continue
        rows.append((ENV_NAME[env], len(ab), mm([v['writes'] for v in ab]), sum(1 for v in ab if len(v['sids']) >= 2),
                     sum(1 for v in ab if len(v['stages']) >= 2), len(rel)))
    w(md_table(('环境', '任务目录（绝对路径）', '每个目录写入次数 中位（最大）', '被 ≥2 个会话写过', '跨阶段', '相对路径另有几组'), rows) if rows else '没有识别到三件套写入。')
    w('')
    return '\n'.join(L)


def detail_dump(path, S, turns, shape, sample):
    bu = by_unit(turns)
    out = {
        '提醒': '本文件含会话编号、文件路径%s。只在本机看，不要提交到仓库，不要公开。' % ('和对话原文' if sample else ''),
        '生成': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        '单位明细': [],
        '会话长度明细': {ENV_NAME[e]: rs for e, rs in session_lengths(turns).items()},
        '三件套目录': [{'环境': ENV_NAME[e], '目录': d, '绝对路径': a, '会话': sorted(v['sids']), '阶段': sorted(v['stages']), '写入次数': v['writes']}
                  for (e, d, a), v in sorted(trio_dirs(turns).items())],
    }
    for k in sort_cells(bu.keys(), S):
        for u in sorted(bu[k], key=lambda u: u['t0']):
            row = {'环境 · 阶段': cell_label(k)}
            row.update(u)
            row['t0'] = fmt_time(u['t0'])
            out['单位明细'].append(row)
    if shape:
        out['Claude 会话形状'] = sorted(shape, key=lambda r: -r['calls'])
    if sample:
        random.seed(7)
        out['抽样'] = []
        for env in ENVS:
            pool = [x for x in turns if x.env == env and x.f['multi'] and x.f['natural']]
            for x in random.sample(pool, min(sample, len(pool))):
                ft, _ = final_text(x.ev)
                out['抽样'].append({'环境 · 阶段': cell_label(cell_key(x)), '会话': x.sid, '回合开始': fmt_time(x.t),
                                  '末尾交代': x.f['kinds'], '末尾有问句': ask_end(ft), '末段最后 300 字': ft[-300:]})
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1, default=str)


def in_git_repo(path):
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.exists(os.path.join(d, '.git')):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description='从本机会话记录统计和 Qlimen 协议条款对应的行为（只读）。汇总表打到标准输出。')
    ap.add_argument('--stages', metavar='JSON', help='阶段配置，照 tools/统计/阶段.example.json 写；不给就整个算一个阶段')
    ap.add_argument('--claude', metavar='目录', help='Claude Code 会话目录（默认 ~/.claude/projects；也可以给其中某个项目目录）')
    ap.add_argument('--dsh', metavar='目录', help='DSH 会话目录（默认 ~/.dsh/sessions）')
    ap.add_argument('--codex', metavar='目录', help='Codex 会话目录（默认 ~/.codex/sessions）')
    ap.add_argument('--no-claude', action='store_true', help='不读 Claude Code')
    ap.add_argument('--no-dsh', action='store_true', help='不读 DSH')
    ap.add_argument('--no-codex', action='store_true', help='不读 Codex')
    ap.add_argument('--node', metavar='路径', help='node 可执行文件（默认从 PATH 找；读 DSH 用）')
    ap.add_argument('--detail', metavar='文件', help='把明细（含会话编号、文件路径）写到这个本地文件，别提交、别公开')
    ap.add_argument('--sample', metavar='N', type=int, default=0, help='明细里再附每个环境 N 条多步回合的末段原文（校准正则用），必须和 --detail 一起用')
    a = ap.parse_args(argv)
    if a.sample and not a.detail:
        ap.error('--sample 会带出对话原文，只能写进 --detail 指定的本地文件，不往标准输出打。')
    if a.sample < 0:
        ap.error('--sample 不能是负数')

    cfg = None
    if a.stages:
        try:
            with open(a.stages, encoding='utf-8') as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            ap.error('阶段配置读不了：%s' % e)
    else:
        print('提示：没给 --stages，所有记录算一个阶段「全部」。', file=sys.stderr)
    S = Stages(cfg)

    defaults = {'claude': '~/.claude/projects', 'dsh': '~/.dsh/sessions', 'codex': '~/.codex/sessions'}
    envs_info = {}; turns = []; shape = []
    for env in ENVS:
        if getattr(a, 'no_' + env):
            continue
        given = getattr(a, env)
        root = os.path.expanduser(given or defaults[env])
        info = {'stats': collections.Counter()}
        envs_info[env] = info
        if not os.path.isdir(root):
            if given:
                ap.error('%s 会话目录不存在：%s' % (ENV_NAME[env], given))
            info['skip'] = '默认目录不存在'
            continue
        print('读 %s 会话……' % ENV_NAME[env], file=sys.stderr, flush=True)
        if env == 'claude':
            ts, shape = parse_claude(root, S, info['stats'])
        elif env == 'dsh':
            node, why = find_node(a.node)
            if not node:
                info['skip'] = why
                print('跳过 DSH：%s' % why, file=sys.stderr)
                continue
            ts = parse_dsh(root, S, info['stats'], node)
        else:
            ts = parse_codex(root, S, info['stats'])
        turns += ts
    for x in turns:
        x.f = turn_flags(x)
    print(report(S, envs_info, turns, shape))
    if a.detail:
        detail_dump(a.detail, S, turns, shape, a.sample)
        print('\n明细已写到 %s：含会话编号、文件路径%s。只在本机看，别提交到仓库、别公开。' % (a.detail, '和对话原文' if a.sample else ''), file=sys.stderr)
        repo = in_git_repo(a.detail)
        if repo:
            print('注意：这个位置在一个 git 仓库里，提交前确认它不会被加进去（写进 .gitignore，或换个目录）。', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
