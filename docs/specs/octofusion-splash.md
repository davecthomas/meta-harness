# The Octofusion AI splash

## Why

The sign-in screen is the first thing a person sees, and the Help page is where they go to understand the product. Both open with the octopus alive: its arms moving the way a real octopus's do, then the name and the tagline. It was built by hand, with the owner judging the motion in a browser, since a coding agent cannot see what it draws. It is built: `console/src/brand/` holds it, and this spec says what it does. The rest of the rebrand is `octofusion-rebrand.md`.

## What we have

- `console/brand/source/octofusion-symbol.svg`: the whole octopus, on 1418 units. Its eight arms are not separate shapes: the file is three color layers, and one path in each layer covers all the arms.
- `console/brand/source/octofusion-wordmark.svg`: the name, with its magenta fill.
- The tagline exists only as paths inside `octofusion-full-lockup.svg`, in the band below y = 1250 of its 1426 units. No font came with the artwork.
- A 10-second video came with the artwork. It is not used: it is 3.8 MB, with a baked-in background and a sound track.

## What we want

### How the arms move

The arms bend along their length, as a real octopus's do. A wave of curl travels from where an arm joins the body out to its tip, so the tip trails the base and the arm never swings as a stiff piece. Each arm moves out of step with its neighbors. The head and body rise and settle slightly, as if breathing.

The artwork is not cut into parts. Every point of every path is moved by one field of motion that follows each arm's centerline, so the three color layers stay in register and no gap opens between an arm and the body. With the motion at zero the drawing is the symbol, point for point.

### What plays

1. The octopus rises into view.
2. Its arms flail for about 3 seconds: wide, quick curls.
3. The arms settle toward the symbol's pose.
4. As they begin to settle, the name comes out of the arms: it starts small behind them and slides down to sit just under their tips.
5. Once the name has landed, the tagline rolls out from under it.

The three sit close together, the name just under the arm tips and the tagline just under the name.

After that the arms keep a slow, small sway for as long as the screen is open.

### Where it shows

- **The sign-in screen**, above the sign-in card.
- **The Help page**, at its top, each time Help is opened.

### What it must not do

- It never delays sign-in. The sign-in card and Google's button are on the screen and usable from the first paint.
- It plays in full once in a browser session on each screen. Opened again in the same session, it starts on the settled pose with the slow sway.
- With the system's reduced-motion setting on, nothing moves: the settled octopus, the name and the tagline show.
- It stops drawing while its tab is hidden or it is scrolled out of view.
- It is drawn from SVG by the browser. No video is loaded and no runtime dependency is added.
- The artwork, the animation and its code add under 200 KB to what a screen loads, before compression, and under 60 KB compressed.

## Where it lives

- `console/src/brand/octopus.ts`: each arm's centerline, and the motion. It draws by rewriting the paths' points on each frame, which takes about 3 milliseconds.
- `console/src/brand/Splash.tsx` and `splash.css`: the component the two screens show, and what plays when.
- `console/src/brand/octopus.svg`, `wordmark.svg` and `tagline.svg`: the artwork as shipped, made from `console/brand/source/` with its coordinates in whole units. The tagline is cut from the full lockup.

## How we will know it works

- A person watching it says the arms move like an animal's and the settle is smooth. The owner approved it on a preview on 2026-11-22.
- With motion at zero, the drawn paths equal the symbol's.
- The sign-in screen's Google button works while the splash plays.
- With reduced motion on, neither screen animates.
- The console's tests cover the three states on each screen: first visit, a later visit in the session, and reduced motion.

## Constraints

- The artwork is not redrawn or recolored. The outer outline stays, and no arm takes a color of its own.
- Text the console shows keeps the writing rules.
