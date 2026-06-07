#!/usr/bin/env python3
"""
generate_hydro_outputs.py
=========================
Standalone generator for the India Drought Monitor's *generated* assets.

It is a local, dependency-light port of `IHO_Pipeline_Final.ipynb` (the Colab
notebook). It produces, into the exact locations the website reads from:

  Hydrologic_Outlook/Output/All_Maps/<Parameter>/<Panel>.png   (individual maps)
  Hydrologic_Outlook/Output/Dashboards/<Parameter>_dashboard.png (9-panel composites)
  Hydrologic_Outlook/Output/PDF_Archive/Hydrolook_<YYYY_MM_DD>.pdf (monthly report)
  data/summaries/summary_<YYYY-MM-DD>.txt + index.json            (weekly AI summaries)
  data/summary_latest.txt                                          (latest summary)

The map/dashboard rendering (colormaps, the three panel renderers, the legend, and
the dashboard layout) is ported VERBATIM from the notebook so fidelity matches the
published indiahydrolook.in dashboards.

Differences from the Colab notebook, by necessity (all degrade gracefully):
  * No Google Drive — everything is local, relative to --repo (default: this dir).
  * Boundaries: uses the repo's own state_vector_boundaries.json to draw outlines
    if geopandas + an admin-boundary shapefile aren't available. (The notebook used
    GADM/DataMeet via geopandas; if you have geopandas + a boundary file, pass it
    with --boundaries to get district/state lines identical to the notebook.)
  * Summaries: generated via your LAN Ollama server (same endpoint/model the website
    uses) — NOT HuggingFace Transformers. Non-thinking mode.
  * PDF: assembled with matplotlib (one page per dashboard + a summary page), instead
    of the notebook's XeLaTeX template (which needs TeX Live + the HydroQA package).
    Set --pdf-engine latex with a hydrolook.tex template if you want the LaTeX path.

USAGE
-----
  # generate everything for the latest date found in the Input folder:
  python generate_hydro_outputs.py

  # a specific date, custom repo location, and a non-default Ollama host:
  python generate_hydro_outputs.py --date 2026_02_28 \
      --repo /path/to/IDM --api http://10.0.60.193:11434/api/generate --model qwen3.5:4b

  # skip the LLM (use a deterministic template summary) if the model is unreachable:
  python generate_hydro_outputs.py --no-llm

  # only regenerate certain stages:
  python generate_hydro_outputs.py --only maps,dashboards
  python generate_hydro_outputs.py --only summaries

Requirements: numpy, pandas, matplotlib, scipy  (pip install numpy pandas matplotlib scipy)
              requests is only needed if generating summaries with the LLM.
              geopandas + shapely are optional (nicer boundaries).
"""

from __future__ import annotations  # allow modern type hints on Python 3.7-3.9

import argparse
import json
import os
import re
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.colors import ListedColormap, BoundaryNorm

plt.rcParams["font.family"] = "DejaVu Sans"

# Optional geo stack (nicer boundaries). The script works without it.
try:
    import geopandas as gpd
    from shapely import vectorized as shp_vec
    from shapely.geometry import shape as shapely_shape
    from shapely.ops import unary_union
    HAVE_GEO = True
except Exception:
    HAVE_GEO = False

from scipy.interpolate import griddata


# ===========================================================================
# Column layout + per-parameter configuration (VERBATIM from the notebook)
# ===========================================================================
COLUMN_KEYS = ["c1", "c2", "current", "forecast", "prev1", "prev2", "prev3", "prev4",
               "last_year", "lowest", "highest"]
PLOT_COLUMNS = COLUMN_KEYS[2:]

# --- colormaps & bins, verbatim ---
P_BOUNDS = list(range(0, 101, 5))
P_COLORS = ['#9E0142','#B01546','#CE374D','#E2524A','#F36C43','#F98F53','#FDB264','#FECD7B',
            '#FEE6A4','#FFF7B1','#F8FCB4','#EAF79F','#D0EC9C','#AFDDA3','#89D1A4','#65C1A5',
            '#48A1B3','#3881BA','#505CAA','#5E4FA2']
P_CATEGORY_LABELS = ['Exceptional low','Extreme low','Very low','Low','Below average',
                     'Above average','High','Very high','Extreme high','Exceptional high']

T_BOUNDS = [-1e9,-4.5,-4,-3.5,-3,-2.5,-2,-1.5,-1,-0.5,0,0.5,1,1.5,2,2.5,3,3.5,4,4.5,1e9]
T_COLORS = ['#000000','#0A3278','#0F4BA5','#1E6EC8','#3CA0F0','#50B4FA','#82D2FF','#A0F0FF',
            '#C8FAFF','#E6FFFF','#FFFADC','#FFE878','#FFC03C','#FFA000','#FF6000','#FF3200',
            '#E11400','#C00000','#A50000','#800026']
T_CATEGORY_LABELS = ['Exceptional cold','Extreme cold','Very cold','Medium cold','Less cold',
                     'Low warm','Average warm','Very warm','Extreme warm','Exceptional warm']

SM_BOUNDS = [-100,-80,-60,-40,-20,0,20,40,60,80,100]
SM_COLORS = ['#990000','#FF6666','#FFA31A','#FFDB4D','#FFF3E6','#E6F2FF','#99CCFF','#00B8E6',
             '#0086B3','#005266']
SM_CATEGORY_LABELS = ['Very dry','Medium dryness','Low dryness','Low wetness','Medium wetness','Very wet']

RO_BOUNDS = [-1e9,-45,-40,-35,-30,-25,-20,-15,-10,-5,0,5,10,15,20,25,30,35,40,45,1e9]
RO_COLORS = ['#660000','#A50000','#C00000','#E11400','#FF3200','#FF6000','#FFA000','#FFC03C',
             '#FFE878','#FFFADC','#E6FFFF','#C8FAFF','#A0F0FF','#82D2FF','#50B4FA','#3CA0F0',
             '#1E6EC8','#0F4BA5','#0A3278','#000066']
RO_CATEGORY_LABELS = ['Extreme deficit','Very high deficit','High deficit','Medium deficit',
                      'Less deficit','Less surplus','Medium surplus','High surplus',
                      'Very high surplus','Extreme surplus']

ET_BOUNDS = [-1e9,-20,-17.5,-15,-12.5,-10,-7.5,-5,-2.5,0,2.5,5,7.5,10,12.5,15,17.5,20,1e9]
ET_COLORS = ['#A50000','#C00000','#FF1400','#FF3200','#FF6000','#FFA000','#FFC03C','#FFE878',
             '#FFFADC','#E6FFFF','#C8FAFF','#A0F0FF','#82D2FF','#50B4FA','#3CA0F0','#1E6EC8',
             '#0F4BA5','#0A3278']
ET_CATEGORY_LABELS = ['Extreme low','Very low','Low','Below average','Above average','High',
                      'Very high','Extreme high']

Q_BOUNDS = list(range(0, 101, 5))
Q_COLORS = list(P_COLORS)
Q_CATEGORY_LABELS = list(P_CATEGORY_LABELS)

SQ_BOUNDS = [0,2,5,10,20,30,70,80,90,95,98,100]
SQ_COLORS = ['#4D2600','#993300','#FF0000','#FF9900','#FFFF00','#FFFFFF','#BFFF00','#99CC00',
             '#33CC33','#009900','#3377FF']
SQ_CATEGORY_LABELS = ['Exceptional low','Extreme low','Very low','Low','Below average','Average',
                      'Above average','High','Very high','Extreme high','Exceptional high']

# Per-parameter table. Streamflow_Network / Streamflow_Stations are defined but
# EXCLUDED from website output by default (per the data notes "To Be Removed").
PARAMS = {
    'Rainfall': dict(file_prefix='P', kind='grid', bounds=P_BOUNDS, colors=P_COLORS,
        category_lbl=P_CATEGORY_LABELS, cb_title='Rainfall\nPercentile',
        description='Monthly rainfall expressed as a percentile of the\nhistorical record. Low percentiles (red) indicate\ndrier-than-normal conditions, high percentiles\n(blue) indicate wetter-than-normal conditions.',
        lowest_label='August (2002)', lowest_tag='Driest', lowest_color='#C40000',
        highest_label='December (1997)', highest_tag='Wettest', highest_color='#0033A0', tick_format='{:.0f}'),
    'Temperature': dict(file_prefix='T', kind='grid', bounds=T_BOUNDS, colors=T_COLORS,
        category_lbl=T_CATEGORY_LABELS, cb_title='Temperature\nAnomaly (°C)',
        description='Surface air temperature anomaly compared to the\nlong-term mean for the same month. Negative\nvalues (blue) indicate cooler-than-average\nconditions; positive values (red) indicate\nwarmer-than-average conditions.',
        lowest_label='May (2023)', lowest_tag='Coldest month', lowest_color='#0033A0',
        highest_label='April (2010)', highest_tag='Warmest month', highest_color='#C40000', tick_format='{:.1f}'),
    'Relative_Wetness': dict(file_prefix='sm', kind='grid', bounds=SM_BOUNDS, colors=SM_COLORS,
        category_lbl=SM_CATEGORY_LABELS, cb_title='Relative Wetness (%)',
        description='Soil-moisture anomaly (60 cm depth) as a % of\nmaximum (positive wetness) or minimum (negative\nwetness) soil moisture anomaly.',
        lowest_label='August (2002)', lowest_tag='Driest', lowest_color='#C40000',
        highest_label='February (2022)', highest_tag='Wettest', highest_color='#0033A0', tick_format='{:.0f}'),
    'Total_Runoff': dict(file_prefix='ro', kind='grid', bounds=RO_BOUNDS, colors=RO_COLORS,
        category_lbl=RO_CATEGORY_LABELS, cb_title='Total Runoff\nAnomaly (mm)',
        description='Total runoff anomaly compared to the long-term\nmonthly mean. Red (negative) = deficit, blue\n(positive) = surplus runoff.',
        lowest_label='August (2002)', lowest_tag='Lowest', lowest_color='#C40000',
        highest_label='October (2019)', highest_tag='Highest', highest_color='#0033A0', tick_format='{:.0f}'),
    'Evapotranspiration': dict(file_prefix='ET', kind='grid', bounds=ET_BOUNDS, colors=ET_COLORS,
        category_lbl=ET_CATEGORY_LABELS, cb_title='Evapotranspiration\nAnomaly (mm)',
        description='Evapotranspiration anomaly compared to the\nlong-term monthly mean. Red = below normal,\nblue = above normal.',
        lowest_label='July (2002)', lowest_tag='Lowest', lowest_color='#C40000',
        highest_label='June (2021)', highest_tag='Highest', highest_color='#0033A0', tick_format='{:.1f}'),
    'Streamflow_Network': dict(file_prefix='Q', kind='network', bounds=Q_BOUNDS, colors=Q_COLORS,
        category_lbl=Q_CATEGORY_LABELS, cb_title='Streamflow\nPercentile',
        description="Streamflow percentile across India's stream\nnetwork, plotted along river segments.",
        lowest_label='August (2002)', lowest_tag='Lowest flow', lowest_color='#C40000',
        highest_label='January (2022)', highest_tag='Highest flow', highest_color='#0033A0', tick_format='{:.0f}'),
    'Streamflow_Stations': dict(file_prefix='Station_Q', kind='station', bounds=SQ_BOUNDS, colors=SQ_COLORS,
        category_lbl=SQ_CATEGORY_LABELS, cb_title='Streamflow\nPercentile',
        description='Monthly streamflow percentile observed at gauge\nstations across India.',
        lowest_label='June (2003)', lowest_tag='Lowest flow', lowest_color='#C40000',
        highest_label='August (2020)', highest_tag='Highest flow', highest_color='#0033A0', tick_format='{:.0f}'),
}

# Website shows these five (Streamflow excluded by default).
WEBSITE_PARAMS = ['Rainfall', 'Temperature', 'Relative_Wetness', 'Total_Runoff', 'Evapotranspiration']

INDIA_BBOX = (66.0, 6.5, 98.5, 38.5)
FINE_RESOLUTION = 0.08

# These get filled in main() once paths/date are known.
LABELS = {}
STATE_GDF = None        # geopandas GeoDataFrame OR None
INDIA_UNION = None      # shapely geometry OR None


# ===========================================================================
# Boundaries
# ===========================================================================

# --- PDF archive policy ------------------------------------------------------
# PDF archives are kept ONLY for these languages (site text still covers every
# configured language); each archive series keeps at most the newest 4 PDFs,
# and a PDF that already exists for a given date is never rebuilt.
ARCHIVE_LANGS = ("English", "Hindi")   # languages that keep a multi-date PDF archive
PDF_ARCHIVE_KEEP = 4                    # ...this many newest per series, for those languages

def _archive_keep(key):
    # Every language gets the CURRENT pdf; non-archive languages keep only it.
    return PDF_ARCHIVE_KEEP if key in ARCHIVE_LANGS else 1

def prune_pdf_archive(archive_dir, keep=PDF_ARCHIVE_KEEP, log=print):
    """Keep only the newest `keep` PDFs per filename series in archive_dir
    (dates sort lexically in both YYYY-MM-DD and YYYY_MM_DD forms)."""
    from collections import defaultdict
    d = Path(archive_dir)
    if not d.is_dir():
        return
    groups = defaultdict(list)
    for p in d.glob("*.pdf"):
        m = re.match(r"(.*?)(\d{4}[-_]\d{2}[-_]\d{2})\.pdf$", p.name)
        if m:
            groups[m.group(1)].append((m.group(2), p))
    for _prefix, items in groups.items():
        for _date, p in sorted(items)[:-keep]:
            try:
                p.unlink()
                log("  archive prune: removed %s" % p.name)
            except OSError:
                pass


def load_boundaries(repo, boundaries_path):
    """Best-effort India boundary for clipping + outlines.
    Priority: explicit --boundaries shapefile (geopandas) > repo state_vector_boundaries.json.
    Sets globals STATE_GDF and INDIA_UNION."""
    global STATE_GDF, INDIA_UNION
    if HAVE_GEO and boundaries_path and os.path.exists(boundaries_path):
        STATE_GDF = gpd.read_file(boundaries_path).to_crs(4326)
        INDIA_UNION = unary_union(STATE_GDF.geometry.values)
        print(f"  boundaries: {boundaries_path} ({len(STATE_GDF)} features) via geopandas")
        return
    # Fallback: the repo's own state polygons. Two possible formats:
    #  (a) GeoJSON-like list of features with a 'geometry' dict, or
    #  (b) the IDM format: list of {state_id, name, coordinates:[{lat,lng}, ...]} rings.
    sj = repo / "state_vector_boundaries.json"
    if HAVE_GEO and sj.exists():
        try:
            from shapely.geometry import Polygon
            data = json.loads(sj.read_text())
            geoms = []
            for feat in data:
                # format (a)
                g = feat.get("geometry") if isinstance(feat, dict) else None
                if isinstance(g, dict) and "type" in g:
                    geoms.append(shapely_shape(g)); continue
                # format (b)
                coords = feat.get("coordinates") if isinstance(feat, dict) else None
                if isinstance(coords, list) and len(coords) >= 3:
                    ring = []
                    for pt in coords:
                        if isinstance(pt, dict) and "lat" in pt and "lng" in pt:
                            ring.append((pt["lng"], pt["lat"]))   # shapely wants (x=lon, y=lat)
                        elif isinstance(pt, (list, tuple)) and len(pt) == 2:
                            ring.append((pt[0], pt[1]))
                    if len(ring) >= 3:
                        try:
                            poly = Polygon(ring)
                            if poly.is_valid and poly.area > 0:
                                geoms.append(poly)
                        except Exception:
                            pass
            if geoms:
                STATE_GDF = gpd.GeoDataFrame(geometry=geoms, crs=4326)
                INDIA_UNION = unary_union(geoms)
                print(f"  boundaries: state_vector_boundaries.json ({len(geoms)} polygons)")
                return
        except Exception as e:
            print(f"  boundaries: could not parse state_vector_boundaries.json ({e})")
    print("  boundaries: none available — maps render without admin outlines and "
          "clip to the data's convex extent (install geopandas + pass --boundaries for outlines).")


def add_basemap(ax):
    if STATE_GDF is not None:
        try:
            STATE_GDF.boundary.plot(ax=ax, linewidth=0.6, color="black", zorder=6)
        except Exception:
            pass


# ===========================================================================
# Data loading (verbatim logic)
# ===========================================================================
def load_param(prefix, input_dir: Path, date_str: str):
    path = input_dir / f"{prefix}_{date_str}"
    if not path.exists():
        raise FileNotFoundError(f"Cannot find {path}")
    df = pd.read_csv(path, sep=r"\s+", header=None, names=COLUMN_KEYS, engine="python")
    # Auto-detect lat/lon order: India latitudes <= 38, longitudes >= 65.
    if df["c1"].max() > 50:
        df = df.rename(columns={"c1": "lon", "c2": "lat"})
    else:
        df = df.rename(columns={"c1": "lat", "c2": "lon"})
    return df


# ===========================================================================
# Renderers (verbatim from the notebook)
# ===========================================================================
def make_cmap_norm(colors, bounds):
    cmap = ListedColormap(colors)
    cmap.set_bad("white", alpha=0.0)
    norm = BoundaryNorm(bounds, cmap.N)
    return cmap, norm


def render_grid(ax, df, value_col, cmap, norm):
    lons = df["lon"].to_numpy(); lats = df["lat"].to_numpy(); vals = df[value_col].to_numpy()
    pad = 0.5
    fine_lons = np.arange(lons.min() - pad, lons.max() + pad + FINE_RESOLUTION, FINE_RESOLUTION)
    fine_lats = np.arange(lats.min() - pad, lats.max() + pad + FINE_RESOLUTION, FINE_RESOLUTION)
    LX, LY = np.meshgrid(fine_lons, fine_lats)
    pts = np.column_stack([lons, lats])
    fine_lin = griddata(pts, vals, (LX, LY), method="linear")
    fine_nn = griddata(pts, vals, (LX, LY), method="nearest")
    fine_grid = np.where(np.isnan(fine_lin), fine_nn, fine_lin)
    if INDIA_UNION is not None and HAVE_GEO:
        inside = shp_vec.contains(INDIA_UNION, LX, LY)
        fine_grid = np.where(inside, fine_grid, np.nan)
    masked = np.ma.array(fine_grid, mask=np.isnan(fine_grid))
    ax.pcolormesh(LX, LY, masked, cmap=cmap, norm=norm, shading="nearest", antialiased=False, zorder=1)
    add_basemap(ax)
    ax.set_xlim(INDIA_BBOX[0], INDIA_BBOX[2]); ax.set_ylim(INDIA_BBOX[1], INDIA_BBOX[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)


def render_network(ax, df, value_col, cmap, norm, marker_size=0.6):
    add_basemap(ax)
    ax.scatter(df["lon"], df["lat"], c=df[value_col], cmap=cmap, norm=norm,
               s=marker_size, marker="o", linewidths=0, zorder=2)
    ax.set_xlim(INDIA_BBOX[0], INDIA_BBOX[2]); ax.set_ylim(INDIA_BBOX[1], INDIA_BBOX[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)


def render_station(ax, df, value_col, cmap, norm, marker_size=20):
    add_basemap(ax)
    ax.scatter(df["lon"], df["lat"], c=df[value_col], cmap=cmap, norm=norm,
               s=marker_size, marker="o", edgecolors="black", linewidths=0.3, zorder=3)
    ax.set_xlim(INDIA_BBOX[0], INDIA_BBOX[2]); ax.set_ylim(INDIA_BBOX[1], INDIA_BBOX[3])
    ax.set_aspect("equal"); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)


def render_panel(ax, df, value_col, cfg):
    cmap, norm = make_cmap_norm(cfg["colors"], cfg["bounds"])
    if cfg["kind"] == "grid":      render_grid(ax, df, value_col, cmap, norm)
    elif cfg["kind"] == "network": render_network(ax, df, value_col, cmap, norm)
    elif cfg["kind"] == "station": render_station(ax, df, value_col, cmap, norm)
    else: raise ValueError(cfg["kind"])


def draw_legend(fig, ax_pos, cfg):
    L, B, W, H = ax_pos
    bounds, colors, cats, fmt = cfg["bounds"], cfg["colors"], cfg["category_lbl"], cfg["tick_format"]
    cb_h = 0.034; cb_w = W * 0.50; cb_l = L + W * 0.04; cb_b = B + H * 0.42
    cax = fig.add_axes([cb_l, cb_b, cb_w, cb_h])
    cmap, norm = make_cmap_norm(colors, bounds)
    cb = mpl.colorbar.ColorbarBase(cax, cmap=cmap, norm=norm, orientation="horizontal",
                                   spacing="uniform", ticks=[])
    cb.outline.set_linewidth(0.4)
    n = len(colors)
    for i, edge in enumerate(bounds):
        x = i / n
        if abs(edge) >= 1e8:
            label = f"< {fmt.format(bounds[1])}" if i == 0 else f"> {fmt.format(bounds[-2])}"
        elif i == 0 and bounds[0] == 0 and bounds[-1] == 100:
            label = "0"
        else:
            label = fmt.format(edge)
        cax.text(x, -0.7, label, transform=cax.transAxes, ha="center", va="top", fontsize=7.5, rotation=90)
    n_cats = len(cats)
    for k, txt in enumerate(cats):
        x = (k + 0.5) / n_cats
        cax.text(x, 1.6, txt, transform=cax.transAxes, ha="center", va="bottom",
                 fontsize=8.5, rotation=90, fontweight="bold")
    text_x = cb_l + cb_w + W * 0.05
    fig.text(text_x, cb_b + cb_h * 1.0, cfg["cb_title"], ha="left", va="top",
             fontsize=15, fontweight="bold", linespacing=1.1)
    fig.text(text_x, cb_b - cb_h * 0.6, cfg["description"], ha="left", va="top",
             fontsize=8.0, color="#222", style="italic", linespacing=1.4)


# ===========================================================================
# Individual maps + dashboards
# ===========================================================================
def panel_label_for(col_key, cfg):
    L = LABELS
    return {
        "current": f"{L['current']} (Current)", "forecast": f"{L['forecast']} (Forecast)",
        "prev1": L["prev1"], "prev2": L["prev2"], "prev3": L["prev3"], "prev4": L["prev4"],
        "last_year": L["last_year"], "lowest": cfg["lowest_label"], "highest": cfg["highest_label"],
    }.get(col_key, col_key)


def save_individual_map(df, value_col, cfg, out_dir, panel_label):
    fig = plt.figure(figsize=(10, 12), dpi=150, facecolor="white")
    ax = fig.add_axes([0.04, 0.30, 0.92, 0.66])
    render_panel(ax, df, value_col, cfg)
    ax.set_title(panel_label, fontsize=16, fontweight="bold", pad=10)
    draw_legend(fig, (0.04, 0.02, 0.92, 0.22), cfg)
    out = Path(out_dir) / f"{panel_label}.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def _box2axes(box):
    y1, x1, y2, x2 = box
    return (x1 / 1000, 1 - y2 / 1000, (x2 - x1) / 1000, (y2 - y1) / 1000)

LAYOUT_BOXES = {
    'current': [39,11,473,298], 'prev1': [49,319,296,485], 'prev2': [49,489,296,654],
    'prev3': [49,660,296,827], 'prev4': [49,831,296,997], 'legend': [334,224,532,814],
    'forecast': [538,11,971,289], 'last_year': [640,295,971,510], 'lowest': [640,517,971,735],
    'highest': [640,765,971,983],
}
LAYOUT = {k: _box2axes(v) for k, v in LAYOUT_BOXES.items()}


def build_dashboard(pretty_name, cfg, out_path, input_dir, date_str):
    fig = plt.figure(figsize=(22, 15.4), dpi=140, facecolor="white")
    df = load_param(cfg["file_prefix"], input_dir, date_str)
    panels = [
        ('current','current', panel_label_for('current',cfg),'bold',None,None),
        ('prev1','prev1', panel_label_for('prev1',cfg),'bold',None,None),
        ('prev2','prev2', panel_label_for('prev2',cfg),'bold',None,None),
        ('prev3','prev3', panel_label_for('prev3',cfg),'bold',None,None),
        ('prev4','prev4', panel_label_for('prev4',cfg),'bold',None,None),
        ('forecast','forecast', panel_label_for('forecast',cfg),'bold',None,None),
        ('last_year','last_year', panel_label_for('last_year',cfg),'bold',None,None),
        ('lowest','lowest', cfg['lowest_label'],'bold', cfg['lowest_tag'], cfg['lowest_color']),
        ('highest','highest', cfg['highest_label'],'bold', cfg['highest_tag'], cfg['highest_color']),
    ]
    for col_key, slot, label, weight, tag, tag_color in panels:
        L, B, W, H = LAYOUT[slot]
        ax = fig.add_axes([L, B, W, H])
        render_panel(ax, df, col_key, cfg)
        ax.text(0.5, 1.02, label, transform=ax.transAxes, ha="center", va="bottom",
                fontsize=15 if slot in ("current", "forecast") else 12, fontweight=weight)
        if tag:
            ax.text(0.97, 0.97, tag, transform=ax.transAxes, ha="right", va="top",
                    fontsize=11, fontweight="bold", color=tag_color,
                    bbox=dict(boxstyle="round,pad=0.25", facecolor="white", edgecolor=tag_color, linewidth=1.2))
            for s in ax.spines.values(): s.set_visible(False)
            ax.add_patch(mpatches.Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                         fill=False, lw=2.5, edgecolor=tag_color, clip_on=False))
    draw_legend(fig, LAYOUT["legend"], cfg)
    fig.savefig(out_path, dpi=140, facecolor="white")
    plt.close(fig)


def generate_dashboards_for_pdf(input_dir, dest_dir, date_str, log=print):
    """The PDF report needs the five grid dashboards (the two streamflow products /
    pages 7 & 8 have been removed). Render them into dest_dir."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    pdf_params = ['Rainfall', 'Temperature', 'Relative_Wetness', 'Total_Runoff', 'Evapotranspiration']
    for pretty_name in pdf_params:
        cfg = PARAMS[pretty_name]
        out = dest_dir / f"{pretty_name}_dashboard.png"
        # Always re-render: the filename carries no date, so an exists-skip would
        # freeze the first month's dashboards into every later month's PDF.
        try:
            build_dashboard(pretty_name, cfg, out, input_dir, date_str)
            log(f"    dashboard: {pretty_name}")
        except FileNotFoundError as e:
            log(f"    ! cannot build {pretty_name} dashboard: {e}")


def generate_maps_and_dashboards(input_dir, out_base, date_str, params_to_do):
    all_maps_dir = out_base / "All_Maps"
    dash_dir = out_base / "Dashboards"
    for pretty_name in params_to_do:
        cfg = PARAMS[pretty_name]
        pdir = all_maps_dir / pretty_name
        if pdir.exists(): shutil.rmtree(pdir)
        pdir.mkdir(parents=True, exist_ok=True)
        try:
            df = load_param(cfg["file_prefix"], input_dir, date_str)
        except FileNotFoundError as e:
            print(f"  ! skip {pretty_name}: {e}")
            continue
        print(f"  {pretty_name}: maps", end="", flush=True)
        for col_key in PLOT_COLUMNS:
            save_individual_map(df, col_key, cfg, pdir, panel_label_for(col_key, cfg))
        print(" + dashboard", flush=True)
        dash_dir.mkdir(parents=True, exist_ok=True)
        build_dashboard(pretty_name, cfg, dash_dir / f"{pretty_name}_dashboard.png", input_dir, date_str)


# ===========================================================================
# National drought summary (weekly) — via the LAN Ollama endpoint
# ===========================================================================
def classify_cdi(v):
    # Absolute CDI thresholds (same as the website engine + legend).
    if v > -0.5: return "None"
    if v > -0.8: return "D0"
    if v > -1.3: return "D1"
    if v > -1.6: return "D2"
    if v > -2.0: return "D3"
    return "D4"


def build_summary_context(repo: Path):
    """Compact numeric context from the national time series + per-state grid."""
    ts_path = repo / "data" / "India_Drought_Area_Timeseries.txt"
    rows = []
    for line in ts_path.read_text().strip().split("\n"):
        p = line.split()
        if len(p) < 9: continue
        y, m, d = int(p[0]), int(p[1]), int(p[2])
        rows.append(dict(date=f"{y:04d}-{m:02d}-{d:02d}", year=y, month=m, day=d,
                         normal=float(p[3]), d0=float(p[4]), d1=float(p[5]),
                         d2=float(p[6]), d3=float(p[7]), d4=float(p[8])))
    cur = rows[-1]; prev = rows[-2] if len(rows) > 1 else cur; mago = rows[-5] if len(rows) > 4 else rows[0]
    delta = cur["d0"] - prev["d0"]
    trend = "expanded" if delta > 0.3 else "contracted" if delta < -0.3 else "held roughly steady"

    # per-state (best effort; needs the CDI grid + state grid)
    worst, best = [], []
    try:
        from collections import defaultdict
        compact = cur["date"].replace("-", "")
        cdi_path = repo / "data" / "Drough_TS" / f"CDI_{compact}.txt"
        if not cdi_path.exists():
            cdi_path = repo / "data" / "Current_CDI.txt"
        cdi = {}
        for line in cdi_path.read_text().strip().split("\n"):
            pp = line.split()
            if len(pp) < 3: continue
            try: cdi[(round(float(pp[0]), 3), round(float(pp[1]), 3))] = float(pp[2])
            except ValueError: continue
        names = {int(p["state_id"]): p["name"] for p in json.loads((repo / "state_vector_boundaries.json").read_text())}
        sg = pd.read_csv(repo / "states_with_boundaries.csv")
        sg = sg[sg["value"].astype(int) >= 2]
        deltas = [0, 0.0625, -0.0625, 0.125, -0.125]
        def nearest(lat, lng):
            for a in deltas:
                for b in deltas:
                    v = cdi.get((round(lat + a, 3), round(lng + b, 3)))
                    if v is not None: return v
            return None
        tally = defaultdict(lambda: defaultdict(int))
        for r in sg.itertuples():
            v = nearest(float(r.lat), float(r.lng))
            if v is None: continue
            tally[int(r.value)][classify_cdi(v)] += 1
            tally[int(r.value)]["tot"] += 1
        st = []
        for sid, c in tally.items():
            if c["tot"] < 8: continue
            none = 100 * c["None"] / c["tot"]
            st.append((names.get(sid, str(sid)), round(100 - none, 1)))
        st.sort(key=lambda x: -x[1])
        worst = st[:6]; best = st[-4:][::-1]
    except Exception as e:
        print(f"    (per-state context unavailable: {e})")

    def pct(x): return f"{x:.1f}%"
    lines = [f"Week ending: {cur['date']}",
             "National area by drought class (cumulative, % of India):",
             f"  Normal: {pct(cur['normal'])}; D0+: {pct(cur['d0'])}; D1+: {pct(cur['d1'])}; "
             f"D2+: {pct(cur['d2'])}; D3+: {pct(cur['d3'])}; D4: {pct(cur['d4'])}",
             f"Total drought area (D0+): this week {pct(cur['d0'])}, last week {pct(prev['d0'])}, "
             f"~1 month ago {pct(mago['d0'])} => drought {trend} ({delta:+.1f} pts week-on-week)."]
    if worst:
        lines.append("Most-affected states (by % area in any drought):")
        for s, d in worst: lines.append(f"  {s}: {pct(d)} in drought")
    if best:
        lines.append("Least-affected states: " + ", ".join(f"{s} ({pct(d)})" for s, d in best))
    return "\n".join(lines), cur["date"]


SUMMARY_SYSTEM = (
    "You are a hydroclimatology analyst writing the weekly national summary for the India Drought "
    "Monitor (IDM), produced by the Water and Climate Lab, IIT Gandhinagar. The IDM uses a Combined "
    "Drought Index (CDI) with six classes: Normal, D0 (Abnormally Dry), D1 (Moderate), D2 (Severe), "
    "D3 (Extreme), D4 (Exceptional). Write clear, factual, neutral prose for a general audience. "
    "CRITICAL: use ONLY the numbers provided. Do NOT invent figures, place names, dates, or trends "
    "not supported by the data.")


def llm_summary(context, api_url, model):
    import requests
    prompt = ("Here is this week's India Drought Monitor data:\n\n" + context + "\n\n"
              "Note: the national class areas are CUMULATIVE (e.g. 'D2 or worse' already includes D3 and D4).\n\n"
              "Write a concise national drought summary of about 150-180 words in plain paragraphs "
              "(no headings, no bullet points, no markdown), covering: (1) a one-sentence overview of "
              "national conditions this week, (2) the week-on-week trend, and (3) which regions/states "
              "are most affected and any notably better.")
    body = {"model": model, "prompt": prompt, "system": SUMMARY_SYSTEM, "stream": False,
            "think": False, "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": 600, "num_ctx": 8192}}
    r = requests.post(api_url, json=body, timeout=300)
    r.raise_for_status()
    txt = r.json().get("response", "").strip()
    if "</think>" in txt: txt = txt.split("</think>")[-1].strip()
    return txt


def template_summary(context):
    """Deterministic fallback summary (no LLM) built from the numeric context."""
    cur = {}
    for ln in context.split("\n"):
        if ln.startswith("  Normal:"):
            for part in ln.split(";"):
                if ":" in part:
                    k, v = part.split(":"); cur[k.strip()] = v.strip()
    normal = cur.get("Normal", "n/a"); d0 = cur.get("D0+", "n/a"); d2 = cur.get("D2+", "n/a")
    trend = "expanded" if "expanded" in context else "contracted" if "contracted" in context else "held roughly steady"
    return (f"About {d0} of India's area is in some level of drought (D0 or worse), with {normal} at "
            f"normal conditions. Severe-to-extreme drought (D2 or worse) covers roughly {d2} of the "
            f"country. Compared with the previous week, the drought-affected area {trend}. Conditions "
            f"reflect the balance of pre-monsoon rainfall, soil moisture and runoff captured in the "
            f"Combined Drought Index, with the most persistent stress across rain-fed parts of "
            f"peninsular and central India.")


def generate_summary(repo: Path, api_url, model, use_llm):
    context, week = build_summary_context(repo)
    if use_llm:
        try:
            body = llm_summary(context, api_url, model)
            print(f"  summary: generated via LLM ({model})")
        except Exception as e:
            print(f"  summary: LLM unreachable ({e}); using template fallback")
            body = template_summary(context)
    else:
        body = template_summary(context)
        print("  summary: template (LLM disabled)")

    header = f"# India Drought Monitor — National Summary for week ending {week}\n"
    arch_dir = repo / "data" / "summaries"
    arch_dir.mkdir(parents=True, exist_ok=True)
    (arch_dir / f"summary_{week}.txt").write_text(header + "\n" + body + "\n")
    (repo / "data" / "summary_latest.txt").write_text(header + "\n" + body + "\n")

    months = {1:"January",2:"February",3:"March",4:"April",5:"May",6:"June",7:"July",
              8:"August",9:"September",10:"October",11:"November",12:"December"}
    items = []
    for fn in os.listdir(arch_dir):
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", fn)
        if fn.endswith(".txt") and m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            items.append({"file": fn, "date": f"{y:04d}-{mo:02d}-{d:02d}", "label": f"{months[mo]} {d}, {y}"})
    items.sort(key=lambda x: x["date"], reverse=True)
    (arch_dir / "index.json").write_text(json.dumps({"summaries": items}, indent=2))
    print(f"  summary: wrote summary_{week}.txt ; archive now has {len(items)} weeks")
    return week


# ===========================================================================
# PDF report (matplotlib assembly: dashboards + summary page)
# ===========================================================================
def build_pdf(out_base, repo, date_str, params_to_do, summary_week):
    from matplotlib.backends.backend_pdf import PdfPages
    dash_dir = out_base / "Dashboards"
    archive = out_base / "PDF_Archive"
    archive.mkdir(parents=True, exist_ok=True)
    out_pdf = archive / f"Hydrolook_{date_str}.pdf"
    if out_pdf.exists():
        print(f"  = Hydrolook_{date_str}.pdf already in archive -- skipped")
        prune_pdf_archive(archive)
        return out_pdf

    summary_txt = ""
    sp = repo / "data" / "summaries" / f"summary_{summary_week}.txt"
    if sp.exists():
        summary_txt = re.sub(r"^#[^\n]*\n+", "", sp.read_text()).strip()

    with PdfPages(out_pdf) as pdf:
        # cover / summary page
        fig = plt.figure(figsize=(11.7, 8.3), facecolor="white")  # A4 landscape
        fig.text(0.5, 0.86, "India Hydrological Outlook", ha="center", fontsize=26, fontweight="bold")
        fig.text(0.5, 0.80, f"{LABELS.get('current','')} {LABELS.get('current_year','')}",
                 ha="center", fontsize=16, color="#444")
        fig.text(0.5, 0.74, "Water and Climate Lab, IIT Gandhinagar", ha="center", fontsize=12, color="#666")
        if summary_txt:
            fig.text(0.08, 0.64, "National Drought Summary", fontsize=14, fontweight="bold")
            import textwrap
            wrapped = "\n".join("\n".join(textwrap.wrap(p, 110)) for p in summary_txt.split("\n"))
            fig.text(0.08, 0.60, wrapped, fontsize=10.5, va="top", linespacing=1.5, wrap=True)
        pdf.savefig(fig); plt.close(fig)

        # one page per dashboard
        for pretty in params_to_do:
            img = dash_dir / f"{pretty}_dashboard.png"
            if not img.exists(): continue
            arr = plt.imread(str(img))
            h, w = arr.shape[0], arr.shape[1]
            fig = plt.figure(figsize=(w / 140, h / 140), facecolor="white")
            ax = fig.add_axes([0, 0, 1, 1]); ax.imshow(arr); ax.axis("off")
            pdf.savefig(fig, dpi=140); plt.close(fig)

    print(f"  pdf: wrote {out_pdf.name} ({len(params_to_do)} dashboards + summary page)")
    return out_pdf


# ===========================================================================
# Main
# ===========================================================================
DATE_RE = re.compile(r"_(\d{4}_\d{2}_\d{2})$")

def autodetect_date(input_dir: Path) -> str:
    found = set()
    for p in input_dir.iterdir():
        m = DATE_RE.search(p.name)
        if m: found.add(m.group(1))
    if not found:
        raise FileNotFoundError(f"No files matching *_YYYY_MM_DD in {input_dir}")
    return sorted(found)[-1]


def step_month(base, n):
    mm, yy = base.month + n, base.year
    while mm <= 0: mm += 12; yy -= 1
    while mm > 12: mm -= 12; yy += 1
    return datetime(yy, mm, 1)


def load_pdf_languages(repo):
    """Read Texts/languages.json; fall back to English-only if missing."""
    p = Path(repo) / "Texts" / "languages.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))["languages"]
        except Exception as e:
            print(f"  ! could not read languages.json ({e}); English only")
    return [{"key": "English", "code": "eng_Latn", "pdf_font": "Carlito"}]


def load_pdf_texts(repo, lang_key):
    """Load Texts/<lang_key>/pdf.json (the static PDF strings) for the target language.
    Static-string translation is a separate, later task, so if a language has no
    pdf.json we fall back to the English labels (the dynamic prose is still
    translated). Returns None only if even English/pdf.json is missing."""
    p = Path(repo) / "Texts" / lang_key / "pdf.json"
    if not p.exists():
        p = Path(repo) / "Texts" / "English" / "pdf.json"
        if not p.exists():
            return None
    return json.loads(p.read_text(encoding="utf-8"))


def translate_paragraphs(paragraphs, tgt_lang_name, api_url, model="gemma4:e2b"):
    """Translate the dynamic LLM paragraphs (dict slot->text) into the target language
    via the shared idm_llm module. Each paragraph is LENGTH-BOUNDED relative to its
    English source (the PDF layout's word budgets only constrain the English), so a
    translation can never overflow its fixed-height box: the model is told to match
    the source length, over-long attempts get "shorten" retries, and as a last resort
    whole sentences are trimmed. Returns a new dict, or None if the server is down."""
    slots = list(paragraphs.keys())
    texts = [paragraphs[s] for s in slots]
    labels = ["%s -> %s" % (s, tgt_lang_name) for s in slots]
    try:
        out = _simple_translate(texts, tgt_lang_name, api_url, model, labels=labels)
    except Exception as e:
        print(f"    (paragraph translation failed: {e})")
        return None
    return {s: out[i] for i, s in enumerate(slots)}


def _simple_translate(texts, tgt_lang_name, api_url, model="gemma4:e2b", labels=None):
    """Translate each paragraph through idm_llm's length-bounded translator, which
    uses the proper translation prompt, enforces the per-paragraph length budget and
    sanitises the result for LaTeX (strips markdown/preamble, collapses blank
    lines). `api_url` is honoured so a direct run's --translate-api wins."""
    import idm_llm
    labels = labels or [None] * len(texts)
    return [idm_llm.translate_bounded(t, tgt_lang_name, model=model, url=api_url, label=lab)
            for t, lab in zip(texts, labels)]


def main():
    ap = argparse.ArgumentParser(description="Generate IDM hydro maps, dashboards, PDF, and summaries.")
    ap.add_argument("--repo", default=".", help="Path to the IDM repo root (default: current dir)")
    ap.add_argument("--date", default=None, help="Forecast date YYYY_MM_DD (default: latest in Input/)")
    ap.add_argument("--api", default="http://10.0.60.193:11434/api/generate", help="Ollama /api/generate URL")
    ap.add_argument("--model", default="gemma4:e2b", help="Ollama model name (prose generation)")
    ap.add_argument("--no-llm", action="store_true", help="Skip the LLM; use a template summary")
    ap.add_argument("--boundaries", default=None, help="Optional admin-boundary file (shapefile/GeoJSON) for outlines")
    ap.add_argument("--include-streamflow", action="store_true",
                    help="Also generate the two Streamflow products (excluded by default)")
    ap.add_argument("--only", default="maps,dashboards,summaries,pdf",
                    help="Comma list of stages to run: maps,dashboards,summaries,pdf")
    ap.add_argument("--pdf-engine", default="latex", choices=["latex", "matplotlib"],
                    help="latex = exact 9-page XeLaTeX outlook (needs xelatex+Carlito); "
                         "matplotlib = lightweight fallback PDF")
    ap.add_argument("--langs", nargs="*", default=None,
                    help="languages to build PDFs for (default: all in Texts/languages.json). "
                         "English is always built. e.g. --langs English Hindi Tamil")
    ap.add_argument("--translate-api", default="http://10.0.60.193:11434/api/generate",
                    help="Ollama /api/generate URL used to translate non-English PDF prose")
    ap.add_argument("--translate-model", default="gemma4:e2b",
                    help="Ollama model for translation (same model as prose by default)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    input_dir = repo / "Hydrologic_Outlook" / "Input"
    out_base = repo / "Hydrologic_Outlook" / "Output"
    stages = set(s.strip() for s in args.only.split(","))

    print(f"IDM hydro generator")
    print(f"  repo       : {repo}")
    print(f"  input dir  : {input_dir}")
    if not input_dir.exists():
        sys.exit(f"ERROR: input dir not found: {input_dir}\n"
                 f"Place the 7 parameter files (P_YYYY_MM_DD, T_..., sm_..., ro_..., ET_..., "
                 f"Q_..., Station_Q_...) in {input_dir}.")

    date_str = args.date or autodetect_date(input_dir)
    y, m, d = map(int, date_str.split("_"))
    base = datetime(y, m, 1)
    global LABELS
    LABELS = {
        "current": base.strftime("%B"), "forecast": step_month(base, +1).strftime("%B"),
        "prev1": step_month(base, -1).strftime("%B"), "prev2": step_month(base, -2).strftime("%B"),
        "prev3": step_month(base, -3).strftime("%B"), "prev4": step_month(base, -4).strftime("%B"),
        "last_year": f"{base.strftime('%B')} ({base.year - 1}*)",
        "current_year": base.year, "forecast_year": step_month(base, +1).year,
    }
    print(f"  date       : {date_str}  (current={LABELS['current']} {y}, forecast={LABELS['forecast']} {LABELS['forecast_year']})")

    params_to_do = list(WEBSITE_PARAMS)
    if args.include_streamflow:
        params_to_do += ["Streamflow_Network", "Streamflow_Stations"]

    print("Loading boundaries…")
    load_boundaries(repo, args.boundaries)

    if "maps" in stages or "dashboards" in stages:
        print("Generating maps and dashboards…")
        generate_maps_and_dashboards(input_dir, out_base, date_str, params_to_do)

    summary_week = None
    if "summaries" in stages:
        print("Generating national summary…")
        summary_week = generate_summary(repo, args.api, args.model, use_llm=not args.no_llm)

    if "pdf" in stages:
        if summary_week is None:
            # derive latest week from the timeseries even if summaries stage was skipped
            try:
                _, summary_week = build_summary_context(repo)
            except Exception:
                summary_week = None
        if args.pdf_engine == "latex":
            print("Building PDF report (exact XeLaTeX outlook)…")
            import hydro_pdf
            if not shutil.which("xelatex"):
                print("  ! xelatex not found on PATH. Install TeX Live (with fontspec/tikz/tcolorbox + "
                      "the Carlito/Noto fonts), or rerun with --pdf-engine matplotlib.")
            else:
                pdf_dash_dir = repo / ".pdf_dashboards"
                print("  generating the 5 dashboards the PDF needs (shared across languages)…")
                generate_dashboards_for_pdf(input_dir, pdf_dash_dir, date_str)
                # Evidence packet (verbatim Section 7) feeds the LLM — English prose first.
                EVIDENCE = hydro_pdf.build_evidence(load_param, input_dir, date_str)
                if args.no_llm:
                    print("  paragraphs: offline template (LLM disabled)")
                    en_paragraphs = hydro_pdf.offline_paragraphs(EVIDENCE, LABELS, y)
                else:
                    try:
                        en_paragraphs = hydro_pdf.generate_all_paragraphs(EVIDENCE, LABELS, y, args.api, args.model)
                    except Exception as e:
                        print(f"  ! Ollama paragraph generation failed ({e}); using offline template.")
                        en_paragraphs = hydro_pdf.offline_paragraphs(EVIDENCE, LABELS, y)

                # which languages to build
                langs = load_pdf_languages(repo)
                if args.langs:
                    want = set(x.lower() for x in args.langs)
                    sel = [l for l in langs if l["key"].lower() in want or l["code"].lower() in want]
                    if not any(l["key"] == "English" for l in sel):
                        eng = [l for l in langs if l["key"] == "English"]
                        sel = eng + sel  # always include English
                    langs = sel or langs
                try:
                    for lang in langs:
                        key = lang["key"]
                        out_pdf_check = out_base / key / "PDF_Archive" / f"Hydrolook_{date_str}.pdf"
                        if out_pdf_check.exists():
                            print(f"  = {key}: Hydrolook_{date_str}.pdf already in archive -- skipped")
                            continue
                        texts = load_pdf_texts(repo, key)
                        if texts is None:
                            print(f"  ! {key}: Texts/{key}/pdf.json not found — run translate_texts.py; skipping")
                            continue
                        # paragraphs: English as-is; otherwise translate the English prose
                        if key == "English":
                            paragraphs = en_paragraphs
                        else:
                            paragraphs = translate_paragraphs(
                                en_paragraphs, lang.get("label") or lang["key"],
                                args.translate_api, model=args.translate_model)
                            if paragraphs is None:
                                print(f"  ! {key}: translation server unavailable — skipping (English PDF still built)")
                                continue
                        lang_out = out_base / key
                        print(f"  [{key}] building PDF (font: {lang['pdf_font']}) -> Output/{key}/")
                        hydro_pdf.build_latex_pdf(repo, lang_out, date_str, LABELS, y, m, d,
                                                  paragraphs, dashboards_src=pdf_dash_dir,
                                                  texts=texts, pdf_font=lang["pdf_font"])
                finally:
                    shutil.rmtree(pdf_dash_dir, ignore_errors=True)
                for lang in langs:
                    prune_pdf_archive(out_base / lang["key"] / "PDF_Archive", keep=_archive_keep(lang["key"]))
        else:
            print("Building PDF report (matplotlib fallback)…")
            build_pdf(out_base, repo, date_str, params_to_do, summary_week)

    print("\nDone. Outputs written under:")
    print(f"  {out_base/'All_Maps'}")
    print(f"  {out_base/'Dashboards'}")
    print(f"  {out_base}/<Language>/  (per-language PDFs + PDF_Archive)")
    print(f"  {repo/'data'/'summaries'}")


if __name__ == "__main__":
    main()
