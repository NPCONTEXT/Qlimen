import { expect, mock, test } from 'claude-code/testing'

const SURFACES = ['terminal', 'desktop'] as const

const PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns: 80,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
}

// 压缩前后都至少要有一条消息
const MESSAGES = [{ role: 'user' as const, text: '之前的对话', toolUses: [] }]
const SUMMARY = { messages: [{ role: 'user' as const, text: '摘要', toolUses: [] }] }

const usageAt = (tokens: number) => ({
  startedAt: 0,
  context: { tokens, window: 1_000_000, percent: Math.round(tokens / 10_000) },
  rateLimits: [],
})

// 一个回合结束；横条在回合结束时刷新用量
const turnEnd = (answer: string, extra: Record<string, unknown> = {}) => ({
  answer,
  durationMs: 1,
  isAborted: false,
  turnId: 't1',
  reason: 'answer' as const,
  ...extra,
})

// 画出来的整棵树里按 key 找元素：按钮的 hover 只在这棵树里，find 拿不到（10-27 查过）
const drawnByKey = (node: any, key: string): any => {
  if (node?.props?.key === key) return node
  for (const child of node?.children ?? []) {
    const hit = typeof child === 'object' ? drawnByKey(child, key) : undefined
    if (hit) return hit
  }
  return undefined
}

const CASES = [
  { tokens: 500_000, text: '上下文 50%（50 万 / 100 万）', color: undefined },
  { tokens: 750_000, text: '上下文 75%（75 万 / 100 万）· 该准备交接了', color: 'warning' },
  { tokens: 900_000, text: '上下文 90%（90 万 / 100 万）· 现在交接', color: 'error' },
] as const

for (const surface of SURFACES) {
  for (const c of CASES) {
    test(`${surface}：用量 ${c.tokens} 时横条的文字和颜色`, async ($, on) => {
      on('session.usage', () => ({ value: usageAt(c.tokens) }))
      on('ui.toast', () => ({ value: undefined }))
      on('turn.complete', ($, e) => ({ text: e.answer }))
      await $.turn.complete(turnEnd('你好'))

      const ui = await $.ui.mount({ plugin: 'context-bar', surface, component: 'AbovePrompt', props: PROPS })
      const bar = await ui.find({ type: 'Text' })
      expect(bar?.text).toBe(c.text)
      expect(bar?.props.color).toBe(c.color)
    })
  }
}

for (const trigger of ['manual', 'auto', 'precompute'] as const) {
  test(`${trigger} 压缩：摘要要求带上简体中文，并照常压缩`, async ($, on) => {
    let told: string | undefined
    on('session.usage', () => ({ value: usageAt(300_000) }))
    on('session.compact', ($, e) => {
      told = e.instructions
      return SUMMARY
    })
    const own = trigger === 'manual' ? { instructions: '重点保留 GIF 工具的停点' } : {}
    const ran = await $.session.compact({ trigger, messages: MESSAGES, ...own })

    expect(ran).toMatchObject({ messages: [{ text: '摘要' }] })
    expect(told).toContain('简体中文')
    expect(told).toContain('压缩后提醒')
    if (trigger === 'manual') expect(told).toContain('重点保留 GIF 工具的停点')
  })
}

test('子代理自己的压缩不改摘要要求', async ($, on) => {
  let told: string | undefined = 'unset'
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('session.compact', ($, e) => {
    told = e.instructions
    return SUMMARY
  })
  await $.session.compact({ trigger: 'auto', messages: MESSAGES, agentId: 'a1' })

  expect(told).toBeUndefined()
})

test('跨过 85% 弹一次提示，不重复；降下来再涨过去再弹', async ($, on) => {
  const toasts: string[] = []
  let tokens = 800_000
  on('session.usage', () => ({ value: usageAt(tokens) }))
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('turn.complete', ($, e) => ({ text: e.answer }))
  const refresh = () => $.turn.complete(turnEnd('好'))

  await refresh()
  tokens = 860_000
  await refresh()
  tokens = 900_000
  await refresh()
  tokens = 200_000
  await refresh()
  tokens = 870_000
  await refresh()

  expect(toasts).toEqual(['上下文已到 86%，现在让 Claude 写交接', '上下文已到 87%，现在让 Claude 写交接'])
})

// ---- 「交接并压缩」按钮 ----

test('空闲时显示「交接并压缩」按钮，回合进行中不显示', async ($, on) => {
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))

  const idle = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  expect((await idle.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
  const busy = await $.ui.mount({
    plugin: 'context-bar',
    surface: 'desktop',
    component: 'AbovePrompt',
    props: { ...PROPS, isWorking: true },
  })
  expect(await busy.find({ key: 'handoff' })).toBeUndefined()
})

test('点按钮：发出交接请求；回复最后一行是「交接完成」，排队运行 /compact', async ($, on) => {
  const clock = mock.clock(on)
  const sent: string[] = []
  const runs: { command: string; args: string }[] = []
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('ui.toast', () => ({ value: undefined }))
  on('prompt.submit', ($, e) => {
    sent.push(e.text)
    return { text: e.text }
  })
  on('command.run', ($, e) => {
    runs.push({ command: e.command, args: e.args })
    return { text: '' }
  })
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  await ui.press({ key: 'handoff' })
  await ui.press({ key: 'handoff-confirm' })
  expect(sent).toHaveLength(1)
  expect(sent[0]).toContain('最后一行只写：交接完成')
  expect((await ui.find({ type: 'Text' }))?.text).toContain('交接中')
  expect(await ui.find({ key: 'handoff' })).toBeUndefined()

  await $.turn.complete(turnEnd('【已完成】交接写好了。\n\n**交接完成**'))
  expect(runs).toHaveLength(0)
  expect((await ui.find({ type: 'Text' }))?.text).toContain('压缩中')
  await clock.advance(500)
  await clock.settle()
  expect(runs).toEqual([{ command: 'compact', args: expect.stringContaining('交接写进了哪些文件') }])
  expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
})

const NOT_DONE = [
  ['最后一行不是「交接完成」', turnEnd('我想先问一下：交接写到哪个目录？')],
  ['被打断', turnEnd('交接完成', { reason: 'aborted', isAborted: true })],
  ['出错', turnEnd('交接完成', { reason: 'error' })],
] as const

for (const [name, end] of NOT_DONE) {
  test(`点按钮后${name}：不压缩，弹「没压缩」提示，按钮回来`, async ($, on) => {
    const clock = mock.clock(on)
    const toasts: string[] = []
    let compacted = false
    on('session.usage', () => ({ value: usageAt(300_000) }))
    on('ui.toast', ($, e) => {
      toasts.push(e.text)
      return { value: undefined }
    })
    on('prompt.submit', ($, e) => ({ text: e.text }))
    on('command.run', () => {
      compacted = true
      return { text: '' }
    })
    on('turn.complete', ($, e) => ({ text: e.answer }))
    await $.turn.complete(turnEnd('你好'))

    const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
    await ui.press({ key: 'handoff' })
  await ui.press({ key: 'handoff-confirm' })
    await $.turn.complete(end)
    await clock.advance(10_000)
    await clock.settle()

    expect(compacted).toBe(false)
    expect(toasts.some(text => text.startsWith('没压缩'))).toBe(true)
    expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
  })
}

test('交接中子代理的回合结束不算数，等主回合', async ($, on) => {
  const clock = mock.clock(on)
  let compacted = false
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('ui.toast', () => ({ value: undefined }))
  on('prompt.submit', ($, e) => ({ text: e.text }))
  on('command.run', () => {
    compacted = true
    return { text: '' }
  })
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  await ui.press({ key: 'handoff' })
  await ui.press({ key: 'handoff-confirm' })
  await $.turn.complete(turnEnd('交接完成', { agentId: 'a1' }))
  await clock.advance(10_000)
  await clock.settle()
  expect(compacted).toBe(false)

  await $.turn.complete(turnEnd('交接完成'))
  await clock.advance(1000)
  await clock.settle()
  expect(compacted).toBe(true)
})

test('/compact 被引擎拒绝时，提示里带上原因，按钮回来', async ($, on) => {
  const clock = mock.clock(on)
  const toasts: string[] = []
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('prompt.submit', ($, e) => ({ text: e.text }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  await ui.press({ key: 'handoff' })
  await ui.press({ key: 'handoff-confirm' })
  await $.turn.complete(turnEnd('交接完成'))
  await clock.advance(500)
  await clock.settle()

  expect(toasts.some(text => text.startsWith('没压缩：') && text.length > '没压缩：'.length)).toBe(true)
  expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
})

// ---- 压缩完：横条不显示旧数字 ----

const STALE = '上下文：刚压缩完，下一条回复后更新'

test('压缩完：显示「刚压缩」、不显示按钮、不弹提示；出现新的请求后恢复数字和按钮', async ($, on) => {
  const toasts: string[] = []
  let tokens = 880_000
  on('session.usage', () => ({ value: usageAt(tokens) }))
  on('ui.toast', ($, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('session.compact', () => SUMMARY)
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))
  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  expect(toasts).toHaveLength(1)

  await $.session.compact({ trigger: 'manual', messages: MESSAGES })
  expect((await ui.find({ type: 'Text' }))?.text).toBe(STALE)
  expect(await ui.find({ key: 'handoff' })).toBeUndefined()
  await $.turn.complete(turnEnd('请求大小没变'))
  expect((await ui.find({ type: 'Text' }))?.text).toBe(STALE)

  tokens = 90_000
  await $.turn.complete(turnEnd('好的'))
  expect((await ui.find({ type: 'Text' }))?.text).toBe('上下文 9%（9 万 / 100 万）')
  expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
  expect(toasts).toHaveLength(1)
})

test('预先计算的压缩不算压缩完，横条照常显示数字', async ($, on) => {
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('session.compact', () => SUMMARY)
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))
  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })

  await $.session.compact({ trigger: 'precompute', messages: MESSAGES })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('上下文 30%（30 万 / 100 万）')
})

test('被跳过的压缩不算压缩完，横条照常显示数字', async ($, on) => {
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('session.compact', () => ({ skip: '测试里跳过' }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))
  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })

  await $.session.compact({ trigger: 'manual', messages: MESSAGES })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('上下文 30%（30 万 / 100 万）')
})

// ---- 交接两步确认（10-26 作者要求：点一次只出确认，第二次点才交接） ----

test('点一次只出「确认交接」和「取消」，不发请求；点取消复原', async ($, on) => {
  mock.clock(on)
  const sent: string[] = []
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('prompt.submit', ($, e) => {
    sent.push(e.text)
    return { text: e.text }
  })
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  await ui.press({ key: 'handoff' })
  expect(sent).toHaveLength(0)
  expect((await ui.find({ key: 'handoff-confirm' }))?.props.label).toBe('确认交接')
  expect((await ui.find({ key: 'handoff-cancel' }))?.props.label).toBe('取消')
  expect(await ui.find({ key: 'handoff' })).toBeUndefined()
  // 红框贴着按钮、不用黑色主按钮，和下面黑色的「直接发送」分开；字只能 hover 时变红（桌面系统按钮里的字设不了颜色），所以这一行要带 key（10-27）
  const confirm = await ui.find({ key: 'handoff-confirm' })
  expect(confirm?.props.variant).toBeUndefined()
  expect(drawnByKey(await ui.drawn(), 'handoff-confirm')?.hover?.color).toBe('error')
  expect(await ui.find({ type: 'Box', key: 'context-row' })).toBeDefined()
  expect((await ui.findAll({ type: 'Box' })).filter(b => b.props.borderColor === 'error' && b.props.borderStyle === 'round' && b.props.padding === 0)).toHaveLength(1)

  await ui.press({ key: 'handoff-cancel' })
  expect(sent).toHaveLength(0)
  expect(await ui.find({ key: 'handoff-confirm' })).toBeUndefined()
  expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
})

test('点一次后 5 秒内没确认：自动复原，不发请求', async ($, on) => {
  const clock = mock.clock(on)
  const sent: string[] = []
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('prompt.submit', ($, e) => {
    sent.push(e.text)
    return { text: e.text }
  })
  on('turn.complete', ($, e) => ({ text: e.answer }))
  await $.turn.complete(turnEnd('你好'))

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  await ui.press({ key: 'handoff' })
  await clock.advance(4_000)
  await clock.settle()
  expect((await ui.find({ key: 'handoff-confirm' }))?.props.label).toBe('确认交接')

  await clock.advance(1_500)
  await clock.settle()
  expect(await ui.find({ key: 'handoff-confirm' })).toBeUndefined()
  expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
  expect(sent).toHaveLength(0)
})
