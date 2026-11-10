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
// Autosizing text areas re-measure when web fonts finish loading.
Object.defineProperty(document, "fonts", {
  value: { addEventListener: () => {}, removeEventListener: () => {}, ready: Promise.resolve() },
});
window.IntersectionObserver = Observer as unknown as typeof IntersectionObserver;
// A combobox scrolls its selected option into view, which jsdom does not implement.
Element.prototype.scrollIntoView = function scrollIntoView() {};
