# IDM boundary update - what to do

## 1. Files in this bundle (copy each to the SAME path in your repo; overwrite)

NEW scripts / docs
  build_boundaries.py            builds ALL boundary files from one district shapefile
  verify_boundaries.py           28 consistency checks on the shipped outputs
  BOUNDARIES.md                  documentation (source, method, known limits)
  STEPS.md                       this file

CHANGED code
  build_districts.py             now only computes per-district drought stats (no download)
  idm_maps.py                    Ladakh (38) -> North; old id 10 removed
  assets/interactive/drought-map.engine.js   even-odd district hover; no doubled border lines
  assets/interactive/drought-map.app.js      reads the "ut" flag; Ladakh added to UT fallback
  assets/ai/idm-ai.js            chatbot told the exact state spellings (Keralam, Ladakh, ...)
  README.md  .gitignore          docs + ignore the 180 MB raw shapefile

REGENERATED data (already built for you)
  state_vector_boundaries.json   19.6 MB -> 2.0 MB, 36 states/UTs, Keralam, Ladakh, merged DNH&DD
  states_with_boundaries.csv     state-id raster (0 outside, 1 coastal fringe, >=2 state id)
  india_mainland_boundary.csv    national outline (123,034 -> 12,178 points)
  assets/interactive/india_districts.json   784 districts, all named
  data/districts/                index.json + state_<id>.json + grid_<id>.csv (36 states)

NOT in the bundle (you already have them)
  the raw shapefile  File_549996_....zip  (keep it OUT of git; ~180 MB)

## 2. Steps
  cd <your repo>
  # a) remove the OLD district folder first, so stale ids (state_10 ...) cannot linger
  git rm -r data/districts          # (or rm -rf data/districts)
  # b) copy the bundle contents over the repo (keeps paths)
  # c) delete the byte-identical duplicates (45 MB):
  git rm "build (copy).py" "build_districts (copy).py" "state_vector_boundaries (copy).json" \
         "states_with_boundaries (copy).csv" "forecast (copy).html" "index (copy).html"
  # d) per-district drought numbers for the current week (needs your real CDI data):
  python3 build_districts.py                      # uses data/Current_CDI.txt
  #   or:  python3 build.py --with-districts
  # e) check:
  python3 verify_boundaries.py                    # expect: 28 checks passed, 0 failed
  # f) look at the site
  python3 -m http.server 8000   ->  http://localhost:8000
      click Gujarat (districts appear), Ladakh (own state), Keralam, Puducherry (4 districts),
      hover inside Pudukkottai's enclaves; open Data Tables (Ladakh row, Keralam name).
  # g) commit
  git add -A && git commit -m "Rebuild all administrative boundaries from one district dataset"

## 3. When the boundary source changes later (new districts, new state names)
  put the new DISTRICT_BOUNDARY.* in data/boundaries/_source/   (or pass --source file.zip)
  python3 build_boundaries.py          # ~2 min, no network
  python3 build_districts.py
  python3 verify_boundaries.py
  New state name?  add it to STATES in build_boundaries.py (and KERALA_DISPLAY-style constant).

## 4. Known limits (the source file has no geometry for these)
  * Gujarat shows 33 districts: Vav-Tharad (34th) is missing.
  * Delhi shows the pre-2026 layout (11 + "Nazul"), not the 13 districts in force since 1 Jan 2026.
  * The 28 inter-state "disputed" slivers (45 km2 total) are merged into the neighbouring claimant
    district.  Use  python3 build_boundaries.py --disputed exclude  to leave them out instead.
  * Smallest Lakshadweep islets shrink in the national-detail layer (-14 % of 30 km2).
