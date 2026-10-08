#!/usr/bin/env python3
"""
India Drought Monitor - automated weekly PDF bulletin (data-driven)
===================================================================

Builds a 2-page A4 bulletin straight from the IndianDroughtMonitor repository
files (no browser scraping, since every map on the site is drawn from these files):

    data/Current_CDI.txt  or  data/Drough_TS/CDI_<YYYYMMDD>.txt   -> map
    data/India_Drought_Area_Timeseries.txt                         -> area table + trend chart
    data/summaries/index.json, summary_<date>.txt                  -> narrative + regional text
    assets/logos/*.png                                             -> header logos

The map is rendered with the repo's own idm_maps.render_param_map(), so colours,
interpolation, boundaries and legend are identical to the website.

Install   pip install numpy scipy matplotlib reportlab pillow
Run       python idm_bulletin.py --repo /path/to/IndianDroughtMonitor
          python idm_bulletin.py --repo . --date 2026-09-23
          (put the script inside the repo root and --repo defaults to ".")

Automate  Run it after build.py, e.g. as the last step of the weekly build, or:
          cron:   30 9 * * 4  cd /path/IndianDroughtMonitor && python3 idm_bulletin.py
          Windows: schtasks /create /tn "IDM Bulletin" /sc weekly /d THU /st 09:30 ^
                   /tr "cmd /c cd /d C:\\path\\IndianDroughtMonitor && python idm_bulletin.py"

HOW TO ADJUST THE LAYOUT
------------------------
Edit block [A] LAYOUT KNOBS below. Every knob is commented with what it does
and which way to turn it. The most common fixes:

  Page 1 spills onto page 2 ........ lower MAP_MAX_H, or BODY_PT 10 -> 9
  Header touches body text ......... MARGIN_T must be >= HEADER_H + 5 mm
  Title touches a logo ............. shorten TITLE_TEXT or lower TITLE_PT / LOGO_H
  Trend chart too tall/short ....... TREND_H
  Want page 2 content to follow on . FORCE_PAGE_BREAK_BEFORE_DETAIL = False
  Landscape layout ................. PAGE_SIZE = landscape(A4)
  Smaller file ..................... MAP_DPI_WIDTH = 1100
"""

import argparse
import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape  # noqa: F401  (landscape for PAGE_SIZE)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import simpleSplit
from reportlab.platypus import (BaseDocTemplate, KeepTogether, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table,
                                TableStyle)
from reportlab.platypus import Image as RLImage

# =============================================================================
# [A] LAYOUT KNOBS  (edit here to make the content fit)
# =============================================================================
PAGE_SIZE = A4                 # A4 or landscape(A4)

# --- Margins -----------------------------------------------------------------
MARGIN_L = 15 * mm             # left / right margins: reduce to widen the text column
MARGIN_R = 15 * mm
MARGIN_T = 30 * mm             # MUST be >= HEADER_H + ~5 mm or body text hits the header
MARGIN_B = 20 * mm             # MUST be >= FOOTER_H + ~3 mm

# --- Header / footer ---------------------------------------------------------
HEADER_H = 22 * mm             # height of the logo / title band on every page
LOGO_H = 13 * mm               # logo height; width follows aspect ratio
LOGOS = ["wcl.png", "iitgn.png"]   # files in assets/logos/. First = left corner, last = right corner.
TITLE_TEXT = "India Drought Monitor - Weekly Bulletin"
TITLE_PT = 16                  # shorten TITLE_TEXT or reduce this if it touches a logo
SUBTITLE_PT = 10
FOOTER_H = 12 * mm
FOOTER_PT = 7                  # footer text size (max 2 lines are drawn)
DISCLAIMER_TEXT = ("Water and Climate Lab, IIT Gandhinagar. Indices are provided as is, "
                   "for research and demonstration purposes. indiadroughtmonitor.in")

# --- Colours -----------------------------------------------------------------
BRAND = colors.HexColor("#da3910")     # headings, rules
TEXT_GREY = colors.HexColor("#333333")
# Class colours come from idm_maps.CDI (same as the website). Class order below
# is No drought, D0 ... D4; labels shown in the table header.
CLASS_LABELS = ["No Drought", "D0", "D1", "D2", "D3", "D4"]

# --- Body text ---------------------------------------------------------------
BODY_PT = 10                   # body size: use 9 if page 1 overflows
BODY_LEADING = 14              # line spacing, keep ~1.3-1.4 x BODY_PT
H2_PT = 12                     # section heading size
SPACE_AFTER_PARA = 6           # gap between paragraphs (pt)

# --- Page 1 blocks -----------------------------------------------------------
MAP_MAX_H = 120 * mm           # tallest the map may be (it is scaled to fit width first)
MAP_DPI_WIDTH = 1500           # pixel width of the rendered map; 1100 = smaller PDF
MAP_FINE_STEP = 0.05           # interpolation step in degrees; 0.1 = faster, blockier map
SHOW_AREA_TABLE = True         # table: % area per class, this week vs last week
SHOW_NATIONAL_SUMMARY = True   # first paragraph of the weekly summary text

# --- Page 2 blocks -----------------------------------------------------------
SHOW_TREND_CHART = True        # stacked % area per class over the last N weeks
TREND_WEEKS = 26               # 13 = quarter, 52 = a year
TREND_H = 70 * mm              # chart height; width = text width
SHOW_REGIONAL_TABLE = True     # North / Northwest / ... rows parsed from the summary text
REGION_COL_W = 28 * mm         # width of the region-name column in that table
FORCE_PAGE_BREAK_BEFORE_DETAIL = True   # True: trend + regions always start on page 2

# --- Table look --------------------------------------------------------------
TABLE_PT = 9                   # font size inside tables
TABLE_PAD = 3                  # row padding (pt): lower to make tables denser


# =============================================================================
# Data loading
# =============================================================================
def load_weeks(repo):
    """Return [(date_str, label, summary_path)] newest first from data/summaries/index.json."""
    idx = json.loads((repo / "data" / "summaries" / "index.json").read_text(encoding="utf-8"))
    out = []
    for s in idx["summaries"]:
        out.append((s["date"], s["label"], repo / "data" / "summaries" / s["file"]))
    return out


def load_area_series(repo):
    """India_Drought_Area_Timeseries.txt -> {date_str: [Normal, D0+, D1+, D2+, D3+, D4+]}
    Columns: year month day  Normal  D0+  D1+  D2+  D3+  D4+  (cumulative % area)."""
    series = {}
    for line in (repo / "data" / "India_Drought_Area_Timeseries.txt").read_text().splitlines():
        p = line.split()
        if len(p) < 9:
            continue
        try:
            y, m, d = int(p[0]), int(p[1]), int(p[2])
            vals = [float(x) for x in p[3:9]]
        except ValueError:
            continue
        series[f"{y:04d}-{m:02d}-{d:02d}"] = vals
    return series


def exclusive_classes(cum):
    """Cumulative [Normal, D0+, D1+, D2+, D3+, D4+] -> exclusive [Normal, D0, D1, D2, D3, D4]."""
    normal, d0p, d1p, d2p, d3p, d4p = cum
    return [normal, d0p - d1p, d1p - d2p, d2p - d3p, d3p - d4p, d4p]


def parse_summary(path):
    """Split summary text into (national paragraph, [(region, text)])."""
    txt = path.read_text(encoding="utf-8")
    lines = [l for l in txt.splitlines() if not l.startswith("#")]
    body = "\n".join(lines).strip()
    parts = re.split(r"\n\s*Regional conditions this week:\s*\n", body, maxsplit=1)
    national = parts[0].strip()
    regions = []
    if len(parts) > 1:
        for l in parts[1].splitlines():
            m = re.match(r"\s*-\s*([A-Za-z ]+):\s*(.+)", l)
            if m:
                regions.append((m.group(1).strip(), m.group(2).strip()))
    return national, regions


# =============================================================================
# Figures
# =============================================================================
def render_map(repo, date_str, out_png):
    """Render the CDI map for the week with the repo's own renderer (site colours/legend)."""
    sys.path.insert(0, str(repo))
    import idm_maps as M
    ymd = date_str.replace("-", "")
    grid = repo / "data" / "Drough_TS" / f"CDI_{ymd}.txt"
    if not grid.exists():
        grid = repo / "data" / "Current_CDI.txt"     # fall back to the latest grid
    M.render_param_map(repo, grid, M.CDI, out_png, fine_step=MAP_FINE_STEP,
                       width_px=MAP_DPI_WIDTH, legend="cdi",
                       legend_title="Drought classification (CDI)", log=lambda *a: None)
    return M


def class_colors(M):
    """[No drought, D0, D1, D2, D3, D4] hex colours from idm_maps.CDI."""
    return [M.CDI["above"]] + [h for _, h in reversed(M.CDI["bands"])]


def render_trend(series, end_date, weeks, hex_colors, out_png, width_mm, height_mm):
    """Stacked-area chart of exclusive class % over the last `weeks` weeks."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    dates = sorted(d for d in series if d <= end_date)[-weeks:]
    ys = np.array([exclusive_classes(series[d]) for d in dates]).T      # (6, n)
    x = [dt.date.fromisoformat(d) for d in dates]
    fig, ax = plt.subplots(figsize=(width_mm / 25.4, height_mm / 25.4), dpi=200)
    ax.stackplot(x, ys[1:], colors=hex_colors[1:], labels=CLASS_LABELS[1:],
                 edgecolor="#8a8a8a", linewidth=0.3)
    ax.set_ylabel("% of India area", fontsize=8)
    ax.set_ylim(0, max(10, float(ys[1:].sum(axis=0).max()) * 1.1))
    ax.tick_params(labelsize=7)
    ax.legend(loc="upper left", fontsize=7, ncol=5, frameon=False)
    ax.grid(axis="y", linewidth=0.3, color="#cccccc")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    fig.savefig(out_png, facecolor="white")
    plt.close(fig)


# =============================================================================
# PDF building
# =============================================================================
def fit_image(path, max_w, max_h):
    with Image.open(path) as im:
        w, h = im.size
    s = min(max_w / w, max_h / h)
    return RLImage(str(path), width=w * s, height=h * s)


def make_styles():
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=BODY_PT,
                          leading=BODY_LEADING, textColor=TEXT_GREY,
                          alignment=TA_JUSTIFY, spaceAfter=SPACE_AFTER_PARA)
    h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=H2_PT,
                        leading=H2_PT + 3, textColor=BRAND, spaceBefore=8, spaceAfter=4)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=TABLE_PT,
                          leading=TABLE_PT + 2.5, textColor=TEXT_GREY)
    cell_r = ParagraphStyle("cell_r", parent=cell, alignment=TA_RIGHT)
    cell_c = ParagraphStyle("cell_c", parent=cell, alignment=TA_CENTER)
    return body, h2, cell, cell_r, cell_c


def area_table(width, this_cum, prev_cum, hex_colors, cell, cell_r, cell_c):
    """% area per exclusive class: this week, last week, change (percentage points)."""
    now = exclusive_classes(this_cum)
    prev = exclusive_classes(prev_cum) if prev_cum else None
    head = [Paragraph("<b>Class</b>", cell), Paragraph("<b>This week (%)</b>", cell_r),
            Paragraph("<b>Last week (%)</b>", cell_r), Paragraph("<b>Change (pp)</b>", cell_r)]
    rows = [head]
    for i, name in enumerate(CLASS_LABELS):
        lab = Paragraph(f"<b>{name}</b>", cell_c)
        if name in ("D3", "D4"):
            lab = Paragraph(f"<font color='white'><b>{name}</b></font>", cell_c)
        diff = f"{now[i] - prev[i]:+.1f}" if prev else "-"
        rows.append([lab, Paragraph(f"{now[i]:.1f}", cell_r),
                     Paragraph(f"{prev[i]:.1f}" if prev else "-", cell_r),
                     Paragraph(diff, cell_r)])
    cw = [width * 0.22] + [width * 0.26] * 3
    t = Table(rows, colWidths=cw)
    st = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
          ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
          ("TOPPADDING", (0, 0), (-1, -1), TABLE_PAD),
          ("BOTTOMPADDING", (0, 0), (-1, -1), TABLE_PAD)]
    for i in range(6):
        st.append(("BACKGROUND", (0, i + 1), (0, i + 1), colors.HexColor(hex_colors[i])))
    t.setStyle(TableStyle(st))
    return t


def regional_table(width, regions, cell):
    rows = [[Paragraph("<b>Region</b>", cell), Paragraph("<b>Conditions this week</b>", cell)]]
    for name, text in regions:
        rows.append([Paragraph(f"<b>{escape(name)}</b>", cell), Paragraph(escape(text), cell)])
    t = Table(rows, colWidths=[REGION_COL_W, width - REGION_COL_W], repeatRows=1)
    t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                           ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                           ("TOPPADDING", (0, 0), (-1, -1), TABLE_PAD),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), TABLE_PAD)]))
    return t


def build_pdf(ctx, out_path):
    W, H = PAGE_SIZE
    frame_w = W - MARGIN_L - MARGIN_R
    frame_h = H - MARGIN_T - MARGIN_B
    body, h2, cell, cell_r, cell_c = make_styles()
    label = ctx["label"]

    def decorate(canv, doc):
        canv.saveState()
        top = H - 8 * mm
        paths = ctx["logos"]
        for path, side in ((paths[0], "L"), (paths[-1], "R")) if paths else ():
            with Image.open(path) as im:
                lw = LOGO_H * im.width / im.height
            x = MARGIN_L if side == "L" else W - MARGIN_R - lw
            canv.drawImage(str(path), x, top - LOGO_H, width=lw, height=LOGO_H,
                           mask="auto", preserveAspectRatio=True)
        canv.setFillColor(BRAND)
        canv.setFont("Helvetica-Bold", TITLE_PT)
        canv.drawCentredString(W / 2, top - 6 * mm, TITLE_TEXT)
        canv.setFillColor(TEXT_GREY)
        canv.setFont("Helvetica", SUBTITLE_PT)
        canv.drawCentredString(W / 2, top - 11.5 * mm, f"Week ending {label}")
        canv.setStrokeColor(BRAND)
        canv.setLineWidth(1.2)
        yr = H - HEADER_H - 4 * mm
        canv.line(MARGIN_L, yr, W - MARGIN_R, yr)
        canv.setStrokeColor(colors.lightgrey)
        canv.setLineWidth(0.5)
        canv.line(MARGIN_L, FOOTER_H, W - MARGIN_R, FOOTER_H)
        canv.setFont("Helvetica", FOOTER_PT)
        canv.setFillColor(colors.grey)
        y = FOOTER_H - 3.5 * mm
        for ln in simpleSplit(DISCLAIMER_TEXT, "Helvetica", FOOTER_PT, frame_w - 20 * mm)[:2]:
            canv.drawString(MARGIN_L, y, ln)
            y -= FOOTER_PT + 1.5
        canv.drawRightString(W - MARGIN_R, FOOTER_H - 3.5 * mm, f"Page {doc.page}")
        canv.restoreState()

    doc = BaseDocTemplate(str(out_path), pagesize=PAGE_SIZE,
                          leftMargin=MARGIN_L, rightMargin=MARGIN_R,
                          topMargin=MARGIN_T, bottomMargin=MARGIN_B,
                          title=f"India Drought Monitor Bulletin - {label}",
                          author="Water and Climate Lab, IIT Gandhinagar")
    doc.addPageTemplates([PageTemplate(
        id="all", onPage=decorate,
        frames=[__import__("reportlab.platypus", fromlist=["Frame"]).Frame(
            MARGIN_L, MARGIN_B, frame_w, frame_h, leftPadding=0, rightPadding=0,
            topPadding=0, bottomPadding=0)])])

    # ---- page 1: map, area table, national summary ----------------------------
    story = [Paragraph("Current Drought Conditions (Combined Drought Index)", h2),
             fit_image(ctx["map_png"], frame_w, MAP_MAX_H)]
    if SHOW_AREA_TABLE:
        story += [Paragraph("Area in Each Drought Class", h2),
                  area_table(frame_w, ctx["cum"], ctx["prev_cum"], ctx["hex"], cell, cell_r, cell_c)]
    if SHOW_NATIONAL_SUMMARY and ctx["national"]:
        story += [Paragraph("National Summary", h2), Paragraph(escape(ctx["national"]), body)]

    # ---- page 2: trend + regional table --------------------------------------
    detail = []
    if SHOW_TREND_CHART and ctx["trend_png"]:
        detail += [Paragraph(f"Drought Area Trend - last {TREND_WEEKS} weeks", h2),
                   fit_image(ctx["trend_png"], frame_w, TREND_H)]
    if SHOW_REGIONAL_TABLE and ctx["regions"]:
        detail += [Paragraph("Regional Outlook", h2), regional_table(frame_w, ctx["regions"], cell)]
    if detail:
        if FORCE_PAGE_BREAK_BEFORE_DETAIL:
            story.append(PageBreak())
        story += detail
    doc.build(story)


# =============================================================================
# CLI
# =============================================================================
def main():
    ap = argparse.ArgumentParser(description="Build the India Drought Monitor weekly PDF bulletin")
    ap.add_argument("--repo", default=".", help="path to the IndianDroughtMonitor repo (default: .)")
    ap.add_argument("--date", help="week to build, YYYY-MM-DD (default: latest in summaries/index.json)")
    ap.add_argument("--out", help="output PDF (default: <repo>/data/bulletins/IDM_Bulletin_<date>.pdf)")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    weeks = load_weeks(repo)
    date_str, label, summary_path = weeks[0]
    if args.date:
        match = [w for w in weeks if w[0] == args.date]
        if not match:
            sys.exit(f"No summary for {args.date}. Available: {', '.join(w[0] for w in weeks)}")
        date_str, label, summary_path = match[0]

    series = load_area_series(repo)
    if date_str not in series:
        sys.exit(f"{date_str} not found in India_Drought_Area_Timeseries.txt")
    earlier = sorted(d for d in series if d < date_str)
    national, regions = parse_summary(summary_path)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        map_png = tmp / "map.png"
        M = render_map(repo, date_str, map_png)
        hex_colors = class_colors(M)
        trend_png = None
        if SHOW_TREND_CHART:
            trend_png = tmp / "trend.png"
            W = PAGE_SIZE[0]
            render_trend(series, date_str, TREND_WEEKS, hex_colors, trend_png,
                         (W - MARGIN_L - MARGIN_R) / mm, TREND_H / mm)
        logos = [repo / "assets" / "logos" / n for n in LOGOS if (repo / "assets" / "logos" / n).exists()]
        ctx = dict(label=label, map_png=map_png, trend_png=trend_png, hex=hex_colors,
                   cum=series[date_str], prev_cum=series[earlier[-1]] if earlier else None,
                   national=national, regions=regions, logos=logos)
        out = Path(args.out) if args.out else repo / "data" / "bulletins" / f"IDM_Bulletin_{date_str}.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        build_pdf(ctx, out)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
