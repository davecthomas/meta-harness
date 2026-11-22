# The console becomes Octofusion AI

## Why

The product has a name and a mark now: Octofusion AI, "The Open Meta-Harness for Coding at Scale", with an octopus as its symbol. The console still calls itself Meta-harness and shows a stock hexagon icon. This spec puts the new name and artwork on every place a person first meets the console: the browser tab, the sign-in screen, the header and a new footer.

## What we have

The brand's source artwork is in `console/brand/source/`. It was checked before this spec was written, and `console/brand/README.md` records what was found. What matters for the work:

- Every file is a path-based SVG with no embedded image. Colors are flat fills: purple `#670fdc`, cyan `#08e7f5`, near-black `#0a0a14`, coral `#fb5b52`, and magenta `#c402f5` in the wordmark.
- `octofusion-symbol.svg` is the whole octopus on a square canvas of 1418 units. Its eight arms are not separate shapes. The file is three color layers, and one path in each layer covers all the arms. Nothing in it can be moved by itself yet.
- `octofusion-wordmark.svg` is the name, "OCTOFUSION AI", with its magenta fill. The lockup files that came with the artwork fill the name with the octopus's purple, which does not match the approved artwork, so they are not used. A lockup is built from the symbol and this wordmark.
- The tagline exists only as paths inside `octofusion-full-lockup.svg`, in the band below y = 1250 of its 1426 units. No font file came with the artwork. The wordmark sits between y = 750 and y = 1250, and the symbol above y = 750, so the three parts separate cleanly by position.
- `octofusion-symbol-black.svg`, `octofusion-symbol-white.svg` and the two wordmark files like them are single-color silhouettes.

The artwork also came with bitmap icons and a 10-second video. Neither is used. The bitmap browser icons show only the octopus's head, and the video is 3.8 MB with a baked-in background and a sound track.

## What we want

### The name

The console calls itself Octofusion AI wherever it names itself: the browser tab's title, the sign-in screen, the header, the footer, the web app manifest and the Help page's introduction.

The tagline is "The Open Meta-Harness for Coding at Scale". Text that uses "the meta-harness" as a common noun for what the product is stays as it is.

### The artwork the console ships

A script in `console/` builds everything the console ships from `console/brand/source/`, and its output is committed. Running it again changes nothing. It makes:

- the symbol, optimized for the web;
- a horizontal lockup, the symbol with the magenta wordmark to its right;
- the tagline as its own SVG, cut from the full lockup;
- the browser icons below.

Each shipped SVG keeps a `title` that says "Octofusion AI". The symbol ships under 40 KB and the horizontal lockup under 120 KB, before compression.

### Browser icons

Every icon shows the whole octopus, arms included. The head-only crop is not used at any size.

- `favicon.svg`: the symbol.
- `favicon.ico` with 16, 32 and 48 pixel entries, and `favicon-32.png`.
- `apple-touch-icon.png`, 180 pixels, the symbol on the near-black background with a margin, since iOS fills a transparent icon with black to its edges.
- `icon-192.png` and `icon-512.png` on a transparent background, and `icon-maskable-512.png` with the symbol inside the middle 80 percent on near-black.
- `site.webmanifest` naming Octofusion AI, the icons, and near-black as the background color.

`index.html` links them, and its `theme-color` becomes the brand's purple.

### The header

The header shows the horizontal lockup where it shows the hexagon and "Meta-harness" today. It is 32 pixels tall and links to the Overview. On a phone-width screen the header shows the symbol alone. The image's accessible name is "Octofusion AI". One full-color file serves the light theme and the dark theme, since its cyan and near-black outlines hold on both.

### The footer

The console has no footer today. Every page gets one at the end of its content, the sign-in screen included. It scrolls with the page and is never fixed to the window. It holds:

- the symbol at 20 pixels and the name;
- the tagline, as text;
- the version and the deployed commit, which move here from the header;
- a link to the project on GitHub.

The footer is one quiet line on a wide screen and stacks on a phone.

### The sign-in splash

The sign-in screen opens with the octopus moving. The animation is drawn by the browser from SVG. No video file is loaded.

**The rig.** The symbol is cut into parts that can move: the head with the body, and each of the eight arms as its own group. Each arm keeps its three color layers and has a pivot where it joins the body. Each arm's shape runs on under the head and under its neighbors, so no gap opens when it moves. With every part at rest the rig looks the same as the symbol. A script draws both and compares them, and fails when more than 1 percent of the pixels differ.

**What plays.**

1. The octopus rises into view over about 400 milliseconds.
2. Its arms flail for about 2 seconds. Each arm swings around its pivot by up to 18 degrees, out of step with the others, so the motion reads as a living animal.
3. The arms settle into the symbol's pose.
4. The wordmark fades in below the octopus, then the tagline fades in below the wordmark.

After that the arms keep a slow sway of a few degrees for as long as the screen is open.

**What it must not do.**

- It never delays sign-in. The sign-in card and Google's button are on the screen and usable from the first paint, below the artwork.
- It plays in full once in a browser session. On a later visit in the same session, such as after a session expires, the screen opens on the settled pose with the slow sway.
- With the system's reduced-motion setting on, nothing moves: the screen shows the settled octopus, the wordmark and the tagline.
- It moves only `transform` and `opacity`, so the browser can draw it without layout work, and it adds no runtime dependency.
- The artwork, the animation and its code add under 150 KB to what the sign-in screen loads, before compression.

## Out of scope

- The repository's name, the Python package, the services' names, the `MH_` settings and the console's address.
- Text the control plane writes to pull requests, issues and file headers, which says "the meta-harness".
- The design document and the README, beyond naming the product where they describe the console's header, footer and sign-in screen.
- The console's color theme. Its primary color stays as it is.
- A landing page, and any use of the video.

## How we will know it works

- A browser tab on the deployed console shows the whole octopus and the title Octofusion AI.
- The sign-in screen plays the splash once, and the Google button works while it plays.
- With reduced motion on, the sign-in screen has no animation.
- The header shows the lockup in both themes, and the symbol alone at phone width.
- Every page ends with the footer, and the version reads there.
- The console's tests cover the header, the footer, the sign-in screen in each of its three states, and that every icon `index.html` and the manifest name is a file the build ships.
- The script that builds the artwork runs in the sandbox's checks and reports no change.

## Constraints

- The artwork is not redrawn or recolored. The outer outline stays, and no arm takes a color of its own.
- Whatever draws SVG to pixels is a development dependency of the console, added with its lock file by the tool.
- A dependency or build change in `console/` runs the slow checks, and must keep them passing.
- Text the console shows keeps the writing rules, and the test that reads the console's strings must pass.
- A change to a documented behavior, such as where the version is shown, updates `docs/technical-design.md` in the same pull request.
