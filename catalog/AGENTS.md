# catalog/: imagery sources

Local rules for the imagery catalog. The top-level `AGENTS.md` comes first; the how-to is `catalog/README.md`, and
finding imagery for a new place is `catalog/FINDING.md`.

- **Data, not code.** One JSON file per source in `sources/<cc>/`, checked against `schema.json`. Outlines in
  `coverage/`. Don't hand-edit `schema.json` or `SOURCES.md`: regenerate them with
  `python -m app.imagery schema --write` and `python -m app.imagery table --write`.
- **Never test at a home.** `verified` is a public landmark (a capitol, a cathedral). Don't fetch or probe a source
  at anyone's address, including the person you work for.
- **Coverage outlines are generous** (`python -m app.imagery coverage …`, buffered). The panel never contacts a
  source whose outline doesn't contain the yard; a too-tight outline hides it from people at the edge.
- **Licences matter.** Fill in `license` and `commercial` from the provider's own terms, with a link; `null` when the
  terms don't say. Don't add a source whose terms forbid this kind of use.
- **`native_m` must be honest.** Automatic mode ranks sources by it; a mosaic must not claim a sharper figure than
  its coarsest part where that part covers yards.
- Check offline with `python -m app.imagery verify catalog/sources/<cc>/<id>.json`; the `--live` render is for the
  PR (attach the image).
