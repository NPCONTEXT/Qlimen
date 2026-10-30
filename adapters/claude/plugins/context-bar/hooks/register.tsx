import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Phase, Usage } from '../types'

// 到 WARN% 变黄，到 DANGER% 变红并弹一次提示
const WARN = 70
const DANGER = 85

// 每次压缩（手动、自动、预先计算）都附上的摘要要求
const ZH_INSTRUCTIONS = [
  '摘要一律用简体中文写；代码、命令、路径、文件名、报错保留原文。',
  '摘要第一行写：【压缩后提醒】所有回复、思考、汇报一律用简体中文；先读任务目录 交接.md 最上面一条和 TODO.md，再接着做。',
  '要保留：用户的原话（要求、纠正、决定，带时刻）；当前停点和下一步；任务目录、交接文件的完整路径；还在跑的后台任务。',
].join('\n')

// 点「交接并压缩」后替用户发的话；回复最后一行是 DONE 才压缩
const DONE = '交接完成'
const HANDOFF_PROMPT = [
  '【交接并压缩】我要压缩上下文了。先把当前任务的交接写好：按协议更新任务目录的 交接.md（停点、下一步、我已定的事、没验证的）和 TODO.md；已经写好的，核对是不是最新。',
  '这一轮不要开始新的工作，也不要向我提问。',
  `写完后，回复的最后一行只写：${DONE}。这个会话没有需要交接的任务，就直接回复：${DONE}。`,
].join('\n')
// 交给 /compact 的参数；中文摘要要求由下面的 session.compact 钩子补上
const AFTER_HANDOFF = '刚写完交接：摘要里写明这次交接写进了哪些文件（完整路径）。'

const usage = atom({ plugin: 'context-bar', key: 'usage' } as const, null)
const warned = atom({ plugin: 'context-bar', key: 'warned' } as const, 0)
const phase = atom({ plugin: 'context-bar', key: 'phase' } as const, 'idle')
// 压缩完时最后一次请求的大小；出现新的请求（数字变了）之前，横条不显示这个旧数字
const stale = atom({ plugin: 'context-bar', key: 'stale' } as const, null)
// 点了一次「交接并压缩」、等第二次确认时，记下第一次点的时刻；CONFIRM_MS 内没确认就自动复原（10-26 作者要求加确认）
const confirming = atom({ plugin: 'context-bar', key: 'confirming' } as const, null)
const CONFIRM_MS = 5000

const PHASE_NOTE: Record<Phase, string> = { idle: '', handoff: '· 交接中…', compacting: '· 压缩中…' }

const wan = (tokens: number) => `${Math.round(tokens / 10000)} 万`

const barText = (u: Usage) => {
  const base = `上下文 ${u.percent}%（${wan(u.tokens)} / ${wan(u.window)}）`
  if (u.percent >= DANGER) return `${base}· 现在交接`
  if (u.percent >= WARN) return `${base}· 该准备交接了`
  return base
}

const withZh = (instructions?: string) => {
  if (!instructions) return ZH_INSTRUCTIONS
  return instructions.includes(ZH_INSTRUCTIONS) ? instructions : `${instructions}\n\n${ZH_INSTRUCTIONS}`
}

// 回复最后一行（去掉空白、加粗、句号）是不是 DONE
const isDone = (answer: string) => {
  const last = answer.trim().split('\n').pop() ?? ''
  return last.replace(/[\s*_`。.!！]/g, '') === DONE
}

// 读一次用量（不花钱）更新横条；刚跨过 DANGER 时弹一次提示。读不到就保持原样
const refresh = async ($: EngineInterface) => {
  try {
    const { context } = await $.session.usage()
    if (context.tokens === undefined || context.percent === undefined) return
    const now: Usage = { tokens: context.tokens, window: context.window, percent: context.percent }
    await update($, usage, () => now)
    const was = await read($, stale)
    if (was !== null && now.tokens === was) return
    if (was !== null) await update($, stale, () => null)
    const level = now.percent >= DANGER ? DANGER : now.percent >= WARN ? WARN : 0
    const last = await read($, warned)
    if (level === DANGER && last < DANGER) {
      $.ui.toast(`上下文已到 ${now.percent}%，现在让 Claude 写交接`, { timeoutMs: 15000 })
    }
    if (level !== last) await update($, warned, () => level)
  } catch {
    // 用量读不到时横条不变
  }
}

// 压缩完：记下这时的请求大小，横条先显示「刚压缩」，等出现新的请求再显示数字
const markStale = async ($: EngineInterface) => {
  try {
    const { context } = await $.session.usage()
    await update($, stale, () => context.tokens ?? 0)
  } catch {
    // 读不到就照常显示
  }
}

// 按钮：替用户发交接请求（不等它返回，它要等整个回合跑完），再等这一回合结束
const startHandoff = async ($: EngineInterface) => {
  if ((await read($, phase)) !== 'idle') return
  await update($, phase, () => 'handoff')
  const giveUp = async () => {
    await update($, phase, () => 'idle')
    $.ui.toast('没发出去：交接请求被拦下了', { timeoutMs: 15000 })
  }
  void $.prompt
    .submit({ text: HANDOFF_PROMPT, asUser: true })
    .then(sent => ('drop' in sent && sent.drop ? giveUp() : undefined))
    .catch(giveUp)
}

// 第一次点：只进入「等确认」；到时还没确认就复原（只复原自己这一次，后来又点的不动）
const askConfirm = async ($: EngineInterface) => {
  const id = await $.clock.now()
  await update($, confirming, () => id)
  $.clock.after(CONFIRM_MS, () => {
    void (async () => {
      if ((await read($, confirming)) === id) await update($, confirming, () => null)
    })()
  })
}

// 第二次点「确认交接」才真的交接
const confirmHandoff = async ($: EngineInterface) => {
  await update($, confirming, () => null)
  await startHandoff($)
}

// 排队运行 /compact，会话空闲后引擎才执行，和你手动输入 /compact 走同一条路。
// $.session.compact 不排队、回合没彻底结束就拒绝（10-18 实测三次全被拒）；
// 在回合等待的钩子里调 $.command.run 也会被拒，所以放到钩子外的定时器里
const compactWhenIdle = ($: EngineInterface) => {
  $.clock.after(500, () => {
    void $.command
      .run({ command: 'compact', args: AFTER_HANDOFF })
      .then(async () => {
        await update($, phase, () => 'idle')
        await refresh($)
      })
      .catch(async (err: unknown) => {
        await update($, phase, () => 'idle')
        const why = err instanceof Error ? err.message : String(err)
        $.ui.toast(`没压缩：${why}`, { timeoutMs: 20000 })
      })
  })
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const ran = await next(e)
    await update($, phase, () => 'idle')
    await refresh($)
    return ran
  })

  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    await refresh($)
    return ran
  })

  on('turn.complete', async ($, e, next) => {
    const ran = await next(e)
    await refresh($)
    if (e.agentId === undefined && (await read($, phase)) === 'handoff') {
      if (e.reason === 'answer' && isDone(e.answer)) {
        await update($, phase, () => 'compacting')
        compactWhenIdle($)
      } else {
        await update($, phase, () => 'idle')
        $.ui.toast(`没压缩：交接没写完（回复最后一行不是「${DONE}」）`, { timeoutMs: 15000 })
      }
    }
    return ran
  })

  on('session.compact', async ($, e, next) => {
    if (e.agentId !== undefined) return next(e)
    const ran = await next({ ...e, instructions: withZh(e.instructions) })
    // 预先计算的不改对话、被跳过的没压，都不算压缩完
    if (e.trigger !== 'precompute' && !('skip' in ran && ran.skip)) await markStale($)
    await refresh($)
    return ran
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const u = await read($, usage)
    if (e.props.hasSurvey || u === null) return next(e)
    // 下面别的模组画的（比如建议那一行）放在上下文这一行的下面、紧贴输入框（10-27 作者选方案乙），不管谁先加载都叠着显示。
    // 不设 rowGap：最底下垫着引擎的空占位，rowGap 也会给它留一份空，两行隔太开、只剩一行时偏上（10-27 作者反馈）；行距由下面那一行自己留
    const below = await next(e).catch(() => null)
    const now = await read($, phase)
    const asking = (await read($, confirming)) !== null
    const { Box, Button, Text } = $.ui.resolve(e)
    if ((await read($, stale)) !== null) {
      return (
        <Box flexDirection="column">
          <Box>
            <Text size="footnote" dimColor>
              上下文：刚压缩完，下一条回复后更新
            </Text>
          </Box>
          {below}
        </Box>
      )
    }
    const color = u.percent >= DANGER ? 'error' : u.percent >= WARN ? 'warning' : undefined

    const idle = now === 'idle' && !e.props.isWorking
    // 「确认交接」加红框，和下面黑色的「直接发送」一眼分开，防误触（10-27 作者要求）；按钮本身只有黑、浅灰两种，没有红色款。终端里框占一整行，不加。
    // 桌面画的是系统按钮，按钮里的字设不了颜色（10-27 实测），只能靠 hover：鼠标在上下文这一行上时字变红（这一行的 Box 带 key 才有 hover）。
    // 框写明 padding 0，让它贴着按钮（10-27 作者：框太大）
    const frame = e.surface === 'terminal' ? undefined : 'round'

    return (
      <Box flexDirection="column">
        <Box key="context-row" flexDirection="row" justifyContent="space-between" alignItems="center">
          <Text size="footnote" color={color} dimColor={color === undefined} weight={u.percent >= DANGER ? 'bold' : undefined}>
            {barText(u) + PHASE_NOTE[now]}
          </Text>
          {idle && !asking && <Button key="handoff" label="交接并压缩" onPress={() => askConfirm($)} />}
          {idle && asking && (
            <Box flexDirection="row" alignItems="center">
              <Box borderStyle={frame} borderColor="error" padding={0}>
                <Button key="handoff-confirm" label="确认交接" hover={{ color: 'error', bold: true }} onPress={() => confirmHandoff($)} />
              </Box>
              <Text> </Text>
              <Button key="handoff-cancel" label="取消" onPress={() => update($, confirming, () => null)} />
            </Box>
          )}
        </Box>
        {below}
      </Box>
    )
  })
}
