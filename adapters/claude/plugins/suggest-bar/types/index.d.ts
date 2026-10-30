// 这一轮猜出的下一句；null 表示没有（正在回复、没猜出来、已填入或已发送）
export type Suggestion = string | null

declare module 'claude-code' {
  interface PluginState {
    // asked：这一轮你自己打的字（后台通知等回合是 null）；round：这一轮回复完的时刻（毫秒）；都放在 $.state 里，模组重新加载也不丢
    'suggest-bar': {
      suggestion: Suggestion
      asked: string | null
      round: number | null
      expanded: boolean
      enabled: boolean
      guessing: boolean
    }
  }
}
