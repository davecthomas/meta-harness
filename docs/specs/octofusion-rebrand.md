# The console becomes Octofusion AI

## Why

The product has a name and a mark now: Octofusion AI, "The Open Meta-Harness for Coding at Scale", with an octopus as its symbol. The console still calls itself Meta-harness and shows a stock hexagon icon. This spec puts the new name and artwork on the places a person first meets the console: the browser tab, the sign-in screen, the header and a new footer. It retires the old name from the console's text.

Two parts of the rebrand are specified apart and are not this spec's work: the animated splash (`octofusion-splash.md`), which needs a person to judge how it moves, and the console's new web addresses (`octofusion-web-addresses.md`), which is infrastructure with a step only a person can take.

## What we have

The brand's source artwork is in `console/brand/source/`. It was checked before this spec was written. What the check found:

- Every file is a path-based SVG with no embedded image. Colors are flat fills: purple `#670fdc`, cyan `#08e7f5`, near-black `#0a0a14`, coral `#fb5b52`, and magenta `#c402f5` in the wordmark.
- `octofusion-symbol.svg` is the whole octopus on a square canvas of 1418 units, in three color layers.
- `octofusion-wordmark.svg` is the name, "OCTOFUSION AI", with its magenta fill. The lockup files that came with the artwork fill the name with the octopus's purple, which does not match the approved artwork, so they are not used. A lockup is built from the symbol and this wordmark.
- `octofusion-symbol-black.svg`, `octofusion-symbol-white.svg` and the two wordmark files like them are single-color silhouettes.
- Each color file groups its paths by color, under ids such as `octofusion-symbol-purple`. No file has a text element or a font.

The artwork also came with bitmap icons and a 10-second video. Neither is used. The bitmap browser icons show only the octopus's head, and the video is 3.8 MB with a baked-in background and a sound track.

## What we want

### Order of work

The name sweep touches most of the console's files, so it comes last: every other part merges first.

### The name

The console calls itself Octofusion AI wherever it names itself: the browser tab's title, the header, the footer, the web app manifest and the Help page's introduction. The sign-in screen already does: the splash there shows the name and the tagline, and is left as it is.

The tagline is "The Open Meta-Harness for Coding at Scale". It is the only place the console says "meta-harness".

Everywhere else the words are retired from what a person reads in the console. About 34 of the console's source files use them today, mostly as "the meta-harness": "issues assigned to the meta-harness", "the meta-harness wrote this summary". Each becomes Octofusion, or the sentence is reworded so it needs no name. This covers:

- the console's own text: labels, help, tooltips, empty states and messages;
- text a service sends for the console to show, such as a tracker's description, a setting's help and an error's message with its remedy;
- the Help pages' topics.

A test reads the console's strings and the strings the services send it, and fails on "meta-harness" in any spelling outside the tagline. A link to the repository keeps the repository's address, which is not text a person reads as the product's name.

### The artwork the console ships

A script in `console/` builds everything the console ships from `console/brand/source/`, and its output is committed. Running it again changes nothing. It makes:

- the symbol, optimized for the web;
- a horizontal lockup, the symbol with the magenta wordmark to its right;
- the browser icons below.

Each shipped SVG keeps a `title` that says "Octofusion AI". The symbol ships under 40 KB and the horizontal lockup under 120 KB, before compression.

### Browser icons

Every icon shows the whole octopus, arms included. The head-only crop is not used at any size. At 16 pixels the arms no longer read one by one and the icon is a purple octopus shape with its cyan outline; that is accepted.

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

## Out of scope

- The repository's name, the Python package, the services' names and the `MH_` settings.
- Text the control plane writes outside the console: pull request and issue text, commit messages and file headers, which say "the meta-harness". A later spec renames those, since they are read on GitHub and in Jira under the old name today.
- A custom domain. The addresses are Firebase's own.
- The design document and the README, beyond naming the product where they describe the console's header, footer and sign-in screen.
- The console's color theme. Its primary color stays as it is.
- The animated splash on the sign-in screen and the Help page, and the tagline as artwork: `octofusion-splash.md`.
- The console's web addresses: `octofusion-web-addresses.md`.
- A landing page, and any use of the video.

## How we will know it works

- A browser tab on the deployed console shows the whole octopus and the title Octofusion AI.
- No page of the console says "meta-harness" outside the tagline.
- The header shows the lockup in both themes, and the symbol alone at phone width.
- Every page ends with the footer, and the version reads there.
- The console's tests cover the header, the footer, and that every icon `index.html` and the manifest name is a file the build ships.
- The script that builds the artwork runs in the sandbox's checks and reports no change.

## Constraints

- The artwork is not redrawn or recolored. The outer outline stays, and no arm takes a color of its own.
- Whatever draws SVG to pixels is a development dependency of the console, added with its lock file by the tool.
- A dependency or build change in `console/` runs the slow checks, and must keep them passing.
- Text the console shows keeps the writing rules, and the test that reads the console's strings must pass.
- A change to a documented behavior, such as where the version is shown, updates `docs/technical-design.md` in the same pull request.
