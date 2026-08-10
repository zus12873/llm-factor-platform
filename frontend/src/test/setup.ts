import "@testing-library/jest-dom/vitest"

/**
 * jsdom implements neither `matchMedia` nor `ResizeObserver`, and antd's
 * responsive layout uses both. Stubbing them is not hiding a bug: the real
 * browser supplies them, and the acceptance run in a real browser is what covers
 * the behaviour they drive.
 */
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia
}

if (!globalThis.ResizeObserver) {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
}
