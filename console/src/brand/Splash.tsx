import { useEffect, useRef, useState } from "react";
import symbol from "./octopus.svg?raw";
import wordmark from "./wordmark.svg?raw";
import { ink, onBox, siphon } from "./ink";
import tagline from "./tagline.svg?raw";
import { createOctopus, energyAt, FLAIL_SECONDS } from "./octopus";
import { createEyes } from "./eyes";
import { spawner, spawnSeconds } from "./spawn";
import "./splash.css";

export const NAME = "Muxxy";
export const TAGLINE = "The Open Meta-Harness for Coding at Scale";

// The bubbles that rise from behind the octopus: where each starts across its width (percent), its size in
// pixels, how long one rise takes and when it first starts, in seconds. Fixed, so the splash is the same on
// every visit and in every test; the different speeds and delays keep them from rising in step.
export const BUBBLES: { left: number; size: number; rise: number; delay: number }[] = [
  { left: 18, size: 6, rise: 6.5, delay: 0.4 },
  { left: 27, size: 11, rise: 8.2, delay: 2.1 },
  { left: 36, size: 5, rise: 5.6, delay: 3.4 },
  { left: 44, size: 8, rise: 7.4, delay: 1.2 },
  { left: 53, size: 4, rise: 5.2, delay: 4.6 },
  { left: 60, size: 13, rise: 9.0, delay: 0.9 },
  { left: 68, size: 6, rise: 6.1, delay: 2.8 },
  { left: 76, size: 9, rise: 7.8, delay: 5.2 },
  { left: 83, size: 5, rise: 5.9, delay: 1.7 },
  { left: 31, size: 7, rise: 7.0, delay: 6.0 },
  { left: 64, size: 4, rise: 5.4, delay: 3.9 },
];

// When the octopus starts spawning the name's letters, and when the tagline rolls out from under the name once
// they have all arrived, in seconds. The wordmark has five letters.
export const NAME_AT = FLAIL_SECONDS * 0.55;
export const TAGLINE_AT = NAME_AT + spawnSeconds(5) + 0.2;
// How far around the octopus a click still makes it swipe, as a share of its width on every side.
export const SWIPE_NEAR = 0.5;

// Whether a click at (x, y) on the screen is near enough the octopus's box for it to swipe.
export function nearBox(box: { left: number; top: number; width: number; height: number }, x: number, y: number): boolean {
  const pad = box.width * SWIPE_NEAR;
  return x >= box.left - pad && x <= box.left + box.width + pad && y >= box.top - pad && y <= box.top + box.height + pad;
}

function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// The octopus, alive: its arms flail, then settle into a slow sway, as the name comes out of them and the
// tagline rolls out from under the name. Its eyes follow the pointer, a click near it gets a swipe of an arm, and
// a click right on it makes it ink: a cloud that billows out from under it, drifts and dissolves (ink.ts).
// It plays in full every time the screen it is on opens: a splash that
// played once in a session and then sat still read as broken. With the system's reduced-motion setting on,
// nothing moves. It draws only while it is on screen and its tab is showing. `where` names the screen.
export function Splash({ where }: { where: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [still] = useState(reducedMotion);
  // What has come into view: the octopus, the name, the tagline.
  const [shown, setShown] = useState(still ? 3 : 0);

  useEffect(() => {
    const node = host.current;
    if (!node) return;
    // The artwork's own markup, from this repository, put in by hand and once. Given to React to render, it
    // was written again on every render, and the animation went on moving a drawing no longer on the page.
    node.innerHTML = symbol;
    const svg = node.querySelector("svg");
    if (!svg) return;
    const octopus = createOctopus(svg as SVGSVGElement);
    if (still || typeof window.requestAnimationFrame !== "function") {
      octopus.draw(0, -1);
      return;
    }
    let frame = 0, visible = true, stage = 1, last = 0;
    const began = performance.now();
    setShown(1);
    // Its eyes follow the pointer, and a click near it gets a swipe of the nearest arm.
    const eyes = createEyes(svg as SVGSVGElement);
    const look = (event: PointerEvent) => eyes.look(event.clientX, event.clientY);
    const poke = (event: PointerEvent) => {
      const box = svg.getBoundingClientRect();
      if (!box.width || !nearBox(box, event.clientX, event.clientY)) return;
      // Right on it, it inks; near it, it swipes an arm at the click.
      if (onBox(box, event.clientX, event.clientY)) {
        // Wherever it was clicked, the ink comes from under its body.
        const from = siphon(box);
        ink(from.x, from.y);
        return;
      }
      const unit = 1418 / box.width;
      eyes.look(event.clientX, event.clientY);
      octopus.swipe((event.clientX - box.left) * unit, (event.clientY - box.top) * unit, (performance.now() - began) / 1000);
    };
    window.addEventListener("pointermove", look);
    window.addEventListener("pointerdown", poke);
    const tick = (now: number) => {
      const seconds = (now - began) / 1000;
      octopus.draw(seconds, energyAt(seconds));
      eyes.step(last ? (now - last) / 1000 : 0);
      last = now;
      const next = seconds > TAGLINE_AT ? 3 : seconds > NAME_AT ? 2 : 1;
      if (next > stage) {
        stage = next;
        setShown(next);
      }
      if (!visible) last = 0;
      frame = visible ? window.requestAnimationFrame(tick) : 0;
    };
    frame = window.requestAnimationFrame(tick);
    // Scrolled out of view, it stops drawing; back in view, it goes on from where the clock is.
    const watcher =
      typeof IntersectionObserver === "function"
        ? new IntersectionObserver(([entry]) => {
            visible = entry.isIntersecting;
            if (visible && !frame) frame = window.requestAnimationFrame(tick);
          })
        : null;
    watcher?.observe(node);
    return () => {
      visible = false;
      window.cancelAnimationFrame(frame);
      watcher?.disconnect();
      window.removeEventListener("pointermove", look);
      window.removeEventListener("pointerdown", poke);
    };
  }, [still]);

  // The name's letters, one path each: put in once, by hand, as the octopus is, in order from left to right.
  const name = useRef<HTMLDivElement>(null);
  const letters = useRef<SVGPathElement[]>([]);
  useEffect(() => {
    const node = name.current;
    if (!node) return;
    // The box names itself to readers; the drawing inside it is not named again.
    node.innerHTML = wordmark.replace('role="img"', 'aria-hidden="true"');
    const paths = [...node.querySelectorAll<SVGPathElement>("path")];
    const leftOf = (path: SVGPathElement) => {
      try {
        return path.getBBox().x;
      } catch {
        return paths.indexOf(path);
      }
    };
    letters.current = [...paths].sort((a, b) => leftOf(a) - leftOf(b));
    letters.current.forEach((path) => path.classList.add("splash-letter"));
  }, []);

  // The octopus spawns them: each comes out from under its body, small, and swims to its place, writhing as its
  // arms do (spawn.ts). They start once its arms have begun to settle, and stop as the wordmark.
  useEffect(() => {
    const node = name.current;
    const svg = node?.querySelector("svg");
    if (shown < 2 || still || !node || !svg || !host.current) {
      if (shown >= 2 || still) letters.current.forEach((path) => (path.style.opacity = "1"));
      return;
    }
    // Under the octopus's body, in the word's own units: the octopus is drawn above the name and over it. A
    // browser that cannot measure the drawing (a test's) shows the letters at rest.
    const box = svg.viewBox?.baseVal;
    if (!box) {
      letters.current.forEach((path) => (path.style.opacity = "1"));
      return;
    }
    const there = svg.getBoundingClientRect();
    const octopus = host.current.getBoundingClientRect();
    const scale = there.width ? box.width / there.width : 1;
    const from: [number, number] = [
      box.x + (octopus.left + octopus.width / 2 - there.left) * scale,
      box.y + (octopus.top + octopus.height * 0.5 - there.top) * scale,
    ];
    const spawn = spawner(letters.current, from);
    if (!spawn) {
      letters.current.forEach((path) => (path.style.opacity = "1"));
      return;
    }
    let frame = 0;
    const began = performance.now();
    // It goes on drawing after the letters arrive, for their faint wriggle. The browser pauses it in a hidden tab.
    const step = (now: number) => {
      spawn.draw((now - began) / 1000);
      frame = window.requestAnimationFrame(step);
    };
    frame = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(frame);
    // Once: when the name is due. A later stage does not restart it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown >= 2, still]);

  return (
    <div className="splash" data-testid="splash" data-where={where} data-shown={shown} data-still={still || undefined}>
      <div className="splash-sea">
        {/* Bubbles rise from behind the octopus, small and of several sizes. Decoration only, so no reader
            is told of them, and with reduced motion there are none. */}
        {!still && (
          <div className={`splash-bubbles${shown >= 1 ? " in" : ""}`} aria-hidden="true" data-testid="splash-bubbles">
            {BUBBLES.map((b, i) => (
              <span
                key={i}
                className="splash-bubble"
                style={{ left: `${b.left}%`, width: b.size, height: b.size, animationDuration: `${b.rise}s`, animationDelay: `${b.delay}s` }}
              />
            ))}
          </div>
        )}
        {/* Empty to React: the effect above draws the octopus into it and moves its paths. */}
        <div ref={host} className={`splash-octopus${shown >= 1 ? " in" : ""}`} role="img" aria-label={`${NAME} octopus`} />
      </div>
      <div className="splash-name">
        {/* Empty to React: the effect below puts the name's letters in, so each can move on its own. */}
        <div ref={name} className={`splash-wordmark${shown >= 2 ? " in" : ""}`} role="img" aria-label={NAME} data-testid="splash-wordmark" />
      </div>
      <div className="splash-roll">
        {/* Drawn in the page, not as an image, so each theme can color it (splash.css). */}
        <div className={`splash-tagline${shown >= 3 ? " in" : ""}`} role="img" aria-label={TAGLINE} dangerouslySetInnerHTML={{ __html: tagline.replace('role="img"', 'aria-hidden="true"') }} />
      </div>
    </div>
  );
}
