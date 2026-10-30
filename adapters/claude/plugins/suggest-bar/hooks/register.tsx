import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

// 原生的灰字建议（Prompt suggestions）在桌面应用里时有时无，这里自己猜：
// 你发的消息回复完后，读你上一句和回答的结尾，猜你下一句，显示在输入框上方。
// 桌面应用里模组截不到 Tab，所以靠按钮：「填入」填进输入框，「直接发送」当成你的消息发出去。
// 三档：收起（新会话默认）：后台照猜，上下文那一行最前面一个「建议 ▾」按钮，猜出新建议时亮起，点开展开、再点收回；
//   展开：建议那一行紧贴输入框，右边「填入」「直接发送」；
//   完全关闭：/suggest off，所有会话都不猜、不花额度（开关存在 $.store），/suggest on 打开。
// 默认用 Opus：作者 10-27 对照测试 30 个样本，有效率 Opus 43%、Haiku 20%。想省额度把 MODEL 换成 'haiku'。

const suggestion = atom({ plugin: 'suggest-bar', key: 'suggestion' } as const, null)
// 这一轮你自己打的字；存在 $.state 里，模组重新加载也不丢
const asked = atom({ plugin: 'suggest-bar', key: 'asked' } as const, null)
// 这一轮回复完的时刻；猜完时还是这一轮才显示（你下一句已经来了，就不再显示旧建议）
const round = atom({ plugin: 'suggest-bar', key: 'round' } as const, null)
// 展开还是收起：只管本会话，新会话收起
const expanded = atom({ plugin: 'suggest-bar', key: 'expanded' } as const, false)
// 没有完全关闭：照 $.store 里的开关，所有会话共用；会话开始、每轮开始各读一次
const enabled = atom({ plugin: 'suggest-bar', key: 'enabled' } as const, true)
// 回复完到猜出来那几秒：展开时显示「正在猜下一句…」
const guessing = atom({ plugin: 'suggest-bar', key: 'guessing' } as const, false)

const SYSTEM = [
  '你在猜用户接下来最可能对 AI 助手说的下一句话。',
  '只输出那一句话本身：简体中文，用用户自己的口吻（照着用户上一句的说话方式，口语、简短），不超过 20 个字，不加引号，不解释。',
  '助手在回复末尾让用户选（比如 A/B、方案一/方案二）时，直接给出最可能的选择。',
  '猜不出有把握的下一句，只输出：无',
].join('\n')

const MODEL = 'opus'
// Opus 会先想一小会儿再答：对照测试里输出中位 62、最多 142 token
const MAX_TOKENS = 300
// 送给模型的长度上限（字）：你上一句取最后 USER_TAIL，回答取最后 ANSWER_TAIL
const USER_TAIL = 600
const ANSWER_TAIL = 2500
const MAX_LEN = 40
// 像助手口吻的不要；「好的，我先看看」是你的口吻，不算（对照测试里误伤过）
const CLAUDE_VOICE = /^(我来|让我|我会|我将|好的，我(来|会|马上|这就)|当然|这是|以下是)/
// Opus 偶尔在开头带乱码（对照测试 60 次里 4 次：making、[[OUTPUT]]、「了，」）
const JUNK_HEAD = /^(\[\[[^\]]*\]\]|[a-z]{3,}(?=[一-鿿])|了[，,])\s*/
// 句中、句尾的 [[…]] 也去掉（出过「乙吧，其他都行[[RESPONSE_END]]」）
const JUNK_ANY = /\[\[[^\]]*\]\]/g
// 乱码符号「�」去掉；整句只剩一个字的，除了字母、数字和这几个常用的单字回答，都不要（出过「汐」「}」）
const BAD_CHAR = /\uFFFD/g
const ONE_CHAR_OK = /^[A-Za-z0-9好行对嗯是可跑批过停改删]$/
// 完全关闭存在 $.store 的这个键下：'off' 关，其余（含没设过）开
const MODE_KEY = 'mode'
// 「建议」按钮外那圈细框：有新建议时用主题的 suggestion 色，没有时透明；框一直在，按钮大小不跳
const FRAME_READY = 'suggestion'
const FRAME_IDLE = '#00000000'
const HELP = '/suggest off 完全关闭（所有会话都不猜，不花额度），/suggest on 打开'

const tail = (s: string, n: number) => (s.length > n ? s.slice(-n) : s)

// 取第一行、去乱码和引号；「无」、空、太长、像助手口吻的都不要
const clean = (raw: string): string | null => {
  const first = (raw.trim().split('\n')[0] ?? '').trim()
  const line = first.replace(BAD_CHAR, '').replace(JUNK_HEAD, '').replace(JUNK_ANY, '').trim().replace(/^["'「『“”]+/, '').replace(/["'」』“”]+$/, '').trim()
  if (line === '无' || !line || line.length > MAX_LEN || CLAUDE_VOICE.test(line) || (line.length === 1 && !ONE_CHAR_OK.test(line))) return null
  return line
}

// t 是这一轮回复完的时刻：你下一句已经来了（round 变了）就不再显示
const guess = async ($: EngineInterface, said: string, answer: string, t: number) => {
  await update($, guessing, () => true)
  const ask = () =>
    $.model.complete({
      model: MODEL,
      system: SYSTEM,
      prompt: `用户上一句：\n${tail(said, USER_TAIL)}\n\n助手的回复（结尾部分）：\n${tail(answer, ANSWER_TAIL)}\n\n用户的下一句：`,
      maxTokens: MAX_TOKENS,
      effort: 'low',
      timeoutMs: 60_000,
    })
  let text: string | null = null
  try {
    let r = await ask()
    // 交白卷就再问一次（对照测试 60 次里 3 次）
    if (!r.isAnswered && r.reason === 'empty-reply') r = await ask()
    if (r.isAnswered) text = clean(r.text)
  } catch {
    // 猜不出就不显示
  }
  if ((await read($, round)) !== t) return
  if (text !== null) await update($, suggestion, () => text)
  await update($, guessing, () => false)
}

const fill = async ($: EngineInterface, text: string) => {
  const f = await $.prompt.fill({ text })
  if (f.isFilled) await update($, suggestion, () => null)
  else $.ui.toast(`没填进去（${f.refusal ?? '原因不明'}）`, { timeoutMs: 8000 })
}

// 直接发送：把建议当作你的消息发出去；不等它返回，它要等整个回合跑完
const send = async ($: EngineInterface, text: string) => {
  await update($, suggestion, () => null)
  void $.prompt
    .submit({ text, asUser: true })
    .then(sent => ('drop' in sent && sent.drop ? $.ui.toast('没发出去：被拦下了', { timeoutMs: 8000 }) : undefined))
    .catch(() => $.ui.toast('没发出去', { timeoutMs: 8000 }))
}

const toggle = async ($: EngineInterface) => {
  const open = !(await read($, expanded))
  await update($, expanded, () => open)
}

// 读 $.store 里的开关；读不到就照旧
const refresh = async ($: EngineInterface) => {
  try {
    const isOn = (await $.store.get(MODE_KEY)) !== 'off'
    await update($, enabled, () => isOn)
  } catch {
    // 照旧
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({ name: 'suggest', description: `建议横条：${HELP}` })
    await refresh($)
    return next(e)
  })

  on('command.run', { command: 'suggest' }, async ($, e) => {
    const arg = e.args.trim().toLowerCase()
    if (arg === 'off' || arg === 'on') {
      await $.store.set(MODE_KEY, arg)
      await update($, enabled, () => arg === 'on')
      if (arg === 'off') {
        await update($, suggestion, () => null)
        await update($, guessing, () => false)
      }
      return {
        text:
          arg === 'off'
            ? '建议横条已完全关闭（所有会话，新开的也算）：不再猜、不花额度。打 /suggest on 重新打开'
            : '建议横条已打开（所有会话）：上下文那一行最前面的「建议」按钮，点开看建议',
      }
    }
    const off = (await $.store.get(MODE_KEY)) === 'off'
    return { text: `建议横条现在${off ? '完全关闭' : '开着'}。${HELP}` }
  })

  // 这一轮是不是你自己打字发的；后台通知、续跑这类（没有文字、以 < 开头）不猜
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined) {
      const t = e.text.trim()
      const typed = t && !t.startsWith('<') ? t : null
      await update($, round, () => null)
      await update($, asked, () => typed)
      await update($, suggestion, () => null)
      await update($, guessing, () => false)
      await refresh($)
    }
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    const ran = await next(e)
    if (e.agentId !== undefined) return ran
    const prompt = await read($, asked)
    await update($, asked, () => null)
    if (e.reason !== 'answer' || prompt === null || !(await read($, enabled))) return ran
    const t = await $.clock.now()
    await update($, round, () => t)
    void guess($, prompt, e.answer, t)
    return ran
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    // 别的模组（上下文横条）画的那一行照常在；完全关闭时这里什么都不加
    const below = await next(e).catch(() => null)
    if (e.props.hasSurvey || !(await read($, enabled))) return below
    const text = await read($, suggestion)
    const open = await read($, expanded)
    const busy = await read($, guessing)
    const { Box, Button, Text } = $.ui.resolve(e)
    const ready = text !== null && !e.props.isWorking
    // 「建议」按钮一直在：有新建议时字是正常色、外面一圈 suggestion 色细框，没有时字淡、框透明。
    // 桌面画的是系统按钮，按钮里的字设不了颜色，所以靠 dimColor 和外面的框；终端里框占一整行，不加
    const frame = e.surface === 'terminal' ? undefined : 'round'
    const chip = (
      <Box borderStyle={frame} borderColor={ready ? FRAME_READY : FRAME_IDLE} padding={0}>
        <Button key="suggest-toggle" label={open ? '建议 ▴' : '建议 ▾'} dimColor={!ready} onPress={() => void toggle($)} />
      </Box>
    )

    // 收起：按钮放在上下文那一行的最前面
    if (!open) {
      return (
        <Box flexDirection="row" alignItems="center">
          {chip}
          <Text> </Text>
          <Box flexDirection="column" flexGrow={1} flexShrink={1}>
            {below}
          </Box>
        </Box>
      )
    }

    // 展开：上下文在上，建议那一行在下、紧贴输入框，按钮在这一行最前面；不用 rowGap（引擎垫的空占位也会分到一份空），只在这一行上面留一小格
    return (
      <Box flexDirection="column">
        {below}
        <Box flexDirection="row" justifyContent="space-between" alignItems="center" marginTop="xs">
          <Box flexDirection="row" alignItems="center" flexShrink={1}>
            {chip}
            <Text> </Text>
            {ready ? <Text>{text}</Text> : <Text dimColor>{busy ? '正在猜下一句…' : '暂时没有建议'}</Text>}
          </Box>
          {ready && (
            <Box flexDirection="row" alignItems="center">
              <Button key="suggest-fill" label="填入" onPress={() => fill($, text)} />
              <Text> </Text>
              <Button key="suggest-send" label="直接发送" variant="primary" onPress={() => send($, text)} />
            </Box>
          )}
        </Box>
      </Box>
    )
  })
}
