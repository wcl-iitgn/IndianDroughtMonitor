#!/usr/bin/env python3
"""
hydro_pdf.py — faithful local port of the IHO_Pipeline_Final.ipynb PDF stage.

Reproduces the exact 9-page India Hydrological Outlook PDF (20x20 inch, XeLaTeX +
Carlito + TikZ), identical to the published Hydrolook_<date>.pdf. The ONLY change
from the notebook is the LLM backend: wherever the pipeline used HuggingFace
Transformers (`BACKEND.chat`) to write the 11 prose paragraphs, this module calls
the LAN Ollama server (`/api/generate`, non-thinking) instead.

Used by generate_hydro_outputs.py (--pdf-engine latex). Requires:
  * xelatex on PATH (TeX Live) with fontspec, tikz, tcolorbox, and the Carlito font
  * the 7 static images in Hydrologic_Outlook/PDF_images/
  * all 7 dashboards (incl. the two Streamflow products) — the PDF has 9 pages
"""
from __future__ import annotations  # allow modern type hints on Python 3.7-3.9

import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


# ===========================================================================
# Section 7 (verbatim) — per-region category histograms = the evidence the LLM sees
# ===========================================================================
def region_mask(df, region):
    lat, lon = df["lat"], df["lon"]
    if region == "north":        return (lat >= 28) & lon.between(73, 88)
    if region == "northeast":    return (lat >= 22) & (lon >= 89)
    if region == "northwest":    return (lat >= 24) & (lon < 76)
    if region == "central":      return lat.between(20, 26) & lon.between(74, 86)
    if region == "east":         return lat.between(20, 27) & lon.between(83, 89)
    if region == "west":         return lat.between(17, 24) & lon.between(68, 75)
    if region == "south":        return lat < 18
    if region == "central_east": return lat.between(18, 24) & lon.between(80, 86)
    if region == "all_india":    return pd.Series(True, index=df.index)
    raise ValueError(region)

REGIONS = ["north", "northeast", "northwest", "central", "east", "west", "south", "central_east"]

def directional_summary(values, parameter):
    v = pd.Series(values).dropna().astype(float)
    if len(v) == 0:
        return {"dominant": "no_data", "narrative": "no data", "n": 0}
    if parameter in ("P", "Q", "Station_Q"):
        below = (v < 35).mean(); near = ((v >= 35) & (v < 65)).mean(); above = (v >= 65).mean()
        far_below = (v < 15).mean(); far_above = (v >= 85).mean()
    elif parameter == "sm":
        below = (v < -20).mean(); near = ((v >= -20) & (v <= 20)).mean(); above = (v > 20).mean()
        far_below = (v < -60).mean(); far_above = (v > 60).mean()
    elif parameter == "T":
        below = (v < -0.5).mean(); near = ((v >= -0.5) & (v <= 0.5)).mean(); above = (v > 0.5).mean()
        far_below = (v < -2.0).mean(); far_above = (v > 2.0).mean()
    elif parameter in ("ro", "ET"):
        below = (v < -5).mean(); near = ((v >= -5) & (v <= 5)).mean(); above = (v > 5).mean()
        far_below = (v < -20).mean(); far_above = (v > 20).mean()
    else:
        raise ValueError(parameter)
    fractions = {"below": float(below), "near": float(near), "above": float(above)}
    dominant_key = max(fractions, key=fractions.get); dominant_frac = fractions[dominant_key]
    VOCAB = {
        "P": {"below": "lower-than-normal rainfall", "near": "near-normal rainfall", "above": "higher-than-normal rainfall", "far_below": "very low rainfall", "far_above": "very high rainfall"},
        "T": {"below": "cooler-than-normal temperatures", "near": "near-normal temperatures", "above": "warmer-than-normal temperatures", "far_below": "much cooler than normal", "far_above": "much warmer than normal"},
        "sm": {"below": "drier-than-normal soil", "near": "near-normal soil moisture", "above": "wetter-than-normal soil", "far_below": "very dry soil", "far_above": "high relative wetness"},
        "ro": {"below": "a runoff deficit", "near": "near-normal runoff", "above": "a runoff surplus", "far_below": "a high runoff deficit", "far_above": "a high runoff surplus"},
        "ET": {"below": "reduced evapotranspiration", "near": "near-normal evapotranspiration", "above": "elevated evapotranspiration", "far_below": "very low evapotranspiration", "far_above": "very high evapotranspiration"},
        "Q": {"below": "lower-than-normal streamflow", "near": "near-normal streamflow", "above": "higher-than-normal streamflow", "far_below": "very low streamflow", "far_above": "very high streamflow"},
        "Station_Q": {"below": "lower-than-normal streamflow", "near": "near-normal streamflow", "above": "higher-than-normal streamflow", "far_below": "very low streamflow", "far_above": "very high streamflow"},
    }
    vocab = VOCAB[parameter]
    if dominant_key == "below" and far_below > 0.25:   narrative = vocab["far_below"]
    elif dominant_key == "above" and far_above > 0.25: narrative = vocab["far_above"]
    elif dominant_frac > 0.55:                         narrative = vocab[dominant_key]
    else:                                              narrative = "mixed conditions, leaning toward " + vocab[dominant_key]
    return {"dominant": dominant_key, "dominant_frac": round(dominant_frac, 2),
            "pct_below": int(round(below*100)), "pct_near": int(round(near*100)), "pct_above": int(round(above*100)),
            "pct_far_below": int(round(far_below*100)), "pct_far_above": int(round(far_above*100)),
            "narrative": narrative, "n": int(len(v))}


def build_evidence(load_param, input_dir, date_str):
    EVIDENCE = {}
    for prefix in ["P", "T", "sm", "ro", "ET", "Q", "Station_Q"]:
        try:
            df = load_param(prefix, input_dir, date_str)
        except FileNotFoundError:
            continue
        EVIDENCE[prefix] = {}
        for region in REGIONS:
            mask = region_mask(df, region)
            if mask.sum() == 0:
                continue
            EVIDENCE[prefix][region] = {
                "current":  directional_summary(df.loc[mask, "current"], prefix),
                "forecast": directional_summary(df.loc[mask, "forecast"], prefix),
            }
    return EVIDENCE


# ===========================================================================
# Section 9 (verbatim, with the LLM backend swapped to Ollama)
# ===========================================================================
TARGETS = {
    "page1_summary": 151, "page1_rainfall_temp": 77, "page1_sm_ro_et": 84, "page1_rivers": 64,
    "page2_rainfall_yellow": 35, "page3_temperature_yellow": 42, "page4_wetness_yellow": 34,
    "page5_runoff_yellow": 26, "page6_et_yellow": 31, "page7_stationq_yellow": 34, "page8_networkq_yellow": 28,
}
TOLERANCE = 0.15
TOLERANCE_BY_SLOT = {"page1_summary": 0.07, "page1_rainfall_temp": 0.10, "page1_sm_ro_et": 0.10, "page1_rivers": 0.10}
MAX_ATTEMPTS = 7

REFERENCE_EXAMPLES = {
    "page1_summary": ("In February, India experienced lower-than-normal rainfall in majority of central parts, and lower-than-normal rainfall in northeast parts, along with lower-than-average temperatures and high relative wetness across India, and decreased total runoff in north-central and north-east India. Above-average soil moisture persisted in central and southern parts, while runoff deficit affected the north-central and north-east India. Streamflow in rivers and reaches was also higher across country except north and northeast India. In March, relatively high rainfall in west, central, and northeast regions, while low rainfall in west central India is expected. India is likely to experience relatively higher temperature in central regions, with relatively very low temperature in northern India and northeast regions. Decreased ET across country, and high deficit of total runoff in north and northeastern India are predicted. Moreover, lower streamflow in river-reaches is predicted in north and central region, while higher streamflow in river-reaches is predicted in south India."),
    "page1_rainfall_temp": ("In February, the east and the majority of central India experienced lower rainfall, while northeast India recorded relatively lower rainfall. In March, a relatively higher rainfall is predicted in west-central and north India; however, west central and northeastern parts of India may receive relatively lower rainfall. The temperature was relatively cooler in February in India, except in the north-east. In March, above-average temperature is predicted in western India, with potential cold spells in northern and northeastern parts."),
    "page1_sm_ro_et": ("In February, soil moisture was relatively higher than normal in west, central and south India. In March, above-average soil moisture is going to persist. A very high surplus in total runoff was observed in central India in February, while in March, a deficit of total runoff may be recorded in northern India. Evapotranspiration (ET) in February was reduced in east-central India, which will be reduced in March. However, a relatively lesser ET in March is expected across India, indicating decreased moisture loss across country."),
    "page1_rivers": ("In February, a relatively very high streamflow in rivers and reaches was observed in most locations in south India, while a relatively low flow is observed in northeast parts. In March, a relatively extreme low streamflow is predicted in south and central regions. Moreover, north-central regions are likely to experience low streamflow in its river networks, indicating sustained low water volumes in these areas."),
    "page2_rainfall_yellow": ("A relatively lower rainfall in February across India. In March, a higher rainfall than usual is predicted in northeast and northwest India, while relatively less rainfall is predicted in eastcentral, and southern parts of country."),
    "page3_temperature_yellow": ("In February, a relatively higher temperature than usual was observed across India except some parts of south, and central-east India. In March, high warm temperature than usual across India. Moreover, extreme low temperature than usual is predicted in north and northeast parts."),
    "page4_wetness_yellow": ("Soil moisture in February is medium wetter than usual time of year across India except north and north-east India. In March, low to medium dry wetness (soil moisture) is persisted in the same regions."),
    "page5_runoff_yellow": ("In February, a relatively normal total runoff across country except north-east and north India. In March, relative lower runoff is predicted in northeast, and north India."),
    "page6_et_yellow": ("In February, a less evapotranspiration (ET) than usual in east-central, north, and northeast region. In March, a relatively low ET is predicted across country except few parts of central east India."),
    "page7_stationq_yellow": ("In February, a relatively higher streamflow for most of the locations except locations in northeast region. In March, a relatively low streamflow is predicted in central India, and very low streamflow in north India."),
    "page8_networkq_yellow": ("In February, a relatively high flow in the rivers except north-central, and northeast India. In March, a relatively low flow is predicted in north, central, and south India."),
}

SYSTEM_PROMPT = """You are the writing assistant for the India Hydrological Outlook, a monthly PDF published by the Water & Climate Lab at IIT Gandhinagar. Your job is to write ONE paragraph of clean, factual prose for the published outlook.

Strict rules - violating any of these is a failure:

1. **Stick to the evidence.** Use ONLY the regional patterns described in the EVIDENCE block. Do NOT invent regions, months, or numerical values. If the evidence says a region is "higher-than-normal", you cannot write that it's "lower-than-normal" or vice-versa.

2. **Use the bulletin's writing style.** Look at the REFERENCE PARAGRAPH (a real paragraph from a previous month's outlook). Match its tone, sentence structure, and geographic phrasing. Use natural phrases like "majority of central parts", "across India except north and northeast", "above-average soil moisture persisted in central and southern parts" - NOT dry region-name lists like "north, northeast, northwest, central, east, west experienced...".

3. **Do not list all the region names.** That reads like a database dump, not a bulletin. Group regions into geographic clusters with natural prose ("northern India", "central and southern parts", "the northeast", "across India except the south").

4. **Mention both the current month (observed) and the forecast month (predicted).** Use present/past tense for the current month and future tense for the forecast.

5. **Write to the requested WORD COUNT.** Going more than 15% over or under is a failure.

6. **No bullet points, no headers, no Markdown.** One flowing paragraph of prose.

7. **Do NOT wrap the paragraph in quotes, code blocks, or labels.** Output ONLY the paragraph text.

8. **Tone: matter-of-fact, present-tense for the current month, future-tense for the forecast.** Match the style of an official hydrology bulletin - no hype, no marketing language."""

# The PDF now omits the two streamflow products (gauge stations + stream network):
# their data is no longer available, so pages 7 & 8 are dropped and the page-1
# "River flows" section / summary streamflow clause appear ONLY if that data is present.
def _has_streamflow(EVIDENCE):
    return bool(EVIDENCE.get("Q")) and bool(EVIDENCE.get("Station_Q"))


def build_paragraph_spec(EVIDENCE):
    """Return the list of (slot, params, instruction) to generate, adapting to whether
    streamflow data is available. Pages 7 & 8 (the streamflow dashboards) are never built."""
    has_sf = _has_streamflow(EVIDENCE)
    if has_sf:
        summ_params = ["P", "T", "sm", "ro", "ET", "Q", "Station_Q"]
        summ_instr = ("This is the top-of-page-1 SUMMARY paragraph. Cover ALL seven parameters in one "
                      "flowing paragraph: rainfall, temperature, relative wetness, total runoff, "
                      "evapotranspiration, streamflow in rivers and reaches, and streamflow at gauge "
                      "stations. Order: current-month observations first, then the forecast. Keep it "
                      "concise — do not exceed the target length.")
    else:
        summ_params = ["P", "T", "sm", "ro", "ET"]
        summ_instr = ("This is the top-of-page-1 SUMMARY paragraph. Cover ALL five parameters in one "
                      "flowing paragraph: rainfall, temperature, relative wetness, total runoff, and "
                      "evapotranspiration. Order: current-month observations first, then the forecast. "
                      "Keep it concise — do not exceed the target length.")
    spec = [
        ("page1_summary", summ_params, summ_instr),
        ("page1_rainfall_temp", ["P", "T"], "This is the 'Rainfall and Temperature' section on page 1. Two sentences on rainfall (current + forecast) and two on temperature (current + forecast)."),
        ("page1_sm_ro_et", ["sm", "ro", "ET"], "This is the 'Soil moisture, Total runoff, and Evapotranspiration' section on page 1. Cover each of the three sub-topics; mention both current and forecast for each."),
    ]
    if has_sf:
        spec.append(("page1_rivers", ["Q", "Station_Q"], "This is the 'River flows' section on page 1. Combine the two streamflow parameters (network reaches + gauge stations). Current first, forecast second."))
    spec += [
        ("page2_rainfall_yellow", ["P"], "This is the yellow Summary banner on the Rainfall page. Brief: one or two short sentences. Cover both current and forecast."),
        ("page3_temperature_yellow", ["T"], "This is the yellow Summary banner on the Temperature page. Brief; current and forecast."),
        ("page4_wetness_yellow", ["sm"], "This is the yellow Summary banner on the Relative Wetness page. Brief; current and forecast."),
        ("page5_runoff_yellow", ["ro"], "This is the yellow Summary banner on the Total Runoff page. Brief; current and forecast."),
        ("page6_et_yellow", ["ET"], "This is the yellow Summary banner on the Evapotranspiration page. Brief; current and forecast."),
    ]
    return spec

PARAM_LABEL = {"P": "Rainfall (percentile)", "T": "Surface air temperature (degC anomaly)",
               "sm": "Relative wetness / soil moisture (% anomaly)", "ro": "Total runoff (mm anomaly)",
               "ET": "Evapotranspiration (mm anomaly)", "Q": "Streamflow at stream network (percentile)",
               "Station_Q": "Streamflow at gauge stations (percentile)"}


def _ollama_chat(system, user, api_url, model, num_predict=400):
    """The ONLY change from the notebook: BACKEND.chat(...) -> Ollama /api/generate."""
    import requests
    body = {"model": model, "prompt": user, "system": system, "stream": False, "think": False,
            "options": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "num_predict": num_predict}}
    r = requests.post(api_url, json=body, timeout=300)
    r.raise_for_status()
    txt = r.json().get("response", "").strip()
    if "</think>" in txt:
        txt = txt.split("</think>")[-1].strip()
    return txt


def word_count(s):
    return len(s.split())

def _clamp_to_budget(text, high_words):
    """Hard cap so a paragraph can never overflow its fixed-height box: keep whole
    sentences until adding the next would exceed `high_words`. Guarantees the layout
    holds even when the model ignores the word budget."""
    if word_count(text) <= high_words:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    out, n = [], 0
    for s in sentences:
        sw = word_count(s)
        if out and n + sw > high_words:
            break
        out.append(s); n += sw
    clamped = " ".join(out).strip()
    return clamped if clamped else text

def _strip_wrapping(text):
    text = text.strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = re.sub(r"^(?:Paragraph|Summary|Answer|Output)\s*:\s*", "", text, flags=re.I)
    if text and text[0] in "\"'\u201c\u2018" and text[-1] in "\"'\u201d\u2019":
        text = text[1:-1].strip()
    return text


def _evidence_block(EVIDENCE, params, current_label, forecast_label):
    lines = []
    for p in params:
        ev = EVIDENCE.get(p, {})
        lines.append("\n== %s ==" % PARAM_LABEL[p])
        for region in REGIONS:
            if region not in ev:
                continue
            cur = ev[region]["current"]; fc = ev[region]["forecast"]
            r_pretty = region.replace("_", "-")
            lines.append("  %-14s  %s: %s" % (r_pretty, current_label, cur["narrative"]))
            lines.append("  %-14s  %s: %s" % (r_pretty, forecast_label, fc["narrative"]))
    return "\n".join(lines)


def generate_paragraph(slot, params, target, extra_instructions, EVIDENCE, labels, year, api_url, model):
    tol = TOLERANCE_BY_SLOT.get(slot, TOLERANCE)
    low = int(round(target * (1 - tol))); high = int(round(target * (1 + tol)))
    ev_block = _evidence_block(EVIDENCE, params, labels["current"], labels["forecast"])
    reference = REFERENCE_EXAMPLES[slot]
    base_user = (
        "EVIDENCE (pre-computed regional patterns for %s %s and the %s forecast):\n%s\n\n"
        "REFERENCE PARAGRAPH (a paragraph from a previous month's outlook - match this writing "
        "style, but DO NOT copy its specific regional claims; the regional patterns may be different "
        "this month):\n\"%s\"\n\n"
        "WRITE: one paragraph for the section \"%s\". %s\n\n"
        "TARGET LENGTH: %d words (between %d and %d inclusive). Mention conditions in %s (current) "
        "AND in %s (forecast).\n\nOutput ONLY the paragraph text. No labels, no quotes, no markdown."
        % (labels["current"], year, labels["forecast"], ev_block, reference, slot,
           extra_instructions, target, low, high, labels["current"], labels["forecast"])
    )
    candidates = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if attempt == 1:
            user = base_user
        else:
            best = min(candidates, key=lambda c: (c[0], c[1]))
            feedback_parts = []
            if best[1] > 0:
                wc = word_count(best[2])
                feedback_parts.append(("shorten (last try was %d words, target %d)" % (wc, target)) if wc > high
                                      else ("lengthen (last try was %d words, target %d)" % (wc, target)))
            rider = "; ".join(feedback_parts)
            user = base_user + ("\n\n(Previous attempt feedback: %s.)" % rider if rider else "")
        try:
            content = _ollama_chat(SYSTEM_PROMPT, user, api_url, model)
        except Exception as e:
            raise RuntimeError("Ollama call failed while writing paragraph '%s': %s" % (slot, e))
        text = _strip_wrapping(content)
        text = _clamp_to_budget(text, high)   # never let it overflow its box
        wc = word_count(text)
        wd = 0 if low <= wc <= high else min(abs(wc - low), abs(wc - high))
        candidates.append((0, wd, text))
        if wd == 0:
            return text, wc, attempt
    candidates.sort(key=lambda c: (c[0], c[1]))
    best = candidates[0]
    return best[2], word_count(best[2]), MAX_ATTEMPTS


def generate_all_paragraphs(EVIDENCE, labels, year, api_url, model, log=print):
    spec = build_paragraph_spec(EVIDENCE)
    paragraphs = {}
    log("  writing %d paragraphs via Ollama (%s)..." % (len(spec), model))
    for slot, params, extra in spec:
        target = TARGETS[slot]
        text, wc, attempts = generate_paragraph(slot, params, target, extra, EVIDENCE, labels, year, api_url, model)
        paragraphs[slot] = text
        log("    %-30s wc=%d (target %d) attempts=%d" % (slot, wc, target, attempts))
    return paragraphs


# ===========================================================================
# Offline fallback paragraphs (no LLM) — derived from the same EVIDENCE
# ===========================================================================
def _offline_sentence(EVIDENCE, p, labels):
    ev = EVIDENCE.get(p, {})
    if not ev:
        return ""
    cur_groups = {}
    for region in REGIONS:
        if region not in ev: continue
        nar = ev[region]["current"]["narrative"]
        cur_groups.setdefault(nar, []).append(region.replace("_", "-"))
    parts = []
    for nar, regs in list(cur_groups.items())[:3]:
        parts.append("%s in %s" % (nar, ", ".join(regs)))
    return "; ".join(parts)

def offline_paragraphs(EVIDENCE, labels, year):
    para = {}
    cur = labels["current"]; fc = labels["forecast"]
    has_sf = _has_streamflow(EVIDENCE)
    if has_sf:
        para["page1_summary"] = ("In %s, observed conditions across India were: %s. The %s forecast continues broadly similar regional patterns across rainfall, temperature, soil moisture, runoff, evapotranspiration and streamflow." % (cur, _offline_sentence(EVIDENCE, "P", labels), fc))
    else:
        para["page1_summary"] = ("In %s, observed conditions across India were: %s. The %s forecast continues broadly similar regional patterns across rainfall, temperature, soil moisture, runoff and evapotranspiration." % (cur, _offline_sentence(EVIDENCE, "P", labels), fc))
    para["page1_rainfall_temp"] = ("In %s, rainfall showed %s. Temperatures showed %s. Forecasts for %s continue these regional tendencies." % (cur, _offline_sentence(EVIDENCE, "P", labels), _offline_sentence(EVIDENCE, "T", labels), fc))
    para["page1_sm_ro_et"] = ("In %s, soil moisture showed %s; runoff showed %s; evapotranspiration showed %s. Similar conditions are expected in %s." % (cur, _offline_sentence(EVIDENCE, "sm", labels), _offline_sentence(EVIDENCE, "ro", labels), _offline_sentence(EVIDENCE, "ET", labels), fc))
    if has_sf:
        para["page1_rivers"] = ("In %s, streamflow in rivers and reaches showed %s, while gauge stations showed %s. The %s forecast continues these patterns." % (cur, _offline_sentence(EVIDENCE, "Q", labels), _offline_sentence(EVIDENCE, "Station_Q", labels), fc))
    for slot, p in [("page2_rainfall_yellow","P"),("page3_temperature_yellow","T"),("page4_wetness_yellow","sm"),("page5_runoff_yellow","ro"),("page6_et_yellow","ET")]:
        para[slot] = ("In %s, %s. Similar conditions are forecast for %s." % (cur, _offline_sentence(EVIDENCE, p, labels), fc))
    # clamp every paragraph to its box budget
    for slot in list(para.keys()):
        if slot in TARGETS:
            tol = TOLERANCE_BY_SLOT.get(slot, TOLERANCE)
            para[slot] = _clamp_to_budget(para[slot], int(round(TARGETS[slot] * (1 + tol))))
    return para


# ===========================================================================
# LaTeX template (VERBATIM from the notebook) + substitution + compile
# ===========================================================================
TEX_TEMPLATE = r'''
% ============================================================
% India Hydrological Outlook - AUTO-GENERATED
% Page size: 20x20 inches
% ============================================================
\documentclass[20pt]{extarticle}

\usepackage[paperwidth=20in, paperheight=20in, margin=0in]{geometry}
\usepackage{graphicx}
\usepackage[table]{xcolor}
\usepackage{tikz}
\usepackage{tcolorbox}
\usepackage{ragged2e}
\usepackage{xurl}
\usepackage{hyperref}
\usepackage{array}
\usepackage{fontspec}
\setmainfont{{{T_pdf_font}}}
\setsansfont{{{T_pdf_font}}}
\newfontfamily\latinfont{Carlito}
\renewcommand{\familydefault}{\sfdefault}
{{T_fontfallback}}
\usepackage{microtype}
\tcbuselibrary{skins}
\usetikzlibrary{positioning, calc}

\graphicspath{{images/}}

\definecolor{titleblue}{HTML}{0F2F4A}
\definecolor{textblack}{HTML}{1A1A1A}
\definecolor{summarygold}{HTML}{F5E9A8}
\definecolor{summarygoldborder}{HTML}{4D7A38}
\definecolor{sidebandorange}{HTML}{E89640}
\definecolor{footerblue}{HTML}{B7D3E5}
\definecolor{linkblue}{HTML}{1A5BAA}

\hypersetup{colorlinks=true, urlcolor=linkblue, linkcolor=linkblue}
\setlength{\parindent}{0pt}
\pagestyle{empty}
\hbadness=10000 \vbadness=10000

\newcommand{\pageribbon}{%
\begin{tikzpicture}[remember picture, overlay]
  \fill[sidebandorange] ([xshift=-0.85in]current page.north east) rectangle
                        ([xshift=0in]current page.south east);
  \node[rotate=-90, text=white, font=\Huge\bfseries, anchor=center]
    at ([xshift=-0.42in,yshift=-4.0in]current page.north east) {{{RIBBON_DATE}}};
  \node[rotate=-90, text=white, font=\Huge\bfseries, anchor=center]
    at ([xshift=-0.42in,yshift=-13.5in]current page.north east) { {{T_ribbon_title}} };
\end{tikzpicture}%
}

\newcommand{\pageheader}[1]{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north west, inner sep=0pt] at (0.4,-0.25) {%
    \includegraphics[height=1.4in]{WCL_Logo_cropped.png}%
  };
  \node[anchor=center, text=titleblue, font=\Large\bfseries, align=center, text width=14in]
    at (9.5,-0.55) {#1};
  \node[anchor=center, text=textblack, font=\normalsize, align=center]
    at (9.5,-1.05) { {{T_based_on}} };
  \node[anchor=east, text=textblack, font=\normalsize] at (18.95,-1.05) { {{T_issue_date_label}} };
\end{tikzpicture}%
}

\newcommand{\pagenum}[1]{%
\begin{tikzpicture}[remember picture, overlay]
  \node[text=white, font=\LARGE\bfseries, anchor=center]
    at ([xshift=-0.42in,yshift=0.5in]current page.south east) {#1};
\end{tikzpicture}%
}

\newcommand{\pagefooterbanner}{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.south west)]
  \node[anchor=south west, inner sep=0pt] at (0.5,0.5) {%
    \begin{minipage}{18.4in}
      \begin{tcolorbox}[colback=footerblue, colframe=footerblue, boxrule=0.8pt, arc=8pt,
                        left=18pt, right=18pt, top=14pt, bottom=14pt]
      {\normalsize\textcolor{textblack}{ {{T_footer_blurb}}~\href{http://www.indiahydrolook.in}{{\latinfont www.indiahydrolook.in}}}}
      \end{tcolorbox}
    \end{minipage}%
  };
\end{tikzpicture}%
}

\newcommand{\pagefooterfull}{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.south west)]
  \node[anchor=south west, inner sep=0pt] at (0.5,2.05) {%
    \begin{minipage}{18.4in}
      \begin{tcolorbox}[colback=footerblue, colframe=footerblue, boxrule=0.8pt, arc=8pt,
                        left=18pt, right=18pt, top=14pt, bottom=14pt]
      {\normalsize\textcolor{textblack}{ {{T_footer_blurb}}~\href{http://www.indiahydrolook.in}{{\latinfont www.indiahydrolook.in}}}}
      \end{tcolorbox}
    \end{minipage}%
  };
  \node[anchor=west, inner sep=0pt] at (0.5,1.0) {\includegraphics[height=1.55in]{IITGN.png}};
  \node[anchor=west, inner sep=0pt, text=textblack] at (2.3,1.0) {%
    \begin{tabular}{@{}l@{}}
      {\large\textbf{\textcolor{titleblue}{\latinfont IIT Gandhinagar}}}\\[3pt]
      {\normalsize\textcolor{textblack}{\latinfont Indian Institute of}}\\[2pt]
      {\normalsize\textcolor{textblack}{\latinfont Technology Gandhinagar}}
    \end{tabular}};
  \node[anchor=center, inner sep=0pt] at (9.5,1.0) {\includegraphics[height=1.6in]{WCL_Logo_cropped.png}};
  \node[anchor=east, inner sep=0pt] at (18.85,1.0) {\includegraphics[height=1.75in]{IMD_nobg.png}};
\end{tikzpicture}%
}

\newcommand{\summaryat}[2]{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north west, inner sep=0pt] at (0.5,-#1) {%
    \begin{minipage}{18.4in}
      \begin{tcolorbox}[colback=summarygold, colframe=summarygoldborder,
                        boxrule=0pt, leftrule=3pt, arc=0pt, sharp corners,
                        left=14pt, right=14pt, top=10pt, bottom=10pt]
      {\normalsize\textbf{ {{T_summary_label}} } #2}
      \end{tcolorbox}
    \end{minipage}%
  };
\end{tikzpicture}%
}

\newcommand{\summaryone}[2]{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north west, inner sep=0pt] at (0.5,-#1) {%
    \begin{minipage}{18.4in}
      \begin{tcolorbox}[colback=footerblue, colframe=footerblue, boxrule=0.8pt, arc=8pt,
                        left=18pt, right=18pt, top=14pt, bottom=14pt]
      {\normalsize\textcolor{textblack}{\textbf{ {{T_summary_heading}} } #2}}
      \end{tcolorbox}
    \end{minipage}%
  };
\end{tikzpicture}%
}

\newcommand{\introat}[2]{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north west, inner sep=0pt] at (0.5,-#1) {%
    \begin{minipage}{18.4in}\justifying\normalsize\textcolor{textblack}{#2}\end{minipage}};
\end{tikzpicture}%
}

\newcommand{\imageat}[3]{%
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north, inner sep=0pt] at (9.5,-#1) {\includegraphics[width=18.3in,height=#2in,keepaspectratio]{#3}};
\end{tikzpicture}%
}

\newcommand{\circphoto}[2]{\includegraphics[width=#2in]{#1}}

\begin{document}

% ========== PAGE 1 ==========
\pageribbon
\pageheader{ {{T_ribbon_title}} }
\summaryone{1.7}{{{page1_summary}}}
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north west, inner sep=0pt] at (0.5,-5.9) {%
    \begin{minipage}[t]{8.2in}\vspace{0pt}
      {\textbf{\textcolor{titleblue}{\Large {{T_section_rainfall_temp}} }}}\\[0.5em]
      {\justifying\normalsize {{page1_rainfall_temp}}\par}
      \vspace{1.4em}
      {\textbf{\textcolor{titleblue}{\Large {{T_section_sm_ro_et}} }}}\\[0.5em]
      {\justifying\normalsize {{page1_sm_ro_et}}\par}
      {{PAGE1_RIVERS_BLOCK}}
    \end{minipage}};
  \node[anchor=north west, inner sep=0pt] at (9.2,-5.9) {%
    \begin{minipage}[t]{9.5in}\vspace{0pt}\centering
      \includegraphics[width=\linewidth]{1-s2_0-S0022169421010271-gr1_lrg.png}\\[0.5em]
      {\justifying\normalsize\textbf{ {{T_figure_label}} } {{T_figure_caption}} }
    \end{minipage}};
\end{tikzpicture}
\pagefooterfull\pagenum{1}\null\clearpage

% ========== PAGE 2 — Rainfall ==========
\pageribbon\pageheader{ {{T_title_rainfall}} }
\introat{1.9}{ {{T_intro_rainfall}} }
\summaryat{4.3}{{{page2_rainfall_yellow}}}
\imageat{5.7}{12.5}{Rainfall_dashboard.png}
\pagefooterbanner\pagenum{2}\null\clearpage

% ========== PAGE 3 — Temperature ==========
\pageribbon\pageheader{ {{T_title_temperature}} }
\introat{1.9}{ {{T_intro_temperature}} }
\summaryat{4.3}{{{page3_temperature_yellow}}}
\imageat{5.7}{12.5}{Temperature_dashboard.png}
\pagefooterbanner\pagenum{3}\null\clearpage

% ========== PAGE 4 — Relative Wetness ==========
\pageribbon\pageheader{ {{T_title_wetness}} }
\introat{1.9}{ {{T_intro_wetness}} }
\summaryat{4.3}{{{page4_wetness_yellow}}}
\imageat{5.7}{12.5}{Relative_Wetness_dashboard.png}
\pagefooterbanner\pagenum{4}\null\clearpage

% ========== PAGE 5 — Total Runoff ==========
\pageribbon\pageheader{ {{T_title_runoff}} }
\introat{1.9}{ {{T_intro_runoff}} }
\summaryat{4.3}{{{page5_runoff_yellow}}}
\imageat{5.7}{12.5}{Total_Runoff_dashboard.png}
\pagefooterbanner\pagenum{5}\null\clearpage

% ========== PAGE 6 — Evapotranspiration ==========
\pageribbon\pageheader{ {{T_title_et}} }
\introat{1.9}{ {{T_intro_et}} }
\summaryat{4.3}{{{page6_et_yellow}}}
\imageat{5.7}{12.5}{Evapotranspiration_dashboard.png}
\pagefooterbanner\pagenum{6}\null\clearpage

% ========== PAGE 7 — About / Disclaimer / Contact (STATIC) ==========
\pageribbon\pageheader{ {{T_ribbon_title}} }
\begin{tikzpicture}[remember picture, overlay, x=1in, y=1in, shift=(current page.north west)]
  \node[anchor=north west, inner sep=0pt] at (0.5,-2.2) {%
    \begin{minipage}[t]{9.0in}\vspace{0pt}
      {\textcolor{titleblue}{\LARGE {{T_about_heading}} }}\\[0.5em]
      {\justifying\normalsize\textcolor{textblack}{ {{T_about_body}} }\par}
      \vspace{1.3em}
      {\textcolor{titleblue}{\LARGE {{T_datasets_heading}} }}\\[0.5em]
      {\justifying\normalsize\textcolor{textblack}{ {{T_datasets_body_pre}}~\href{https://www.imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html}{{\latinfont https://www.imdpune.gov.in/cmpg/Griddata/Rainfall\_25\_Bin.html}}. {{T_datasets_body_mid}}~\href{https://indiawris.gov.in/wris\#RiverMonitoring}{{\latinfont https://indiawris.gov.in/wris\#RiverMonitoring}}.}\par}
      \vspace{1.3em}
      {\textcolor{titleblue}{\LARGE {{T_model_heading}} }}\\[0.5em]
      {\justifying\normalsize\textcolor{textblack}{ {{T_model_body}} }\par}
    \end{minipage}};
  \node[anchor=north west, inner sep=0pt] at (9.9,-2.2) {%
    \begin{minipage}[t]{8.9in}\vspace{0pt}
      {\textcolor{titleblue}{\LARGE {{T_disclaimer_heading}} }}\\[0.5em]
      {\justifying\normalsize\textcolor{textblack}{ {{T_disclaimer_body}} }\par}
      \vspace{0.7em}
      {\justifying\normalsize\textcolor{textblack}{ {{T_funding_body}} }\par}
      \vspace{1.3em}
      {\textcolor{titleblue}{\LARGE {{T_contact_heading}} }}\\[0.5em]
      {\normalsize\textcolor{textblack}{ {{T_contact_address}}~\href{http://www.indiahydrolook.in}{ {{T_contact_lab_link}} }}\par}
      \vspace{0.4em}
      {\normalsize\textcolor{textblack}{ {{T_contact_portal_label}}~\href{http://www.indiahydrolook.in}{{\latinfont www.indiahydrolook.in}}}\par}
      \vspace{1.0em}
      \begin{tabular}{@{}m{2.0in}@{\hspace{0.3in}}m{6.5in}@{}}
      \circphoto{prof_vimal_circle.png}{1.9} &
      \normalsize\textcolor{textblack}{ {{T_person1_name}}\newline
      {{T_person1_dept}}\newline
      \textbf{ {{T_person1_email_label}} }: {\latinfont vmishra@iitgn.ac.in}\newline
      \textbf{ {{T_person1_office_label}} }: {{T_person1_office}} }
      \end{tabular}
      \vspace{1.2em}
      \begin{tabular}{@{}>{\centering\arraybackslash}m{4.2in}@{\hspace{0.3in}}>{\centering\arraybackslash}m{4.2in}@{}}
      \circphoto{devesh_circle.png}{1.9} &
      \circphoto{paras_circle.png}{1.9} \\[0.4em]
      \normalsize\textcolor{textblack}{ {{T_person2_name}}\newline {{T_person2_role}}\newline {{T_affiliation}}\newline {\latinfont 24350007@iitgn.ac.in}} &
      \normalsize\textcolor{textblack}{ {{T_person3_name}}\newline {{T_person3_role}}\newline {{T_affiliation}}\newline {\latinfont paras.sharma@iitgn.ac.in}}
      \end{tabular}
    \end{minipage}};
\end{tikzpicture}
\pagefooterfull\pagenum{7}\null

\end{document}
'''


def latex_escape(s):
    if s is None:
        return ""
    repl = [("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
            ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"),
            ("~", r"\textasciitilde{}"), ("^", r"\textasciicircum{}")]
    for a, b in repl:
        s = s.replace(a, b)
    return s


# Latin run = letters/digits plus the punctuation that holds URLs/emails together.
_LATIN_RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,:/@()\-]*[A-Za-z0-9)]|[A-Za-z0-9]")

def _wrap_latin(escaped):
    """Wrap maximal Latin/ASCII runs in \\latinfont{...} so they render in Carlito even when the
    document's main font is an Indic script font (which lacks Latin glyphs). Operates on an
    ALREADY latex-escaped string; leaves Devanagari/other-script text untouched."""
    out = []
    last = 0
    for m in _LATIN_RUN.finditer(escaped):
        run = m.group(0)
        # skip pure spaces/punctuation with no alphanumerics (regex guarantees >=1, but be safe)
        if not re.search(r"[A-Za-z0-9]", run):
            continue
        out.append(escaped[last:m.start()])
        out.append(r"{\latinfont " + run + "}")
        last = m.end()
    out.append(escaped[last:])
    return "".join(out)


def _ordinal(d):
    if 10 <= d % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return "%d%s" % (d, suf)


def _fill_date(text, placeholder, date_str):
    """Substitute a date into a (possibly translated) label string.
    If the placeholder is intact, replace it. If the translator mangled it into stray
    digits/punctuation (e.g. 'मुद्दे की तारीख: 0000'), strip that trailing junk and append
    the real date, so the header never shows a corrupted value."""
    if placeholder in text:
        return text.replace(placeholder, date_str)
    # placeholder lost in translation: drop a trailing run of digit/space/punct junk
    base = re.sub(r"[\s0-9Oo।.:：/\\\-]+$", "", text).rstrip()
    sep = ": " if base and base[-1] not in ":：" else " "
    return (base + sep + date_str).strip()


def _flatten_pdf_texts(texts, pdf_font):
    """Map the pdf.json structure to the flat {{T_...}} placeholder names used in the template."""
    pt = texts.get("page_titles", {}); pi = texts.get("page_intros", {}); ab = texts.get("about", {})
    is_latin = (pdf_font or "Carlito").strip().lower() == "carlito"
    # For non-Latin main fonts we wrap Latin/digit runs (URLs, emails, acronyms) in \latinfont
    # at substitution time (see _wrap_latin). No global font-switching package — those break
    # inside TikZ nodes and braced macro arguments.
    fontfallback = ""
    m = {
        "T_pdf_font": pdf_font,
        "T_fontfallback": fontfallback,
        "T_ribbon_title": texts.get("ribbon_title", "India Hydrological Outlook"),
        "T_based_on": texts.get("based_on", "Based on daily observations till {observation_date}"),
        "T_issue_date_label": texts.get("issue_date", "Issue date: {issue_date}"),
        "T_summary_heading": texts.get("summary_heading", "SUMMARY:"),
        "T_summary_label": texts.get("summary_label") or texts.get("summary_heading", "Summary:"),
        "T_footer_blurb": texts.get("footer_blurb", ""),
        "T_section_rainfall_temp": texts.get("section_rainfall_temp", "Rainfall and Temperature:"),
        "T_section_sm_ro_et": texts.get("section_sm_ro_et", "Soil moisture, Total runoff, and Evapotranspiration:"),
        "T_section_rivers": texts.get("section_rivers", "River flows:"),
        "T_figure_label": texts.get("figure_label", "Figure."),
        "T_figure_caption": texts.get("figure_caption", ""),
        "T_title_rainfall": pt.get("rainfall", ""), "T_title_temperature": pt.get("temperature", ""),
        "T_title_wetness": pt.get("wetness", ""), "T_title_runoff": pt.get("runoff", ""),
        "T_title_et": pt.get("et", ""),
        "T_intro_rainfall": pi.get("rainfall", ""), "T_intro_temperature": pi.get("temperature", ""),
        "T_intro_wetness": pi.get("wetness", ""), "T_intro_runoff": pi.get("runoff", ""),
        "T_intro_et": pi.get("et", ""),
        "T_about_heading": ab.get("about_heading", ""), "T_about_body": ab.get("about_body", ""),
        "T_datasets_heading": ab.get("datasets_heading", ""),
        "T_datasets_body_pre": ab.get("datasets_body_pre", ""),
        "T_datasets_body_mid": ab.get("datasets_body_mid", ""),
        "T_model_heading": ab.get("model_heading", ""), "T_model_body": ab.get("model_body", ""),
        "T_disclaimer_heading": ab.get("disclaimer_heading", ""),
        "T_disclaimer_body": ab.get("disclaimer_body", ""),
        "T_funding_body": ab.get("funding_body", ""),
        "T_contact_heading": ab.get("contact_heading", ""),
        "T_contact_address": ab.get("contact_address", ""),
        "T_contact_lab_link": ab.get("contact_lab_link", "Water & Climate Lab"),
        "T_contact_portal_label": ab.get("contact_portal_label", ""),
        "T_person1_name": ab.get("person1_name", ""), "T_person1_dept": ab.get("person1_dept", ""),
        "T_person1_email_label": ab.get("person1_email_label", "Email"),
        "T_person1_office_label": ab.get("person1_office_label", "Office"),
        "T_person1_office": ab.get("person1_office", ""),
        "T_person2_name": ab.get("person2_name", ""), "T_person2_role": ab.get("person2_role", ""),
        "T_person3_name": ab.get("person3_name", ""), "T_person3_role": ab.get("person3_role", ""),
        "T_affiliation": ab.get("affiliation", "IIT Gandhinagar"),
    }
    return m


def build_latex_pdf(repo, out_base, date_str, labels, year, month, day,
                    paragraphs, dashboards_src, texts=None, pdf_font="Carlito",
                    out_name=None, log=print):
    """Assemble images + substitute paragraphs into the verbatim template + compile
    with XeLaTeX (twice). Writes Output/<out_name> and the archive copy.
    `texts` is the loaded pdf.json dict for the target language; `pdf_font` the script font."""
    repo = Path(repo).resolve(); out_base = Path(out_base).resolve()
    dashboards_src = Path(dashboards_src).resolve()
    build_dir = repo / ".pdf_build"
    images_dir = build_dir / "images"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    images_dir.mkdir(parents=True)

    # static images shipped in the repo
    static_dir = repo / "Hydrologic_Outlook" / "PDF_images"
    STATIC_FILES = ["WCL_Logo_cropped.png", "IITGN.png", "IMD_nobg.png",
                    "1-s2_0-S0022169421010271-gr1_lrg.png",
                    "prof_vimal_circle.png", "devesh_circle.png", "paras_circle.png"]
    for fn in STATIC_FILES:
        src = static_dir / fn
        if not src.exists():
            raise FileNotFoundError("Static image missing: %s" % src)
        shutil.copy(src, images_dir / fn)

    # dashboards — five grid products only (streamflow pages 7 & 8 have been removed)
    DASH_FILES = ["Rainfall_dashboard.png", "Temperature_dashboard.png", "Relative_Wetness_dashboard.png",
                  "Total_Runoff_dashboard.png", "Evapotranspiration_dashboard.png"]
    missing = []
    for fn in DASH_FILES:
        src = Path(dashboards_src) / fn
        if src.exists():
            shutil.copy(src, images_dir / fn)
        else:
            missing.append(fn)
    if missing:
        raise FileNotFoundError("Dashboards missing for the PDF: %s" % missing)

    # dates
    snap = datetime(year, month, day)
    issue = snap + timedelta(days=1)
    observation_date = "%s %s %d" % (_ordinal(day), labels["current"], year)
    issue_date_str = "%02d.%02d.%d" % (issue.day, issue.month, issue.year)
    ribbon_date = "%s %d" % (labels["current"], year)

    texts = texts or {}
    tmap = _flatten_pdf_texts(texts, pdf_font)
    is_latin_font = (pdf_font or "Carlito").strip().lower() == "carlito"

    def esc(s):
        """Escape for LaTeX; for Indic fonts also wrap Latin runs in \\latinfont."""
        e = latex_escape(s)
        return e if is_latin_font else _wrap_latin(e)

    # the "based on" / "issue date" header strings carry their own date placeholders.
    # sarvam-translate sometimes mangles the placeholder token (e.g. turning it into stray
    # digits like "0000"); _fill_date is tolerant of that so the date is always correct.
    tmap["T_based_on"] = esc(_fill_date(tmap["T_based_on"], "{observation_date}", observation_date))
    tmap["T_issue_date_label"] = esc(_fill_date(tmap["T_issue_date_label"], "{issue_date}", issue_date_str))

    tex = TEX_TEMPLATE
    tex = tex.replace("{{RIBBON_DATE}}", esc(ribbon_date))

    # Page-1 "River flows" section is included only when streamflow prose was produced
    # (i.e. that data was available). Otherwise the section is omitted entirely.
    if paragraphs.get("page1_rivers"):
        rivers_heading = tmap.get("T_section_rivers", "River flows:")
        rivers_block = (r"\vspace{1.4em}" + "\n"
                        r"{\textbf{\textcolor{titleblue}{\Large " + esc(rivers_heading) + r"}}}\\[0.5em]" + "\n"
                        r"{\justifying\normalsize " + esc(paragraphs["page1_rivers"]) + r"\par}")
    else:
        rivers_block = ""
    tex = tex.replace("{{PAGE1_RIVERS_BLOCK}}", rivers_block)

    # static UI text (titles, intros, About page, captions). T_pdf_font/T_fontfallback are raw
    # LaTeX/font names and must not be escaped or wrapped; the date strings were handled above.
    no_escape = {"T_pdf_font", "T_based_on", "T_issue_date_label", "T_fontfallback"}
    for k, v in tmap.items():
        val = v if k in no_escape else esc(v)
        tex = tex.replace("{{" + k + "}}", val)

    # dynamic LLM paragraphs
    for slot, text in paragraphs.items():
        tex = tex.replace("{{" + slot + "}}", esc(text))

    tex_path = build_dir / "hydrolook.tex"
    tex_path.write_text(tex, encoding="utf-8")
    log("  wrote %s (%d chars); images: %d files" % (tex_path.name, len(tex), len(list(images_dir.iterdir()))))

    def run_xelatex():
        cmd = ["xelatex", "-interaction=nonstopmode", "-halt-on-error",
               "-output-directory", str(build_dir), str(tex_path)]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=str(build_dir))

    log("  xelatex pass 1 ...")
    r1 = run_xelatex()
    if r1.returncode != 0:
        log(r1.stdout[-2500:]); raise RuntimeError("xelatex pass 1 failed")
    log("  xelatex pass 2 ...")
    r2 = run_xelatex()
    if r2.returncode != 0:
        log(r2.stdout[-2500:]); raise RuntimeError("xelatex pass 2 failed")

    out_pdf_local = build_dir / "hydrolook.pdf"
    if not out_pdf_local.exists():
        raise RuntimeError("xelatex returned 0 but no PDF found")

    final_name = out_name or ("Hydrolook_%s.pdf" % date_str)
    archive_dir = out_base / "PDF_Archive"
    out_base.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    # remove only a prior copy with the SAME name (don't wipe other languages/months)
    for d in (out_base, archive_dir):
        old = d / final_name
        if old.exists():
            try: old.unlink()
            except OSError: pass
    latest_path = out_base / final_name
    shutil.copy(out_pdf_local, latest_path)
    archive_path = archive_dir / final_name
    shutil.copy(out_pdf_local, archive_path)
    shutil.rmtree(build_dir, ignore_errors=True)
    log("  PDF: wrote %s (%.1f MB) + archive copy" % (final_name, latest_path.stat().st_size / 1e6))
    return latest_path
