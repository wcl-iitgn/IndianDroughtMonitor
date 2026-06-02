#!/usr/bin/env python3
# =============================================================================
# build_districts.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Adds DISTRICT-level data (analogous to the state data), fetched from a public
# GeoJSON and reduced into small, lazy-loadable, per-state files so the website
# only loads a state's districts when that state is clicked, and the chatbot can
# answer district questions.
#
# SOURCE GeoJSON (MIT-licensed, ~759 districts, modern boundaries):
#   https://raw.githubusercontent.com/udit-001/india-maps-data/main/geojson/india.geojson
#   properties: district, dt_code, st_nm, st_code, year
#
# OUTPUTS (all small -> easy to push to GitHub; nothing exceeds 20 MiB):
#   data/districts/index.json                 manifest: state_id -> file + district list
#   data/districts/state_<id>.json            per-state district BOUNDARIES (polylines)
#   data/districts/grid_<id>.csv              per-state district-id GRID at 0.0625 deg
#   data/districts/district-stats.json        per-district drought % (for the chatbot)
#   data/districts/_source/india_districts.geojson   the fetched source (provenance)
#
# District -> state is reconciled to the EXISTING state ids (2..37) used by
# state_vector_boundaries.json. The 2019/2020 reorganisations are folded back to
# match that older state layer: Ladakh -> Jammu & Kashmir (15); the merged
# "Dadra and Nagar Haveli and Daman and Diu" UT is split back to 9 / 10 by
# district name.
#
# Drought classification + thresholds match the map engine and idm-ai.js exactly.
#
# REQUIREMENTS: python3 with numpy + matplotlib (matplotlib.path for point-in-
# polygon). Network access to raw.githubusercontent.com for the first run.
#
# USAGE:
#   python3 build_districts.py                 # fetch (if needed) + build everything
#   python3 build_districts.py --no-grids      # boundaries + stats only
#   python3 build_districts.py --cdi data/Current_CDI.txt
# =============================================================================

import argparse
import csv
import json
import sys
import urllib.request
from pathlib import Path

import numpy as np
from matplotlib.path import Path as MplPath

REPO = Path(__file__).resolve().parent
SRC_URL = "https://raw.githubusercontent.com/udit-001/india-maps-data/main/geojson/india.geojson"
SRC_PATH = REPO / "data" / "districts" / "_source" / "india_districts.geojson"
OUT_DIR = REPO / "data" / "districts"

LON_W, LON_E, LAT_N, LAT_S = 68.0, 97.5, 37.0, 7.0
STATE_STEP = 0.0625
COORD_DECIMALS = 4          # ~11 m; plenty for display, keeps files small
MAX_BYTES = 20 * 1024 * 1024  # 20 MiB GitHub-friendliness guard

# st_nm (source) -> existing state_id (state_vector_boundaries.json). None = split by district.
ST_NM_TO_ID = {
    "Andaman and Nicobar Islands": 2, "Andhra Pradesh": 3, "Arunachal Pradesh": 4,
    "Assam": 5, "Bihar": 6, "Chandigarh": 7, "Chhattisgarh": 8,
    "Dadra and Nagar Haveli and Daman and Diu": None,
    "Delhi": 26, "Goa": 11, "Gujarat": 12, "Haryana": 13, "Himachal Pradesh": 14,
    "Jammu and Kashmir": 15, "Jharkhand": 16, "Karnataka": 17, "Kerala": 18,
    "Ladakh": 15, "Lakshadweep": 19, "Madhya Pradesh": 20, "Maharashtra": 21,
    "Manipur": 22, "Meghalaya": 23, "Mizoram": 24, "Nagaland": 25, "Odisha": 37,
    "Puducherry": 27, "Punjab": 28, "Rajasthan": 29, "Sikkim": 30, "Tamil Nadu": 31,
    "Telangana": 32, "Tripura": 33, "Uttar Pradesh": 34, "Uttarakhand": 35, "West Bengal": 36,
}
# names so the per-state files can show the original (pre-split) state label
ID_TO_NAME = {
    2: "Andaman & Nicobar Islands", 3: "Andhra Pradesh", 4: "Arunachal Pradesh", 5: "Assam",
    6: "Bihar", 7: "Chandigarh", 8: "Chhattisgarh", 9: "Dadra & Nagar Haveli", 10: "Daman & Diu",
    11: "Goa", 12: "Gujarat", 13: "Haryana", 14: "Himachal Pradesh", 15: "Jammu & Kashmir",
    16: "Jharkhand", 17: "Karnataka", 18: "Kerala", 19: "Lakshadweep", 20: "Madhya Pradesh",
    21: "Maharashtra", 22: "Manipur", 23: "Meghalaya", 24: "Mizoram", 25: "Nagaland",
    26: "NCT of Delhi", 27: "Puducherry", 28: "Punjab", 29: "Rajasthan", 30: "Sikkim",
    31: "Tamil Nadu", 32: "Telangana", 33: "Tripura", 34: "Uttar Pradesh", 35: "Uttarakhand",
    36: "West Bengal", 37: "Odisha",
}


def resolve_state_id(st_nm, district):
    sid = ST_NM_TO_ID.get(st_nm, None)
    if sid is not None:
        return sid
    # merged UT: split back by district name
    d = (district or "").lower()
    if "dadra" in d or "nagar haveli" in d:
        return 9
    if "daman" in d or "diu" in d:
        return 10
    return 9  # fallback within the merged UT


def fetch_source():
    if SRC_PATH.exists() and SRC_PATH.stat().st_size > 100000:
        return
    SRC_PATH.parent.mkdir(parents=True, exist_ok=True)
    print("Fetching district GeoJSON ...")
    req = urllib.request.Request(SRC_URL, headers={"User-Agent": "idm-build/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(SRC_PATH, "wb") as f:
        f.write(r.read())
    print("  saved %s (%.1f MB)" % (SRC_PATH.relative_to(REPO), SRC_PATH.stat().st_size / 1e6))


def rings_of(geom):
    """Yield exterior+interior rings as lists of [lng,lat], for Polygon/MultiPolygon."""
    t = geom["type"]; c = geom["coordinates"]
    polys = [c] if t == "Polygon" else c
    out = []
    for poly in polys:
        for ring in poly:
            out.append([(float(x), float(y)) for x, y in ring])
    return out


def round_ring(ring):
    """Round coords and drop consecutive duplicates -> smaller files."""
    out = []
    last = None
    for x, y in ring:
        p = [round(x, COORD_DECIMALS), round(y, COORD_DECIMALS)]
        if p != last:
            out.append(p)
            last = p
    return out


# ---- CDI grid (0.25 deg) for nearest-value lookup -----------------------------
def load_cdi(path):
    grid = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = line.split()
            if len(p) < 3:
                continue
            try:
                la, lo, v = float(p[0]), float(p[1]), float(p[2])
            except ValueError:
                continue
            if v != v:  # NaN
                continue
            grid[(round(la / 0.25) * 0.25, round(lo / 0.25) * 0.25)] = v
    return grid


def cdi_at(grid, lat, lng):
    return grid.get((round(lat / 0.25) * 0.25, round(lng / 0.25) * 0.25))


def classify(v):
    if v > -0.5: return "none"
    if v > -0.8: return "d0"
    if v > -1.3: return "d1"
    if v > -1.6: return "d2"
    if v > -2.0: return "d3"
    return "d4"


def latest_week(repo):
    ts = repo / "data" / "India_Drought_Area_Timeseries.txt"
    try:
        last = [l for l in ts.read_text().splitlines() if l.strip()][-1].split()
        return "%04d-%02d-%02d" % (int(float(last[0])), int(float(last[1])), int(float(last[2])))
    except Exception:
        return None


def build(make_grids=True, cdi_path=None):
    fetch_source()
    gj = json.loads(SRC_PATH.read_text(encoding="utf-8"))
    feats = gj["features"]

    cdi = load_cdi(cdi_path or (REPO / "data" / "Current_CDI.txt"))
    week = latest_week(REPO)

    # 0.0625 deg lattice (the district grid lives on the same lattice as the state grid)
    lat_axis = np.round(np.arange(LAT_N, LAT_S - 1e-9, -STATE_STEP), 4)
    lon_axis = np.round(np.arange(LON_W, LON_E + 1e-9, STATE_STEP), 4)

    by_state = {}             # state_id -> list of district dicts (boundaries)
    grid_rows = {}            # state_id -> list of (lat,lng,district_id)
    stats = []                # per-district drought stats

    next_did = {}             # state_id -> running district number (stable ids)

    for f in feats:
        pr = f["properties"]
        st_nm = pr.get("st_nm", ""); dname = pr.get("district", "") or "Unknown"
        sid = resolve_state_id(st_nm, dname)
        next_did[sid] = next_did.get(sid, 0) + 1
        did = sid * 1000 + next_did[sid]          # e.g. 12001 = Gujarat district #1

        rings = [round_ring(r) for r in rings_of(f["geometry"])]
        rings = [r for r in rings if len(r) >= 4]
        by_state.setdefault(sid, []).append({"id": did, "name": dname, "rings": rings})

        # ---- rasterise this district onto the 0.0625 deg lattice (point-in-poly) ----
        xs = [x for r in rings for x, y in r]; ys = [y for r in rings for x, y in r]
        if not xs:
            continue
        c0, c1 = max(LON_W, min(xs)), min(LON_E, max(xs))
        r0, r1 = max(LAT_S, min(ys)), min(LAT_N, max(ys))
        col_sel = lon_axis[(lon_axis >= c0 - STATE_STEP) & (lon_axis <= c1 + STATE_STEP)]
        row_sel = lat_axis[(lat_axis <= r1 + STATE_STEP) & (lat_axis >= r0 - STATE_STEP)]
        if col_sel.size == 0 or row_sel.size == 0:
            continue
        LonG, LatG = np.meshgrid(col_sel, row_sel)
        pts = np.column_stack([LonG.ravel(), LatG.ravel()])
        inside = np.zeros(len(pts), dtype=bool)
        for ring in rings:                         # union of all rings (incl. multipolygon)
            inside |= MplPath(np.asarray(ring)).contains_points(pts)
        if not inside.any():
            # tiny district: fall back to its centroid cell
            cx, cy = float(np.mean(xs)), float(np.mean(ys))
            inside_pts = [(round(cy / STATE_STEP) * STATE_STEP, round(cx / STATE_STEP) * STATE_STEP)]
        else:
            inside_pts = [(round(la, 4), round(lo, 4)) for lo, la in pts[inside]]

        tally = {"none": 0, "d0": 0, "d1": 0, "d2": 0, "d3": 0, "d4": 0}
        tot = 0
        for la, lo in inside_pts:
            if make_grids:
                grid_rows.setdefault(sid, []).append((la, lo, did))
            v = cdi_at(cdi, la, lo)
            if v is None:
                continue
            tally[classify(v)] += 1; tot += 1
        if tot >= 1:
            def pc(k): return round(100.0 * tally[k] / tot, 1)
            row = {"district": dname, "state": ID_TO_NAME.get(sid, st_nm), "state_id": sid,
                   "none_pct": pc("none"), "d0_pct": pc("d0"), "d1_pct": pc("d1"),
                   "d2_pct": pc("d2"), "d3_pct": pc("d3"), "d4_pct": pc("d4")}
            row["drought_pct"] = round(100.0 - row["none_pct"], 1)
            stats.append(row)

    # ---- write per-state boundary files + manifest ----
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {"week_ending": week, "states": []}
    for sid in sorted(by_state):
        dists = sorted(by_state[sid], key=lambda d: d["name"])
        obj = {"state_id": sid, "state": ID_TO_NAME.get(sid, ""), "districts": dists}
        fp = OUT_DIR / ("state_%d.json" % sid)
        fp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        split_if_big(fp)
        manifest["states"].append({
            "state_id": sid, "state": ID_TO_NAME.get(sid, ""),
            "file": "state_%d.json" % sid, "grid": ("grid_%d.csv" % sid) if make_grids else None,
            "districts": [d["name"] for d in dists]
        })
    (OUT_DIR / "index.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # ---- write per-state grids ----
    if make_grids:
        for sid, rows in grid_rows.items():
            fp = OUT_DIR / ("grid_%d.csv" % sid)
            with open(fp, "w", newline="") as fh:
                w = csv.writer(fh); w.writerow(["lat", "lng", "district_id"])
                w.writerows(rows)
            split_if_big(fp)

    # ---- write chatbot stats ----
    stats.sort(key=lambda r: (r["state"], r["district"]))
    (OUT_DIR / "district-stats.json").write_text(
        json.dumps({"week_ending": week, "districts": stats}, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8")

    # ---- report ----
    tot_b = sum((OUT_DIR / ("state_%d.json" % s)).stat().st_size for s in by_state)
    print("Districts: %d across %d states." % (sum(len(v) for v in by_state.values()), len(by_state)))
    print("  boundaries: %d files, %.1f MB total" % (len(by_state), tot_b / 1e6))
    if make_grids:
        print("  grids: %d files" % len(grid_rows))
    print("  stats: %d districts -> data/districts/district-stats.json (week %s)" % (len(stats), week))
    big = [p.name for p in OUT_DIR.rglob("*") if p.is_file() and p.stat().st_size > MAX_BYTES]
    print("  files over 20 MiB:", big or "none")


def split_if_big(path):
    """If a file exceeds 20 MiB, split into <name>.partNN (line-wise) so GitHub stays happy.
    Per-state splitting already keeps everything small, so this rarely triggers."""
    if path.stat().st_size <= MAX_BYTES:
        return
    data = path.read_bytes()
    n = (len(data) + MAX_BYTES - 1) // MAX_BYTES
    for i in range(n):
        (path.parent / ("%s.part%02d" % (path.name, i + 1))).write_bytes(data[i * MAX_BYTES:(i + 1) * MAX_BYTES])
    print("  ! %s was >20 MiB; split into %d parts (reassemble with `cat %s.part* > %s`)"
          % (path.name, n, path.name, path.name))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build district boundaries/grids/stats for the IDM.")
    ap.add_argument("--no-grids", action="store_true", help="skip the per-state district-id grids")
    ap.add_argument("--cdi", default=None, help="CDI grid to compute stats from (default data/Current_CDI.txt)")
    args = ap.parse_args()
    build(make_grids=not args.no_grids, cdi_path=args.cdi)
