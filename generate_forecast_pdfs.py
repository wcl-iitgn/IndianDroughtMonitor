#!/usr/bin/env python3
# =============================================================================
# generate_forecast_pdfs.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Pre-generates downloadable FORECAST SUMMARY PDFs -- one per parameter, per
# forecast initialization date, per language. Each PDF contains:
#     - WCL + partner branding, the parameter, and the forecast init date
#     - a DASHBOARD: the 7-day, 15-day and 30-day forecast maps for that
#       parameter (same DATA + COLORMAP as the interactive Forecast page,
#       interpolated the normal way -- see idm_maps.py)
#     - a data-derived REGIONAL OUTLOOK (North, Northwest, Northeast, East,
#       Central, West, South) of what to expect over the 7- to 30-day horizon
#
# Parallels generate_summary_pdfs.py (the CDI summary). Output:
#     data/forecast_summaries/<Language>/PDF_Archive/IDM_Forecast_<Param>_<date>.pdf
# Map cache: data/forecast_summaries/_maps/<param>_<h>day_<date>.png
# On-site text (per parameter+date, English, data-derived):
#     data/forecast_summaries/<param>_<date>.txt
#
# The init dates come from data/forecast_summaries/index.json. Forecast grids:
#     data/Future_CDI_<h>day.txt, data/P_mag_<h>day.txt, data/R_mag_<h>day.txt,
#     data/SM_mag_<h>day.txt   (h in 7,15,30)
#
# REQUIREMENTS (Ubuntu): python3 (numpy scipy matplotlib Pillow); XeLaTeX +
#   Carlito + per-language Noto fonts (see generate_summary_pdfs.py header).
#
# USAGE:
#   python3 generate_forecast_pdfs.py                 # English (default)
#   python3 generate_forecast_pdfs.py --langs all
#   python3 generate_forecast_pdfs.py --params rainfall soil
#   python3 generate_forecast_pdfs.py --maps-only
# =============================================================================

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

import idm_maps as M

HORIZONS = [7, 15, 30]

# parameter key -> definition
PARAMS = {
    "drought":  {"name": "Drought (CDI)",  "cmap": M.CDI,       "prefix": "Future_CDI", "kind": "cdi", "unit": "", "file_label": "Drought"},
    "rainfall": {"name": "Rainfall",       "cmap": M.PRECIP,    "prefix": "P_mag",      "kind": "mag", "unit": "mm", "file_label": "Rainfall"},
    "runoff":   {"name": "Runoff",         "cmap": M.RUNOFF,    "prefix": "R_mag",      "kind": "mag", "unit": "mm", "file_label": "Runoff"},
    "soil":     {"name": "Soil Moisture",  "cmap": M.SOILMOIST, "prefix": "SM_mag",     "kind": "mag", "unit": "v/v", "file_label": "SoilMoisture"},
}
PARAM_ORDER = ["drought", "rainfall", "runoff", "soil"]


def grid_path(repo, param, h):
    return Path(repo) / "data" / ("%s_%dday.txt" % (PARAMS[param]["prefix"], h))


# ----------------------------------------------------------------------------- regional outlook
def _fmt(v, kind):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    if kind == "cdi":
        return "%.0f%%" % v
    return ("%.2f" % v) if v < 10 else ("%.0f" % v)


def regional_forecast(repo, param, date_str):
    """Compute national + per-region forecast statistics across the 7/15/30-day horizons.
    Returns (national_sentence, [(region, sentence), ...]) -- all data-derived."""
    P = PARAMS[param]
    kind = P["kind"]
    grids = {}
    rid = None
    for h in HORIZONS:
        gp = grid_path(repo, param, h)
        Z, rows, cols = M.read_grid(gp)
        grids[h] = Z
        if rid is None or rid.shape != Z.shape:
            rid = M.region_id_grid(repo, rows, cols)
            # align shapes (grids share the lattice; guard just in case)
            if rid.shape != Z.shape:
                rr = min(rid.shape[0], Z.shape[0]); cc = min(rid.shape[1], Z.shape[1])
                rid = rid[:rr, :cc]

    def stat(Z, mask):
        rr = min(rid.shape[0], Z.shape[0]); cc = min(rid.shape[1], Z.shape[1])
        Zc = Z[:rr, :cc]; mc = mask[:rr, :cc] & ~np.isnan(Zc)
        if mc.sum() == 0:
            return None
        vals = Zc[mc]
        if kind == "cdi":
            return float((vals <= -0.5).mean() * 100.0)   # % in forecast drought (D0+)
        return float(np.nanmean(vals))

    # national
    land_full = rid >= 0
    nat = {h: stat(grids[h], land_full) for h in HORIZONS}
    unit = (" " + P["unit"]) if P["unit"] else ""
    if kind == "cdi":
        national = ("Forecast drought (D0\u2013D4) is expected to cover about %s of India at 7 days, "
                    "%s at 15 days and %s at 30 days." %
                    (_fmt(nat[7], kind), _fmt(nat[15], kind), _fmt(nat[30], kind)))
    else:
        national = ("Nationally, expected %s averages about %s%s (7-day), %s%s (15-day) and %s%s (30-day)." %
                    (P["name"].lower(),
                     _fmt(nat[7], kind), unit, _fmt(nat[15], kind), unit, _fmt(nat[30], kind), unit))

    lines = []
    for i, region in enumerate(M.REGION_ORDER):
        mask = (rid == i)
        s = {h: stat(grids[h], mask) for h in HORIZONS}
        if all(v is None for v in s.values()):
            continue
        if kind == "cdi":
            txt = ("%s \u2014 forecast drought (D0\u2013D4): %s (7d), %s (15d), %s (30d)."
                   % (region, _fmt(s[7], kind), _fmt(s[15], kind), _fmt(s[30], kind)))
        else:
            txt = ("%s \u2014 expected %s: %s%s (7d), %s%s (15d), %s%s (30d)."
                   % (region, P["name"].lower(),
                      _fmt(s[7], kind), unit, _fmt(s[15], kind), unit, _fmt(s[30], kind), unit))
        lines.append((region, txt))
    return national, lines


def write_text_file(repo, param, date_str, national, lines):
    out = Path(repo) / "data" / "forecast_summaries" / ("%s_%s.txt" % (param, date_str))
    out.parent.mkdir(parents=True, exist_ok=True)
    body = [national, "", "Regional outlook (7 to 30 days):"]
    body += ["- %s" % s for _, s in lines]
    out.write_text("# %s forecast outlook initialized %s\n\n" % (PARAMS[param]["name"], date_str)
                   + "\n".join(body) + "\n", encoding="utf-8")
    return out


# ----------------------------------------------------------------------------- LaTeX
TEX_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[a4paper,margin=1.7cm]{geometry}
\usepackage{graphicx}
\usepackage[table]{xcolor}
\usepackage{ragged2e}
\usepackage{fontspec}
\setmainfont{{{T_pdf_font}}}
\setsansfont{{{T_pdf_font}}}
\newfontfamily\latinfont{Carlito}
\renewcommand{\familydefault}{\sfdefault}
\definecolor{titleblue}{HTML}{0F2F4A}
\definecolor{textblack}{HTML}{1A1A1A}
\definecolor{rule}{HTML}{C9C4C0}
\definecolor{muted}{HTML}{6B635E}
\setlength{\parindent}{0pt}\setlength{\parskip}{0.5em}
\pagestyle{empty}
\begin{document}
{{BODYDIR}}
\noindent
\begin{minipage}[c]{0.16\textwidth}\includegraphics[width=\linewidth]{wcl.png}\end{minipage}\hfill
\begin{minipage}[c]{0.80\textwidth}\raggedleft
  {\fontsize{18}{21}\selectfont\bfseries\color{titleblue}{{T_doc_title}}}\\[2pt]
  {\normalsize\color{muted}{{T_init_label}}}
\end{minipage}
\vspace{6pt}{\color{rule}\hrule height 1pt}\vspace{10pt}
{\large\bfseries\color{titleblue}{{T_dash_heading}}}\\[4pt]
\noindent
\begin{minipage}[t]{0.323\textwidth}\centering{\small\bfseries\color{muted}{{T_h7}}}\\[2pt]\includegraphics[width=\linewidth]{map7.png}\end{minipage}\hfill
\begin{minipage}[t]{0.323\textwidth}\centering{\small\bfseries\color{muted}{{T_h15}}}\\[2pt]\includegraphics[width=\linewidth]{map15.png}\end{minipage}\hfill
\begin{minipage}[t]{0.323\textwidth}\centering{\small\bfseries\color{muted}{{T_h30}}}\\[2pt]\includegraphics[width=\linewidth]{map30.png}\end{minipage}
\\[3pt]{\small\color{muted}{{T_caption}}}\par
\vspace{8pt}
{\large\bfseries\color{titleblue}{{T_overview_heading}}}\\[2pt]
{\justifying\normalsize\color{textblack}{{T_overview_body}}\par}
\vspace{6pt}
{\large\bfseries\color{titleblue}{{T_regional_heading}}}\\[2pt]
{\normalsize\color{textblack}{{T_regional_body}}\par}
\vfill
{\color{rule}\hrule height 0.7pt}\vspace{8pt}
\noindent
\begin{minipage}[c]{0.62\textwidth}{\footnotesize\color{muted}{{T_footer_blurb}}}\end{minipage}\hfill
\begin{minipage}[c]{0.34\textwidth}\raggedleft
  \includegraphics[height=0.95cm]{wcl.png}\hspace{8pt}%
  \includegraphics[height=0.95cm]{iitgn.png}\hspace{8pt}%
  \includegraphics[height=0.95cm]{imd.png}
\end{minipage}
\vspace{6pt}\noindent{\scriptsize\color{muted}{{T_copyright}}}
\end{document}
"""

PDF_STRINGS = {
    "English": {
        "doc_title": "{param} Forecast Outlook",
        "dash_heading": "Forecast maps (7 / 15 / 30 day)",
        "overview_heading": "Outlook",
        "regional_heading": "Regional Outlook",
        "h7": "7-day", "h15": "15-day", "h30": "30-day",
        "caption": "Forecast {param_l} for the 7-, 15- and 30-day horizons, initialized {label}.",
        "footer_blurb": ("Developed by the Water and Climate Lab, IIT Gandhinagar. Forecasts are "
                         "driven by the IMD Extended Range Forecast System."),
        "copyright": ("\u00a9 2026 Water and Climate Lab \u00b7 Indian Institute of Technology "
                      "Gandhinagar \u00b7 For research and demonstration purposes."),
        "init_label": "Forecast initialized {label}  \u00b7  valid through {valid}",
    },
}


def build_forecast_pdf(repo, lang, param, date_str, label, national, lines, maps, out_pdf, log=print):
    repo = Path(repo)
    pdf_font = lang.get("pdf_font", "Carlito")
    is_rtl = (lang.get("dir") or "ltr").lower() == "rtl"
    S = PDF_STRINGS.get(lang.get("key", "English"), PDF_STRINGS["English"])
    esc = M.make_escaper(pdf_font)
    P = PARAMS[param]

    valid = (dt.date.fromisoformat(date_str) + dt.timedelta(days=30)).strftime("%b %d, %Y")
    regional_tex = r" \\ ".join("%s" % esc(s) for _, s in lines) if lines else esc("Not available.")

    tmap = {
        "T_pdf_font": pdf_font,
        "T_doc_title": esc(S["doc_title"].replace("{param}", P["name"])),
        "T_init_label": esc(S["init_label"].replace("{label}", label).replace("{valid}", valid)),
        "T_dash_heading": esc(S["dash_heading"]),
        "T_h7": esc(S["h7"]), "T_h15": esc(S["h15"]), "T_h30": esc(S["h30"]),
        "T_caption": esc(S["caption"].replace("{param_l}", P["name"].lower()).replace("{label}", label)),
        "T_overview_heading": esc(S["overview_heading"]),
        "T_overview_body": M.tex_paragraphs(national, esc),
        "T_regional_heading": esc(S["regional_heading"]),
        "T_regional_body": regional_tex,
        "T_footer_blurb": esc(S["footer_blurb"]),
        "T_copyright": esc(S["copyright"]),
        "BODYDIR": r"\textdir TRT\pardir TRT\relax" if is_rtl else "",
    }
    tex = TEX_TEMPLATE
    for k, v in tmap.items():
        tex = tex.replace("{{" + k + "}}", v)

    build_dir = repo / "data" / "forecast_summaries" / "_build" / ("%s_%s_%s" % (lang.get("key"), param, date_str))
    build_dir.mkdir(parents=True, exist_ok=True)
    staged = [("map7.png", maps[7]), ("map15.png", maps[15]), ("map30.png", maps[30]),
              ("wcl.png", repo / "assets" / "logos" / "wcl.png"),
              ("iitgn.png", repo / "assets" / "logos" / "iitgn.png"),
              ("imd.png", repo / "assets" / "logos" / "imd.png")]
    for name, src in staged:
        (build_dir / name).write_bytes(Path(src).read_bytes())
    tex_path = build_dir / "forecast.tex"
    tex_path.write_text(tex, encoding="utf-8")

    cmd = ["xelatex", "-interaction=nonstopmode", "-halt-on-error",
           "-output-directory", str(build_dir), str(tex_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(build_dir))
    if r.returncode != 0:
        log(r.stdout[-1800:]); raise RuntimeError("xelatex failed (%s %s %s)" % (lang.get("key"), param, date_str))
    built = build_dir / "forecast.pdf"
    if not built.exists():
        raise RuntimeError("no PDF produced (%s %s %s)" % (lang.get("key"), param, date_str))
    out_pdf = Path(out_pdf); out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(built.read_bytes())
    return out_pdf


def main():
    ap = argparse.ArgumentParser(description="Pre-generate IDM Forecast Summary PDFs.")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--langs", nargs="+", default=["English"], help="languages or 'all' (default English)")
    ap.add_argument("--params", nargs="+", default=PARAM_ORDER, help="subset of: " + ", ".join(PARAM_ORDER))
    ap.add_argument("--dates", nargs="+", default=None, help="forecast init dates (default: all in index.json)")
    ap.add_argument("--fine-step", type=float, default=0.05)
    ap.add_argument("--maps-only", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    fdir = repo / "data" / "forecast_summaries"
    inits = json.loads((fdir / "index.json").read_text(encoding="utf-8")).get("forecasts", [])
    if args.dates:
        inits = [x for x in inits if x["date"] in args.dates]
    params = [p for p in args.params if p in PARAMS]
    if not inits or not params:
        print("Nothing to do (check index.json / --params)."); return 1

    # 1) Render the 7/15/30-day maps for each parameter x date (language-independent),
    #    and compute + write the data-derived regional outlook text.
    print("Rendering forecast maps + regional outlooks ...")
    maps_for, text_for = {}, {}
    for it in inits:
        d = it["date"]
        for param in params:
            mp = {}
            ok = True
            for h in HORIZONS:
                gp = grid_path(repo, param, h)
                if not gp.exists():
                    print("  ! missing %s" % gp.relative_to(repo)); ok = False; break
                out_png = fdir / "_maps" / ("%s_%dday_%s.png" % (param, h, d))
                M.render_param_map(repo, gp, PARAMS[param]["cmap"], out_png, fine_step=args.fine_step)
                mp[h] = out_png
            if not ok:
                continue
            maps_for[(param, d)] = mp
            national, lines = regional_forecast(repo, param, d)
            text_for[(param, d)] = (national, lines)
            write_text_file(repo, param, d, national, lines)
            print("  %-8s %s : maps + regional text" % (param, d))

    if args.maps_only:
        print("Done (maps + text only)."); return 0

    langs = M.load_languages(repo, args.langs)
    if not langs:
        print("No valid languages."); return 1

    built = skipped = 0
    for lang in langs:
        key = lang["key"]
        for it in inits:
            d, label = it["date"], it.get("label", it["date"])
            for param in params:
                if (param, d) not in maps_for:
                    skipped += 1; continue
                national, lines = text_for[(param, d)]
                out_pdf = fdir / key / "PDF_Archive" / ("IDM_Forecast_%s_%s.pdf" % (PARAMS[param]["file_label"], d))
                try:
                    build_forecast_pdf(repo, lang, param, d, label, national, lines, maps_for[(param, d)], out_pdf)
                    print("  + %-9s %-8s %s" % (key, param, d)); built += 1
                except Exception as e:
                    print("  ! %-9s %-8s %s : %s" % (key, param, d, e)); skipped += 1

    print("\nDone. PDFs built: %d, skipped: %d" % (built, skipped))
    print("English forecast PDFs: data/forecast_summaries/English/PDF_Archive/")
    print("Regional outlook text is data-derived (English). Other languages: the lab can translate "
          "the text/labels via Sarvam-Translate; maps + numbers are language-independent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
