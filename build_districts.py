#!/usr/bin/env python3
# =============================================================================
# build_districts.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Per-DISTRICT drought statistics (for the chatbot / data query page).
#
# Boundaries are NOT fetched here any more.  They come from build_boundaries.py,
# which derives states, districts, the 0.0625-deg rasters and the national outline
# from ONE dataset (LGD district boundaries) so they can never disagree.  This
# script only:
#   1. makes sure data/districts/ exists (runs build_boundaries.py if it doesn't,
#      or when --rebuild-boundaries is given);
#   2. samples the CDI grid at every district's 0.0625-deg nodes
#      (data/districts/grid_<state_id>.csv) and tallies the drought classes;
#   3. writes data/districts/district-stats.json and stamps the week into
#      data/districts/index.json (build.py uses it to detect stale district data).
#
# Classification thresholds match the map engine and idm-ai.js exactly.
# Masked / NaN CDI cells are "no data": never counted as any drought class.
#
# USAGE:
#   python3 build_districts.py                       # stats from data/Current_CDI.txt
#   python3 build_districts.py --cdi data/Drough_TS/CDI_20260603.txt
#   python3 build_districts.py --rebuild-boundaries  # re-derive boundaries first (network)
# =============================================================================

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
OUT_DIR = REPO / "data" / "districts"
CDI_STEP = 0.25                      # CDI lattice: origin 37N / 68E, 0.25 deg


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
            grid[(round(la / CDI_STEP) * CDI_STEP, round(lo / CDI_STEP) * CDI_STEP)] = v
    return grid


def cdi_at(grid, lat, lng):
    return grid.get((round(lat / CDI_STEP) * CDI_STEP, round(lng / CDI_STEP) * CDI_STEP))


def classify(v):
    if v is None or v != v:  # None or NaN -> no data, never an exceptional-drought cell
        return None
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


def build(cdi_path=None, rebuild_boundaries=False):
    idx_path = OUT_DIR / "index.json"
    if rebuild_boundaries or not idx_path.exists():
        import build_boundaries
        build_boundaries.build(None, detail_files=True)

    cdi_file = Path(cdi_path) if cdi_path else REPO / "data" / "Current_CDI.txt"
    if not cdi_file.exists():
        sys.exit("CDI grid not found: %s  (pass --cdi PATH)" % cdi_file)
    cdi = load_cdi(cdi_file)
    week = latest_week(REPO)

    manifest = json.loads(idx_path.read_text(encoding="utf-8"))
    stats = []
    for st in manifest["states"]:
        sid, sname = st["state_id"], st["state"]
        names = {d["id"]: d["name"] for d in
                 json.loads((OUT_DIR / st["file"]).read_text(encoding="utf-8"))["districts"]}
        lgd = {d["id"]: d.get("lgd", 0) for d in
               json.loads((OUT_DIR / st["file"]).read_text(encoding="utf-8"))["districts"]}
        tally = {did: {"none": 0, "d0": 0, "d1": 0, "d2": 0, "d3": 0, "d4": 0, "tot": 0} for did in names}
        with open(OUT_DIR / st["grid"], newline="") as fh:
            for row in csv.DictReader(fh):
                did = int(row["district_id"])
                cls = classify(cdi_at(cdi, float(row["lat"]), float(row["lng"])))
                if cls is None:           # NaN/masked cell -> not counted toward coverage or drought
                    continue
                t = tally[did]
                t[cls] += 1
                t["tot"] += 1
        for did, t in tally.items():
            if t["tot"] < 1:
                continue
            pc = lambda k: round(100.0 * t[k] / t["tot"], 1)
            r = {"district": names[did], "state": sname, "state_id": sid, "district_id": did,
                 "lgd": lgd[did],
                 "none_pct": pc("none"), "d0_pct": pc("d0"), "d1_pct": pc("d1"),
                 "d2_pct": pc("d2"), "d3_pct": pc("d3"), "d4_pct": pc("d4")}
            r["drought_pct"] = round(100.0 - r["none_pct"], 1)
            stats.append(r)

    stats.sort(key=lambda r: (r["state"], r["district"]))
    (OUT_DIR / "district-stats.json").write_text(
        json.dumps({"week_ending": week, "districts": stats}, ensure_ascii=False,
                   separators=(",", ":")) + "\n", encoding="utf-8")
    manifest["week_ending"] = week
    idx_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    total = sum(len(json.loads((OUT_DIR / s["file"]).read_text(encoding="utf-8"))["districts"])
                for s in manifest["states"])
    print("District stats: %d of %d districts have valid CDI cells -> data/districts/district-stats.json (week %s)"
          % (len(stats), total, week))
    if len(stats) < total:
        print("  (%d districts have no valid CDI cell this week -- masked/no-data, left out)" % (total - len(stats)))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Per-district drought stats for the IDM.")
    ap.add_argument("--cdi", default=None, help="CDI grid to compute stats from (default data/Current_CDI.txt)")
    ap.add_argument("--rebuild-boundaries", action="store_true",
                    help="re-run build_boundaries.py first (needs network once)")
    ap.add_argument("--no-grids", action="store_true", help="(ignored; kept for backward compatibility)")
    a = ap.parse_args()
    build(cdi_path=a.cdi, rebuild_boundaries=a.rebuild_boundaries)
