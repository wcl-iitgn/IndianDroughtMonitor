#!/usr/bin/env python3
# =============================================================================
# pdf_layout_budget.py
#   Width-aware translation budgets for the hydro (Hydrolook) PDF.
#
#   The hydro PDF places each block of prose in a FIXED-WIDTH slot at an absolute
#   position (hydro_pdf.py). The English text in each slot is tuned to fit; a
#   translation overflows when the target script's glyphs are wider than Latin's,
#   so the same character count spills onto extra lines and collides with the
#   block below (e.g. the Malayalam "Model" block on the About page).
#
#   This module gives each block an absolute character budget so the translation
#   occupies ~the same number of LINES as the English source did in that slot:
#
#       lines_used   = ceil( len(english) / chars_per_line(width, Latin) )
#       budget_chars = round( lines_used * chars_per_line(width, script) * FILL )
#
#   It is paired with an auto-shrink backstop in the template (\adjustbox in
#   hydro_pdf.py): the budget gets the length ~right, and \adjustbox guarantees a
#   fit if a translation still runs a little long.
#
#   NOTE: EM_PER_CHAR are ESTIMATES. The Indic faces are not installed in the
#   build sandbox to measure their true average advance widths, so this budget is
#   approximate; the \adjustbox auto-shrink is the hard guarantee, not this file.
#   The estimates and FILL are easy to tune once the PDFs are eyeballed.
# =============================================================================

import math

FONT_PT = 20.0        # hydro body text: \documentclass[20pt]{extarticle} -> \normalsize = 20pt
PT_PER_IN = 72.27
FILL = 0.95           # small safety margin under the English line count
MIN_BUDGET_LEN = 60   # don't budget short labels/titles; also sidesteps the
                      # page_titles/page_intros shared leaf-name clash (titles are short)

# Average glyph advance as a fraction of the em (= font size). ESTIMATES (see note above).
EM_PER_CHAR = {
    "latin":      0.50,
    "devanagari": 0.55,
    "bengali":    0.58,
    "gujarati":   0.55,
    "gurmukhi":   0.55,
    "kannada":    0.60,
    "tamil":      0.60,
    "telugu":     0.60,
    "malayalam":  0.65,
}
DEFAULT_EM = 0.58

# Usable text width (inches) of each slot, read directly from hydro_pdf.py's template.
SLOT_WIDTH_IN = {
    # ---- dynamic LLM paragraphs (generate_hydro_outputs.py) ----
    "page1_summary":            17.9,   # \summaryone box: 18.4in minipage - 2*18pt padding
    "page1_rainfall_temp":       8.2,   # page-1 left column
    "page1_sm_ro_et":            8.2,
    "page1_rivers":              8.2,
    "page2_rainfall_yellow":    18.0,   # \summaryat yellow box: 18.4in - 2*14pt padding
    "page3_temperature_yellow": 18.0,
    "page4_wetness_yellow":     18.0,
    "page5_runoff_yellow":      18.0,
    "page6_et_yellow":          18.0,
    "page7_stationq_yellow":    18.0,
    "page8_networkq_yellow":    18.0,
    # ---- static strings (translate_pdf_strings.py, keyed by pdf.json leaf name) ----
    # page intros use the full-width \introat minipage. (page_titles share these
    # leaf names but are < MIN_BUDGET_LEN, so they are never budgeted.)
    "rainfall":    18.4, "temperature": 18.4, "wetness": 18.4,
    "runoff":      18.4, "et":          18.4,
    # About page, left column (9.0in)
    "about_body":        9.0, "datasets_body_pre": 9.0,
    "datasets_body_mid": 9.0, "model_body":        9.0,
    # About page, right column (8.9in)
    "disclaimer_body": 8.9, "funding_body": 8.9, "contact_address": 8.9,
    # page-1 figure caption (right column, 9.5in)
    "figure_caption": 9.5,
}


def _chars_per_line(width_in, script):
    em = EM_PER_CHAR.get(script, DEFAULT_EM)
    return (width_in * PT_PER_IN) / (FONT_PT * em)


def budget_chars(slot, english_text, script):
    """Return an absolute character budget for a translated block so it occupies about
    the same number of lines as the English source in its slot, or None when no budget
    should apply (unknown slot, Latin/English target, or text shorter than
    MIN_BUDGET_LEN). When None, the caller falls back to the source-relative ratio
    bound in idm_llm.translate_bounded."""
    if not slot or slot not in SLOT_WIDTH_IN:
        return None
    if not script or script == "latin":
        return None
    try:
        import idm_llm
        src_len = idm_llm.visual_len(english_text or "")
    except Exception:
        src_len = len(english_text or "")
    if src_len < MIN_BUDGET_LEN:
        return None
    w = SLOT_WIDTH_IN[slot]
    cpl_latin = _chars_per_line(w, "latin")
    cpl_script = _chars_per_line(w, script)
    n_lines = max(1, math.ceil(src_len / cpl_latin))
    return int(round(n_lines * cpl_script * FILL))


if __name__ == "__main__":
    # quick self-check / illustration
    samples = [
        ("about_body", 757), ("model_body", 515), ("disclaimer_body", 718),
        ("rainfall", 714), ("page2_rainfall_yellow", 260), ("page1_rainfall_temp", 470),
    ]
    for sc in ["devanagari", "malayalam", "tamil", "bengali", "latin"]:
        print("script=%s" % sc)
        for slot, n in samples:
            eng = "x" * n  # visual_len of plain ASCII == len
            print("   %-26s eng=%4d -> budget=%s" % (slot, n, budget_chars(slot, eng, sc)))
