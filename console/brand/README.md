# Octofusion AI brand source

The artwork the console's brand is built from. `docs/specs/octofusion-rebrand.md` says what is built from it.

**Octofusion AI**. Tagline: "The Open Meta-Harness for Coding at Scale".

## The files in `source/`

| File | What it is | Canvas |
|---|---|---|
| `octofusion-symbol.svg` | The whole octopus, in color | 1418 x 1418 |
| `octofusion-symbol-black.svg`, `octofusion-symbol-white.svg` | Its silhouette in one color | 1418 x 1418 |
| `octofusion-wordmark.svg` | The name, in color | 2075 x 638 |
| `octofusion-wordmark-black.svg`, `octofusion-wordmark-white.svg` | The name in one color | 2075 x 638 |
| `octofusion-full-lockup.svg` | Symbol, name and tagline stacked. Kept for the tagline's paths | 1770 x 1426 |

Colors, as the files fill them: purple `#670fdc`, cyan `#08e7f5`, near-black `#0a0a14`, coral `#fb5b52`, and magenta `#c402f5` in the wordmark.

## What a check of the files found

- Every file is paths only: no embedded image, no text element, no font.
- Each color file is a few groups, one for each color, such as `octofusion-symbol-purple`. The symbol's arms are not separate shapes: one path in each color layer covers all eight. Moving an arm means cutting the artwork into parts first.
- The full lockup fills the name with the octopus's purple. The approved artwork and `octofusion-wordmark.svg` fill it with magenta. Build a lockup from the symbol and the wordmark, never from the full lockup's own name.
- In the full lockup the symbol is above y = 750, the name between 750 and 1250, and the tagline below 1250. No path crosses those lines.
- The artwork's own browser icons were bitmaps of the octopus's head alone, and its splash was a video. Neither is kept here. The console's icons show the whole octopus, and the splash is drawn from SVG.

## Rules for the artwork

Keep the outer outline. Do not stretch it, redraw it or give an arm a color of its own.
