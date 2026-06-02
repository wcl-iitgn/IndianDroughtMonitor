#!/usr/bin/env python3
# =============================================================================
# generate_summary_pdfs.py  --  India Drought Monitor (WCL, IIT Gandhinagar)
# -----------------------------------------------------------------------------
# Pre-generates the downloadable "National Drought Summary" PDFs, one per weekly
# date and per language. Each PDF contains:
#     - WCL + partner branding, the title and the week-ending date
#     - the Combined Drought Index (CDI) map for that week (same DATA + COLORMAP
#       as the interactive site, interpolated the normal way -- see idm_maps.py)
#     - the national summary text, followed by a data-derived REGIONAL OUTLOOK
#       (North, Northwest, Northeast, East, Central, West, South)
#
# Separate from the Hydrological-Outlook report pipeline. Mirrors its conventions
# (XeLaTeX, per-language output folders).
#
# OUTPUT:  data/summaries/<Language>/PDF_Archive/IDM_Summary_<YYYY-MM-DD>.pdf
# MAP CACHE: data/summaries/_maps/CDI_<YYYY-MM-DD>.png
#
# The week list comes from data/summaries/index.json. The English national text
# is read from data/summaries/summary_<date>.txt; this script ALSO writes a
# data-derived "Regional conditions this week:" section back into that file
# (idempotent) so the website's text view and the PDF stay identical. Translated
# text for other languages: data/summaries/<Language>/summary_<date>.txt (from
# the lab's Sarvam-Translate step); missing -> that language is skipped.
#
# REQUIREMENTS (Ubuntu): python3 (numpy scipy matplotlib Pillow); XeLaTeX
#   (texlive-xetex texlive-latex-extra); fonts Carlito + the per-language Noto
#   fonts in Texts/languages.json (fonts-crosextra-carlito fonts-noto-core
#   fonts-noto-extra fonts-noto-ui-core fonts-noto-nastaliq-urdu).
#
# USAGE:
#   python3 generate_summary_pdfs.py                 # English (default)
#   python3 generate_summary_pdfs.py --langs all
#   python3 generate_summary_pdfs.py --dates 2026-05-20
#   python3 generate_summary_pdfs.py --maps-only
# =============================================================================

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

import idm_maps as M


# ----------------------------------------------------------------------------- regional outlook
def _describe_cdi(drought_pct, severe_pct):
    if drought_pct < 5:
        tone = "largely normal conditions"
    elif drought_pct < 20:
        tone = "mostly normal conditions with localised dryness"
    elif drought_pct < 40:
        tone = "moderate, scattered drought"
    elif drought_pct < 65:
        tone = "widespread drought"
    else:
        tone = "extensive drought"
    extra = ""
    if severe_pct >= 10:
        extra = ", including a notable share in severe-or-worse categories"
    elif severe_pct >= 2:
        extra = ", with pockets of severe-or-worse conditions"
    return tone, extra


def compute_regional_cdi(repo, date_str):
    """Return a list of (region, sentence) computed from the real CDI grid for the week."""
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
        n = int(mask.sum())
        if n == 0:
            continue
        vals = Z[mask]
        drought = float((vals <= -0.5).mean() * 100.0)   # D0 or worse
        severe = float((vals <= -1.3).mean() * 100.0)     # D2 or worse
        tone, extra = _describe_cdi(drought, severe)
        out.append((region,
                    "%s shows %s: about %.0f%% of the region is in drought (D0\u2013D4)%s."
                    % (region, tone, drought, extra)))
    return out


def regional_block_text(region_lines):
    """Plain-text block appended to the .txt and shown on the website."""
    if not region_lines:
        return ""
    lines = ["Regional conditions this week:"]
    lines += ["- %s" % s for _, s in region_lines]
    return "\n".join(lines)


_REGION_SENTINEL = "Regional conditions this week:"


def national_part(raw_text):
    """Strip the markdown header and any previously-appended regional section."""
    t = re.sub(r"^#[^\n]*\n+", "", raw_text)
    idx = t.find(_REGION_SENTINEL)
    if idx != -1:
        t = t[:idx]
    return t.strip()


# ----------------------------------------------------------------------------- LaTeX
TEX_TEMPLATE = r"""\documentclass[11pt]{article}
\usepackage[a4paper,margin=1.9cm]{geometry}
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
  {\fontsize{19}{22}\selectfont\bfseries\color{titleblue}{{T_doc_title}}}\\[2pt]
  {\normalsize\color{muted}{{T_week_label}}}
\end{minipage}
\vspace{6pt}{\color{rule}\hrule height 1pt}\vspace{10pt}
\begin{center}
  \includegraphics[width=0.58\textwidth]{cdi_map.png}\\[3pt]
  {\small\color{muted}{{T_caption}}}
\end{center}
\vspace{2pt}
{\large\bfseries\color{titleblue}{{T_summary_heading}}}\\[2pt]
{\justifying\normalsize\color{textblack}{{T_summary_body}}\par}
\vspace{6pt}
{\large\bfseries\color{titleblue}{{T_regional_heading}}}\\[2pt]
{\small\color{textblack}{{T_regional_body}}\par}
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


def build_summary_pdf(repo, lang, date_str, label, national, region_lines, map_png, out_pdf, log=print):
    repo = Path(repo)
    pdf_font = lang.get("pdf_font", "Carlito")
    is_rtl = (lang.get("dir") or "ltr").lower() == "rtl"
    S = PDF_STRINGS.get(lang.get("key", "English"), PDF_STRINGS["English"])
    esc = M.make_escaper(pdf_font)

    regional_tex = r" \\ ".join("%s" % esc(s) for _, s in region_lines) if region_lines else esc("Not available.")

    tmap = {
        "T_pdf_font": pdf_font,
        "T_doc_title": esc(S["doc_title"]),
        "T_week_label": esc(S["week_label"].replace("{label}", label)),
        "T_caption": esc(S["caption"].replace("{label}", label)),
        "T_summary_heading": esc(S["summary_heading"]),
        "T_summary_body": M.tex_paragraphs(national, esc) if national else esc("Summary text not available."),
        "T_regional_heading": esc(S["regional_heading"]),
        "T_regional_body": regional_tex,
        "T_footer_blurb": esc(S["footer_blurb"]),
        "T_copyright": esc(S["copyright"]),
        "BODYDIR": r"\textdir TRT\pardir TRT\relax" if is_rtl else "",
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
    ap.add_argument("--langs", nargs="+", default=["English"], help="languages or 'all' (default English)")
    ap.add_argument("--dates", nargs="+", default=None, help="YYYY-MM-DD weeks (default: all in index.json)")
    ap.add_argument("--fine-step", type=float, default=0.05, help="interpolation grid step in degrees")
    ap.add_argument("--maps-only", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    sumdir = repo / "data" / "summaries"
    weeks = json.loads((sumdir / "index.json").read_text(encoding="utf-8")).get("summaries", [])
    if args.dates:
        weeks = [w for w in weeks if w["date"] in args.dates]
    if not weeks:
        print("No matching weeks in index.json."); return 1

    # 1) CDI map per week (language-independent), with the corrected normal interpolation.
    print("Rendering CDI maps (%d week(s)) ..." % len(weeks))
    map_for, region_for = {}, {}
    for w in weeks:
        d = w["date"]
        ymd = d.replace("-", "")
        grid = repo / "data" / "Drough_TS" / ("CDI_%s.txt" % ymd)
        if not grid.exists():
            alt = repo / "data" / "Current_CDI.txt"
            grid = alt if alt.exists() else grid
        out_png = sumdir / "_maps" / ("CDI_%s.png" % d)
        try:
            M.render_param_map(repo, grid, M.CDI, out_png, fine_step=args.fine_step)
            map_for[d] = out_png
            region_for[d] = compute_regional_cdi(repo, d)
            print("  map+regions: %s" % d)
        except Exception as e:
            print("  ! failed for %s: %s" % (d, e))

    # 2) Write the data-derived regional block back into the English source .txt
    #    (idempotent) so the website text view and the PDF show the same content.
    for w in weeks:
        d = w["date"]
        if d not in region_for:
            continue
        txt = sumdir / ("summary_%s.txt" % d)
        if not txt.exists():
            continue
        raw = txt.read_text(encoding="utf-8")
        header = ""
        m = re.match(r"^(#[^\n]*\n+)", raw)
        if m:
            header = m.group(1)
        nat = national_part(raw)
        block = regional_block_text(region_for[d])
        new = header + nat + ("\n\n" + block if block else "") + "\n"
        txt.write_text(new, encoding="utf-8")
    print("Regional sections written into English summary_*.txt")

    if args.maps_only:
        print("Done (maps + regional text only)."); return 0

    langs = M.load_languages(repo, args.langs)
    if not langs:
        print("No valid languages."); return 1

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
            try:
                build_summary_pdf(repo, lang, d, label, nat, region_for.get(d, []), map_for[d], out_pdf)
                print("  + %-9s %s" % (key, d)); built += 1
            except Exception as e:
                print("  ! %-9s %s : %s" % (key, d, e)); skipped += 1

    print("\nDone. PDFs built: %d, skipped: %d" % (built, skipped))
    print("English PDFs: data/summaries/English/PDF_Archive/")
    print("Non-English weeks need data/summaries/<Language>/summary_<date>.txt (Sarvam-Translate).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
