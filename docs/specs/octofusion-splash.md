# The Octofusion AI splash

## Why

The sign-in screen is the first thing a person sees, and the Help page is where they go to understand the product. Both open with the octopus alive: its arms moving the way a real octopus's do, then the name and the tagline. This is built by hand, with a person judging the motion in a browser, since a coding agent cannot see what it draws. The rest of the rebrand is `octofusion-rebrand.md`.

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
2. Its arms flail for about 2 seconds: wide, quick curls.
3. The arms settle toward the symbol's pose.
4. The name fades in below the octopus, then the tagline below the name.

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
- The artwork, the animation and its code add under 150 KB to what a screen loads, before compression.

## How we will know it works

- A person watching it says the arms move like an animal's and the settle is smooth. That is the owner's call, on a preview, before it is added to the console.
- With motion at zero, the drawn paths equal the symbol's.
- The sign-in screen's Google button works while the splash plays.
- With reduced motion on, neither screen animates.
- The console's tests cover the three states on each screen: first visit, a later visit in the session, and reduced motion.

## Constraints

- The artwork is not redrawn or recolored. The outer outline stays, and no arm takes a color of its own.
- Text the console shows keeps the writing rules.
