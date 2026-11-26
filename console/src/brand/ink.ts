// The octopus inks: a click right on it lets out a black cloud that spreads from where it was clicked over the
// whole screen, holds a moment, and fades away. The name stays on top of the ink (splash.css). It is a joke,
// not a control: it never blocks a click, and one cloud runs at a time.

// How long the whole cloud lasts, in milliseconds: it spreads, holds, then fades (splash.css keeps the same).
export const INK_MS = 3200;
// The puffs of ink, each offset from the click and a little later than the one before, so the cloud billows
// instead of growing as one circle.
export const PUFFS: { dx: number; dy: number; delay: number }[] = [
  { dx: 0, dy: 0, delay: 0 },
  { dx: -9, dy: 4, delay: 60 },
  { dx: 8, dy: -6, delay: 110 },
  { dx: -4, dy: -10, delay: 170 },
  { dx: 11, dy: 7, delay: 230 },
];

// Whether a click at (x, y) is on the octopus itself, its box with no margin round it.
export function onBox(box: { left: number; top: number; width: number; height: number }, x: number, y: number): boolean {
  return x >= box.left && x <= box.left + box.width && y >= box.top && y <= box.top + box.height;
}

// Lets out a cloud of ink from (x, y), on screen coordinates. Returns false when a cloud is already out.
export function ink(x: number, y: number, into: HTMLElement = document.body): boolean {
  if (into.querySelector(":scope > .splash-ink")) return false;
  const cloud = document.createElement("div");
  cloud.className = "splash-ink";
  cloud.setAttribute("aria-hidden", "true");
  cloud.dataset.testid = "splash-ink";
  for (const puff of PUFFS) {
    const blob = document.createElement("span");
    blob.className = "splash-ink-puff";
    blob.style.left = `${x}px`;
    blob.style.top = `${y}px`;
    blob.style.setProperty("--dx", `${puff.dx}vmax`);
    blob.style.setProperty("--dy", `${puff.dy}vmax`);
    blob.style.animationDelay = `${puff.delay}ms`;
    cloud.appendChild(blob);
  }
  into.appendChild(cloud);
  into.classList.add("splash-inking");
  window.setTimeout(() => {
    cloud.remove();
    into.classList.remove("splash-inking");
  }, INK_MS);
  return true;
}
