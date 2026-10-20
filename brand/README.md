# Groundskeeper brand

The logo set for Groundskeeper (formerly "Yarbo panel"). The app serves copies of the mark and icons from
`web/brand/` and `web/favicon.ico`; regenerate here, then copy them across.

The mark is a GK monogram (IBM Plex Sans Condensed Bold) standing over a strip of lawn on a dark turf tile. The robot
sits in the grass as a dot, in the same blue it has on the Live map: mowed short behind it, still long ahead of it.
The favicon carries no letters (at tab size the GK read as a G, too close to Google's): it is the robot's
back-and-forth mowing path on the turf tile, ending at the robot dot.

| Folder | What |
|---|---|
| `logo/` | Mark (full and small-size), lockups on dark and light, the "for Yarbo" lockup, stacked lockup, wordmark, glyphs (colour and one-ink) |
| `app-icons/` | `favicon.svg`, `favicon.ico` (16, 32, 48), favicon PNGs, `apple-touch-icon.png` (180), `icon-192.png` and `icon-512.png` for a web app manifest |
| `icons/` | The panel's nav icons as standalone SVGs, inked `#8BD49A` |
| `social/` | 1200 × 630 link-preview card (PNG, SVG source) |
| `source/` | The generator (`make_brand.py`) and PNG renderer (`render.mjs`) |

Pick a logo file by its ground: `-on-dark` for the dark theme, `-on-light` for light. Below 24 px use the small
mark. Keep a quarter of the mark's height clear on every side.

## Regenerating

The wordmark (IBM Plex Sans Condensed SemiBold) and the monogram (Bold) are set as outlines (OFL), shaped with
HarfBuzz for kerning.
`make_brand.py` needs `fonttools` and `uharfbuzz` and the Plex Sans Condensed SemiBold, Medium and Bold TTFs from google/fonts in a `fonts/`
folder beside it; `render.mjs` needs `@resvg/resvg-js`. Neither is a dependency of the panel.

```bash
python make_brand.py out
```
