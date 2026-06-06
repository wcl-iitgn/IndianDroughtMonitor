#!/usr/bin/env python3
# =============================================================================
# extract_ui_strings.py
#   Rebuild Texts/ui.en.json (the English master for the language picker) from
#   the data-i18n / data-i18n-attr markers in the site's HTML pages.
#
#   Run this after adding or editing any page text that carries a data-i18n key,
#   then top up the translations:
#       python3 extract_ui_strings.py
#       python3 translate_ui_strings.py     # translates only the new keys
#
#   Keys present in the old master but no longer found in any page (e.g. strings
#   referenced only from JavaScript) are KEPT and listed, so nothing silently
#   disappears from the translation files.
# =============================================================================
import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parent
OUT = REPO / "Texts" / "ui.en.json"


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def main():
    flat = {}
    conflicts = []
    pages = sorted(REPO.glob("*.html"))
    for page in pages:
        soup = BeautifulSoup(page.read_text(encoding="utf-8"), "html.parser")
        # text / inner-HTML strings
        for el in soup.find_all(attrs={"data-i18n": True}):
            key = el["data-i18n"].strip()
            if not key:
                continue
            val = norm(el.decode_contents()) if el.has_attr("data-i18n-html") else norm(el.get_text())
            if key in flat:
                if flat[key] != val:
                    conflicts.append((key, page.name))
                continue
            flat[key] = val
        # attribute strings: data-i18n-attr="alt:group.key" (";"-separated for several)
        for el in soup.find_all(attrs={"data-i18n-attr": True}):
            for part in re.split(r"[;,]", el["data-i18n-attr"]):
                part = part.strip()
                if not part or ":" not in part:
                    continue
                attr, key = (x.strip() for x in part.split(":", 1))
                if not key:
                    continue
                val = norm(el.get(attr, ""))
                if key in flat:
                    if flat[key] != val:
                        conflicts.append((key, page.name))
                    continue
                flat[key] = val

    # previous master (to keep JS-only keys and report the diff)
    old_flat = {}
    if OUT.exists():
        old = json.loads(OUT.read_text(encoding="utf-8"))
        for g, sub in old.items():
            if isinstance(sub, dict):
                for k, v in sub.items():
                    old_flat[g + "." + k] = v
            else:
                old_flat[g] = sub

    kept = {k: v for k, v in old_flat.items() if k not in flat}
    merged = dict(flat)
    merged.update(kept)

    nested = {}
    for k, v in sorted(merged.items()):
        g, _, rest = k.partition(".")
        if not rest:
            g, rest = "misc", k
        nested.setdefault(g, {})[rest] = v

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(nested, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    added = sorted(set(flat) - set(old_flat))
    print("pages scanned        : %d" % len(pages))
    print("strings in master    : %d (%d groups)  ->  %s" % (len(merged), len(nested), OUT.relative_to(REPO)))
    print("new since last master: %d" % len(added))
    for k in added[:50]:
        print("   + " + k)
    if len(added) > 50:
        print("   ... and %d more" % (len(added) - 50))
    if kept:
        print("kept although not in HTML (likely used from JS): %d" % len(kept))
        for k in sorted(kept)[:10]:
            print("   = " + k)
    if conflicts:
        print("duplicate keys with differing text (first occurrence kept): %d" % len(conflicts))
        for k, p in conflicts[:10]:
            print("   ! %s  (%s)" % (k, p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
