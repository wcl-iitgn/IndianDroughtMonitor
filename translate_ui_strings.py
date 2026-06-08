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
# Proper-noun / brand strings that must NEVER be translated -- they should read
# identically in every language (like "Google"). Protected the same way as the
# {curly} placeholders: swapped for a sentinel before translation, restored after.
_KEEP = ("India Drought Monitor",)


def _protect(s):
    holders = []
    # brand / do-not-translate terms first, so the product name survives verbatim
    for term in _KEEP:
        while term in s:
            s = s.replace(term, "ZZPH%dZZ" % len(holders), 1)
            holders.append(term)
    # {curly} placeholders
    for h in _PH.findall(s):
        s = s.replace(h, "ZZPH%dZZ" % len(holders), 1)
        holders.append(h)
    return s, holders


def _restore(s, holders):
    for i, h in enumerate(holders):
        s = re.sub(r"ZZPH\s*%d\s*ZZ" % i, lambda _m: h, s)
    return s


def _tr(val, lang_label):
    if not isinstance(val, str) or not val.strip():
        return val
    prot, holders = _protect(val)
    try:
        out = idm_llm.translate(prot, lang_label)
    except Exception as e:  # noqa: BLE001
        print("        (kept English: %s)" % str(e)[:70]); out = prot
    return _restore(out, holders)


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
