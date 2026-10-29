#!/usr/bin/env python3
# =============================================================================
# build_boundaries.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# ONE source of truth for every administrative boundary on the site.
#
# WHY THIS EXISTS
#   Before, the state layer, the district layer, the 0.0625-deg state raster and
#   the national outline came from four unrelated sources of different vintages,
#   so they disagreed with each other (Ladakh folded into J&K, DNH and Daman & Diu
#   split, ~9 % of land cells in no state, 34 "Unknown" districts, Andhra Pradesh
#   at its 2011 district count, ...).  Here EVERYTHING is derived from a single
#   district-level dataset, so by construction:
#       states      = exact dissolve of their districts      (no gaps / overlaps)
#       raster grid = rasterisation of those same districts  (state AND district)
#       outline     = outer ring of that same dissolve
#
# SOURCE
#   DISTRICT_BOUNDARY.shp  (Survey-of-India-style district boundaries, Lambert
#   Conformal Conic, 810 features: 782 named districts + 28 unnamed inter-state
#   "DISPUTED" slivers).  Put the shapefile set (or the .zip you received) in
#   data/boundaries/_source/  or pass --source PATH.  It is ~180 MB: do NOT commit it.
#
#   Handling done here (all printed in the build log):
#     * reprojected to WGS84; invalid geometries repaired; overlaps carved out
#     * the 28 disputed slivers (45 km2 in total) are merged into the adjoining
#       district of a CLAIMANT state they share the longest border with
#       (--disputed exclude leaves them out of every state/district instead)
#     * Puducherry's single multipart polygon is split into Puducherry, Karaikal,
#       Mahe and Yanam
#     * names normalised (upper-case -> Title Case, "and" -> "&", Delhi "Central" ->
#       "Central Delhi", ...);  state name follows the file: KERALAM
#
#   KNOWN GAPS OF THE SOURCE (it has no geometry for them -- update the source file
#   when a newer one is released):  Gujarat's Vav-Tharad district (carved from
#   Banas Kantha), and Delhi's 13-district layout in force since 1 Jan 2026.
#
# OUTPUTS  (all paths relative to the repo root)
#   state_vector_boundaries.json            state/UT polygons + names (+ "ut" flag)
#   states_with_boundaries.csv              state-id raster, 0.0625 deg:
#                                             0 = outside India, >=2 = state/UT id,
#                                             1 = coastal/border fringe (node whose cell
#                                                 touches India; keeps the land mask flush
#                                                 with the outline; NOT part of any state)
#   india_mainland_boundary.csv             national outline (mainland ring)
#   assets/interactive/india_districts.json  every district (national-scale detail)
#   data/districts/state_<id>.json           per-state districts (high detail, lazy-loaded)
#   data/districts/grid_<id>.csv             per-state district-id raster
#   data/districts/index.json                manifest
#   (district-stats.json is produced by build_districts.py from the grids)
#
# STATE IDS  (kept stable so every existing consumer keeps working)
#   2..37 as before.  Changes: 38 = Ladakh (new UT, was inside J&K = 15);
#   9 = "Dadra & Nagar Haveli and Daman & Diu" (merged UT, id 10 retired).
#
# REQUIREMENTS:  pip install geopandas pyarrow shapely numpy
#   (no network needed)
#
# USAGE
#   python3 build_boundaries.py                  # build everything from data/boundaries/_source/
#   python3 build_boundaries.py --source ~/Downloads/File_549996_....zip
#   python3 build_boundaries.py --no-district-files   # only the 3 national files
# =============================================================================

import argparse
import csv
import json
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid

REPO = Path(__file__).resolve().parent
SRC_DIR = REPO / "data" / "boundaries" / "_source"
SRC_SHP = SRC_DIR / "DISTRICT_BOUNDARY.shp"
OUT_DISTRICTS = REPO / "data" / "districts"

# 0.0625-deg lattice used by the whole site (identical to the old state grid)
LON_W, LAT_S, STEP = 68.0, 6.0, 0.0625
N_ROWS, N_COLS = 512, 480                      # lat 6.0..37.9375, lng 68.0..97.9375

TOL_NATIONAL = 0.004     # deg (~450 m): states, outline, all-districts overlay
TOL_DETAIL = 0.001       # deg (~110 m): per-state district files
DEC = 4                  # output coordinate decimals (~11 m)

# --------------------------------------------------------------------------- states
# source STATE_UT (upper-case, " AND " -> " & ") -> (idm state_id, display name, is_union_territory)
KERALA_DISPLAY = "Keralam"      # renamed by the Kerala (Alteration of Name) Act, 2026
STATES = {
    "ANDAMAN & NICOBAR ISLANDS":       (2,  "Andaman & Nicobar Islands", True),
    "ANDHRA PRADESH":                  (3,  "Andhra Pradesh", False),
    "ARUNACHAL PRADESH":               (4,  "Arunachal Pradesh", False),
    "ASSAM":                           (5,  "Assam", False),
    "BIHAR":                           (6,  "Bihar", False),
    "CHANDIGARH":                      (7,  "Chandigarh", True),
    "CHHATTISGARH":                    (8,  "Chhattisgarh", False),
    "DADRA & NAGAR HAVELI & DAMAN & DIU": (9, "Dadra & Nagar Haveli and Daman & Diu", True),
    "GOA":                             (11, "Goa", False),
    "GUJARAT":                         (12, "Gujarat", False),
    "HARYANA":                         (13, "Haryana", False),
    "HIMACHAL PRADESH":                (14, "Himachal Pradesh", False),
    "JAMMU & KASHMIR":                 (15, "Jammu & Kashmir", True),
    "JHARKHAND":                       (16, "Jharkhand", False),
    "KARNATAKA":                       (17, "Karnataka", False),
    "KERALAM":                         (18, KERALA_DISPLAY, False),
    "KERALA":                          (18, KERALA_DISPLAY, False),     # older copies of the file
    "LAKSHADWEEP":                     (19, "Lakshadweep", True),
    "MADHYA PRADESH":                  (20, "Madhya Pradesh", False),
    "MAHARASHTRA":                     (21, "Maharashtra", False),
    "MANIPUR":                         (22, "Manipur", False),
    "MEGHALAYA":                       (23, "Meghalaya", False),
    "MIZORAM":                         (24, "Mizoram", False),
    "NAGALAND":                        (25, "Nagaland", False),
    "DELHI":                           (26, "NCT of Delhi", True),
    "PUDUCHERRY":                      (27, "Puducherry", True),
    "PUNJAB":                          (28, "Punjab", False),
    "RAJASTHAN":                       (29, "Rajasthan", False),
    "SIKKIM":                          (30, "Sikkim", False),
    "TAMIL NADU":                      (31, "Tamil Nadu", False),
    "TELANGANA":                       (32, "Telangana", False),
    "TRIPURA":                         (33, "Tripura", False),
    "UTTAR PRADESH":                   (34, "Uttar Pradesh", False),
    "UTTARAKHAND":                     (35, "Uttarakhand", False),
    "WEST BENGAL":                     (36, "West Bengal", False),
    "ODISHA":                          (37, "Odisha", False),
    "LADAKH":                          (38, "Ladakh", True),
}


def state_key(raw):
    return re.sub(r"\s+", " ", str(raw).upper().replace(" AND ", " & ")).strip()


ID_TO_NAME = {v[0]: v[1] for v in STATES.values()}
ID_TO_UT = {v[0]: v[2] for v in STATES.values()}

# --------------------------------------------------------------------------- names
DELHI_COMPASS = {"Central", "East", "North", "North East", "North West",
                 "South", "South East", "South West", "West"}
KEEP_UPPER = {"NTR"}                       # acronyms that must stay upper-case
NAME_FIX = {                               # (state_id, name after case-normalising) -> display
    (3, "Y.S.R."): "Y.S.R. Kadapa",
    (3, "Ntr"): "NTR",
    (36, "Alipur Duar"): "Alipurduar",                   # source writes it as two words
    (36, "South 24Parganas"): "South 24 Parganas",       # missing space in the source
    (23, "South West Kh>Si Hills"): "South West Khasi Hills",   # one '>' left in the source
}


def clean_name(name, state_id):
    n = re.sub(r"\s+", " ", str(name)).strip()
    if n.isupper() and n not in KEEP_UPPER:
        n = re.sub(r"[A-Za-z][A-Za-z'.]*",
                   lambda m: m.group(0) if "." in m.group(0) else m.group(0).capitalize(), n)
    n = re.sub(r"\s+[Aa]nd\s+", " & ", n)
    n = NAME_FIX.get((state_id, n), n)
    if state_id == 26 and n in DELHI_COMPASS:          # "Central" -> "Central Delhi"
        n = n + " Delhi"
    return n


# --------------------------------------------------------------------------- source
def find_source(arg):
    """Return a path to DISTRICT_BOUNDARY.shp (extracting a .zip if given)."""
    p = Path(arg) if arg else SRC_SHP
    if p.suffix.lower() == ".zip":
        SRC_DIR.mkdir(parents=True, exist_ok=True)
        for old in SRC_DIR.glob("DISTRICT_BOUNDARY.*"):      # no stale index files from an older version
            old.unlink()
        with zipfile.ZipFile(p) as z:
            for m in z.namelist():
                if Path(m).name.startswith("DISTRICT_BOUNDARY.") and not m.endswith("/"):
                    (SRC_DIR / Path(m).name).write_bytes(z.read(m))
        p = SRC_SHP
    if not p.exists():
        sys.exit("District shapefile not found: %s\n"
                 "Put DISTRICT_BOUNDARY.* in data/boundaries/_source/ or pass --source PATH(.shp|.zip)" % p)
    return p


def polys_only(g):
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else Polygon()


def km2(geom):
    import geopandas as gpd
    return float(gpd.GeoSeries([geom], crs=4326).to_crs(6933).area.iloc[0]) / 1e6


CLAIM_WORDS = {"RAJSTHAN": "RAJASTHAN", "RAJASTHAN": "RAJASTHAN", "GUJRAT": "GUJARAT", "GUJARAT": "GUJARAT",
               "MADHYA PRADESH": "MADHYA PRADESH", "MAHARASHTRA": "MAHARASHTRA", "JHARKHAND": "JHARKHAND",
               "BIHAR": "BIHAR", "UTTAR PRADESH": "UTTAR PRADESH", "WEST BENGAL": "WEST BENGAL"}


def load_records(src, disputed_mode="assign"):
    """Read the shapefile -> list of dict(state_id, raw_name, lgd, geom); log all fixes."""
    import geopandas as gpd
    g = gpd.read_file(src)
    if g.crs is None:
        sys.exit("source has no CRS")
    g = g.to_crs(4326).reset_index(drop=True)
    log = []
    geoms = [polys_only(make_valid(x) if not x.is_valid else x) for x in shapely.force_2d(g.geometry.values)]
    named = [i for i in g.index if isinstance(g.STATE_UT[i], str) and isinstance(g.DISTRICT[i], str)]
    disp = [i for i in g.index if i not in set(named)]
    state_of = {}
    for i in named:
        k = state_key(g.STATE_UT[i])
        if k not in STATES:
            sys.exit("Unknown STATE_UT %r -- add it to STATES in build_boundaries.py" % g.STATE_UT[i])
        state_of[i] = STATES[k][0]
    fixed_invalid = [g.DISTRICT[i] for i in named if not g.geometry[i].is_valid]
    if fixed_invalid:
        log.append("repaired invalid geometry: %s" % ", ".join(fixed_invalid))

    # ---- disputed inter-state slivers -> merge into the adjoining claimant district
    if disp and disputed_mode == "assign":
        tree = STRtree([geoms[i] for i in named])
        for d in disp:
            rem = str(g.REMARKS[d]).upper()
            claim = {v for k, v in CLAIM_WORDS.items() if k in rem}
            claim_ids = {STATES[c][0] for c in claim}
            buf = geoms[d].buffer(1e-5)
            best, best_len = None, 0.0
            for pos in tree.query(buf):
                i = named[pos]
                if state_of[i] not in claim_ids:
                    continue
                ln = geoms[i].boundary.intersection(buf).length
                if ln > best_len:
                    best, best_len = i, ln
            if best is None:
                log.append("disputed sliver %d (%s, %.2f km2) touches no claimant district -> left out"
                           % (d, g.REMARKS[d], km2(geoms[d])))
                continue
            geoms[best] = polys_only(unary_union([geoms[best], geoms[d]]))
            log.append("disputed %-40s %5.2f km2 -> %s (%s)" % (g.REMARKS[d], km2(geoms[d]),
                       g.DISTRICT[best], ID_TO_NAME[state_of[best]]))
    elif disp:
        log.append("%d disputed slivers (%.1f km2) left out of every state/district"
                   % (len(disp), sum(km2(geoms[d]) for d in disp)))

    recs = []
    for i in named:
        lg = str(g.DIST_LGD[i]).strip()
        recs.append({"state_id": state_of[i], "raw": g.DISTRICT[i],
                     "lgd": int(lg) if lg.isdigit() else 0, "geom": geoms[i]})

    # ---- Puducherry: one multipart polygon -> Puducherry + Mahe + Yanam
    out = []
    for r in recs:
        if r["state_id"] == 27 and str(r["raw"]).strip().upper() == "PUDUCHERRY":
            parts = list(getattr(r["geom"], "geoms", [r["geom"]]))
            mahe = [p for p in parts if p.centroid.x < 77.0]
            yanam = [p for p in parts if p.centroid.y > 15.0]
            rest = [p for p in parts if p not in mahe and p not in yanam]
            if mahe and yanam and rest:
                out.append({**r, "raw": "Puducherry", "geom": unary_union(rest)})
                out.append({"state_id": 27, "raw": "Mahe", "lgd": 0, "geom": unary_union(mahe)})
                out.append({"state_id": 27, "raw": "Yanam", "lgd": 0, "geom": unary_union(yanam)})
                log.append("split Puducherry multipart polygon into Puducherry / Mahe / Yanam")
                continue
        out.append(r)
    return out, log


PRE_SIMPLIFY = 2e-4  # deg (~22 m) per-district thinning before topology rebuild
SNAP = 1e-4          # deg (~11 m): same precision the outputs are written at
SLIVER_KM2 = 1.0     # gaps smaller than this are digitising slivers, larger ones are real
                     # (Bangladeshi enclaves, harbour inlets) and stay as holes


def conform_coverage(recs, log):
    """Rebuild a CONFORMING topology from the source polygons.

    The source's district edges often run along their neighbour's edge without sharing
    its vertices (97 districts, ~320 micro-gaps and hairline overlaps).  Such a layer
    cannot be simplified consistently.  Here all boundaries are snapped to ~11 m, noded
    together and polygonised into faces; every face goes to exactly one district
    (overlaps -> the smaller district; gap slivers < 1 km2 -> the neighbour sharing the
    longest border).  Each district is then the union of its faces, so neighbours share
    identical vertices by construction."""
    G = np.array([r["geom"] for r in recs], dtype=object)
    # 5.7 M source vertices are far more than needed (outputs are <= 0.001 deg detail);
    # thinning by 0.0002 deg (~22 m) first keeps noding fast and memory small.
    G = np.array([polys_only(shapely.simplify(g, PRE_SIMPLIFY)) for g in G], dtype=object)
    for r, g in zip(recs, G):
        r["geom"] = g
    snapped = shapely.set_precision(G, SNAP, mode="pointwise")
    lines = shapely.union_all(shapely.boundary(snapped))          # nodes every crossing
    faces = list(shapely.get_parts(shapely.polygonize([lines])))
    area_deg = shapely.area(G)
    tree = STRtree(G)
    pts = shapely.point_on_surface(np.array(faces, dtype=object))
    owner = np.full(len(faces), -1)
    n_overlap = 0
    pi, di = tree.query(pts, predicate="within")
    cand = {}
    for p, d in zip(pi, di):
        cand.setdefault(int(p), []).append(int(d))
    for p, ds in cand.items():
        if len(ds) > 1:
            n_overlap += 1
        owner[p] = min(ds, key=lambda d: area_deg[d])            # smaller district keeps overlap
    gap = [i for i in range(len(faces)) if owner[i] < 0]
    big_gaps = 0
    if gap:
        assigned = [i for i in range(len(faces)) if owner[i] >= 0]
        ftree = STRtree([faces[i] for i in assigned])
        for gi in gap:
            f = faces[gi]
            if km2(f) >= SLIVER_KM2:
                big_gaps += 1
                continue
            best, best_len = -1, 0.0
            for pos in ftree.query(f.buffer(1e-9)):
                j = assigned[pos]
                ln = f.boundary.intersection(faces[j].boundary).length
                if ln > best_len:
                    best, best_len = owner[j], ln
            if best >= 0:
                owner[gi] = best
    members = {}
    for i, o in enumerate(owner):
        if o >= 0:
            members.setdefault(int(o), []).append(faces[i])
    changed = 0
    for k, r in enumerate(recs):
        if k in members:
            r["geom"] = polys_only(shapely.union_all(members[k]))
            changed += 1
    filled = int(sum(1 for gi in gap if owner[gi] >= 0))
    log.append("conforming topology: %d faces, %d hairline overlaps resolved, %d gap slivers filled, "
               "%d real holes kept (enclaves/inlets)" % (len(faces), n_overlap, filled, big_gaps))
    return log


def carve_overlaps(recs):
    """Where two districts overlap, the smaller one keeps the overlap."""
    geoms = np.array([r["geom"] for r in recs], dtype=object)
    tree = STRtree(geoms)
    l, r_ = tree.query(geoms, predicate="intersects")
    m = l < r_
    msgs = []
    for i, j in zip(l[m], r_[m]):
        ov = geoms[i].intersection(geoms[j])
        if ov.is_empty or ov.area * 111.0 ** 2 * np.cos(np.radians(23)) < 0.01:
            continue
        if km2(ov) < 0.01:
            continue
        ai, aj = km2(geoms[i]), km2(geoms[j])
        big, small = (i, j) if ai >= aj else (j, i)
        geoms[big] = polys_only(geoms[big].difference(geoms[small]))
        msgs.append("%s overlapped %s by %.1f km2 -> carved from the larger" %
                    (recs[i]["raw"], recs[j]["raw"], km2(ov)))
    for k, r in enumerate(recs):
        r["geom"] = geoms[k]
    return msgs


# --------------------------------------------------------------------------- geometry out
def rnd(v):
    return round(float(v), DEC)


def ring_pts(coords):
    out, last = [], None
    for x, y in coords:
        p = [rnd(x), rnd(y)]
        if p != last:
            out.append(p)
            last = p
    return out


def rings_of(geom):
    """All rings (exteriors, then interiors) as [[lng,lat],...] lists (>= 4 pts)."""
    rings = []
    polys = [geom] if geom.geom_type == "Polygon" else list(getattr(geom, "geoms", []))
    for p in polys:
        if p.is_empty or p.geom_type != "Polygon":
            continue
        for r in [p.exterior] + list(p.interiors):
            pts = ring_pts(r.coords)
            if len(pts) >= 4:
                rings.append(pts)
    return rings


def jdump(obj, path, indent=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"ensure_ascii": False}
    kw.update({"indent": indent} if indent else {"separators": (",", ":")})
    path.write_text(json.dumps(obj, **kw) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------- raster
def rasterise(districts):
    """districts: list of dicts with 'geom','state_id','id'.  Returns
    (state_grid[N_ROWS,N_COLS] int, per-district list of (row,col) nodes)."""
    lat_axis = LAT_S + STEP * np.arange(N_ROWS)
    lon_axis = LON_W + STEP * np.arange(N_COLS)
    grid = np.zeros((N_ROWS, N_COLS), dtype=np.int16)
    owner = {}
    for d in districts:
        minx, miny, maxx, maxy = d["geom"].bounds
        c0 = max(0, int(np.floor((minx - LON_W) / STEP)))
        c1 = min(N_COLS - 1, int(np.ceil((maxx - LON_W) / STEP)))
        r0 = max(0, int(np.floor((miny - LAT_S) / STEP)))
        r1 = min(N_ROWS - 1, int(np.ceil((maxy - LAT_S) / STEP)))
        rr, cc = np.meshgrid(np.arange(r0, r1 + 1), np.arange(c0, c1 + 1), indexing="ij")
        rr, cc = rr.ravel(), cc.ravel()
        inside = shapely.intersects_xy(d["geom"], lon_axis[cc], lat_axis[rr])
        cells = list(zip(rr[inside].tolist(), cc[inside].tolist()))
        if not cells:                       # tiny district: nearest node to an interior point
            pt = d["geom"].representative_point()
            cells = [(int(round((pt.y - LAT_S) / STEP)), int(round((pt.x - LON_W) / STEP)))]
        d["cells"] = cells
        for r, c in cells:
            if grid[r, c] == 0:             # first district wins a node on a shared edge
                grid[r, c] = d["state_id"]
                owner[(r, c)] = d["id"]
    return grid, owner


def add_fringe(grid, country):
    """Mark nodes that are outside every district but whose half-step cell touches
    India as 1.  This is the land-mask dilation the renderers rely on
    (idm_maps.py: land = value >= 1); stats code only counts values >= 2."""
    land = grid > 0
    pad = np.pad(land, 1)
    near = np.zeros_like(land)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            near |= pad[1 + dr:1 + dr + N_ROWS, 1 + dc:1 + dc + N_COLS]
    cand = np.argwhere(near & ~land)
    if len(cand) == 0:
        return 0
    h = STEP / 2.0
    lat = LAT_S + STEP * cand[:, 0]
    lon = LON_W + STEP * cand[:, 1]
    boxes = shapely.box(lon - h, lat - h, lon + h, lat + h)
    shapely.prepare(country)
    hit = shapely.intersects(boxes, country)
    for (r, c) in cand[hit]:
        grid[r, c] = 1
    return int(hit.sum())


# --------------------------------------------------------------------------- main build
def build(source, detail_files=True, disputed_mode="assign"):
    src = find_source(source)
    print("Source: %s" % src)
    base, log = load_records(src, disputed_mode)
    conform_coverage(base, log)
    for line in log:
        print("  fix:", line)
    print("  districts read: %d" % len(base))
    for r in base:
        r["name"] = clean_name(r["raw"], r["state_id"])
    odd = [(ID_TO_NAME[r["state_id"]], r["name"]) for r in base
           if re.search(r"[^A-Za-z0-9 .&()'\-]", r["name"])]
    if odd:
        sys.exit("Garbled district names (symbols in place of letters) - fix them in the shapefile "
                 "or add them to NAME_FIX:\n  " + "\n  ".join("%s / %s" % o for o in odd))

    # ---- topology-preserving simplification (shared borders stay shared) ----
    geoms = [r["geom"] for r in base]
    arr = np.array(geoms, dtype=object)
    nat = shapely.coverage_simplify(arr, TOL_NATIONAL)
    det = shapely.coverage_simplify(arr, TOL_DETAIL)

    # ---- district records ---------------------------------------------------
    recs = []
    for i, r in enumerate(base):
        recs.append({"state_id": r["state_id"], "name": r["name"], "lgd": r["lgd"],
                     "nat": nat[i], "geom": det[i]})
    by_state = {}
    for r in recs:
        by_state.setdefault(r["state_id"], []).append(r)
    for s, lst in by_state.items():            # deterministic ids: state*1000 + alphabetical rank
        lst.sort(key=lambda r: (r["name"], r["lgd"]))
        for n, r in enumerate(lst, 1):
            r["id"] = s * 1000 + n
    dup = {}
    for r in recs:
        dup.setdefault((r["state_id"], r["name"]), []).append(r)
    for (s, nm), lst in dup.items():
        if len(lst) > 1:
            print("  ! duplicate district name inside one state: %s / %s" % (ID_TO_NAME[s], nm))

    # ---- national-scale layers ---------------------------------------------
    ordered = sorted(recs, key=lambda r: r["id"])
    all_districts = [{"id": r["id"], "name": r["name"], "state": ID_TO_NAME[r["state_id"]],
                      "state_id": r["state_id"], "lgd": r["lgd"], "rings": rings_of(r["nat"])}
                     for r in ordered]
    jdump(all_districts, REPO / "assets" / "interactive" / "india_districts.json")

    state_polys = []
    for s in sorted(by_state):
        u = unary_union([r["nat"] for r in by_state[s]])
        parts = [u] if u.geom_type == "Polygon" else list(u.geoms)
        parts.sort(key=lambda p: -p.area)
        for p in parts:
            # exterior CCW, holes CW: the engine fills all rings in ONE canvas path
            # (nonzero rule), so opposite winding is what keeps a hole unfilled
            for ring in rings_of(orient(p, 1.0)):
                state_polys.append({"state_id": s, "name": ID_TO_NAME[s], "ut": ID_TO_UT[s],
                                    "coordinates": [{"lat": y, "lng": x} for x, y in ring]})
    jdump(state_polys, REPO / "state_vector_boundaries.json")

    country = unary_union([r["nat"] for r in recs])
    mainland = max(country.geoms, key=lambda p: p.area)
    with open(REPO / "india_mainland_boundary.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lat", "lng"])
        for x, y in ring_pts(mainland.exterior.coords):
            w.writerow([y, x])

    # ---- raster (state grid and district grids come from the SAME polygons) ---
    grid, owner = rasterise(recs)
    n_fringe = add_fringe(grid, unary_union([r["geom"] for r in recs]))
    with open(REPO / "states_with_boundaries.csv", "w", newline="") as fh:
        fh.write("lat,lng,value\n")
        for r in range(N_ROWS):
            lat = LAT_S + STEP * r
            fh.write("".join("%s,%s,%d\n" % (lat, LON_W + STEP * c, grid[r, c])
                             for c in range(N_COLS)))

    # ---- per-state district files + grids ------------------------------------
    manifest = {"week_ending": None,
                "source": "DISTRICT_BOUNDARY.shp (Survey-style district boundaries, 2026 edition)",
                "states": []}
    if detail_files:
        OUT_DISTRICTS.mkdir(parents=True, exist_ok=True)
        for old in list(OUT_DISTRICTS.glob("state_*.json")) + list(OUT_DISTRICTS.glob("grid_*.csv")):
            old.unlink()                                    # drop stale ids (e.g. old state_10)
        for s in sorted(by_state):
            lst = by_state[s]
            obj = {"state_id": s, "state": ID_TO_NAME[s],
                   "districts": [{"id": r["id"], "name": r["name"], "lgd": r["lgd"],
                                  "rings": rings_of(r["geom"])} for r in lst]}
            jdump(obj, OUT_DISTRICTS / ("state_%d.json" % s))
            with open(OUT_DISTRICTS / ("grid_%d.csv" % s), "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["lat", "lng", "district_id"])
                for r in lst:
                    for rr, cc in r["cells"]:
                        w.writerow([LAT_S + STEP * rr, LON_W + STEP * cc, r["id"]])
            manifest["states"].append({"state_id": s, "state": ID_TO_NAME[s],
                                       "file": "state_%d.json" % s, "grid": "grid_%d.csv" % s,
                                       "districts": [r["name"] for r in lst]})
        # keep the stats week if a previous build_districts run had set one
        old_idx = OUT_DISTRICTS / "index.json"
        try:
            manifest["week_ending"] = json.loads(old_idx.read_text(encoding="utf-8")).get("week_ending")
        except Exception:
            pass
        jdump(manifest, old_idx, indent=1)

    # ---- report ---------------------------------------------------------------
    nz = int((grid >= 2).sum())
    cells = {s: int((grid == s).sum()) for s in by_state}
    empty = [ID_TO_NAME[s] for s, n in cells.items() if n == 0]
    print("States/UTs: %d   Districts: %d   State nodes: %d   Fringe nodes: %d" % (len(by_state), len(recs), nz, n_fringe))
    print("  state polygons: %d rings   mainland outline: %d pts" %
          (len(state_polys), len(mainland.exterior.coords)))
    if empty:
        print("  ! states with no raster node:", empty)
    for f in ["state_vector_boundaries.json", "states_with_boundaries.csv", "india_mainland_boundary.csv",
              "assets/interactive/india_districts.json"]:
        print("  %-42s %6.2f MB" % (f, (REPO / f).stat().st_size / 1e6))
    if detail_files:
        tot = sum(p.stat().st_size for p in OUT_DISTRICTS.iterdir() if p.is_file())
        print("  data/districts/ (%d files)%s %6.2f MB" % (len(list(OUT_DISTRICTS.iterdir())), " " * 14, tot / 1e6))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build all IDM administrative boundary files.")
    ap.add_argument("--source", default=None, help="DISTRICT_BOUNDARY.shp or the .zip containing it "
                    "(default: data/boundaries/_source/DISTRICT_BOUNDARY.shp)")
    ap.add_argument("--disputed", choices=["assign", "exclude"], default="assign",
                    help="28 inter-state disputed slivers: merge into a claimant district (default) or leave out")
    ap.add_argument("--no-district-files", action="store_true", help="skip data/districts/*")
    a = ap.parse_args()
    build(a.source, detail_files=not a.no_district_files, disputed_mode=a.disputed)
