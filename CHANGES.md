# India Drought Monitor — Change Summary (v13: PDF summary overflow fix + pages 7-8 removed)

## 1. Page-1 SUMMARY no longer overflows its box

The model sometimes wrote a much longer SUMMARY than the 151-word budget (your last run was
~232 words), and it spilled past the box into the "Rainfall and Temperature" heading. Fixed by a
hard word-budget clamp that can never be exceeded:
- `_clamp_to_budget(text, high_words)` keeps whole sentences until adding the next would exceed the
  box budget (target x (1 + tolerance); for the summary that is 151 x 1.07 = 162 words).
- Applied to every generated paragraph (and every offline-template paragraph), so no paragraph can
  overflow its fixed-height region regardless of how verbose the model is.
- Verified: a 232-word summary is trimmed to 158 words ending on a complete sentence, and page 1
  renders with clear space before the "Rainfall and Temperature" heading even with all three body
  sections present.

The clamp keeps all of the current-month text plus as much of the forecast as fits — same density
as the published reference (whose summary was ~151 words and fit the same box).

## 2. Streamflow pages 7 & 8 removed (data no longer available)

The two streamflow products (Streamflow at Gauge Stations, Streamflow at Stream Network) are gone
from the PDF, which is now **7 pages**: cover (page 1), the five grid variables (pages 2-6), and the
About page (now page 7, was 9). Specifically:
- The PAGE 7 and PAGE 8 blocks were deleted from the LaTeX template and the About page renumbered
  to `\pagenum{7}`.
- The PDF now builds only the five grid dashboards (Rainfall, Temperature, Relative Wetness, Total
  Runoff, Evapotranspiration) — the two streamflow dashboards are no longer rendered.
- The streamflow yellow-banner paragraphs (page7/page8) are never generated.
- Streamflow is now fully **data-driven**: the page-1 "River flows" section and the streamflow
  clause in the page-1 SUMMARY appear only if the streamflow Input files (`Q_*`, `Station_Q_*`) are
  present. With the data present (as in your current run) page 1 keeps the River flows section; once
  those files stop arriving, that section and the summary's streamflow mention drop out automatically
  and the SUMMARY covers the five remaining parameters.

This means: if you keep dropping `Q_*` / `Station_Q_*` into `Input/`, the only change vs. before is
that pages 7-8 are gone. If you stop providing them, the PDF stays coherent with no dangling
streamflow references.

## 3. Robustness fix

`build_latex_pdf` now resolves the repo / output / dashboard paths to absolute before invoking
xelatex, so generation works whether you pass a relative or absolute `--repo` (previously a relative
path could break the compile because xelatex runs in the build directory).

## Unchanged
The LLM swap is exactly as before — the 11 (now up to 9) paragraphs are written by your Ollama
server (`/api/generate`, non-thinking, qwen3.5:4b); that is still the only change from the notebook's
PDF logic. The 5-dashboard rendering, colormaps, legend, layout, and the rest of the template are
verbatim.

## How to regenerate
```
cd IDM
python3 generate_hydro_outputs.py              # exact 7-page PDF, prose via Ollama
python3 generate_hydro_outputs.py --no-llm     # exact 7-page PDF, offline template prose
```
Output: Hydrologic_Outlook/Output/Hydrolook_<date>.pdf (+ a copy in PDF_Archive/).

## Validation
Compiled in-environment (xelatex present): a 7-page, 20x20-inch PDF; page 1 SUMMARY fits cleanly
even at the worst-case clamped length; page 7 (About) renumbered correctly; no streamflow pages.
The Ollama path was verified by mocking the endpoint (correct slots with/without streamflow, never
pages 7-8, clamp applied, request still non-thinking qwen3.5:4b).
