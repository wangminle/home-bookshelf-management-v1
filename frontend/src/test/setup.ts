/**
 * WBS-3：Vitest 测试环境初始化
 *
 * 全局 mock 和测试工具配置。
 */

// Mock clipboard API
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockResolvedValue(undefined),
  },
})

// Mock matchMedia (used by some Vue components)
window.matchMedia = window.matchMedia || vi.fn().mockReturnValue({
  matches: false,
  media: '',
  onchange: null,
  addListener: vi.fn(),
  removeListener: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  dispatchEvent: vi.fn(),
})

// Mock execCommand (jsdom doesn't implement it; used by clipboard fallback)
document.execCommand = vi.fn().mockReturnValue(true)
