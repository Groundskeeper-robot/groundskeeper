# Finding imagery for a new place

For people and AI assistants setting up a panel somewhere the catalog does not yet serve well. The goal is the
sharpest **free** aerial photo that covers the yard, added as a catalog entry (bundled, or local to one server).

## 1. What the catalog already has

```bash
python -m app.imagery covers <LAT> <LON>
```

Offline: it reads the catalog and contacts nothing. It lists every source whose outline contains the point, sharpest
first, keyed ones last. In a running panel the same list is Settings → Map imagery (or the MCP tool
`imagery_sources`), with each source's real sample of the yard.

Good enough when a free source at 30 cm or better is listed. National 60 cm programmes (USGS NAIP) are a fallback,
not an answer: lawn edges need 10–30 cm.

### What the numbers mean

`native_m` is metres of ground per pixel; the panel shows it in cm. Smaller is sharper. Say it to a user in cm, with
what it shows:

| `native_m` | Per pixel | What you can make out |
|---|---|---|
| 0.05–0.10 | 5–10 cm (0.25 ft) | Single pavers, sprinkler heads, the mower; a crisp lawn edge. County GIS flights, some states |
| 0.15–0.20 | 15–20 cm (0.5 ft) | Beds, paths and the lawn edge are clear; fine for no-go zones |
| 0.25–0.30 | 25–30 cm (1 ft) | Edges soft, zones still placeable to within a step |
| 0.5–0.6 | 50–60 cm | A lawn edge is one blurry pixel. USGS NAIP, most national programmes |
| 1 and up | 1 m+ | Whole beds blur together; keep looking |

The most detailed imagery for a yard is almost always a **county or state** orthoimagery service (7–15 cm), then a
keyed worldwide one (Esri, Mapbox, MapTiler: 7–30 cm depending on the place), then national programmes (30–60 cm).
Sharpest is not always best: look at the sample. A clear 15 cm leaf-off flight beats a cloudy or leaf-on 7.6 cm one.

## 2. Where to look

Most sharp free imagery is published by a public body for its own territory. Search from the smallest to the largest:

| Level | Who publishes | Search terms that work |
|---|---|---|
| County / city (US) | County GIS, often via a regional consortium | `<county> county orthoimagery ImageServer`, `<county> GIS aerial 2024` |
| State / province | State GIS office, DNR, DOT, emergency management | `<state> statewide orthoimagery WMS`, `<state> ortho ImageServer`, the state's geoportal "imagery" page |
| Country | The national mapping agency (IGN, Kartverket, Lantmäteriet, swisstopo…) | `<agency> orthophoto WMS`, `<country> INSPIRE orthoimagery view service` |
| Europe | INSPIRE OI (orthoimagery) view services, listed in the INSPIRE geoportal | `inspire-geoportal.ec.europa.eu orthoimagery <country>` |
| Worldwide | Commercial, with the user's own key: Esri, Mapbox, MapTiler (already bundled) | — |

Where to look on a portal: "web services", "WMS", "REST services", "ArcGIS server", "open data". ArcGIS servers list
their services at `…/arcgis/rest/services?f=json`; an imagery one is an `ImageServer` or a `MapServer` named ortho,
imagery, aerial or basemap. US state ImageServers often hide in a `…/arcgis_image/…` or `…/imagery/…` folder.

## 3. Check what you found, without sending the yard

- **Paste the address into Settings → Map imagery → Add a source.** The panel asks only that address what it serves
  (capabilities, its own extent) and never sends a location. Then **Try it at your yard** is the one deliberate step
  that sends the yard to that service.
- **For the catalog, test at a public landmark, never at a home:** fill `verified` with a landmark inside the
  coverage and run `python -m app.imagery verify <file> --live --out out/`, then look at the image.

## 4. Traps

- **Blank is not an error.** ArcGIS returns a black JPEG with HTTP 200 where it has no data; WMS returns white or
  transparent. The panel detects blank images; `verify --live` fails on them. Always look at the render.
- **Feet.** ArcGIS services in State Plane report cell sizes in US feet. `native_m` is metres: 0.25 ft = 0.0762 m,
  0.5 ft = 0.1524 m, 1 ft = 0.3048 m.
- **The latest layer moves.** Many services publish one layer per year. Prefer a "latest" alias when the provider
  keeps one; otherwise note the year in `vintage` (and in the catalog README's maintenance notes, as Flanders does).
- **Leaf-off beats leaf-on** for a lawn under trees: spring flights show the ground. Note the season in `vintage`.
- **County mosaics are uneven.** A statewide service built from county flights can be 7.6 cm in one county and
  30 cm in the next; `native_m` is the sharpest, and the yard sample in Settings shows what the yard really gets.
- **Licence.** "Public" does not mean "free to reuse". Read the terms; when they do not say, `commercial: null`.
  Credit lines go in `attribution`.
- **Keys and accounts.** A service that needs a key or an account goes in with `auth`, and the panel contacts it
  only once the user saves a key. Never put a key in a bundled file; it becomes `{key}`.
- **Projections.** WMS 1.3.0 with EPSG:4326 swaps the axis order; the `wms` kind handles it when `version` and `crs`
  are set right. Tile services must be in the Web Mercator grid (`GoogleMapsCompatible`).

## 5. Add it

Follow [README.md](README.md) → "Add a source" (template, outline from Natural Earth, `verify --live`,
`table --write`). On one server only: put the file in `DATA_DIR/imagery/sources/` instead.

## Privacy, always

The panel contacts only sources whose outline contains the yard, and a keyed one only after a key is saved.
Probing every source "to see" would send the home location to all of them. Searching the web for a place's
imagery is fine; typing the home address or coordinates into a third-party viewer or search is not.
