#!/usr/bin/env python3
# =============================================================================
# idm_maps.py  --  shared helpers for the IDM summary / forecast PDF generators
# -----------------------------------------------------------------------------
# Used by generate_summary_pdfs.py (CDI) and generate_forecast_pdfs.py (forecast).
# Provides:
#   - the map grid geometry (same lattice the interactive site uses)
#   - STANDARD bilinear/linear interpolation of a grid to a fine raster
#     (NOT a re-implementation of the engine's per-pixel algorithm -- just the
#      same DATA and the same COLORMAPS, interpolated the normal way)
#   - the exact WCL colormaps (CDI + forecast magnitudes), transcribed from
#     assets/interactive/drought-map.colormaps.js
#   - India land mask + state-id grid + compass REGION grouping
#   - a single render_param_map() that draws any parameter grid to a PNG
#   - LaTeX escaping / Latin-run wrapping (same conventions as hydro_pdf.py)
# =============================================================================

import json
import re
from pathlib import Path

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection

try:
    from scipy.interpolate import griddata
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False


# ----------------------------------------------------------------------------- geometry
LON_W, LON_E = 68.0, 97.5
LAT_N, LAT_S = 37.0, 7.0
DATA_STEP = 0.25
STATE_STEP = 0.0625


# ----------------------------------------------------------------------------- colormaps
# Each colormap: list of (upTo, "#RRGGBB"); a value v takes the FIRST band whose
# upTo >= v, else `above`. Transcribed verbatim from drought-map.colormaps.js.
CDI = {
    "label": "CDI / Drought",
    "bands": [(-2.0, "#A52A2A"), (-1.6, "#FF0000"), (-1.3, "#FFA500"),
              (-0.8, "#FCD394"), (-0.5, "#FFFF00")],
    "above": "#FFFFFF",
}
PRECIP = {
    "label": "Rainfall (mm)",
    "bands": [(1, "#FFFFFF"), (5, "#DEEBF7"), (10, "#C6DBEF"), (25, "#9ECAE1"),
              (50, "#6BAED6"), (75, "#4292C6"), (100, "#2171B5"), (150, "#08519C"),
              (200, "#08306B")],
    "above": "#08306B",
}
RUNOFF = {
    "label": "Runoff (mm)",
    "bands": [(0.5, "#FFFFFF"), (1, "#E0F3DB"), (2, "#CCEBC5"), (5, "#A8DDB5"),
              (10, "#7BCCC4"), (20, "#4EB3D3"), (40, "#2B8CBE"), (80, "#0868AC")],
    "above": "#084081",
}
SOILMOIST = {
    "label": "Soil Moisture (v/v)",
    "bands": [(0.05, "#8C510A"), (0.10, "#BF812D"), (0.15, "#DFC27D"), (0.20, "#F6E8C3"),
              (0.25, "#C7EAE5"), (0.30, "#80CDC1"), (0.40, "#35978F"), (0.50, "#01665E")],
    "above": "#003C30",
}


def _hex_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def band_rgb(values, cmap):
    """Vectorised colormap lookup -> (H, W, 3) uint8. NaN -> above colour (masked later)."""
    out = np.empty(values.shape + (3,), dtype=np.uint8)
    out[...] = _hex_rgb(cmap["above"])
    for upTo, hexc in reversed(cmap["bands"]):
        out[values <= upTo] = _hex_rgb(hexc)
    out[np.isnan(values)] = _hex_rgb(cmap["above"])
    return out


# ----------------------------------------------------------------------------- grid io
def read_grid(path):
    """Read a 'lat lon value' grid onto the fixed 0.25 deg lattice (origin 37.0 N, 68.0 E).
    Missing / NaN cells stay NaN. Returns (Z, rows, cols)."""
    lats, lons, vals = [], [], []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            p = line.split()
            if len(p) < 3:
                continue
            try:
                la, lo, v = float(p[0]), float(p[1]), float(p[2])
            except ValueError:
                continue
            lats.append(la); lons.append(lo); vals.append(v)
    lats = np.array(lats); lons = np.array(lons); vals = np.array(vals)
    finite = np.isfinite(lats) & np.isfinite(lons)
    lats, lons, vals = lats[finite], lons[finite], vals[finite]
    r = np.rint((LAT_N - lats) / DATA_STEP).astype(int)
    c = np.rint((lons - LON_W) / DATA_STEP).astype(int)
    rows = int(max(r.max() + 1, round((LAT_N - LAT_S) / DATA_STEP)))
    cols = int(max(c.max() + 1, round((LON_E - LON_W) / DATA_STEP)))
    Z = np.full((rows, cols), np.nan, dtype=float)
    ok = (r >= 0) & (r < rows) & (c >= 0) & (c < cols)
    Z[r[ok], c[ok]] = vals[ok]
    return Z, rows, cols


_STATE_GRID = {}


def load_state_grid(repo):
    """states_with_boundaries.csv -> int state-id grid at 0.0625 deg. value>=1 == land."""
    key = str(repo)
    if key in _STATE_GRID:
        return _STATE_GRID[key]
    arr = np.loadtxt(Path(repo) / "states_with_boundaries.csv", delimiter=",", skiprows=1)
    la, lo, val = arr[:, 0], arr[:, 1], arr[:, 2].astype(int)
    sR = np.rint((LAT_N - la) / STATE_STEP).astype(int)
    sC = np.rint((lo - LON_W) / STATE_STEP).astype(int)
    rows, cols = int(sR.max() + 1), int(sC.max() + 1)
    Sg = np.zeros((rows, cols), dtype=np.int16)
    ok = (sR >= 0) & (sR < rows) & (sC >= 0) & (sC < cols)
    Sg[sR[ok], sC[ok]] = val[ok]
    _STATE_GRID[key] = Sg
    return Sg


# ----------------------------------------------------------------------------- regions
# state_id -> compass region (ids from state_vector_boundaries.json). id 1 = generic land.
STATE_REGION = {
    29: "Northwest", 12: "Northwest", 28: "Northwest", 13: "Northwest", 7: "Northwest", 26: "Northwest",
    15: "North", 14: "North", 35: "North", 34: "North",
    5: "Northeast", 4: "Northeast", 22: "Northeast", 23: "Northeast", 24: "Northeast",
    25: "Northeast", 33: "Northeast", 30: "Northeast",
    6: "East", 16: "East", 36: "East", 37: "East",
    20: "Central", 8: "Central",
    21: "West", 11: "West", 9: "West", 10: "West",
    3: "South", 17: "South", 18: "South", 31: "South", 32: "South", 27: "South", 2: "South", 19: "South",
}
REGION_ORDER = ["North", "Northwest", "Northeast", "East", "Central", "West", "South"]


_REGION_GRID = {}


def region_id_grid(repo, rows, cols):
    """For the 0.25 deg CDI lattice, return an (rows, cols) array of region indices
    (index into REGION_ORDER) or -1 for ocean / unassigned. Memoised by shape."""
    ck = (str(repo), rows, cols)
    if ck in _REGION_GRID:
        return _REGION_GRID[ck]
    Sg = load_state_grid(repo)
    rid = np.full((rows, cols), -1, dtype=np.int8)
    region_index = {name: i for i, name in enumerate(REGION_ORDER)}
    for r in range(rows):
        lat = LAT_N - r * DATA_STEP
        sR = int(round((LAT_N - lat) / STATE_STEP))
        if sR < 0 or sR >= Sg.shape[0]:
            continue
        for c in range(cols):
            lon = LON_W + c * DATA_STEP
            sC = int(round((lon - LON_W) / STATE_STEP))
            if sC < 0 or sC >= Sg.shape[1]:
                continue
            reg = STATE_REGION.get(int(Sg[sR, sC]))
            if reg is not None:
                rid[r, c] = region_index[reg]
    _REGION_GRID[ck] = rid
    return rid


# ----------------------------------------------------------------------------- interpolation + render
def interp_fine(Z, fine_step=0.05):
    """STANDARD interpolation: linearly interpolate the valid grid values onto a fine
    lat/lon mesh (scipy griddata, method='linear'). Returns (Zf, fine_lat, fine_lon).
    Areas outside the data hull stay NaN (masked out by the land mask at draw time)."""
    rows, cols = Z.shape
    lat_nodes = LAT_N - np.arange(rows) * DATA_STEP
    lon_nodes = LON_W + np.arange(cols) * DATA_STEP
    LonN, LatN = np.meshgrid(lon_nodes, lat_nodes)
    valid = ~np.isnan(Z)

    fine_lat = np.arange(lat_nodes.max(), lat_nodes.min() - 1e-9, -fine_step)
    fine_lon = np.arange(lon_nodes.min(), lon_nodes.max() + 1e-9, fine_step)
    FLon, FLat = np.meshgrid(fine_lon, fine_lat)

    if _HAVE_SCIPY and valid.sum() >= 4:
        pts = np.column_stack([LonN[valid], LatN[valid]])
        Zf = griddata(pts, Z[valid], (FLon, FLat), method="linear")
    else:
        # Fallback: nearest block upsample (no scipy).
        fy = max(1, int(round(DATA_STEP / fine_step)))
        Zf = np.kron(np.nan_to_num(Z, nan=np.nan), np.ones((fy, fy)))[:FLat.shape[0], :FLat.shape[1]]
    return Zf, fine_lat, fine_lon


def render_param_map(repo, grid_path, cmap, out_png, fine_step=0.05, width_px=1500, log=print):
    """Render any parameter grid to a PNG matching the site: same data, exact colormap,
    standard interpolation, India land-mask, black state + mainland boundaries."""
    repo = Path(repo)
    Z, rows, cols = read_grid(grid_path)
    Zf, fine_lat, fine_lon = interp_fine(Z, fine_step=fine_step)

    # India land mask at fine resolution from the 0.0625 deg state grid.
    Sg = load_state_grid(repo)
    sR = np.clip(np.rint((LAT_N - fine_lat) / STATE_STEP).astype(int), 0, Sg.shape[0] - 1)
    sC = np.clip(np.rint((fine_lon - LON_W) / STATE_STEP).astype(int), 0, Sg.shape[1] - 1)
    land = Sg[np.ix_(sR, sC)] >= 1

    rgb = band_rgb(Zf, cmap)
    alpha = np.where(land & ~np.isnan(Zf), 255, 0).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])

    aspect = (LON_E - LON_W) / (LAT_N - LAT_S)
    fig_w = 7.4
    fig = plt.figure(figsize=(fig_w, fig_w / aspect), dpi=width_px / fig_w)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(LON_W, LON_E); ax.set_ylim(LAT_S, LAT_N)
    ax.set_aspect("equal"); ax.axis("off")

    hs = fine_step / 2.0
    ax.imshow(rgba, extent=[fine_lon[0] - hs, fine_lon[-1] + hs, fine_lat[-1] - hs, fine_lat[0] + hs],
              origin="upper", interpolation="nearest", zorder=1)

    try:
        sv = json.loads((repo / "state_vector_boundaries.json").read_text(encoding="utf-8"))
        segs = []
        for item in sv:
            pts = item.get("coordinates", item) if isinstance(item, dict) else item
            seg = [(p["lng"], p["lat"]) for p in pts if "lng" in p and "lat" in p]
            if len(seg) > 1:
                segs.append(seg)
        if segs:
            ax.add_collection(LineCollection(segs, colors="#000000", linewidths=0.45, zorder=2))
    except Exception as e:
        log("  ! state vectors not drawn (%s)" % e)
    try:
        ml = np.loadtxt(repo / "india_mainland_boundary.csv", delimiter=",", skiprows=1)
        ax.plot(ml[:, 1], ml[:, 0], color="#000000", linewidth=1.3, zorder=3,
                solid_capstyle="round", solid_joinstyle="round")
    except Exception as e:
        log("  ! mainland outline not drawn (%s)" % e)

    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=fig.dpi, facecolor="white", bbox_inches="tight", pad_inches=0.04)
    plt.close(fig)
    return out_png


# ----------------------------------------------------------------------------- latex helpers
def latex_escape(s):
    if s is None:
        return ""
    repl = [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
            ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
            ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")]
    for a, b in repl:
        s = s.replace(a, b)
    return s


_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,:/@()\-]*[A-Za-z0-9)]|[A-Za-z0-9]")


def wrap_latin(escaped):
    out, last = [], 0
    for m in _LATIN_RUN.finditer(escaped):
        run = m.group(0)
        if not re.search(r"[A-Za-z0-9]", run):
            continue
        out.append(escaped[last:m.start()]); out.append(r"{\latinfont " + run + "}"); last = m.end()
    out.append(escaped[last:])
    return "".join(out)


def make_escaper(pdf_font):
    is_latin = (pdf_font or "Carlito").strip().lower() == "carlito"

    def esc(s):
        e = latex_escape(s)
        return e if is_latin else wrap_latin(e)
    return esc


def tex_paragraphs(body, esc):
    """Turn a plain-text body (blank-line paragraphs, single newlines = line breaks)
    into LaTeX with \\par between paragraphs and \\\\ between lines."""
    out_paras = []
    for para in re.split(r"\n\s*\n", body.strip()):
        lines = [esc(ln.strip()) for ln in para.splitlines() if ln.strip()]
        out_paras.append(r" \\ ".join(lines))
    return r" \par\medskip ".join(out_paras)


def load_languages(repo, wanted):
    data = json.loads((Path(repo) / "Texts" / "languages.json").read_text(encoding="utf-8"))
    langs = data.get("languages", [])
    if wanted == ["all"]:
        return langs
    by_key = {l["key"]: l for l in langs}
    chosen = []
    for w in wanted:
        if w in by_key:
            chosen.append(by_key[w])
        else:
            print("  ! unknown language '%s' -- skipping" % w)
    return chosen
