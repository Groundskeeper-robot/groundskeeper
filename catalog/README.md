# Imagery catalog

The aerial imagery sources the panel knows: one JSON file per source. Adding a country, state or county is a data
change, not a code change. [SOURCES.md](SOURCES.md) lists what is here; `python -m app.imagery covers LAT LON`
says which entries claim a place. Looking for imagery for a new place: [FINDING.md](FINDING.md).

## Add a source

1. Copy `_template.json` to `sources/<cc>/<id>.json`. `<cc>` is the ISO country code in lower case (`world` for a
   worldwide service); the id starts with it (`nl_pdok_hr`) and names the file.
2. Fill it in. Editors check the file against `schema.json` as you type. The fields that need thought:
   - `kind`: `tiles` for a `{z}/{x}/{y}` template (XYZ; WMTS in the GoogleMapsCompatible grid; TMS with
     `"scheme": "tms"`), `wms`, `arcgis_image` (an ImageServer) or `arcgis_map` (a MapServer).
   - `native_m`: ground metres per pixel. ArcGIS services in State Plane report **feet** (0.25 = 7.6 cm).
   - `license`: an SPDX id where one exists, and `commercial`: may a commercial deployment use it? `null` when the
     terms do not say.
   - `auth`, only when the service needs the user's own key: `{"label": "a free … API key", "signup": "https://…"}`.
     `{key}` in the url is replaced by it.
   - `coverage`: `{"ref": "<name>"}` for an outline in `coverage/`, or `{"type": "World"}`. **Generous on purpose.**
     The panel never contacts a source whose outline does not contain the yard, so a tight outline hides the source
     from people at its edges, and a loose one only costs a quick check.
   - `verified`: a **public** landmark inside the coverage where you checked the imagery (a cathedral, a capitol).
     Never a home.
3. No outline yet? `python -m app.imagery coverage --country <CC>` (add `--state "<name>"` for a state or province)
   draws one from Natural Earth, buffered 10 km (`--all-states` for a country with small exclaves, `--clip=W,S,E,N`
   to leave out overseas parts). The command is recorded in the file. Commit the `.geojson`.
4. Check it:

   ```bash
   python -m app.imagery verify catalog/sources/<cc>/<id>.json --live --out out/
   ```

   Offline, it checks the file, the outline, and that no other country's outline claims your landmark. Live, it
   renders the source at the landmark (`out/<id>.jpg`: look at it), and checks `max_zoom`, `max_px`, the WMS layer
   and the links against the service. A keyed source is checked to ask for its key; pass `--key <id>=...` to render it.
5. `python -m app.imagery table --write` updates SOURCES.md. Run `pytest`, then open a pull request with the image.

Setup: `pip install -r requirements.txt -r requirements-dev.txt`.

## Starting from the panel

Settings → Map imagery → **Add a source**: paste the service's address, review what the panel found, try it at your
yard, save. Then **Export** on its card makes this file (and its outline) for you: it asks for the public landmark
and the coverage, replaces any key in the address with `{key}`, and refuses anything that would put your yard in a
public file. Continue at step 4 above.

## Without a pull request

Put the same file in `DATA_DIR/imagery/sources/` on your own server, with any outline in
`DATA_DIR/imagery/coverage/`. A local file with a bundled id replaces the bundled one, which fixes a broken URL
without waiting for a release. Problems show in Settings → Map imagery.

## A new protocol

A kind is code: a subclass of `ExportKind` (a server renders any box) or `TileKind` (pre-cut tiles) in
`app/imagery/kinds/`, registered in `kinds/__init__.py`, with a request-shape case in `tests/test_kinds.py`. Its
`fields` become schema, validation and form fields; `python -m app.imagery schema --write` regenerates `schema.json`.
Give it `claims(url)` and `discover(http, url, lat)` and the add-a-source flow recognises its addresses too.

## Keeping it current

Most US county and city entries point at one flight's service (the year is in the id and the url). When a newer
flight is published, update `url`, `name` and `vintage` in the same file, check it again with `verify --live`, and
keep the id (a user's choice is saved by id). Ones that move faster than a year:

- `ri_rigis_2026_3in`: RIGIS publishes a new service each season (`RI_YYYYMM_RGB_3in_web`), with no "latest" alias.
- `az_maricopa_aerials`: the Assessor's `Aerials<year>` service; the county GIS office's own aliases lag behind.
- `mn_hennepin_2024`: `UTM_Aerial_2026` exists but answered blank on most requests in October 2026.
- `mt_naip_2025_30cm`: a provisional service (`…provisional…v1`) the State Library may replace.

Waiting on the provider (checked October 2026; add them once they answer):

- Maine statewide: `gis.maine.gov` (`/arcgis` and `/image`) answers HTTP 502. Its `orthoRegional` ImageServer and
  the 6-inch coastal flights would replace the three city entries.
- Oregon statewide: `imagery.oregonexplorer.info` (OSIP 2024, 30 cm) has a certificate that expired 2026-09-26.
- San Diego: SANDAG `SD2023_9inch` (gis.sandag.org) times out; it would replace SanGIS 2019.
- Huntsville, Alabama: `maps.huntsvilleal.gov` (`AerialImagery2026`) resets non-browser connections.
- Ramsey County, Minnesota (3 inch 2024): behind a bot check that refuses the panel.

Many county flights are flown by a vendor (EagleView, Nearmap, Sanborn). Where the county only licenses the imagery
and publishes it for viewing, `license.name` says so (`ca_lariac7_2023`, `wa_king_2025`, `sd_sioux_falls_2025`);
`commercial` stays `null` unless the terms say otherwise. Left out on purpose: the Harris County Appraisal District's
EagleView service (Houston, 5 cm), which publishes no reuse terms where other districts' EagleView licences forbid
reuse.

## Also

- `python -m app.imagery verify` checks every file offline; add `--live` to fetch from all of them.
- `python -m app.imagery sheet --out out/sheet.png`: every keyless source at its landmark, on one image.
- `context/world.json` is the zoomed-out layer around the yard (Sentinel-2 cloudless by EOX: **non-commercial**).
