// jsdom lacks the browser APIs Mantine's components read at render.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

class Observer {
  observe() {}
  unobserve() {}
  disconnect() {}
}
window.ResizeObserver = Observer as unknown as typeof ResizeObserver;
window.IntersectionObserver = Observer as unknown as typeof IntersectionObserver;
