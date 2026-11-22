import { useEffect, useRef, useState } from "react";
import symbol from "./octopus.svg?raw";
import wordmark from "./wordmark.svg";
import tagline from "./tagline.svg";
import { createOctopus, energyAt, FLAIL_SECONDS } from "./octopus";
import "./splash.css";

export const NAME = "Octofusion AI";
export const TAGLINE = "The Open Meta-Harness for Coding at Scale";

// When the name comes out of the arms, and when the tagline rolls out from under it, in seconds.
const NAME_AT = FLAIL_SECONDS + 0.2;
const TAGLINE_AT = FLAIL_SECONDS + 1.2;
// Far enough along that the arms have settled: where a splash that has already played starts from.
const SETTLED = 60;

function reducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

// Whether this screen's splash has played in this browser session. Storage can be closed to a page (a
// private window, blocked site data): then it plays each time, which is the harmless way to be wrong.
function played(where: string): boolean {
  try {
    return window.sessionStorage.getItem(`splash:${where}`) === "played";
  } catch {
    return false;
  }
}
function markPlayed(where: string): void {
  try {
    window.sessionStorage.setItem(`splash:${where}`, "played");
  } catch {
    // Nothing to do: it plays again next time.
  }
}

// The octopus, alive: its arms flail, then settle into a slow sway, as the name comes out of them and the
// tagline rolls out from under the name. It plays in full once in a browser session for each screen it is on
// (`where`); opened again, it starts settled. With the system's reduced-motion setting on, nothing moves. It
// draws only while it is on screen and its tab is showing.
export function Splash({ where }: { where: string }) {
  const host = useRef<HTMLDivElement>(null);
  const [still] = useState(reducedMotion);
  const [seen] = useState(() => played(where));
  // What has come into view: the octopus, the name, the tagline.
  const [shown, setShown] = useState(still || seen ? 3 : 0);

  useEffect(() => {
    const svg = host.current?.querySelector("svg");
    if (!svg) return;
    const octopus = createOctopus(svg as SVGSVGElement);
    if (still || typeof window.requestAnimationFrame !== "function") {
      octopus.draw(0, -1);
      return;
    }
    markPlayed(where);
    let frame = 0, visible = true, stage = seen ? 3 : 1;
    const began = performance.now() - (seen ? SETTLED * 1000 : 0);
    if (!seen) setShown(1);
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
    if (host.current) watcher?.observe(host.current);
    return () => {
      visible = false;
      window.cancelAnimationFrame(frame);
      watcher?.disconnect();
    };
  }, [still, seen, where]);

  return (
    <div className="splash" data-testid="splash" data-shown={shown} data-still={still || undefined}>
      {/* The artwork's own markup, from this repository: drawn here so its paths can be moved. */}
      <div ref={host} className={`splash-octopus${shown >= 1 ? " in" : ""}`} role="img" aria-label={`${NAME} octopus`} dangerouslySetInnerHTML={{ __html: symbol }} />
      <div className="splash-name">
        <img className={`splash-wordmark${shown >= 2 ? " in" : ""}`} src={wordmark} alt={NAME} />
      </div>
      <div className="splash-roll">
        <img className={`splash-tagline${shown >= 3 ? " in" : ""}`} src={tagline} alt={TAGLINE} />
      </div>
    </div>
  );
}
