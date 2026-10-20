# Imagery source catalog

How the panel finds, chooses and fetches the aerial photo under the robot's map, and how a contributor adds a
source. Context: `design.md` §7.2 (basemap) and §21 (distribution). The contributor guide itself is
`catalog/README.md`; where to look for imagery in a new place is `catalog/FINDING.md`.

## 0. Principles

- **The catalog is data, not code.** One JSON file per source under `catalog/`. Adding a source is a data change and
  should take ten minutes; adding a protocol ("kind") is one file of code.
- **Privacy by coverage.** Every entry carries a coverage outline. The panel decides locally which entries cover the
  yard and **never contacts the others**. Tests assert no network call for a non-covering source.
- **Server-side fetching.** The browser never sends the home location anywhere; the yard image, the samples and the
  context tiles are fetched and cached by the panel.
- **No new runtime dependencies** for the catalog's tooling: `jsonschema` and `shapely` are dev-only.

Vocabulary: a **source** is one catalog entry (one service layer, e.g. "PDOK aerial 8 cm"); its **provider** is the
organisation (PDOK); a **kind** is the protocol adapter in code (`tiles`, `wms`, `arcgis_image`, `arcgis_map`).

## 1. Repository layout

```
catalog/                       # THE DATA. Pull requests for new sources land only here.
  README.md                    # the contributor guide
  FINDING.md                   # where to look for imagery for a new place, and the traps
  SOURCES.md                   # generated table of every bundled source (do not edit)
  schema.json                  # generated JSON Schema for one source file
  _template.json
  sources/<cc>/<id>.json       # one file per source, grouped by ISO country code; world/ for worldwide
  coverage/<name>.geojson      # shared outlines, referenced by name from sources
  context/                     # the zoomed-out world layer
app/imagery/                   # THE CODE
  catalog.py                   # load bundled dir + local dir, validate, resolve coverage refs, migrate v1
  coverage.py                  # point-in-polygon, outline helpers
  mercator.py                  # Web Mercator and tile maths (pure functions)
  kinds/                       # REGISTRY of protocol adapters: tiles.py, arcgis.py, wms.py, base.py
  discover.py                  # "paste a URL" dispatcher and shared helpers
  share.py                     # export a local source as a bundled-style file
  service.py                   # yard image, samples, probe, automatic mode, context tiles, caches
  tools.py                     # python -m app.imagery verify|sheet|table|schema|coverage|covers
DATA_DIR/imagery/              # per install, never in the repository
  sources/*.json               # the user's own sources (same schema)
  previews/<id>.jpg            # the yard as each covering source sees it
```

`catalog/` sits at the repository root because it is what people open the repository for, and a pull request that
touches only `catalog/sources/xx/…` is reviewable at a glance. One file per source keeps merge conflicts and stray
commas local.

## 2. The source file

```jsonc
{
  "$schema": "../../schema.json",
  "id": "nl_pdok_hr",                      // ^[a-z][a-z0-9_]{2,40}$, prefixed with the country code
  "name": "PDOK aerial photo (8 cm)",
  "provider": "Beeldmateriaal Nederland / PDOK",
  "country": "NL",                         // ISO 3166-1 alpha-2; "XX" for worldwide
  "region": "Netherlands",
  "homepage": "https://www.pdok.nl/…",     // where terms and, for keyed sources, the key live
  "kind": "tiles",
  "url": "https://service.pdok.nl/…/EPSG:3857/{z}/{x}/{y}.jpeg",
  "max_zoom": 21,                          // tiles only
  "native_m": 0.08,                        // ground metres per pixel at full resolution
  "vintage": "Yearly leaf-off flight; latest year",
  "attribution": "Imagery: Beeldmateriaal Nederland / PDOK",
  "license": {
    "id": "CC-BY-4.0",                     // SPDX id where one exists; "public-domain"; "provider-terms" for keyed APIs
    "name": "Open data (CC BY 4.0), credit required",
    "url": "https://creativecommons.org/licenses/by/4.0/",
    "commercial": true,                    // null where the terms do not say
    "credit": true
  },
  "cache_days": 90,
  "auth": null,                            // or {"label", "signup", "header": {"name", "format": "Bearer {key}"}}
  "coverage": { "ref": "nl" },             // catalog/coverage/nl.geojson · {"type": "World"} · or inline GeoJSON
  "verified": {                            // the public landmark this entry was checked at
    "date": "2026-10-03",
    "landmark": [4.8913, 52.3731],
    "place": "Dam, Amsterdam"
  }
}
```

- `{key}` in `url` is substituted after the query string is parsed (quoted in paths and tile addresses); ArcGIS adds
  `token=` itself.
- `tiles` covers XYZ and WMTS templates, with optional `scheme: "tms"` (y-flip) and `tile_size: 512`.
- Kind-specific fields are declared by the kind, and `schema.json` is **generated** from them
  (`python -m app.imagery schema --write`); a test fails when it is stale. The runtime validator and the schema are
  tested against the same list of bad entries.
- **World coverage ⇔ country `XX`.** A regional service with World coverage would make every panel worldwide send
  its yard to that service.

**Coverage outlines are generous on purpose** (a source that turns out blank costs one discarded sample; a missing
area costs a user the source). Country outlines are generated, not drawn: `python -m app.imagery coverage --country
NL` reads Natural Earth (public domain) at authoring time, buffers by 10 km, simplifies (capped at 0.6 × the buffer
so the land stays inside) and writes the file; the runtime never downloads outlines. A 25 km buffer was tried and
reached into neighbouring countries. `--all-states` and `--clip` handle countries whose 1:50m polygon omits
territories or overreaches. Sub-national shapes are exported from the service's own extent and committed the same way.

**Nested coverage is legitimate** (a state inside a national programme). The rule the tests enforce: a source's
outline contains its own landmark, and never contains the landmark of a source from a different `country`.

## 3. Kinds: the code extension point

```python
class Kind(Protocol):
    name: str                      # "wms"
    label: str                     # shown in the Add-a-source form
    fields: dict[str, Field]       # extra fields this kind reads (type, required, help, label)
    url_pattern: ...               # used by both the validator and the schema

    def validate(self, src) -> list[str]
    def max_zoom(self, src, lat) -> int
    def claims(self, url) -> bool
    async def discover(self, http, url, lat) -> Draft | None
```

Two base classes do the shared work:

- `ExportKind` (ArcGIS, WMS): `request(src, key, box, w, h) -> (url, params, headers)`; fetching splits a box into
  chunks of `max_px` and pastes them; a tile is rendered as its box at 256 px.
- `TileKind`: `request(src, key, z, x, y) -> (url, headers)`; a box is stitched at the right zoom, cropped and
  resampled.

Splitting request-building from fetching means request-shape tests need no HTTP, and the verify tool reuses them. A
registry test fails when a kind has no request-shape test. The service never branches on `kind`.

Room left, not built: a `geotiff` kind (an uploaded drone orthomosaic, warped locally; needs `rasterio`), `quadkey`
(Bing-style), and WMTS with a non-Mercator TileMatrixSet (needs reprojection).

## 4. Where sources come from

1. **Bundled:** `catalog/sources/**/*.json`, shipped in the image.
2. **Local:** `DATA_DIR/imagery/sources/*.json`, same schema and validation. A local id equal to a bundled id
   **replaces** it (fix a URL without waiting for a release); the UI marks it as an override.
3. **Profile filter** (designed, not built): `IMAGERY_PROFILE=commercial` would drop entries with
   `license.commercial: false`.

**Local sources default to World coverage.** The rule "never contact a source whose coverage excludes the yard"
protects users from entries they did not ask for. A source the user added is asked for.

v1 local files (one big JSON, `terms`, `needs_key`, `coverage_from`, `xyz`/`wmts`) are migrated on load with a
warning naming the file; entries the new rules refuse are reported, and the old file is kept.

## 5. What the user sees

### 5.1 The picker (Settings → Map imagery)

One card per source that covers the yard, each with **a sample of the user's own yard** from that source (a 40 m
square at the yard centre at native resolution, capped at 512 px; a 60 cm source gives a ~67 px sample, shown blurry
on purpose), name and region, a resolution badge, a licence badge (**Free** · **Credit required** ·
**Non-commercial** · **Your key**), the vintage, a status line, and the action. Free sources first, keyed services in
their own row. A sentence under the cards names how many sources were contacted and how many were not.

The samples are the point: the choice becomes "which of these looks like my yard best", which is also the only way to
notice that a sharper spring (leaf-off) flight shows bare soil while a summer one shows the beds.

### 5.2 Automatic mode

`imagery_source = {"provider", "mode"}`. No setting → automatic: the panel samples the candidate sources and picks
the sharpest **keyless** source with real imagery. A keyed source is only ever a manual choice. Automatic
re-evaluates when the free sources covering the yard change or a sample goes stale, logs a switch to Events, and
never overrides "My choice".

- An upkeep task runs 30 s after start, then hourly. It re-samples a source whose sample is missing, older than
  `min(cache_days, 90)` days, or taken more than 25 m from today's yard centre. It always goes through the candidate
  filter first, so only covering sources are ever probed.
- A failed automatic switch is retried after a day, not hourly. A passing error keeps the last real result; only a
  blank answer means "no imagery".
- One lock serialises source and mode changes, so a manual choice made during an automatic switch is not lost.
- Removing the source in use (or its key) falls back to automatic with a warning and forgets that source's yard
  image and sample. Keyed services are not re-sampled in the background.

### 5.3 Add a source

1. **Paste a URL.** Every kind that `claims` the address is tried; the others only when none claims it. ArcGIS reads
   `?f=json`; WMS reads `GetCapabilities` (layers inherit CRS, extent, scale and attribution from parents); tiles
   recognise `{z}/{x}/{y}` or `TileMatrix=`/`TileRow=`/`TileCol=`. Discovery contacts only the pasted address,
   sends no location, drops place-like query parameters, streams with a 5 MB cap and a time limit, and refuses
   cross-host redirects.
2. **Review the draft.** Fields come from the kind's `fields`, so a new kind gets a form for free.
3. **Try it** renders the 40 m sample from the unsaved entry. This is the first request that carries the yard, and
   the button says so. For tiles it walks zoom 21 → 12 and fills in `max_zoom`, `tile_size` and `native_m`.
4. **Save** writes `DATA_DIR/imagery/sources/<id>.json` and selects it.

**Export for sharing** turns a local entry into a bundled-style file. Guards: the landmark is a public place at least
2 km from the yard and inside the coverage; every part of the outline is at least 20 × 10 km and no outline edge
comes within 2 km of the yard; World coverage only with an explicit worldwide flag; keys in the address (`token=`,
`key=`, `api_key=`, …) and credentials in the host part are replaced or refused; `native_m` is rounded to two
significant figures (a Web Mercator resolution worked out at the yard would otherwise leak its latitude).

### 5.4 Browse the whole catalog

A read-only list grouped by country and then by state or province, with search and filters, without contacting any
source. The same data generates `catalog/SOURCES.md`.

## 6. What the contributor does

1. Copy `catalog/_template.json` to `catalog/sources/<cc>/<id>.json`.
2. Fill it. For coverage use `{"ref": "<cc>"}` if the outline exists, otherwise generate one
   (`python -m app.imagery coverage --country <CC>`) or export the service's extent, and commit it.
3. `python -m app.imagery verify catalog/sources/<cc>/<id>.json --live --out out/`
   - offline: schema, kind rules, coverage resolves and contains the landmark, no cross-country landmark claims, id
     unique;
   - live: fetches 120 m around the landmark at native resolution → `out/<id>.jpg`, reports pixels, m/px, bytes and
     seconds, runs blank detection; for tiles checks `max_zoom` (and whether a deeper zoom would actually be
     sharper); for WMS checks the layer and `MaxWidth`; for ArcGIS checks the pixel size against `native_m`.
4. Open the pull request with the image attached. CI runs the offline checks and regenerates `SOURCES.md`.

`python -m app.imagery covers LAT LON` lists, offline, the bundled sources whose outline contains a point, sharpest
free first. `python -m app.imagery sheet` renders every keyless source at its landmark into one contact sheet.

**Finding a source** (`catalog/FINDING.md`): look by level (county, state, country, INSPIRE, keyed worldwide);
county and state GIS services are usually the sharpest free imagery (7–15 cm) where national programmes stop at
30–60 cm. **Search by region, never by the home address or coordinates.** An AI assistant connected over MCP reads
only the yard's regions (from the outlines that contain it), never coordinates.

## 7. API

- `GET /api/imagery` (the map's view), `PUT /api/imagery/key`, `POST /api/imagery/probe`, `PUT /api/imagery/context`,
  `POST /api/imagery/refresh`, `/tiles/world/…`, `/tiles/src/…`.
- `GET /api/imagery/sources` (entries with `country, provider, homepage, license, vintage, auth {label, signup},
  verified, preview_url, origin: bundled | local | override`), `PUT /api/imagery/source {mode}` or `{provider}`.
- `GET /api/imagery/kinds`, `POST /api/imagery/discover {url}`, `POST /api/imagery/sources` (`?try=1` renders
  without saving), `DELETE /api/imagery/sources/{id}` (local only), `POST /api/imagery/sources/{id}/extent`,
  `GET /api/imagery/sources/{id}/preview.jpg`, `GET /api/imagery/sources/{id}/export`, `GET /api/imagery/catalog`.

## 8. Tests

- Privacy invariants: no network for a non-covering source; probe and the yard fetch refuse before any request;
  keyed sources without a key draw nothing; context tiles require the yard (not just the tile) inside the outline and
  cap a local world layer's zoom; discovery sends no coordinates; export never contains the yard and refuses small
  outlines; automatic probing contacts exactly the candidates.
- Every bundled file validates against `schema.json` and the runtime validator, and the two agree.
- Each landmark is inside its own coverage and inside no other country's source.
- A request-shape test per kind, enforced by a registry test.
- Migration of v1 local files and the old custom source.
- `tests/fakehttp.py` records requests; it raises if a URL that already has a query is sent with `params`.

Live verification is a script, never a test.

## 9. Traps found by live checks

- **httpx 0.28 drops an address's own query when `params=` is given** (`map=`, `token=` silently lost). Every
  ArcGIS/WMS request merges queries explicitly (`common.merge_query`).
- **Esri answers HTTP 200 with a JSON "Token Required" (code 499)** without a key: read the body, not the status.
- **Blank 200 responses:** services return valid empty images outside their real data. Blank detection discards
  them; partial regional mosaics are harmless for that reason.
- **Overzoom:** some tile services serve deeper zooms by upsampling. Detection must be relative ("detail a tile adds
  over its parent's quarter, enlarged"; threshold 0.4), not an absolute sharpness measure.
- **Units:** projected ArcGIS services (State Plane feet, UTM, national grids) report pixel size in their own units.
  The ArcGIS kind exports a small square (1/200 of the extent) at the centre of the service's extent with
  `imageSR=3857` and reads metres per unit from the answer, snapping to feet or metres within 8 %. A large square is
  off by a few percent (curvature). Degree services use `pixelSizeY`.
- **Export limits:** some ImageServers cap exports below 4096 px (`maxImageWidth`, e.g. 4000); `max_px` follows it.
- **Layer names** may differ in case between `GetCapabilities` and what the server accepts: compare case-insensitively.
- **Generic content types:** some services serve JPEG tiles as `application/octet-stream`; images are recognised by
  their first bytes.
- **Band order:** four-band services need `bandIds=0,1,2` for natural colour; some MapServers need a `layers=show:N`.
- **Legacy TLS renegotiation** is refused by OpenSSL 3, so a few licence pages cannot be checked automatically.
- **Yearly layers** (a year in the layer name) need bumping when a new year is published.
- WMTS REST paths read as z/y/x. A WMS MinScaleDenominator of 1 is ignored.
- Hand-drawn outlines had real holes (cities left out); generated outlines fixed them.

## 10. Coverage gaps

Countries with no free national service (the UK above all) rely on keyed worldwide services. US coverage is state by
state, and counties often have sharper imagery than their state's mosaic.

Tried and not added (October 2026): Minnesota, Iowa and Texas (only NAIP or 50 cm statewide), New Jersey (bot
challenge), Norway (IP-restricted), Lithuania (401), Croatia (fees, no Web Mercator), Italy nationally (2012,
non-commercial), Latvia, Slovakia and Slovenia (no working endpoint found), British Columbia (no open province-wide
orthophoto). Denmark and Sweden need an account or token; no keyless, licence-permitted service is known for the UK
or Ireland. Notes on bundled sources: Ontario's licence is unclear (`commercial: null`); Emilia-Romagna is only partly
published; Estonia burns a small watermark into the image; Pennsylvania is slow (about 30 s per render).
