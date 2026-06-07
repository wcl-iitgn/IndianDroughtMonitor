#!/usr/bin/env python3
# =============================================================================
# generate_forecast_pdfs.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Pre-generates FORECAST SUMMARY PDFs (one per parameter, per forecast init date,
# per language): WCL branding + a persistent footer on EVERY page, a DASHBOARD of
# the 7/15/30-day forecast maps (same DATA + COLORMAP as the Forecast page,
# interpolated normally), a national outlook, and a REGIONAL OUTLOOK TABLE
# (rows = North/Northwest/Northeast/East/Central/West/South; cols = 7/15/30-day).
#
# OUTPUT: data/forecast_summaries/<Language>/PDF_Archive/IDM_Forecast_<Param>_<date>.pdf
# MAP CACHE: data/forecast_summaries/_maps/<param>_<h>day_<date>.png
# ON-SITE TEXT: data/forecast_summaries/<param>_<date>.txt  (English, data-derived)
#
# Init dates: data/forecast_summaries/index.json. Grids: data/<prefix>_<h>day.txt.
#
# REQUIREMENTS (Ubuntu): python3 (numpy scipy matplotlib Pillow); XeLaTeX +
#   fancyhdr + Carlito + per-language Noto fonts (see generate_summary_pdfs.py).
#
# USAGE: python3 generate_forecast_pdfs.py [--langs all|English ...]
#        [--params drought rainfall runoff soil] [--dates ...] [--maps-only]
# =============================================================================

import re
import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

import idm_maps as M
import idm_llm

HORIZONS = [7, 15, 30]

PARAMS = {
    "drought":  {"name": "Drought (CDI)",  "cmap": M.CDI,       "prefix": "Future_CDI", "kind": "cdi", "unit": "",    "file_label": "Drought",
                 "value_header": "Forecast drought area (\\% of region in D0\u2013D4)", "site_metric": "area in forecast drought (D0\u2013D4)"},
    "rainfall": {"name": "Rainfall",       "cmap": M.PRECIP,    "prefix": "P_mag",      "kind": "mag", "unit": "mm",  "file_label": "Rainfall",
                 "value_header": "Expected rainfall (mm)", "site_metric": "expected rainfall"},
    "runoff":   {"name": "Runoff",         "cmap": M.RUNOFF,    "prefix": "R_mag",      "kind": "mag", "unit": "mm",  "file_label": "Runoff",
                 "value_header": "Expected runoff (mm)", "site_metric": "expected runoff"},
    "soil":     {"name": "Soil Moisture",  "cmap": M.SOILMOIST, "prefix": "SM_mag",     "kind": "mag", "unit": "v/v", "file_label": "SoilMoisture",
                 "value_header": "Expected soil moisture (v/v)", "site_metric": "expected soil moisture"},
}
PARAM_ORDER = ["drought", "rainfall", "runoff", "soil"]



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


def grid_path(repo, param, h):
    return Path(repo) / "data" / ("%s_%dday.txt" % (PARAMS[param]["prefix"], h))


# ----------------------------------------------------------------------------- regional outlook
def _num(v, kind):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return "n/a"
    if kind == "cdi":
        return "%.0f" % v
    return ("%.2f" % v) if v < 10 else ("%.0f" % v)


def regional_forecast(repo, param, date_str):
    """Compute national + per-region forecast stats across 7/15/30-day horizons.
    Returns (national_sentence, rows) where rows = [(region, {7:v,15:v,30:v})]."""
    P = PARAMS[param]; kind = P["kind"]
    grids = {}
    rid = None
    for h in HORIZONS:
        Z, rows, cols = M.read_grid(grid_path(repo, param, h))
        grids[h] = Z
        if rid is None:
            rid = M.region_id_grid(repo, rows, cols)

    def stat(Z, mask):
        rr = min(rid.shape[0], Z.shape[0]); cc = min(rid.shape[1], Z.shape[1])
        Zc = Z[:rr, :cc]; mc = mask[:rr, :cc] & ~np.isnan(Zc)
        if mc.sum() == 0:
            return None
        vals = Zc[mc]
        return float((vals <= -0.5).mean() * 100.0) if kind == "cdi" else float(np.nanmean(vals))

    land = rid >= 0
    nat = {h: stat(grids[h], land) for h in HORIZONS}
    unit = (" " + P["unit"]) if P["unit"] else ""
    if kind == "cdi":
        national = ("Forecast drought (D0\u2013D4) is expected to cover about %s%% of India at 7 days, "
                    "%s%% at 15 days and %s%% at 30 days."
                    % (_num(nat[7], kind), _num(nat[15], kind), _num(nat[30], kind)))
    else:
        national = ("Nationally, %s averages about %s%s (7-day), %s%s (15-day) and %s%s (30-day)."
                    % (P["site_metric"], _num(nat[7], kind), unit, _num(nat[15], kind), unit,
                       _num(nat[30], kind), unit))

    rows = []
    for i, region in enumerate(M.REGION_ORDER):
        mask = (rid == i)
        s = {h: stat(grids[h], mask) for h in HORIZONS}
        if all(v is None for v in s.values()):
            continue
        rows.append((region, s))
    return national, rows


def write_text_file(repo, param, date_str, national, rows):
    P = PARAMS[param]; kind = P["kind"]
    unit = (" " + P["unit"]) if P["unit"] else ("%" if kind == "cdi" else "")
    out = Path(repo) / "data" / "forecast_summaries" / ("%s_%s.txt" % (param, date_str))
    out.parent.mkdir(parents=True, exist_ok=True)
    body = [national, "", "Regional outlook \u2014 %s (7 / 15 / 30-day):" % P["site_metric"]]
    for region, s in rows:
        body.append("- %s: %s%s / %s%s / %s%s"
                     % (region, _num(s[7], kind), unit, _num(s[15], kind), unit, _num(s[30], kind), unit))
    out.write_text("# %s forecast outlook initialized %s\n\n" % (P["name"], date_str)
                   + "\n".join(body) + "\n", encoding="utf-8")
    return out


# ----------------------------------------------------------------------------- LaTeX
TEX_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[a4paper,top=1.5cm,bottom=3.0cm,left=1.8cm,right=1.8cm,footskip=2.0cm]{geometry}
\usepackage{graphicx}
\usepackage[table]{xcolor}
\usepackage{ragged2e}
\usepackage{array}
\usepackage{fancyhdr}
\usepackage{fontspec}
\setmainfont{{{T_pdf_font}}}
\setsansfont{{{T_pdf_font}}}
\newfontfamily\latinfont{Carlito}
\renewcommand{\familydefault}{\sfdefault}
\definecolor{titleblue}{HTML}{0F2F4A}
\definecolor{textblack}{HTML}{1A1A1A}
\definecolor{rule}{HTML}{C9C4C0}
\definecolor{muted}{HTML}{6B635E}
\definecolor{rowalt}{HTML}{F3F1EF}
\setlength{\parindent}{0pt}\setlength{\parskip}{0.5em}

\setlength{\headheight}{0pt}\setlength{\headsep}{0pt}
\pagestyle{fancy}\fancyhf{}
\renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0pt}
\fancyfoot[C]{%
  \begin{minipage}{\textwidth}
    {\color{rule}\hrule height 0.7pt}\vspace{4pt}
    \noindent
    \begin{minipage}[c]{0.66\textwidth}{\scriptsize\color{muted}{{T_footer_blurb}}}\end{minipage}\hfill
    \begin{minipage}[c]{0.32\textwidth}\raggedleft
      \includegraphics[height=0.62cm]{wcl.png}\hspace{6pt}%
      \includegraphics[height=0.62cm]{iitgn.png}\hspace{6pt}%
      \includegraphics[height=0.62cm]{imd.png}
    \end{minipage}\\[3pt]
    {\scriptsize\color{muted}{{T_copyright}}}
  \end{minipage}%
}

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
\vspace{10pt}
{\large\bfseries\color{titleblue}{{T_overview_heading}}}\\[2pt]
{\justifying\normalsize\color{textblack}{{T_overview_body}}\par}
\vspace{8pt}
{\large\bfseries\color{titleblue}{{T_regional_heading}}}\\[3pt]
{\normalsize\color{muted}{{T_value_header}}}\\[4pt]
\renewcommand{\arraystretch}{1.3}
\begin{tabular}{@{}p{0.30\textwidth} >{\raggedleft\arraybackslash}p{0.16\textwidth} >{\raggedleft\arraybackslash}p{0.16\textwidth} >{\raggedleft\arraybackslash}p{0.16\textwidth}@{}}
\rowcolor{titleblue}
\textcolor{white}{\textbf{ {{T_col_region}} }} & \textcolor{white}{\textbf{ {{T_h7}} }} & \textcolor{white}{\textbf{ {{T_h15}} }} & \textcolor{white}{\textbf{ {{T_h30}} }}\\
{{T_table_rows}}
\end{tabular}
\end{document}
"""

PDF_STRINGS = {
    "English": {
        "doc_title": "{param} Forecast Outlook",
        "dash_heading": "Forecast maps (7 / 15 / 30 day)",
        "overview_heading": "Outlook",
        "regional_heading": "Regional Outlook",
        "col_region": "Region",
        "h7": "7-day", "h15": "15-day", "h30": "30-day",
        "caption": "Forecast {param_l} for the 7-, 15- and 30-day horizons, initialized {label}.",
        "footer_blurb": ("Developed by the Water and Climate Lab, IIT Gandhinagar. Forecasts are "
                         "driven by the IMD Extended Range Forecast System."),
        "copyright": ("\u00a9 2026 Water and Climate Lab \u00b7 Indian Institute of Technology "
                      "Gandhinagar \u00b7 For research and demonstration purposes."),
        "init_label": "Forecast initialized {label}  \u00b7  valid through {valid}",
    },
}


def build_forecast_pdf(repo, lang, param, date_str, label, national, rows, maps, out_pdf, log=print):
    repo = Path(repo)
    pdf_font = lang.get("pdf_font", "Carlito")
    is_rtl = (lang.get("dir") or "ltr").lower() == "rtl"
    S = PDF_STRINGS.get(lang.get("key", "English"), PDF_STRINGS["English"])
    esc = M.make_escaper(pdf_font)
    P = PARAMS[param]; kind = P["kind"]
    valid = (dt.date.fromisoformat(date_str) + dt.timedelta(days=30)).strftime("%b %d, %Y")

    # alternating-shaded table rows
    trows = []
    for j, (region, s) in enumerate(rows):
        shade = r"\rowcolor{rowalt}" if j % 2 == 0 else ""
        trows.append("%s %s & %s & %s & %s\\\\" % (
            shade, esc(region), esc(_num(s[7], kind)), esc(_num(s[15], kind)), esc(_num(s[30], kind))))
    table_rows = "\n".join(trows)

    tmap = {
        "T_pdf_font": pdf_font,
        "T_doc_title": esc(S["doc_title"].replace("{param}", P["name"])),
        "T_init_label": esc(S["init_label"].replace("{label}", label).replace("{valid}", valid)),
        "T_dash_heading": esc(S["dash_heading"]),
        "T_h7": esc(S["h7"]), "T_h15": esc(S["h15"]), "T_h30": esc(S["h30"]),
        "T_col_region": esc(S["col_region"]),
        "T_caption": esc(S["caption"].replace("{param_l}", P["name"].lower()).replace("{label}", label)),
        "T_overview_heading": esc(S["overview_heading"]),
        "T_overview_body": M.tex_paragraphs(national, esc),
        "T_regional_heading": esc(S["regional_heading"]),
        "T_value_header": P["value_header"] if pdf_font.strip().lower() == "carlito" else M.wrap_latin(P["value_header"]),
        "T_table_rows": table_rows,
        "T_footer_blurb": esc(S["footer_blurb"]),
        "T_copyright": esc(S["copyright"]),
        "BODYDIR": "",  # XeLaTeX has no \textdir/\pardir (those are LuaTeX); match the
                        # hydro PDF, which builds RTL via the script font. Proper bidi
                        # (right-alignment of mixed text) is a separate, testable upgrade.
    }
    tex = TEX_TEMPLATE
    for k, v in tmap.items():
        tex = tex.replace("{{" + k + "}}", v)

    build_dir = repo / "data" / "forecast_summaries" / "_build" / ("%s_%s_%s" % (lang.get("key"), param, date_str))
    build_dir.mkdir(parents=True, exist_ok=True)
    for name, src in [("map7.png", maps[7]), ("map15.png", maps[15]), ("map30.png", maps[30]),
                      ("wcl.png", repo / "assets" / "logos" / "wcl.png"),
                      ("iitgn.png", repo / "assets" / "logos" / "iitgn.png"),
                      ("imd.png", repo / "assets" / "logos" / "imd.png")]:
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


_MONTHS = {1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
           7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December"}


def _seed_forecast_index(fdir, repo):
    """If index.json is absent (e.g. the archive was cleared), seed a single entry
    from the latest week in data/India_Drought_Area_Timeseries.txt, so a fresh
    'remove archive then regenerate' run works without manual setup."""
    idx = fdir / "index.json"
    if idx.exists():
        return
    ts = Path(repo) / "data" / "India_Drought_Area_Timeseries.txt"
    date_str = None
    try:
        last = [ln for ln in ts.read_text(encoding="utf-8").splitlines() if ln.strip()][-1].split()
        date_str = "%04d-%02d-%02d" % (int(float(last[0])), int(float(last[1])), int(float(last[2])))
    except Exception:
        date_str = dt.date.today().isoformat()
    y, mo, d = (int(x) for x in date_str.split("-"))
    label = "%s %d, %d" % (_MONTHS.get(mo, ""), d, y)
    fdir.mkdir(parents=True, exist_ok=True)
    idx.write_text(json.dumps({"forecasts": [{"date": date_str, "label": label}]}, indent=2))
    print("  index.json missing -> seeded latest forecast date %s" % date_str)


def main():
    ap = argparse.ArgumentParser(description="Pre-generate IDM Forecast Summary PDFs.")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--langs", nargs="+", default=["English"])
    ap.add_argument("--params", nargs="+", default=PARAM_ORDER)
    ap.add_argument("--dates", nargs="+", default=None)
    ap.add_argument("--fine-step", type=float, default=0.05)
    ap.add_argument("--maps-only", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-translate per-language text even if it already exists")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    fdir = repo / "data" / "forecast_summaries"
    _seed_forecast_index(fdir, repo)
    inits = json.loads((fdir / "index.json").read_text(encoding="utf-8")).get("forecasts", [])
    if args.dates:
        inits = [x for x in inits if x["date"] in args.dates]
    params = [p for p in args.params if p in PARAMS]
    if not inits or not params:
        print("Nothing to do."); return 1

    print("Rendering forecast maps + regional outlooks ...")
    maps_for, data_for = {}, {}
    for it in inits:
        d = it["date"]
        for param in params:
            mp, ok = {}, True
            for h in HORIZONS:
                gp = grid_path(repo, param, h)
                if not gp.exists():
                    print("  ! missing %s" % gp.relative_to(repo)); ok = False; break
                out_png = fdir / "_maps" / ("%s_%dday_%s.png" % (param, h, d))
                M.render_param_map(repo, gp, PARAMS[param]["cmap"], out_png, fine_step=args.fine_step,
                                   legend=("cdi" if PARAMS[param]["kind"] == "cdi" else "bands"))
                mp[h] = out_png
            if not ok:
                continue
            maps_for[(param, d)] = mp
            national, rows = regional_forecast(repo, param, d)
            data_for[(param, d)] = (national, rows)
            write_text_file(repo, param, d, national, rows)
            print("  %-8s %s : maps + regional table" % (param, d))

    if args.maps_only:
        print("Done (maps + text only)."); return 0

    langs = M.load_languages(repo, args.langs)
    if not langs:
        print("No valid languages."); return 1

    # Translate the English forecast narrative into each language via gemma4 (idm_llm),
    # and write data/forecast_summaries/<lang>/<param>_<date>.txt for the site. Only the
    # prose sentence is translated; the regional table (numbers) and PDF labels stay as-is.
    nat_for = {}
    todo = [l for l in langs if l["key"] != "English"]
    if todo:
        print("Translating forecast text -> %d language(s) via %s ..." % (len(todo), idm_llm.MODEL))
    for lang in langs:
        key = lang["key"]; label = lang.get("label") or key
        for it in inits:
            d = it["date"]
            for param in params:
                if (param, d) not in data_for:
                    continue
                national, _rows = data_for[(param, d)]
                if key == "English":
                    nat_for[(key, param, d)] = national
                    continue
                per = fdir / key / ("%s_%s.txt" % (param, d))
                if per.exists() and not args.force:
                    nat_for[(key, param, d)] = per.read_text(encoding="utf-8").strip()
                    continue
                try:
                    tnat = idm_llm.translate(national, label)
                except Exception as e:
                    print("  ! translate %-9s %-8s %s : %s" % (key, param, d, e)); tnat = national
                nat_for[(key, param, d)] = tnat
                try:
                    per.parent.mkdir(parents=True, exist_ok=True)
                    per.write_text(tnat + "\n", encoding="utf-8")
                except Exception:
                    pass

    built = skipped = 0
    for lang in langs:
        key = lang["key"]
        for it in inits:
            d, label = it["date"], it.get("label", it["date"])
            for param in params:
                if (param, d) not in maps_for:
                    skipped += 1; continue
                national, rows = data_for[(param, d)]
                national = nat_for.get((key, param, d), national)
                out_pdf = fdir / key / "PDF_Archive" / ("IDM_Forecast_%s_%s.pdf" % (PARAMS[param]["file_label"], d))
                if out_pdf.exists():
                    print("  = %-9s %-8s %s (already in archive)" % (key, param, d)); skipped += 1; continue
                try:
                    build_forecast_pdf(repo, lang, param, d, label, national, rows, maps_for[(param, d)], out_pdf)
                    print("  + %-9s %-8s %s" % (key, param, d)); built += 1
                except Exception as e:
                    print("  ! %-9s %-8s %s : %s" % (key, param, d, e)); skipped += 1

    for lang in langs:
        prune_pdf_archive(fdir / lang["key"] / "PDF_Archive", keep=_archive_keep(lang["key"]))
    print("\nDone. PDFs built: %d, skipped: %d" % (built, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
