#!/usr/bin/env python3
# =============================================================================
# generate_summary_pdfs.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Pre-generates the downloadable "National Drought Summary" PDFs (one per weekly
# date, per language): WCL branding + a persistent footer on EVERY page, the
# week's CDI map (same DATA + COLORMAP as the site, interpolated normally), the
# national summary text, and a REGIONAL OUTLOOK with a bold sub-heading per
# region (North, Northwest, Northeast, East, Central, West, South).
#
# OUTPUT:  data/summaries/<Language>/PDF_Archive/IDM_Summary_<YYYY-MM-DD>.pdf
# MAP CACHE: data/summaries/_maps/CDI_<YYYY-MM-DD>.png
#
# Week list: data/summaries/index.json. English national text:
# data/summaries/summary_<date>.txt (this script ALSO writes the data-derived
# "Regional conditions this week:" block back into it, idempotently, so the
# website text view and the PDF match). Other languages:
# data/summaries/<Language>/summary_<date>.txt (Sarvam-Translate; missing=skip).
#
# REQUIREMENTS (Ubuntu): python3 (numpy scipy matplotlib Pillow); XeLaTeX
#   (texlive-xetex texlive-latex-extra) with fancyhdr; fonts Carlito + the
#   per-language Noto fonts in Texts/languages.json.
#
# USAGE: python3 generate_summary_pdfs.py [--langs all|English ...]
#        [--dates 2026-05-20 ...] [--fine-step 0.05] [--maps-only]
# =============================================================================

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

import idm_maps as M
import idm_llm


# ----------------------------------------------------------------------------- regional outlook

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


def _describe_cdi(drought_pct, severe_pct):
    if drought_pct < 5:
        tone = "Largely normal conditions"
    elif drought_pct < 20:
        tone = "Mostly normal, with localised dryness"
    elif drought_pct < 40:
        tone = "Moderate, scattered drought"
    elif drought_pct < 65:
        tone = "Widespread drought"
    else:
        tone = "Extensive drought"
    if severe_pct >= 10:
        extra = " A notable share is in severe-or-worse categories (D2\u2013D4)."
    elif severe_pct >= 2:
        extra = " Pockets of severe-or-worse conditions are present."
    else:
        extra = ""
    return "%s. About %.0f%% of the area is in drought (D0\u2013D4).%s" % (tone, drought_pct, extra)


def compute_regional_cdi(repo, date_str):
    """Return [(region, description)] from the real CDI grid (description has NO region prefix)."""
    ymd = date_str.replace("-", "")
    grid = Path(repo) / "data" / "Drough_TS" / ("CDI_%s.txt" % ymd)
    if not grid.exists():
        alt = Path(repo) / "data" / "Current_CDI.txt"
        grid = alt if alt.exists() else grid
    Z, rows, cols = M.read_grid(grid)
    rid = M.region_id_grid(repo, rows, cols)
    out = []
    for i, region in enumerate(M.REGION_ORDER):
        mask = (rid == i) & ~np.isnan(Z)
        if int(mask.sum()) == 0:
            continue
        vals = Z[mask]
        drought = float((vals <= -0.5).mean() * 100.0)
        severe = float((vals <= -1.3).mean() * 100.0)
        out.append((region, _describe_cdi(drought, severe)))
    return out


_REGION_SENTINEL = "Regional conditions this week:"


def regional_block_text(region_lines):
    if not region_lines:
        return ""
    return "\n".join([_REGION_SENTINEL] + ["- %s: %s" % (r, d) for r, d in region_lines])


def national_part(raw_text):
    """Strip the markdown header and any previously-appended regional section."""
    t = re.sub(r"^#[^\n]*\n+", "", raw_text)
    idx = t.find(_REGION_SENTINEL)
    if idx != -1:
        t = t[:idx]
    return t.strip()


# ----------------------------------------------------------------------------- LaTeX
# A persistent footer (logos + blurb + copyright) is placed via fancyhdr on EVERY
# page, inside the reserved bottom margin -- body text can never seep into it.
TEX_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[a4paper,top=1.5cm,bottom=3.0cm,left=1.9cm,right=1.9cm,footskip=2.0cm]{geometry}
\usepackage{graphicx}
\usepackage[table]{xcolor}
\usepackage{ragged2e}
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
\setlength{\parindent}{0pt}\setlength{\parskip}{0.5em}

\setlength{\headheight}{0pt}\setlength{\headsep}{0pt}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}
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
  {\fontsize{19}{22}\selectfont\bfseries\color{titleblue}{{T_doc_title}}}\\[2pt]
  {\normalsize\color{muted}{{T_week_label}}}
\end{minipage}
\vspace{6pt}{\color{rule}\hrule height 1pt}\vspace{10pt}
\begin{center}
  \includegraphics[width=0.60\textwidth]{cdi_map.png}\\[3pt]
  {\small\color{muted}{{T_caption}}}
\end{center}
\vspace{2pt}
{\large\bfseries\color{titleblue}{{T_summary_heading}}}\\[3pt]
{\justifying\normalsize\color{textblack}{{T_summary_body}}\par}
\vspace{8pt}
{\large\bfseries\color{titleblue}{{T_regional_heading}}}\\[2pt]
{{T_regional_body}}
\end{document}
"""

PDF_STRINGS = {
    "English": {
        "doc_title": "National Drought Summary",
        "summary_heading": "Summary",
        "regional_heading": "Regional Outlook",
        "caption": "Combined Drought Index (CDI) for the week ending {label}.",
        "footer_blurb": ("Developed by the Water and Climate Lab, IIT Gandhinagar, using "
                         "hydro-meteorological data from IMD and partner agencies."),
        "copyright": ("\u00a9 2026 Water and Climate Lab \u00b7 Indian Institute of Technology "
                      "Gandhinagar \u00b7 For research and demonstration purposes."),
        "week_label": "Week ending {label}",
    },
}


def regional_tex(region_lines, esc):
    """One bold sub-heading per region (run-in heading), each its own paragraph."""
    if not region_lines:
        return esc("Not available.")
    parts = []
    for region, desc in region_lines:
        parts.append(r"{\normalsize\color{titleblue}\textbf{%s}}\enspace {\normalsize\color{textblack}%s}"
                     % (esc(region), esc(desc)))
    return r" \par\smallskip ".join(parts)


def build_summary_pdf(repo, lang, date_str, label, national, region_lines, map_png, out_pdf, log=print):
    repo = Path(repo)
    pdf_font = lang.get("pdf_font", "Carlito")
    is_rtl = (lang.get("dir") or "ltr").lower() == "rtl"
    S = PDF_STRINGS.get(lang.get("key", "English"), PDF_STRINGS["English"])
    esc = M.make_escaper(pdf_font)

    tmap = {
        "T_pdf_font": pdf_font,
        "T_doc_title": esc(S["doc_title"]),
        "T_week_label": esc(S["week_label"].replace("{label}", label)),
        "T_caption": esc(S["caption"].replace("{label}", label)),
        "T_summary_heading": esc(S["summary_heading"]),
        "T_summary_body": M.tex_paragraphs(national, esc) if national else esc("Summary text not available."),
        "T_regional_heading": esc(S["regional_heading"]),
        "T_regional_body": regional_tex(region_lines, esc),
        "T_footer_blurb": esc(S["footer_blurb"]),
        "T_copyright": esc(S["copyright"]),
        "BODYDIR": "",  # XeLaTeX has no \textdir/\pardir (those are LuaTeX); match the
                        # hydro PDF, which builds RTL via the script font. Proper bidi
                        # (right-alignment of mixed text) is a separate, testable upgrade.
    }
    tex = TEX_TEMPLATE
    for k, v in tmap.items():
        tex = tex.replace("{{" + k + "}}", v)

    build_dir = repo / "data" / "summaries" / "_build" / ("%s_%s" % (lang.get("key"), date_str))
    build_dir.mkdir(parents=True, exist_ok=True)
    for name, src in [("cdi_map.png", map_png),
                      ("wcl.png", repo / "assets" / "logos" / "wcl.png"),
                      ("iitgn.png", repo / "assets" / "logos" / "iitgn.png"),
                      ("imd.png", repo / "assets" / "logos" / "imd.png")]:
        (build_dir / name).write_bytes(Path(src).read_bytes())
    tex_path = build_dir / "summary.tex"
    tex_path.write_text(tex, encoding="utf-8")

    cmd = ["xelatex", "-interaction=nonstopmode", "-halt-on-error",
           "-output-directory", str(build_dir), str(tex_path)]
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(build_dir))
    if r.returncode != 0:
        log(r.stdout[-1800:]); raise RuntimeError("xelatex failed (%s %s)" % (lang.get("key"), date_str))
    built = build_dir / "summary.pdf"
    if not built.exists():
        raise RuntimeError("no PDF produced (%s %s)" % (lang.get("key"), date_str))
    out_pdf = Path(out_pdf); out_pdf.parent.mkdir(parents=True, exist_ok=True)
    out_pdf.write_bytes(built.read_bytes())
    return out_pdf


def main():
    ap = argparse.ArgumentParser(description="Pre-generate IDM National Drought Summary PDFs.")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--langs", nargs="+", default=["English"])
    ap.add_argument("--dates", nargs="+", default=None)
    ap.add_argument("--fine-step", type=float, default=0.05)
    ap.add_argument("--maps-only", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="re-translate per-language text even if it already exists")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    sumdir = repo / "data" / "summaries"
    weeks = json.loads((sumdir / "index.json").read_text(encoding="utf-8")).get("summaries", [])
    if args.dates:
        weeks = [w for w in weeks if w["date"] in args.dates]
    if not weeks:
        print("No matching weeks in index.json."); return 1

    print("Rendering CDI maps (%d week(s)) ..." % len(weeks))
    map_for, region_for = {}, {}
    for w in weeks:
        d = w["date"]; ymd = d.replace("-", "")
        grid = repo / "data" / "Drough_TS" / ("CDI_%s.txt" % ymd)
        if not grid.exists():
            alt = repo / "data" / "Current_CDI.txt"
            grid = alt if alt.exists() else grid
        out_png = sumdir / "_maps" / ("CDI_%s.png" % d)
        try:
            M.render_param_map(repo, grid, M.CDI, out_png, fine_step=args.fine_step, legend="cdi")
            map_for[d] = out_png
            region_for[d] = compute_regional_cdi(repo, d)
            print("  map+regions: %s" % d)
        except Exception as e:
            print("  ! failed for %s: %s" % (d, e))

    # Write the data-derived regional block into the English source .txt (idempotent).
    for w in weeks:
        d = w["date"]
        if d not in region_for:
            continue
        txt = sumdir / ("summary_%s.txt" % d)
        if not txt.exists():
            continue
        raw = txt.read_text(encoding="utf-8")
        m = re.match(r"^(#[^\n]*\n+)", raw)
        header = m.group(1) if m else ""
        nat = national_part(raw)
        block = regional_block_text(region_for[d])
        txt.write_text(header + nat + ("\n\n" + block if block else "") + "\n", encoding="utf-8")
    print("Regional sections written into English summary_*.txt")

    if args.maps_only:
        print("Done (maps + regional text only)."); return 0

    langs = M.load_languages(repo, args.langs)
    if not langs:
        print("No valid languages."); return 1

    # Translate the English national narrative into each non-English language via
    # gemma4 (idm_llm) and write data/summaries/<lang>/summary_<date>.txt. This is the
    # per-language on-site text, and it's what the render loop below reads. Only the
    # dynamic narrative is translated; PDF labels and the regional block stay English
    # for now (static-text localisation is the separate, later task).
    todo = [l for l in langs if l["key"] != "English"]
    if todo:
        print("Translating summary text -> %d language(s) via %s ..." % (len(todo), idm_llm.MODEL))
    for lang in todo:
        key = lang["key"]; label = lang.get("label") or key
        for w in weeks:
            d = w["date"]
            eng = sumdir / ("summary_%s.txt" % d)
            per = sumdir / key / ("summary_%s.txt" % d)
            if not eng.exists():
                continue
            if per.exists() and not args.force:
                continue
            try:
                nat = national_part(eng.read_text(encoding="utf-8"))
                per.parent.mkdir(parents=True, exist_ok=True)
                per.write_text(idm_llm.translate(nat, label) + "\n", encoding="utf-8")
                print("  translated %-9s %s" % (key, d))
            except Exception as e:
                print("  ! translate %-9s %s : %s" % (key, d, e))

    built = skipped = 0
    for lang in langs:
        key = lang["key"]
        for w in weeks:
            d, label = w["date"], w.get("label", w["date"])
            if d not in map_for:
                skipped += 1; continue
            txt = (sumdir / ("summary_%s.txt" % d)) if key == "English" else (sumdir / key / ("summary_%s.txt" % d))
            if not txt.exists():
                print("  - %-9s %s : no text (%s) -- skipped" % (key, d, txt.relative_to(repo)))
                skipped += 1; continue
            nat = national_part(txt.read_text(encoding="utf-8"))
            out_pdf = sumdir / key / "PDF_Archive" / ("IDM_Summary_%s.pdf" % d)
            if out_pdf.exists():
                print("  = %-9s %s (already in archive)" % (key, d)); skipped += 1; continue
            try:
                build_summary_pdf(repo, lang, d, label, nat, region_for.get(d, []), map_for[d], out_pdf)
                print("  + %-9s %s" % (key, d)); built += 1
            except Exception as e:
                print("  ! %-9s %s : %s" % (key, d, e)); skipped += 1

    for lang in langs:
        prune_pdf_archive(sumdir / lang["key"] / "PDF_Archive", keep=_archive_keep(lang["key"]))
    print("\nDone. PDFs built: %d, skipped: %d" % (built, skipped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
