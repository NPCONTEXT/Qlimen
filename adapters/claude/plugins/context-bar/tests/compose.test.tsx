import { expect, test } from 'claude-code/testing'

const PROPS = {
  hasSurvey: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns: 80,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
}

const usageAt = (tokens: number) => ({
  startedAt: 0,
  context: { tokens, window: 1_000_000, percent: Math.round(tokens / 10_000) },
  rateLimits: [],
})

test('方案乙：下面别的模组画的一行（建议）排在上下文这一行下面，两行都在', async ($, on) => {
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  // 这个钩子代表排在下面的模组（比如建议横条）
  on('ui.render', ($, e) => {
    const { Text } = $.ui.resolve(e)
    return <Text>建议：方案二</Text>
  })
  await $.turn.complete({ answer: '你好', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('上下文 30%（30 万 / 100 万）')
  expect((await ui.find({ type: 'Text', text: /建议/ }))?.text).toBe('建议：方案二')
  expect((await ui.find({ key: 'handoff' }))?.props.label).toBe('交接并压缩')
})

test('下面只有引擎的空占位时：各层都不设行距，只剩上下文一行也不偏上', async ($, on) => {
  on('session.usage', () => ({ value: usageAt(300_000) }))
  on('turn.complete', ($, e) => ({ text: e.answer }))
  // 代表引擎垫在最底下的空占位（10-27 作者反馈：rowGap 给它也留了一份空，横条只剩一行时偏上）
  on('ui.render', ($, e) => {
    const { Box } = $.ui.resolve(e)
    return <Box />
  })
  await $.turn.complete({ answer: '你好', durationMs: 1, isAborted: false, turnId: 't1', reason: 'answer' })

  const ui = await $.ui.mount({ plugin: 'context-bar', surface: 'desktop', component: 'AbovePrompt', props: PROPS })
  expect((await ui.find({ type: 'Text' }))?.text).toBe('上下文 30%（30 万 / 100 万）')
  const boxes = await ui.findAll({ type: 'Box' })
  expect(boxes.length).toBeGreaterThan(1)
  for (const box of boxes) {
    expect(box.props.rowGap).toBeUndefined()
    expect(box.props.gap).toBeUndefined()
  }
})
