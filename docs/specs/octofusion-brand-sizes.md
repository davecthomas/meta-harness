<!-- Created by the meta-harness in run t-d6d4bf394c35:run-1 on harness:pi-lean/claude:claude-sonnet-5-5. -->

# Octofusion brand file sizes after optimizing

`docs/specs/octofusion-rebrand.md` caps the symbol at 40 KB and the horizontal lockup at 120 KB, before compression. This note records whether an SVG optimizer reaches both limits. The operator decides from it whether a limit is relaxed before `brand-build-script` merges.

Result: both files fit at path precision 2. The symbol is 35,341 bytes and the lockup is about 96.2 KB. No limit needs to be relaxed.

## Method

- Tool: svgo 4.1.0, run through `npx` in a scratch directory outside the repository. Nothing was added to `console/`.
- Inputs: `console/brand/source/octofusion-symbol.svg` (70,744 bytes) and `console/brand/source/octofusion-wordmark.svg` (156,982 bytes).
- Config: `preset-default` with `floatPrecision` set to 3, 2, 1 and 0. The overrides `removeTitle: false`, `removeDesc: false` and `cleanupIds: false` keep the title, the description and the ids.
- The lockup is the symbol plus the wordmark. It was not composed here. Its size is the sum of the two optimized files. Placing the symbol beside the wordmark adds a small wrapper, a few hundred bytes.
- Check: each result was rendered with resvg at 32 and 512 pixels wide on white and compared with a render of the source. The comparison is a pixel difference, on a 0 to 255 scale, summed over all channels. See the limits below.

## Bytes at each precision

| Precision | `octofusion-symbol` | Wordmark | Lockup (sum) | Symbol under 40 KB | Lockup under 120 KB |
| --- | --- | --- | --- | --- | --- |
| source | 70,744 | 156,982 | 227,726 | no | no |
| 3 | 46,413 | 89,405 | 135,818 | no | no |
| 2 | 35,341 | 60,826 | 96,167 | yes | yes |
| 1 | 24,107 | 34,428 | 58,535 | yes | yes |
| 0 | 12,152 | 13,573 | 25,725 | yes | yes |

40 KB is read as 40,000 bytes and 120 KB as 120,000 bytes. With 1,024 bytes per KB the limits are looser and the conclusions do not change.

## Match against the source

Mean and largest per-value difference from the source render:

| File | Size | p3 | p2 | p1 | p0 |
| --- | --- | --- | --- | --- | --- |
| symbol | 32 px | 0.000 / 0 | 0.004 / 6 | 0.042 / 14 | 0.298 / 25 |
| symbol | 512 px | 0.001 / 15 | 0.003 / 46 | 0.056 / 62 | 0.305 / 138 |
| wordmark | 32 px | 0.215 / 29 | 0.209 / 29 | 0.368 / 36 | 0.812 / 35 |
| wordmark | 512 px | 0.002 / 35 | 0.010 / 47 | 0.090 / 84 | 0.590 / 168 |

- Precision 2 differs from the source only in single edge pixels, at anti-aliasing level. Precision 3 and 2 look the same as the source in this measure.
- At precision 1 the largest edge difference at 512 px reaches 62 for the symbol and 84 for the wordmark. At precision 0 it reaches 138 and 168, which is visible drift on outlines.
- The lowest precision that still matches the source is 2. Precision 1 is close but was not accepted.
- Limit of this check: the run had no way to show images to a person. The "by eye" review in the task is not done. The pixel difference stands in for it. Someone should open `symbol-p2` and `wordmark-p2` at 32 and 512 px next to the sources before the build script fixes precision 2.

## Constraints

- Artwork is not redrawn or recolored. The symbol keeps `#08e7f5`, `#0a0a14` and `#670fdc`. The wordmark keeps `#08e7f5`, `#0a0a14`, `#c402f5` and `#fb5b52`. The magenta fill `#c402f5` is unchanged.
- svgo merges each group's paths into one path per color. The outer outline path stays. No arm gets a color of its own.
- Each file keeps `<title>Octofusion AI</title>` with its id.
- svgo 4 drops `role="img"` from the root element in this config. The build script should set it back, or turn off `removeUnknownsAndDefaults` for that attribute, so the accessible name still works.
- svgo also removes the XML declaration and moves `viewBox` after other attributes. Neither changes rendering.

## Fit

- Symbol: fits. At precision 2 it is 35,341 bytes, 4,659 bytes under the limit. At precision 3 it is 46,413 bytes and does not fit.
- Lockup: fits at precision 2, at about 96.2 KB, about 23.8 KB under the limit. At precision 3 it is about 135.8 KB and does not fit.
- No file misses its limit at the lowest matching precision, so no smallest-size or relaxed-limit figure is needed. If precision 3 were required, the symbol would need a limit of about 46.5 KB and the lockup about 136 KB.

## For the build script

- Use `floatPrecision: 2` for both files.
- Build the lockup from the optimized symbol and wordmark, then check the final size against 120,000 bytes.
- Fail the build if a shipped file passes its limit or loses its `title`.
