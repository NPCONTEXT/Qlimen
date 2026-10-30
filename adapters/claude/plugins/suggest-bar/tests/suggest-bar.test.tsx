import { expect, mock, test } from 'claude-code/testing'

const PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns: 80,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
}
const USAGE = { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: 0, cache_creation_input_tokens: 0 }
const end = (answer: string, extra: Record<string, unknown> = {}) => ({
  answer,
  durationMs: 1,
  isAborted: false,
  turnId: 't1',
  reason: 'answer' as const,
  ...extra,
})
const mount = ($: any, props: Record<string, unknown> = {}) =>
  $.ui.mount({ plugin: 'suggest-bar', surface: 'desktop', component: 'AbovePrompt', props: { ...PROPS, ...props } })
const CMD = (args: string) => ({ command: 'suggest', args, origin: { kind: 'composer' }, presentation: { isFullscreen: false, columns: 80 } })

// 垫在下面代表引擎：回合开始/结束照常，模型回 reply（给一串就按次序回，null 是交白卷），记下每次请求；
// 输入框上方：引擎自己画的是空的，给了 below 就代表下面还有一个模组画了这一行（上下文横条）；
// $.store 在内存里，store 是开始时存着的；hold：模型先等它放行再答（测「正在猜」）；
// io 记下插件碰过的东西：读输入框、跑外部命令、写文件的次数（这一版一次都不该有），和弹过的提示
type Io = { reads: number; runs: number; writes: number; toasts: string[] }
const newIo = (): Io => ({ reads: 0, runs: 0, writes: 0, toasts: [] })
type Opts = { below?: string; store?: Record<string, unknown>; hold?: Promise<void>; io?: Io }
const engine = (on: any, reply: string | (string | null)[], asks: { model: string; prompt: string }[], opts: Opts = {}) => {
  const replies = typeof reply === 'string' ? null : [...reply]
  const io = opts.io ?? newIo()
  mock.store(on, opts.store ?? {})
  on('prompt.read', () => {
    io.reads += 1
    return { value: { text: '', cursor: 0 } }
  })
  on('process.run', () => {
    io.runs += 1
    return { value: { exitCode: 0, stdout: '', stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
  })
  on('fs.write', () => {
    io.writes += 1
    return { value: undefined }
  })
  on('ui.toast', ($: any, e: any) => {
    io.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.render', ($: any, e: any) => {
    const { Box, Text } = $.ui.resolve(e)
    return opts.below === undefined ? <Box /> : <Text>{opts.below}</Text>
  })
  on('turn.start', ($: any, e: any) => ({ turnId: e.turnId }))
  on('turn.complete', ($: any, e: any) => ({ text: e.answer }))
  on('model.complete', async ($: any, e: any) => {
    asks.push({ model: e.model, prompt: typeof e.prompt === 'string' ? e.prompt : JSON.stringify(e.prompt) })
    if (opts.hold) await opts.hold
    const text = replies === null ? reply : (replies.shift() ?? null)
    return { value: text === null ? { isAnswered: false, reason: 'empty-reply', usage: USAGE } : { isAnswered: true, text, usage: USAGE } }
  })
  on('command.register', () => ({ value: { command: 'suggest' } }))
  return io
}

const frameOf = async (ui: any) => (await ui.findAll({ type: 'Box' })).find((b: any) => b.props.borderStyle === 'round')?.props.borderColor
const texts = async (ui: any) => (await ui.findAll({ type: 'Text' })).map((t: any) => t.text)

// 你发一句、回复完、猜完
const oneTurn = async ($: any, clock: any, said = '这两个方案你推荐哪个？', answer = '……两版各有代价。选哪个？方案一 / 方案二') => {
  await $.turn.start({ text: said, turnId: 't1' })
  await $.turn.complete(end(answer))
  await clock.settle()
}

test('收起（默认）：猜出建议后，上下文那一行最前面的「建议 ▾」亮起，不显示建议和按钮', async ($, on) => {
  const clock = mock.clock(on)
  const asks: { model: string; prompt: string }[] = []
  engine(on, '方案二', asks, { below: '上下文 30%（30 万 / 100 万）' })
  await oneTurn($, clock)

  expect(asks).toHaveLength(1)
  expect(asks[0].model).toBe('opus')
  expect(asks[0].prompt).toContain('这两个方案你推荐哪个？')
  expect(asks[0].prompt).toContain('方案一 / 方案二')

  const ui = await mount($)
  const chip = await ui.find({ key: 'suggest-toggle' })
  expect(chip?.props.label).toBe('建议 ▾')
  expect(chip?.props.dimColor).toBe(false)
  expect(await frameOf(ui)).toBe('suggestion')
  // 按钮和上下文在同一行：最外层是横排
  expect((await ui.findAll({ type: 'Box' }))[0].props.flexDirection).toBe('row')
  expect(await texts(ui)).toContain('上下文 30%（30 万 / 100 万）')
  expect(await texts(ui)).not.toContain('方案二')
  expect(await ui.find({ key: 'suggest-fill' })).toBeUndefined()
})

test('点「建议 ▾」展开：上下文在上、建议一行在下，按钮变「建议 ▴」到第二行；再点收回', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, '方案二', [], { below: '上下文 30%（30 万 / 100 万）' })
  await oneTurn($, clock)

  const ui = await mount($)
  await ui.press({ key: 'suggest-toggle' })
  expect((await ui.find({ key: 'suggest-toggle' }))?.props.label).toBe('建议 ▴')
  expect((await ui.findAll({ type: 'Box' }))[0].props.flexDirection).toBe('column')
  expect(await texts(ui)).toContain('上下文 30%（30 万 / 100 万）')
  expect(await texts(ui)).toContain('方案二')
  expect((await ui.find({ key: 'suggest-fill' }))?.props.label).toBe('填入')
  expect((await ui.find({ key: 'suggest-send' }))?.props.label).toBe('直接发送')
  // 各层不设行距（引擎的空占位不再分到一份空），只在建议这一行上面留一小格
  const boxes = await ui.findAll({ type: 'Box' })
  for (const box of boxes) expect(box.props.rowGap).toBeUndefined()
  expect(boxes.filter((b: any) => b.props.marginTop === 'xs')).toHaveLength(1)

  await ui.press({ key: 'suggest-toggle' })
  expect((await ui.find({ key: 'suggest-toggle' }))?.props.label).toBe('建议 ▾')
  expect(await ui.find({ key: 'suggest-fill' })).toBeUndefined()
})

test('展开后点「填入」：填进输入框，第二行换成「暂时没有建议」，按钮变淡、框透明', async ($, on) => {
  const clock = mock.clock(on)
  const filled: string[] = []
  engine(on, '方案二', [])
  on('prompt.fill', ($, e) => {
    filled.push(e.text)
    return { isFilled: true, text: e.text }
  })
  await oneTurn($, clock)

  const ui = await mount($)
  await ui.press({ key: 'suggest-toggle' })
  await ui.press({ key: 'suggest-fill' })
  expect(filled).toEqual(['方案二'])
  expect(await ui.find({ key: 'suggest-fill' })).toBeUndefined()
  expect(await texts(ui)).toContain('暂时没有建议')
  expect((await ui.find({ key: 'suggest-toggle' }))?.props.dimColor).toBe(true)
  expect(await frameOf(ui)).toBe('#00000000')
})

test('正在猜的那几秒：展开显示「正在猜下一句…」，猜出来就换成建议', async ($, on) => {
  const clock = mock.clock(on)
  let release = () => {}
  const hold = new Promise<void>(r => {
    release = r
  })
  engine(on, '方案二', [], { hold })
  await $.turn.start({ text: '选哪个', turnId: 't1' })
  await $.turn.complete(end('方案一还是方案二？'))

  const ui = await mount($)
  await ui.press({ key: 'suggest-toggle' })
  expect(await texts(ui)).toContain('正在猜下一句…')
  release()
  await clock.settle()
  expect(await texts(ui)).toContain('方案二')
  expect(await texts(ui)).not.toContain('正在猜下一句…')
})

test('猜得慢、你下一句已经发了：旧建议不再冒出来', async ($, on) => {
  const clock = mock.clock(on)
  let release = () => {}
  const hold = new Promise<void>(r => {
    release = r
  })
  engine(on, '方案二', [], { hold })
  await $.turn.start({ text: '选哪个', turnId: 't1' })
  await $.turn.complete(end('方案一还是方案二？'))
  await $.turn.start({ text: '先不选，看看日志', turnId: 't2' })
  release()
  await clock.settle()

  const ui = await mount($)
  expect((await ui.find({ key: 'suggest-toggle' }))?.props.dimColor).toBe(true)
  await ui.press({ key: 'suggest-toggle' })
  expect(await texts(ui)).not.toContain('方案二')
  expect(await texts(ui)).toContain('暂时没有建议')
})

// 「汐」「}」：出过的整句只有一个怪字
for (const reply of ['无', '我来帮你看看这个问题', '好的，我来帮你看', '「」', '这是一句特别特别长的话，长到超过四十个字的上限，所以不应该显示在横条上面的吧', '汐', '}']) {
  test(`模型回「${reply.slice(0, 8)}…」：不显示，按钮淡色；展开是「暂时没有建议」`, async ($, on) => {
    const clock = mock.clock(on)
    engine(on, reply, [])
    await oneTurn($, clock, '好的', '做完了。')
    const ui = await mount($)
    expect((await ui.find({ key: 'suggest-toggle' }))?.props.dimColor).toBe(true)
    await ui.press({ key: 'suggest-toggle' })
    expect(await ui.find({ key: 'suggest-fill' })).toBeUndefined()
    expect(await texts(ui)).toContain('暂时没有建议')
  })
}

for (const text of ['', '<task-notification>后台任务跑完了</task-notification>']) {
  test(`不是你打字发的回合（${text ? '后台通知' : '没有文字'}）：不调 Opus`, async ($, on) => {
    const clock = mock.clock(on)
    const asks: { model: string; prompt: string }[] = []
    engine(on, '继续', asks)
    await $.turn.start({ text, turnId: 't1' })
    await $.turn.complete(end('收到通知。'))
    await clock.settle()
    expect(asks).toHaveLength(0)
  })
}

test('子代理的回合结束不算，被打断的回合也不猜', async ($, on) => {
  const clock = mock.clock(on)
  const asks: { model: string; prompt: string }[] = []
  engine(on, '继续', asks)
  await $.turn.start({ text: '查一下', turnId: 't1' })
  await $.turn.complete(end('子代理的结果', { agentId: 'a1' }))
  await $.turn.complete(end('', { reason: 'aborted', isAborted: true }))
  await clock.settle()
  expect(asks).toHaveLength(0)
})

test('正在回复时：按钮还在但变淡，不显示建议；下一轮开始就清掉旧建议', async ($, on) => {
  const clock = mock.clock(on)
  engine(on, '可以试试', [])
  await oneTurn($, clock, '能修吗', '能，要试吗？')

  const working = await mount($, { isWorking: true })
  expect((await working.find({ key: 'suggest-toggle' }))?.props.dimColor).toBe(true)
  const ui = await mount($)
  await ui.press({ key: 'suggest-toggle' })
  expect((await ui.find({ key: 'suggest-fill' }))?.props.label).toBe('填入')
  await $.turn.start({ text: '可以试试', turnId: 't2' })
  expect(await (await mount($)).find({ key: 'suggest-fill' })).toBeUndefined()
})

test('展开后点「直接发送」：把建议当作你的消息发出去，建议消失', async ($, on) => {
  const clock = mock.clock(on)
  const sent: string[] = []
  engine(on, '检查剩余五篇文章的审核状态', [])
  on('prompt.submit', ($, e) => {
    sent.push(e.text)
    return { text: e.text }
  })
  await oneTurn($, clock, '还有几篇没发？', '还剩五篇在审核。')

  const ui = await mount($)
  await ui.press({ key: 'suggest-toggle' })
  await ui.press({ key: 'suggest-send' })
  await clock.settle()
  expect(sent).toEqual(['检查剩余五篇文章的审核状态'])
  expect(await ui.find({ key: 'suggest-send' })).toBeUndefined()
  expect(await ui.find({ key: 'suggest-fill' })).toBeUndefined()
})

// 对照测试里 Opus 出过的乱码开头、句尾乱码、乱码符号，和不该误伤的你的口吻、单字回答
for (const [reply, shown] of [
  ['making可以，都按这个来', '可以，都按这个来'],
  ['[[OUTPUT]]好的，明天见', '好的，明天见'],
  ['了，登录成功', '登录成功'],
  ['好的，我先看看', '好的，我先看看'],
  ['乙吧，其他都行[[RESPONSE_END]]', '乙吧，其他都行'],
  ['�好，辛苦了', '好，辛苦了'],
  ['好', '好'],
]) {
  test(`模型回「${reply}」：显示「${shown}」`, async ($, on) => {
    const clock = mock.clock(on)
    engine(on, reply, [])
    await oneTurn($, clock, '好了吗', '好了，你看看。')
    const ui = await mount($)
    await ui.press({ key: 'suggest-toggle' })
    const all = await texts(ui)
    expect(all).toContain(shown)
    expect(all.some((t: string) => t !== shown && t.includes(shown))).toBe(false)
  })
}

test('交白卷就再问一次：第二次答上来就显示', async ($, on) => {
  const clock = mock.clock(on)
  const asks: { model: string; prompt: string }[] = []
  engine(on, [null, '好'], asks)
  await oneTurn($, clock, '行吗', '行，要我做吗？')
  expect(asks).toHaveLength(2)
  const ui = await mount($)
  await ui.press({ key: 'suggest-toggle' })
  expect((await ui.find({ key: 'suggest-fill' }))?.props.label).toBe('填入')
})

test('两次都交白卷：不显示，也不问第三次', async ($, on) => {
  const clock = mock.clock(on)
  const asks: { model: string; prompt: string }[] = []
  engine(on, [null, null], asks)
  await oneTurn($, clock, '行吗', '行，要我做吗？')
  expect(asks).toHaveLength(2)
  expect((await (await mount($)).find({ key: 'suggest-toggle' }))?.props.dimColor).toBe(true)
})

test('只猜不记：不读输入框、不跑外部命令、不写文件，几轮下来也一样', async ($, on) => {
  const clock = mock.clock(on)
  const io = newIo()
  engine(on, '方案二', [], { io })
  await oneTurn($, clock)
  await clock.advance(10_000)
  await $.turn.start({ text: '方案二', turnId: 't2' })
  await $.turn.complete(end('好，按方案二做。'))
  await clock.advance(10_000)
  await clock.settle()
  expect(io.reads).toBe(0)
  expect(io.runs).toBe(0)
  expect(io.writes).toBe(0)
  expect(io.toasts).toHaveLength(0)
})

test('/suggest off：完全关闭——按钮消失、不调 Opus；/suggest on 恢复', async ($, on) => {
  const clock = mock.clock(on)
  const asks: { model: string; prompt: string }[] = []
  engine(on, '先查日志', asks, { below: '上下文 30%（30 万 / 100 万）' })
  const offed = await $.command.run(CMD('off'))
  expect(offed.text).toContain('完全关闭')
  expect((await $.command.run(CMD(''))).text).toContain('现在完全关闭')

  await oneTurn($, clock, '跑完了', '要我先查什么？')
  expect(asks).toHaveLength(0)
  const ui = await mount($)
  expect(await ui.find({ key: 'suggest-toggle' })).toBeUndefined()
  expect(await texts(ui)).toContain('上下文 30%（30 万 / 100 万）')

  expect((await $.command.run(CMD('on'))).text).toContain('已打开')
  expect((await (await mount($)).find({ key: 'suggest-toggle' }))?.props.label).toBe('建议 ▾')
  await $.turn.start({ text: '跑一下测试', turnId: 't2' })
  await $.turn.complete(end('跑完了，全过。'))
  await clock.settle()
  expect(asks).toHaveLength(1)
})

test('别的会话关了（存的是 off）：这边下一轮开始照着关，不猜、不画按钮', async ($, on) => {
  const clock = mock.clock(on)
  const asks: { model: string; prompt: string }[] = []
  engine(on, '先查日志', asks, { store: { mode: 'off' } })
  await oneTurn($, clock, '跑完了', '要我先查什么？')
  expect(asks).toHaveLength(0)
  expect(await (await mount($)).find({ key: 'suggest-toggle' })).toBeUndefined()
})
