// The octopus's eyes follow the pointer: a slit pupil in each glowing eye, kept inside it, turned toward
// wherever the pointer is on the page. A small surprise on the splash screens.

type Point = [number, number];

// Each eye's outline in the symbol's own units (1418 square), the left one from the artwork, the right its
// mirror about the octopus's center line (octopus.ts).
const CENTER = 730;
const LEFT_EYE: Point[] = [[590, 597], [626, 612], [681, 652], [641, 657], [612, 648], [601, 630]];
export const EYES: Point[][] = [LEFT_EYE, LEFT_EYE.map(([x, y]): Point => [2 * CENTER - x, y])];
// Where each pupil rests, and how far it may move from there, across and up and down.
export const RESTS: Point[] = [[636, 636], [2 * CENTER - 636, 636]];
export const REACH: Point = [20, 9];
// How far the pointer must be, in the symbol's units, for the pupil to look all the way that way.
const FULL = 420;
// How quickly a pupil catches up with where it should look, per second.
const FOLLOW = 14;

// Where a pupil sits, as an offset from its rest, for a pointer at (x, y) in the symbol's units.
export function lookAt(rest: Point, x: number, y: number): Point {
  const dx = x - rest[0], dy = y - rest[1];
  const far = Math.hypot(dx, dy);
  if (!far) return [0, 0];
  const share = Math.min(1, far / FULL);
  return [(dx / far) * REACH[0] * share, (dy / far) * REACH[1] * share];
}

export interface Eyes {
  // The pointer is at this place on the screen.
  look: (clientX: number, clientY: number) => void;
  // Move the pupils toward where they should look; dt in seconds.
  step: (dt: number) => void;
}

const NS = "http://www.w3.org/2000/svg";

// Adds the pupils to the octopus's drawing, after octopus.ts has read its paths, so they are not bent with the arms.
export function createEyes(svg: SVGSVGElement): Eyes {
  const group = document.createElementNS(NS, "g");
  group.setAttribute("aria-hidden", "true");
  const defs = document.createElementNS(NS, "defs");
  group.appendChild(defs);
  const pupils = EYES.map((outline, i) => {
    const clip = document.createElementNS(NS, "clipPath");
    clip.setAttribute("id", `splash-eye-${i}`);
    const shape = document.createElementNS(NS, "polygon");
    shape.setAttribute("points", outline.map((p) => p.join(",")).join(" "));
    clip.appendChild(shape);
    defs.appendChild(clip);
    const holder = document.createElementNS(NS, "g");
    holder.setAttribute("clip-path", `url(#splash-eye-${i})`);
    const pupil = document.createElementNS(NS, "ellipse");
    pupil.setAttribute("class", "splash-pupil");
    pupil.setAttribute("cx", String(RESTS[i][0]));
    pupil.setAttribute("cy", String(RESTS[i][1]));
    pupil.setAttribute("rx", "9");
    pupil.setAttribute("ry", "21");
    pupil.setAttribute("fill", "#0a0a14");
    holder.appendChild(pupil);
    group.appendChild(holder);
    return pupil;
  });
  svg.appendChild(group);

  const now: Point[] = RESTS.map(() => [0, 0]);
  const wanted: Point[] = RESTS.map(() => [0, 0]);

  function look(clientX: number, clientY: number): void {
    const box = svg.getBoundingClientRect();
    if (!box.width) return;
    const unit = 1418 / box.width;
    const x = (clientX - box.left) * unit, y = (clientY - box.top) * unit;
    RESTS.forEach((rest, i) => (wanted[i] = lookAt(rest, x, y)));
  }

  function step(dt: number): void {
    const k = 1 - Math.exp(-FOLLOW * Math.max(0, dt));
    pupils.forEach((pupil, i) => {
      now[i] = [now[i][0] + (wanted[i][0] - now[i][0]) * k, now[i][1] + (wanted[i][1] - now[i][1]) * k];
      pupil.setAttribute("transform", `translate(${now[i][0].toFixed(1)} ${now[i][1].toFixed(1)})`);
    });
  }

  return { look, step };
}
