import { useEffect, useRef, useState } from "react";
import symbol from "./octopus.svg?raw";
import wordmark from "./wordmark.svg";
import tagline from "./tagline.svg";
import { createOctopus, energyAt, FLAIL_SECONDS } from "./octopus";
import "./splash.css";

export const NAME = "Octofusion AI";
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

// When the name comes out of the arms, and when the tagline rolls out from under it, in seconds.
const NAME_AT = FLAIL_SECONDS + 0.2;
const TAGLINE_AT = FLAIL_SECONDS + 1.2;
function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// The octopus, alive: its arms flail, then settle into a slow sway, as the name comes out of them and the
// tagline rolls out from under the name. It plays in full every time the screen it is on opens: a splash that
// played once in a session and then sat still read as broken. With the system's reduced-motion setting on,
// nothing moves. It draws only while it is on screen and its tab is showing. `where` names the screen.
export function Splash({ where }: { where: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [still] = useState(reducedMotion);
  // Whether bubbles rise behind the octopus. Off, as the splash has always been; the spacebar turns them on and
  // off, so the owner can see the two side by side before choosing. Not while typing in a field.
  const [bubbles, setBubbles] = useState(false);
  useEffect(() => {
    const toggle = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat) return;
      const at = event.target as HTMLElement | null;
      if (at && (at.isContentEditable || ["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(at.tagName))) return;
      event.preventDefault();
      setBubbles((on) => !on);
    };
    window.addEventListener("keydown", toggle);
    return () => window.removeEventListener("keydown", toggle);
  }, []);
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
    let frame = 0, visible = true, stage = 1;
    const began = performance.now();
    setShown(1);
    const tick = (now: number) => {
      const seconds = (now - began) / 1000;
      octopus.draw(seconds, energyAt(seconds));
      const next = seconds > TAGLINE_AT ? 3 : seconds > NAME_AT ? 2 : 1;
      if (next > stage) {
        stage = next;
        setShown(next);
      }
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
    };
  }, [still]);

  return (
    <div className="splash" data-testid="splash" data-where={where} data-shown={shown} data-still={still || undefined} data-bubbles={bubbles || undefined}>
      <div className="splash-sea">
        {/* Bubbles rise from behind the octopus, small and of several sizes. Decoration only, so no reader
            is told of them, and with reduced motion there are none. */}
        {!still && bubbles && (
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
        <img className={`splash-wordmark${shown >= 2 ? " in" : ""}`} src={wordmark} alt={NAME} />
      </div>
      <div className="splash-roll">
        <img className={`splash-tagline${shown >= 3 ? " in" : ""}`} src={tagline} alt={TAGLINE} />
      </div>
    </div>
  );
}
