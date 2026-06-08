#!/usr/bin/env python3
# =============================================================================
# translate_ui_strings.py
#   Translate the website's static UI strings into every language, via gemma4.
#
# Source  : Texts/ui.en.json   (English; extracted from the pages' data-i18n keys)
# Output  : assets/i18n/<Language>.json   (one file per language; what i18n.js loads)
#
# Cached + resumable per language (skips a language whose file exists; --force to
# redo). The UI strings change rarely, so the workflow is: run once, eyeball,
# commit assets/i18n/*.json.
#
# NOTE: there are ~480 strings, so all languages is a long one-time job on a small
# model. Run it in the background and, for quality on the harder scripts, use a
# bigger model:  IDM_LLM_MODEL=gemma4:e4b python3 translate_ui_strings.py
#
#   python3 translate_ui_strings.py                 # all languages, cached
#   python3 translate_ui_strings.py --langs Hindi   # one language
#   python3 translate_ui_strings.py --force         # redo even if present
# =============================================================================

import argparse
import json
import re
from pathlib import Path

import idm_llm

REPO = Path(__file__).resolve().parent
SRC = REPO / "Texts" / "ui.en.json"
LANGS = REPO / "Texts" / "languages.json"
OUTDIR = REPO / "assets" / "i18n"

_PH = re.compile(r"\{[^}{]+\}")
# Product name(s) that must NEVER be translated -- they should read identically in every
# language (like "Google"). We keep them verbatim the simplest leak-proof way: translate
# only the text AROUND them and never send the name to the model, so it cannot be
# transliterated. {curly} placeholders are kept verbatim the same way. This protection is
# deliberately scoped to the STATIC UI STRINGS only -- dynamic text and PDFs are untouched.
_KEEP = ("India Drought Monitor",)
# one matcher for every span we keep verbatim: a product name OR a {curly} placeholder
_KEEP_RE = re.compile("|".join([re.escape(k) for k in _KEEP] + [_PH.pattern]))


def _tr_plain(text, lang_label):
    """Translate a run of ordinary text; keep the English for it if the model errors."""
    try:
        return idm_llm.translate(text, lang_label)
    except Exception as e:  # noqa: BLE001
        print("        (kept English: %s)" % str(e)[:70])
        return text


def _tr(val, lang_label):
    if not isinstance(val, str) or not val.strip():
        return val
    out, pos = [], 0
    for m in _KEEP_RE.finditer(val):
        gap = val[pos:m.start()]
        if gap.strip():
            lead = gap[:len(gap) - len(gap.lstrip())]
            trail = gap[len(gap.rstrip()):]
            out.append(lead + _tr_plain(gap.strip(), lang_label) + trail)
        else:
            out.append(gap)
        out.append(m.group(0))          # the product name / placeholder, kept verbatim
        pos = m.end()
    tail = val[pos:]
    if tail.strip():
        lead = tail[:len(tail) - len(tail.lstrip())]
        trail = tail[len(tail.rstrip()):]
        out.append(lead + _tr_plain(tail.strip(), lang_label) + trail)
    else:
        out.append(tail)
    return "".join(out)


def _walk(obj, lang_label, n):
    if isinstance(obj, dict):
        return dict((k, _walk(v, lang_label, n)) for k, v in obj.items())
    if isinstance(obj, str):
        n[0] += 1
        if n[0] % 25 == 0:
            print("        ... %d strings" % n[0], flush=True)
        return _tr(obj, lang_label)
    return obj


def main():
    ap = argparse.ArgumentParser(description="Translate website UI strings via gemma4.")
    ap.add_argument("--langs", nargs="+", default=None,
                    help="languages to do (default: all in Texts/languages.json)")
    ap.add_argument("--force", action="store_true", help="overwrite existing files")
    args = ap.parse_args()

    if not SRC.exists():
        print("! missing %s (run the extractor first)" % SRC); return 1
    english = json.loads(SRC.read_text(encoding="utf-8"))
    languages = json.loads(LANGS.read_text(encoding="utf-8"))["languages"]
    want = set(x.lower() for x in args.langs) if args.langs else None

    done = skipped = 0
    for lang in languages:
        key = lang["key"]
        if key == "English":
            continue
        if want and key.lower() not in want and str(lang.get("code", "")).lower() not in want:
            continue
        out = OUTDIR / (key + ".json")
        if out.exists() and not args.force:
            print("  skip  %-10s (assets/i18n/%s.json exists)" % (key, key)); skipped += 1
            continue
        label = lang.get("label") or key
        n = [0]
        print("translating UI strings -> %-10s via %s ..." % (key, idm_llm.MODEL))
        translated = _walk(english, label, n)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(translated, ensure_ascii=False, indent=2), encoding="utf-8")
        print("  wrote assets/i18n/%s.json (%d strings)" % (key, n[0])); done += 1

    print("\nUI-string translation: %d written, %d skipped." % (done, skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
