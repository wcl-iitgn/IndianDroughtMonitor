# India Drought Monitor (IDM)

A fully data-driven web front end for the India Drought Monitor, developed for the
**Water and Climate Lab (WCL), IIT Gandhinagar**. The site structure and styling
follow the U.S. Drought Monitor, re-implemented for India (Regions -> States).

**Every map on the site is rendered live in the browser from the gridded data
files — there are no pre-rendered map images.**

## How to view

The site must be served over HTTP (the maps fetch data files, which the `file://`
protocol blocks):

```bash
cd IDM
python3 -m http.server 8000
# open http://localhost:8000
```

Works as-is on GitHub Pages.

## Architecture

```
assets/interactive/
├── drought-map.core.js     verbatim copy of pranav-joshi-iitgn/IndiaDroughtMonitor/script.js
│                           (kept for reference; unmodified)
├── drought-map.engine.js   a factory wrapper around that same code: createDroughtMap(opts).
│                           Pranav's rendering/interpolation/zoom/animation logic is byte-for-byte
│                           identical; only the hardcoded canvas + file references became opts.
├── drought-map.app.js      site controller: product catalogue (CDI, SPI, SRI, SSMI, forecasts),
│                           weekly date list, and helpers that wire page controls to the engine.
├── drought-map.colormaps.js EXACT colour-maps transcribed from the old WCL site
│                           (Legend*.jsx): CDI, SPI/SRI/SSMI, streamflow, persistence.
│                           Maps are keyed to ACTUAL data values, not min/max.
└── drought-map.css         shared map / control-panel styling (USDM look).
```

A page builds one or more maps with `IDM.mountMap({...})` or `IDM.buildMap(...)`,
each pointing at a data grid. The engine reads the grid via AlaSQL and paints the
canvas. The same engine drives the single-map pages and the two-map pages
(Compare, Slider).


### Map interactions

- **Zoom modes** — a toolbar button toggles between click-state-zoom and a
  rectangle (drag-a-box) zoom; right-click always resets the view.
- **Comparison slider** — two identical full-size maps overlaid; the top (older)
  layer is revealed up to a draggable handle via CSS `clip-path` (never resized).

### Libraries

PapaParse and AlaSQL are vendored under `assets/vendor/` (no CDN; works offline).

## Pages (all live)

- **index.html** — Current Map. The live interactive CDI map with week selector,
  state jump, zoom, hover readout, isolate toggle, and an animation player.
- **maps.html** — Maps landing.
- **compare.html** — two live maps side by side, independent week pickers.
- **slider.html** — two live maps with a draggable wipe handle.
- **animations.html** — live week-by-week playback of the CDI record.
- **archive.html** — render any week back to July 2021, then zoom/inspect.
- **conditions.html** — one live map with a product switcher (SPI / SRI / SSMI at
  several windows, plus 7/15/30-day CDI forecasts).
- **data.html** — Data landing.
- **data-graphs.html** — stacked-area chart of national % area per drought class,
  drawn live from `India_Drought_Area_Timeseries.txt`.
- **data-tables.html** — per-state drought percentages, computed live in the browser
  from the CDI grid + state grid for any selected week.
- **data-download.html** — download links for every raw data product.
- **summary.html / about.html / contact.html** — India-specific narrative pages.

## Hydrological Outlook

An image/PDF section built from the `IHO_Pipeline_Final.ipynb` outputs in
`Hydrologic_Outlook/Output/` (the interactive drought maps are unchanged):

- **hydro.html** — per-variable dashboards (Rainfall, Temperature, Relative Wetness,
  Total Runoff, Evapotranspiration), shown as the composite PNG the pipeline renders.
- **hydro-maps.html** — the individual maps that make up each dashboard, full-size, with a lightbox.
- **hydro-reports.html** — the PDF report archive (inline preview + download).

Driven by `assets/hydro/hydro-manifest.json` and `assets/hydro/reports-manifest.json` via
`assets/hydro/hydro.js`. To publish a new month: add the PNGs/PDF under
`Hydrologic_Outlook/Output/` and add the corresponding entries to the two manifests.

## Data

```
data/
├── Current_CDI.txt                     latest weekly CDI
├── Future_CDI_{7,15,30}day.txt         short-range CDI forecasts
├── SPI_*.txt SRI_*.txt SSMI_*.txt      standardised indices (multiple windows)
├── {P,R,SM}_mag_*.txt                  precip / runoff / soil-moisture magnitudes
├── drought_persist_*.txt               persistence forecasts
├── India_Drought_Area_Timeseries.txt   weekly national % area per class
└── Drough_TS/CDI_YYYYMMDD.txt          253 weekly CDI grids (Jul 2021 -> May 2026)
state_vector_boundaries.json            state/UT polygons + names
states_with_boundaries.csv              rasterised state-id grid (0.0625 deg)
india_mainland_boundary.csv             national outline
```

Every grid is whitespace-separated `latitude longitude value` (CDI/indices: negative =
drier). Drought classes follow the six-bucket CDI scale (No Drought -> D0 -> ... -> D4).

## Credits

- Interactive drought-map engine: Pranav Joshi (pranav-joshi-iitgn/IndiaDroughtMonitor).
- Structure/styling adapted from the U.S. Drought Monitor.
- Developed for the Water and Climate Lab, IIT Gandhinagar.
