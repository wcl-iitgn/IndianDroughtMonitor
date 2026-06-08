#!/usr/bin/env python3
# =============================================================================
# translate_pdf_strings.py
#   Translate the *static* text of the hydro (Hydrolook) PDF into every language.
#
# The hydro PDF has two kinds of text:
#   - dynamic : the 8 LLM paragraphs + the summary sentences (translated at build
#               time inside generate_hydro_outputs.py)
#   - static  : doc title, section headings, the long "page intro" explanations,
#               the whole About/Datasets/Model/Disclaimer/Contact block, footer, etc.
#               These live in Texts/English/pdf.json and, until now, fell back to
#               English for every language.
#
# This script translates that static file once per language, with gemma4 (idm_llm),
# and writes Texts/<lang>/pdf.json. hydro_pdf.py already prefers a per-language
# pdf.json (load_pdf_texts), so once these files exist the hydro PDFs are fully
# localised - static + dynamic.
#
# It is CACHED: a language whose pdf.json already exists is skipped (use --force to
# redo). The static text rarely changes, so the intended workflow is: run once,
# eyeball, then commit Texts/<lang>/pdf.json so it never has to run again.
#
# Care taken:
#   * proper nouns / codes / link anchors are kept verbatim (SKIP_KEYS)
#   * map-only labels (legend_titles, panel_tags) are left in English - they are
#     rendered into the PNGs by matplotlib, not into the LaTeX, so translating them
#     here would have no effect on the PDF
#   * {placeholders} like {observation_date} are protected and restored exactly
#   * multi-line values keep their line breaks (each line translated separately)
#
#   python3 translate_pdf_strings.py                 # all languages, cached
#   python3 translate_pdf_strings.py --langs Hindi   # one language
#   python3 translate_pdf_strings.py --force         # redo even if present
# =============================================================================

import argparse
import json
import re
from pathlib import Path

import idm_llm

REPO = Path(__file__).resolve().parent
ENG = REPO / "Texts" / "English" / "pdf.json"
LANGS = REPO / "Texts" / "languages.json"

# Leaf/sub-tree keys to keep EXACTLY as written (people, room/affiliation, link text,
# and the map-only label groups that never reach the LaTeX).
SKIP_KEYS = {
    "person1_name", "person2_name", "person3_name",
    "person1_dept", "person1_office", "affiliation", "contact_lab_link",
    "legend_titles", "panel_tags",
    # the two header strings carry {observation_date}/{issue_date}; keeping them English
    # guarantees the date substitution in hydro_pdf.py is never broken by translation.
    "based_on", "issue_date",
}

_PH = re.compile(r"\{[^}{}]+\}")


def _protect(s):
    """Swap {placeholders} for sentinels the translator won't touch; return (s, holders)."""
    holders = _PH.findall(s)
    for i, h in enumerate(holders):
        s = s.replace(h, "ZZPH%dZZ" % i, 1)
    return s, holders


def _restore(s, holders):
    for i, h in enumerate(holders):
        # tolerate the model inserting a space inside the sentinel
        s = re.sub(r"ZZPH\s*%d\s*ZZ" % i, lambda _m: h, s)
    return s


def _tr_value(val, lang_label, key=None, script=None):
    """Translate one string, preserving placeholders and any line breaks. If a line
    fails to translate, keep the English for that line and carry on. For single-line
    values, a width-aware character budget (pdf_layout_budget) keeps the translation to
    ~the English line count in its PDF slot; multi-line values fall back to the default
    source-relative bound."""
    if not isinstance(val, str) or not val.strip():
        return val
    nonblank = [ln for ln in val.split("\n") if ln.strip()]
    max_chars = None
    if len(nonblank) == 1:
        try:
            import pdf_layout_budget
            max_chars = pdf_layout_budget.budget_chars(key, val, script)
        except Exception:
            max_chars = None
    out = []
    for line in val.split("\n"):
        if not line.strip():
            out.append(line)
            continue
        prot, holders = _protect(line)
        try:
            tr = idm_llm.translate_bounded(prot, lang_label, max_chars=max_chars)
        except Exception as e:  # noqa: BLE001
            print("        (kept English for one line: %s)" % str(e)[:70])
            tr = prot
        out.append(_restore(tr, holders))
    return "\n".join(out)


def _walk(obj, lang_label, n, script=None, key=None):
    if isinstance(obj, dict):
        res = {}
        for k, v in obj.items():
            res[k] = v if k in SKIP_KEYS else _walk(v, lang_label, n, script=script, key=k)
        return res
    if isinstance(obj, list):
        return [_walk(x, lang_label, n, script=script, key=key) for x in obj]
    if isinstance(obj, str):
        n[0] += 1
        preview = obj.replace("\n", " ")[:46]
        print("      [%2d] %s%s" % (n[0], preview, "…" if len(obj) > 46 else ""), flush=True)
        return _tr_value(obj, lang_label, key=key, script=script)
    return obj


def main():
    ap = argparse.ArgumentParser(description="Translate hydro PDF static strings via gemma4.")
    ap.add_argument("--langs", nargs="+", default=None,
                    help="languages to do (default: all in Texts/languages.json)")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing Texts/<lang>/pdf.json")
    args = ap.parse_args()

    if not ENG.exists():
        print("! missing %s" % ENG); return 1
    english = json.loads(ENG.read_text(encoding="utf-8"))
    languages = json.loads(LANGS.read_text(encoding="utf-8"))["languages"]
    want = set(x.lower() for x in args.langs) if args.langs else None

    done = skipped = 0
    for lang in languages:
        key = lang["key"]
        if key == "English":
            continue
        if want and key.lower() not in want and str(lang.get("code", "")).lower() not in want:
            continue
        out = REPO / "Texts" / key / "pdf.json"
        if out.exists() and not args.force:
            print("  skip  %-10s (Texts/%s/pdf.json exists)" % (key, key)); skipped += 1
            continue
        label = lang.get("label") or key
        n = [0]
        print("  translating static strings -> %-10s via %s ..." % (key, idm_llm.MODEL))
        translated = _walk(english, label, n, script=lang.get("script"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8")
        print("    wrote Texts/%s/pdf.json (%d strings)" % (key, n[0])); done += 1

    print("\nStatic-string translation: %d written, %d skipped." % (done, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
