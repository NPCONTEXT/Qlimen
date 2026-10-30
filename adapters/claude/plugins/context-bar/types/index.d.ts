export type Usage = { tokens: number; window: number; percent: number }

// 「交接并压缩」走到哪一步：没在做 / 等交接那一回合结束 / 正在压缩
export type Phase = 'idle' | 'handoff' | 'compacting'

declare module 'claude-code' {
  interface PluginState {
    'context-bar': { usage: Usage | null; warned: number; phase: Phase; stale: number | null; confirming: number | null }
  }
}
