#!/usr/bin/env python3
# =============================================================================
# verify_boundaries.py  --  sanity-check every boundary file the website uses.
# Re-reads ONLY the shipped outputs (not the build intermediates), so it catches
# any drift between states, districts, rasters and the national outline.
#
#   python3 verify_boundaries.py          # exit code 0 = all checks pass
# =============================================================================
import csv
import json
import sys
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parent
OK, BAD = [], []


def check(name, cond, detail=""):
    (OK if cond else BAD).append(name)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name, ("  -- " + detail) if detail else ""))


def poly_from_rings(rings):
    """even-odd polygon from a list of [lng,lat] rings"""
    acc = Polygon()
    for r in rings:
        p = Polygon(r)
        if not p.is_valid:
            p = p.buffer(0)
        acc = acc.symmetric_difference(p)
    return acc


def main():
    sv = json.loads((REPO / "state_vector_boundaries.json").read_text(encoding="utf-8"))
    dd = json.loads((REPO / "assets/interactive/india_districts.json").read_text(encoding="utf-8"))
    idx = json.loads((REPO / "data/districts/index.json").read_text(encoding="utf-8"))
    names = {}
    for p in sv:
        names.setdefault(p["state_id"], p["name"])
    ids = sorted(names)

    print("State layer")
    check("36 states/UTs present", len(ids) == 36, "%d found" % len(ids))
    check("ids are the expected set (2..9,11..38; 10 retired)",
          ids == [2, 3, 4, 5, 6, 7, 8, 9] + list(range(11, 39)))
    check("Ladakh (38) exists as its own UT", names.get(38) == "Ladakh")
    check("merged DNH & DD is id 9, no id 10",
          names.get(9) == "Dadra & Nagar Haveli and Daman & Diu" and 10 not in names)
    typos = [n for n in names.values() if n in ("Arunanchal Pradesh", "Dadara & Nagar Havelli",
                                                "Andaman & Nicobar Island")]
    check("old misspelt names are gone", not typos, str(typos))
    check("every ring is closed", all(p["coordinates"][0] == p["coordinates"][-1] for p in sv))
    check("every state has a boolean ut flag", all(isinstance(p.get("ut"), bool) for p in sv))

    print("District layer")
    n_idx = sum(len(st["districts"]) for st in idx["states"])
    check("district count is plausible and matches the manifest", 780 <= len(dd) <= 800 and len(dd) == n_idx,
          "%d districts" % len(dd))
    check("Kerala is spelt Keralam", names.get(18) == "Keralam" and "Kerala" not in names.values())
    pud = sorted(d["name"] for d in dd if d["state_id"] == 27)
    check("Puducherry has its 4 districts", pud == ["Karaikal", "Mahe", "Puducherry", "Yanam"], str(pud))
    check("no upper-case-only district names left (except acronyms)",
          not [d["name"] for d in dd if d["name"].isupper() and d["name"] not in ("NTR",)],
          str([d["name"] for d in dd if d["name"].isupper()][:5]))
    check("no 'Unknown'/empty district names", all(d["name"] and d["name"] != "Unknown" for d in dd))
    check("every district has geometry", all(len(d["rings"]) >= 1 for d in dd))
    check("district ids unique", len({d["id"] for d in dd}) == len(dd))
    seen = {}
    for d in dd:
        seen.setdefault((d["state_id"], d["name"]), 0)
        seen[(d["state_id"], d["name"])] += 1
    check("district names unique within each state", max(seen.values()) == 1)
    check("district state_ids all exist in the state layer", {d["state_id"] for d in dd} <= set(ids))
    per_state = {}
    for d in dd:
        per_state[d["state_id"]] = per_state.get(d["state_id"], 0) + 1
    detail_ids = {}
    ok_files = True
    for st in idx["states"]:
        o = json.loads((REPO / "data/districts" / st["file"]).read_text(encoding="utf-8"))
        detail_ids[st["state_id"]] = {x["id"] for x in o["districts"]}
        if not (REPO / "data/districts" / st["grid"]).exists():
            ok_files = False
    check("per-state files exist for all 36 states", len(idx["states"]) == 36 and ok_files)
    same = all(detail_ids[s] == {d["id"] for d in dd if d["state_id"] == s} for s in ids)
    check("detail files and national overlay carry identical district ids", same)
    check("no stale state_10 files", not (REPO / "data/districts/state_10.json").exists())

    def state_geom(s):
        """even-odd over the state's rings: exterior rings add area, hole rings subtract it"""
        return poly_from_rings([[(c["lng"], c["lat"]) for c in p["coordinates"]]
                                for p in sv if p["state_id"] == s])

    print("Geometry consistency (state = dissolve of its districts)")
    worst, worst_s = 0.0, None
    for s in ids:
        sp = state_geom(s)
        dp = unary_union([poly_from_rings(d["rings"]) for d in dd if d["state_id"] == s])
        a = sp.symmetric_difference(dp).area / max(sp.area, 1e-12)
        if a > worst:
            worst, worst_s = a, names[s]
    check("state polygon == union of its districts (worst symmetric diff < 0.5%)", worst < 0.005,
          "worst %.4f%% (%s)" % (100 * worst, worst_s))

    print("Raster")
    rows = list(csv.DictReader(open(REPO / "states_with_boundaries.csv")))
    la = np.array([float(r["lat"]) for r in rows])
    lo = np.array([float(r["lng"]) for r in rows])
    v = np.array([int(r["value"]) for r in rows])
    check("512 x 480 lattice, 0.0625 deg", len(v) == 512 * 480 and la.min() == 6.0 and lo.min() == 68.0)
    check("only 0, 1 or valid state ids used", set(np.unique(v)) <= {0, 1} | set(ids))
    check("every state/UT owns >= 1 raster node", all((v == s).any() for s in ids),
          str([names[s] for s in ids if not (v == s).any()]))
    rng = np.random.default_rng(1)
    sel = np.where(v >= 2)[0]
    sel = rng.choice(sel, min(4000, len(sel)), replace=False)
    state_poly = {s: state_geom(s) for s in ids}
    inside = 0
    for i in sel:
        g = state_poly[int(v[i])]
        if shapely.distance(g, shapely.Point(lo[i], la[i])) <= 0.01:
            inside += 1
    check("sampled state nodes lie in their own state (>= 99.5%)", inside / len(sel) >= 0.995,
          "%.2f%%" % (100 * inside / len(sel)))
    country = unary_union(list(state_poly.values()))
    out = np.where(v == 0)[0]
    out = rng.choice(out, 4000, replace=False)
    wrong = sum(1 for i in out if country.contains(shapely.Point(lo[i], la[i])))
    check("nodes marked 0 are outside India", wrong == 0, "%d inside" % wrong)
    grid = {(round(a, 4), round(b, 4)): int(c) for a, b, c in zip(la, lo, v)}
    mism = tot = 0
    for st in idx["states"]:
        for r in csv.DictReader(open(REPO / "data/districts" / st["grid"])):
            tot += 1
            if grid.get((round(float(r["lat"]), 4), round(float(r["lng"]), 4))) != st["state_id"]:
                mism += 1
    check("district grids agree with the state raster (>= 99.9%)", mism / tot < 0.001,
          "%d of %d differ" % (mism, tot))

    print("National outline")
    ml = list(csv.DictReader(open(REPO / "india_mainland_boundary.csv")))
    pts = [(float(r["lng"]), float(r["lat"])) for r in ml]
    check("outline is a closed ring", pts[0] == pts[-1], "%d pts" % len(pts))
    mp = Polygon(pts).buffer(0)
    main_area = max((p.area for p in (country.geoms if country.geom_type == "MultiPolygon" else [country])))
    check("outline encloses the mainland (area within 0.5%)", abs(mp.area - main_area) / main_area < 0.005)

    print("\n%d checks passed, %d failed" % (len(OK), len(BAD)))
    sys.exit(1 if BAD else 0)


if __name__ == "__main__":
    main()
